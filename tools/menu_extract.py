"""Menu extraction: a caterer's public menu page -> VendorMenu.

Fetch the page, strip it to text, have the LLM copy items and prices exactly as written, then
validate. Per-person prices are computed here in code (tray price / serves), never by the LLM.
If the page can't be read or has fewer than MIN_PRICED_ITEMS priced items, the menu is
"estimated" with no items: we never invent items or prices.

Owner: Person B
"""

import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Literal, Optional
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ValidationError

from shared.llm import chat_json
from shared.menu_schema import DietaryTag, MenuItem, VendorMenu, slugify

FETCH_TIMEOUT_S = 15
LLM_TIMEOUT_S = 45
MAX_TEXT_CHARS = 12_000
MIN_PRICED_ITEMS = 3
MAX_EXTRA_PAGES = 4  # menu/catering pages to try beyond the one we were given
MENU_LINK = re.compile(r"menu|cater|order|pricing|packages|platters|trays", re.I)
COMMON_MENU_PATHS = ("/catering", "/catering-menu", "/menu", "/menus")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

logger = logging.getLogger("menu_extract")

SKIP_TAGS = {"script", "style", "nav", "footer", "noscript", "svg", "iframe", "form"}
BLOCK_TAGS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "th"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self.links: list[tuple[str, str]] = []  # (href, link text)
        self.image = ""  # og:image, for the vendor's photo
        self._skip = 0
        self._in_title = False
        self._href: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") in ("og:image", "og:image:url") and a.get("content"):
            self.image = self.image or a["content"]
        if tag == "a" and a.get("href"):
            self._href = a["href"]
            self.links.append((a["href"], ""))
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._href is not None and self.links:
            self.links[-1] = (self.links[-1][0], (self.links[-1][1] + " " + data).strip())
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def parse_page(html: str) -> _Text:
    p = _Text()
    p.feed(html)
    return p


def page_text(html: str) -> tuple[str, str]:
    """(title, clean text) with blank lines collapsed, cut at a line boundary."""
    return _clean_text(parse_page(html))


def _clean_text(p: _Text) -> tuple[str, str]:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in "".join(p.parts).splitlines()]
    text, size = [], 0
    for ln in (ln for ln in lines if ln):
        if size + len(ln) + 1 > MAX_TEXT_CHARS:
            break
        text.append(ln)
        size += len(ln) + 1
    return p.title.strip(), "\n".join(text)


class _Extracted(BaseModel):
    """What the LLM returns per item: prices as written; code turns them into price_per_person."""
    name: str
    price: Optional[float] = None
    price_unit: Optional[Literal["person", "tray", "item"]] = None
    serves: Optional[int] = None
    description: Optional[str] = None
    dietary_tags: list[DietaryTag] = []


class _ExtractedMenu(BaseModel):
    items: list[_Extracted]


PROMPT = """You read a caterer's web page and copy its catering menu into JSON.
Business: {name}

Return ONLY a JSON object: {{"items": [{{"name": str, "price": number|null, "price_unit": "person"|"tray"|"item"|null,
"serves": integer|null, "description": str|null, "dietary_tags": [...]}}]}}

Rules:
- Copy every price exactly as written on the page (USD, number only). Never estimate, round or invent a price.
- Only include items that appear on the page. Never invent items.
- price_unit: "person" for per-person, per-guest, per-plate or per-box-lunch prices, and for a plated entrée,
  combo or meal that is one serving for one person (e.g. "Chicken Kabob Plate $19.50"); "tray" for trays, platters, packages or boxes that serve
  several people, "item" for single items. serves: how many people a tray serves, if the page says (use the
  lowest number of a range, e.g. "serves 10-15" -> 10); otherwise null.
- dietary_tags only from: vegetarian, vegan, halal, kosher, gluten-free, contains-nuts, contains-dairy; and only
  when the page labels the item that way. Never assume.
- If the page has no menu, return {{"items": []}}.
"""


def _per_person(item: _Extracted) -> Optional[float]:
    if item.price is None:
        return None
    if item.price_unit == "person":
        return round(item.price, 2)
    if item.price_unit == "tray" and item.serves:
        return round(item.price / item.serves, 2)
    return None  # per-item or unknown unit: not usable as a per-person price


async def _llm_items(name: str, text: str) -> Optional[list[MenuItem]]:
    """LLM extraction with one retry that includes the validation error."""
    system = PROMPT.format(name=name)
    user = text
    for attempt in range(2):
        try:
            data = await chat_json(system, user, max_tokens=4000, timeout_s=LLM_TIMEOUT_S,
                                   reasoning_effort="minimal")
            menu = _ExtractedMenu.model_validate(data or {})
        except ValidationError as e:
            logger.warning(f"[{name}] menu JSON invalid (attempt {attempt + 1}): {e.error_count()} errors")
            user = f"{text}\n\nYour previous JSON was invalid:\n{e}\nReturn corrected JSON only."
            continue
        except Exception as e:
            logger.warning(f"[{name}] menu extraction LLM call failed: {e.__class__.__name__}: {e}")
            return None
        return [
            MenuItem(name=i.name, price_per_person=_per_person(i), description=i.description,
                     dietary_tags=sorted(set(i.dietary_tags)), serves=i.serves)
            for i in menu.items if i.name.strip()
        ]
    return None


def menu_links(base_url: str, page: _Text) -> list[str]:
    """Same-site links that look like a menu or catering page, best guesses first."""
    host = urlparse(base_url).netloc.removeprefix("www.")
    seen, out = {base_url.rstrip("/")}, []
    candidates = [(h, txt) for h, txt in page.links if MENU_LINK.search(h) or MENU_LINK.search(txt)]
    candidates += [(path, "") for path in COMMON_MENU_PATHS]
    for href, _ in candidates:
        url = urljoin(base_url, href).split("#")[0].rstrip("/")
        if urlparse(url).netloc.removeprefix("www.") != host or url in seen or url.lower().endswith(".pdf"):
            continue
        seen.add(url)
        out.append(url)
    return out[:MAX_EXTRA_PAGES]


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[Optional[_Text], str]:
    """(parsed page, reason it couldn't be used)."""
    try:
        r = await client.get(url)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        return None, f"the site refused the request (HTTP {e.response.status_code})"
    except httpx.HTTPError as e:
        return None, f"couldn't fetch the page: {e.__class__.__name__}"
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        return None, "menu is a PDF (not supported yet)"
    if "html" not in ctype and "text" not in ctype:
        return None, f"unsupported page type {ctype!r}"
    return parse_page(r.text), ""


async def extract_menu_with_reason(
    url: str, name: Optional[str] = None, place_id: Optional[str] = None,
    phone: Optional[str] = None, email: Optional[str] = None, **extra,
) -> tuple[VendorMenu, str]:
    """Return (menu, reason). reason explains an "estimated" menu, or is "" when extracted.

    Reads the given page; if it has no usable prices, follows the site's menu/catering links
    (and common paths like /catering) and keeps the page with the most priced items.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        page, reason = await _fetch(client, url)
        title, text = _clean_text(page) if page else ("", "")
        name = name or re.split(r"\s[|\-–]\s", title)[0].strip() or url
        menu = VendorMenu(name=name, slug=slugify(name), source_url=url, place_id=place_id or "",
                          phone=phone or "", email=email or "", **extra)
        if page and page.image:
            menu.photo_url = urljoin(url, page.image)

        best: list[MenuItem] = []
        tried = []
        pages = [(url, page, text, reason)]
        if page:
            extra_urls = menu_links(url, page)
            fetched = await asyncio.gather(*(_fetch(client, u) for u in extra_urls))
            pages += [(u, p, _clean_text(p)[1] if p else "", why) for u, (p, why) in zip(extra_urls, fetched)]

        for page_url, p, page_text_, why in pages:
            if not p:
                tried.append(why)
                continue
            if len(page_text_) < 200:
                tried.append("page has almost no text (probably built with JavaScript)")
                continue
            items = await _llm_items(name, page_text_)
            priced = [i for i in (items or []) if i.price_per_person is not None]
            logger.info(f"[{menu.slug}] {page_url}: {len(priced)} priced items")
            if items is None:
                tried.append("couldn't read a valid menu from the page")
            elif len(priced) > len([i for i in best if i.price_per_person is not None]):
                best, menu.source_url = items, page_url
            if len(priced) >= MIN_PRICED_ITEMS:
                break  # good enough: don't spend more LLM calls

    priced = [i for i in best if i.price_per_person is not None]
    if len(priced) < MIN_PRICED_ITEMS:
        menu.source_url = url
        pages_read = len(pages)
        readable = sum(1 for _, p, txt, _ in pages if p and len(txt) >= 200)
        if best:
            detail = f"only {len(priced)} items with a per-person price"
        elif readable:
            detail = f"no prices on the {readable} page{'s' if readable != 1 else ''} we could read"
        else:
            detail = tried[0] if tried else "no menu found"
        return menu, f"{detail} (checked {pages_read} page{'s' if pages_read != 1 else ''})"
    menu.items = best
    menu.extraction_status = "extracted"
    return menu, ""


async def extract_menu(url: str, name: Optional[str] = None, place_id: Optional[str] = None,
                       phone: Optional[str] = None, email: Optional[str] = None) -> VendorMenu:
    menu, reason = await extract_menu_with_reason(url, name, place_id, phone, email)
    if reason:
        logger.info(f"[{menu.slug}] estimated: {reason}")
    return menu
