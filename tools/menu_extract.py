"""Menu extraction: a caterer's public menu page -> VendorMenu.

Fetch the page, strip it to text, have the LLM copy items and prices exactly as written, then
validate. Per-person prices are computed here in code (tray price / serves), never by the LLM.
If the page can't be read or has fewer than MIN_PRICED_ITEMS priced items, the menu is
"estimated" with no items: we never invent items or prices.

Owner: Person B
"""

import logging
import re
from html.parser import HTMLParser
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, ValidationError

from shared.llm import chat_json
from shared.menu_schema import DietaryTag, MenuItem, VendorMenu, slugify

FETCH_TIMEOUT_S = 15
LLM_TIMEOUT_S = 45
MAX_TEXT_CHARS = 12_000
MIN_PRICED_ITEMS = 3
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
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def page_text(html: str) -> tuple[str, str]:
    """(title, clean text) with blank lines collapsed, cut at a line boundary."""
    p = _Text()
    p.feed(html)
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
- price_unit: "person" for per-person, per-guest, per-plate or per-box-lunch prices, "tray" for trays, platters, packages or boxes that serve
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


async def extract_menu_with_reason(
    url: str, name: Optional[str] = None, place_id: Optional[str] = None,
    phone: Optional[str] = None, email: Optional[str] = None, **extra,
) -> tuple[VendorMenu, str]:
    """Return (menu, reason). reason explains an "estimated" menu, or is "" when extracted."""
    title, text, reason = "", "", ""
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url)
            r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            reason = "menu is a PDF (not supported yet)"
        elif "html" not in ctype and "text" not in ctype:
            reason = f"unsupported page type {ctype!r}"
        else:
            title, text = page_text(r.text)
            if len(text) < 200:
                reason = "page has almost no text (probably built with JavaScript)"
    except httpx.HTTPStatusError as e:
        reason = f"the site refused the request (HTTP {e.response.status_code})"
    except httpx.HTTPError as e:
        reason = f"couldn't fetch the page: {e.__class__.__name__}"

    name = name or re.split(r"\s[|\-–]\s", title)[0].strip() or url
    menu = VendorMenu(name=name, slug=slugify(name), source_url=url, place_id=place_id or "",
                      phone=phone or "", email=email or "", **extra)
    if reason:
        return menu, reason

    items = await _llm_items(name, text)
    if items is None:
        return menu, "couldn't read a valid menu from the page"
    priced = [i for i in items if i.price_per_person is not None]
    if len(priced) < MIN_PRICED_ITEMS:
        return menu, f"only {len(priced)} items with a per-person price (need {MIN_PRICED_ITEMS})"
    menu.items = items
    menu.extraction_status = "extracted"
    return menu, ""


async def extract_menu(url: str, name: Optional[str] = None, place_id: Optional[str] = None,
                       phone: Optional[str] = None, email: Optional[str] = None) -> VendorMenu:
    menu, reason = await extract_menu_with_reason(url, name, place_id, phone, email)
    if reason:
        logger.info(f"[{menu.slug}] estimated: {reason}")
    return menu
