"""Menu extraction: a caterer's public menu -> VendorMenu.

Reads the page we're given and, if it has no usable prices, the site's own menu/catering pages
(links, PDFs, and common paths like /catering), then menu platforms found by a web search
(tools/menu_search.py; only pages that show this business's phone or address). Each page is read as text first; if that has no prices,
its menu images or PDF pages are read with the vision model (OCR). The page with the most priced items
wins. The LLM only copies items and prices exactly as written; per-person prices are computed here in
code (tray price / serves). With fewer than MIN_PRICED_ITEMS priced items the menu is "estimated": it keeps
the real item names it found (shown as "price on request"), but is never priced. We never invent items or prices.

Owner: Person B
"""

import asyncio
import base64
import io
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal, Optional
from urllib.parse import urljoin, urlparse

import httpx
import pypdfium2
from PIL import Image
from pydantic import BaseModel, ValidationError

from shared.llm import chat_json
from shared.menu_schema import DietaryTag, MenuItem, VendorMenu, slugify
from tools.menu_search import AgentSearch, find_menu_pages, same_business

FETCH_TIMEOUT_S = 15
LLM_TIMEOUT_S = 45
MAX_TEXT_CHARS = 12_000
MIN_PRICED_ITEMS = 3
MAX_EXTRA_PAGES = 4  # menu/catering pages to try beyond the one we were given
MAX_OCR_IMAGES = 4  # menu images or PDF pages read with the vision model, per page
MAX_IMAGE_BYTES = 8_000_000
IMAGE_MAX_PX = 1600
MENU_LINK = re.compile(r"menu|cater|order|pricing|packages|platters|trays", re.I)
MENU_IMAGE = re.compile(r"menu|cater|price|platter|tray|package", re.I)
COMMON_MENU_PATHS = ("/catering", "/catering-menu", "/menu", "/menus")
PLACEHOLDER_IMAGE = re.compile(r"blank|placeholder|default|logo|favicon|s0\.wp\.com/i/", re.I)
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

logger = logging.getLogger("menu_extract")

SKIP_TAGS = {"script", "style", "nav", "footer", "noscript", "svg", "iframe", "form"}
BLOCK_TAGS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "th"}


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self.links: list[tuple[str, str]] = []  # (href, link text)
        self.images: list[tuple[str, str]] = []  # (src, alt/title)
        self.og_image = ""
        self._skip = 0
        self._in_title = False
        self._in_link = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("property") in ("og:image", "og:image:url") and a.get("content"):
            self.og_image = self.og_image or a["content"]
        elif tag == "a" and a.get("href"):
            self.links.append((a["href"], ""))
            self._in_link = True
        elif tag == "img":
            src = a.get("data-src") or a.get("src") or ""
            if src and not src.startswith("data:"):
                self.images.append((src, f"{a.get('alt', '')} {a.get('title', '')}".strip()))
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_link = False
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_link and self.links:
            self.links[-1] = (self.links[-1][0], (self.links[-1][1] + " " + data).strip())
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _clean(text: str) -> str:
    """Blank lines collapsed, cut at a line boundary."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    out, size = [], 0
    for ln in (ln for ln in lines if ln):
        if size + len(ln) + 1 > MAX_TEXT_CHARS:
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


def page_text(html: str) -> tuple[str, str]:
    """(title, clean text) of an HTML page."""
    p = _Html()
    p.feed(html)
    return p.title.strip(), _clean("".join(p.parts))


@dataclass
class Page:
    url: str
    kind: str = ""  # "html" | "pdf"
    text: str = ""
    title: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # data URLs for the vision model (PDF pages)
    image_urls: list[str] = field(default_factory=list)  # menu images on an HTML page, still to download
    og_image: str = ""
    error: str = ""


def _data_url(img: Image.Image) -> str:
    img = img.convert("RGB")
    img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _read_pdf(data: bytes) -> tuple[str, list[str]]:
    """(text layer, rendered pages as data URLs) of a PDF."""
    pdf = pypdfium2.PdfDocument(data)
    try:
        pages = [pdf[i] for i in range(min(len(pdf), MAX_OCR_IMAGES))]
        text = "\n".join(p.get_textpage().get_text_range() for p in pages)
        images = [_data_url(p.render(scale=2).to_pil()) for p in pages]
    finally:
        pdf.close()
    return _clean(text), images


async def _fetch(client: httpx.AsyncClient, url: str) -> Page:
    page = Page(url=url)
    try:
        r = await client.get(url)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        page.error = f"the site refused the request (HTTP {e.response.status_code})"
        return page
    except httpx.HTTPError as e:
        page.error = f"couldn't fetch the page: {e.__class__.__name__}"
        return page
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        try:
            page.kind = "pdf"
            page.text, page.images = await asyncio.to_thread(_read_pdf, r.content)
        except Exception as e:
            page.error = f"couldn't read the PDF: {e.__class__.__name__}"
        return page
    if "html" not in ctype and "text" not in ctype:
        page.error = f"unsupported page type {ctype!r}"
        return page
    parser = _Html()
    parser.feed(r.text)
    page.kind, page.title, page.text = "html", parser.title.strip(), _clean("".join(parser.parts))
    og = urljoin(url, parser.og_image) if parser.og_image else ""
    page.links, page.og_image = parser.links, "" if PLACEHOLDER_IMAGE.search(og) else og
    on_menu_page = bool(MENU_LINK.search(urlparse(url).path))
    page.image_urls = [urljoin(url, src) for src, alt in parser.images
                       if MENU_IMAGE.search(src) or MENU_IMAGE.search(alt) or on_menu_page][:MAX_OCR_IMAGES * 2]
    return page


async def _download_images(client: httpx.AsyncClient, urls: list[str]) -> list[str]:
    """Menu images as data URLs (the vision API only takes inline images); small icons are skipped."""
    async def one(u: str) -> Optional[str]:
        try:
            r = await client.get(u)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/") or len(r.content) > MAX_IMAGE_BYTES:
                return None
            img = Image.open(io.BytesIO(r.content))
            return _data_url(img) if min(img.size) >= 300 else None
        except Exception:
            return None
    found = await asyncio.gather(*(one(u) for u in urls))
    return [d for d in found if d][:MAX_OCR_IMAGES]


def menu_links(base_url: str, page: Page) -> list[str]:
    """Same-site links (pages or PDFs) that look like a menu or catering page, best guesses first."""
    host = urlparse(base_url).netloc.removeprefix("www.")
    seen, out = {base_url.rstrip("/")}, []
    candidates = [(h, txt) for h, txt in page.links if MENU_LINK.search(h) or MENU_LINK.search(txt)]
    candidates += [(path, "") for path in COMMON_MENU_PATHS]
    for href, _ in candidates:
        url = urljoin(base_url, href).split("#")[0].rstrip("/")
        if urlparse(url).netloc.removeprefix("www.") != host or url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        out.append(url)
    return out[:MAX_EXTRA_PAGES]


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


PROMPT = """You read a caterer's menu (web page text or a photo/scan of a menu) and copy it into JSON.
Business: {name}

Return ONLY a JSON object: {{"items": [{{"name": str, "price": number|null, "price_unit": "person"|"tray"|"item"|null,
"serves": integer|null, "description": str|null, "dietary_tags": [...]}}]}}

Rules:
- Copy every price exactly as written (USD, number only). Never estimate, round or invent a price.
- Only include items that appear in the menu. Never invent items.
- price_unit: "person" for per-person, per-guest, per-plate or per-box-lunch prices, and for a plated entrée,
  combo or meal that is one serving for one person (e.g. "Chicken Kabob Plate $19.50");
  "tray" for trays, platters, packages or boxes that serve
  several people, "item" for single items. serves: how many people a tray serves, if the menu says (use the
  lowest number of a range, e.g. "serves 10-15" -> 10); otherwise null.
- dietary_tags only from: vegetarian, vegan, halal, kosher, gluten-free, contains-nuts, contains-dairy; and only
  when the menu labels the item that way. Never assume.
- If there is no menu, return {{"items": []}}.
"""


def _per_person(item: _Extracted) -> Optional[float]:
    if item.price is None:
        return None
    if item.price_unit == "person":
        return round(item.price, 2)
    if item.price_unit == "tray" and item.serves:
        return round(item.price / item.serves, 2)
    return None  # per-item or unknown unit: not usable as a per-person price


def _to_items(menu: _ExtractedMenu) -> list[MenuItem]:
    return [
        MenuItem(name=i.name, price_per_person=_per_person(i), description=i.description,
                 dietary_tags=sorted(set(i.dietary_tags)), serves=i.serves)
        for i in menu.items if i.name.strip()
    ]


async def _llm_items(name: str, content: str | list) -> Optional[list[MenuItem]]:
    """LLM extraction from text or an image, with one retry that includes the validation error."""
    system = PROMPT.format(name=name)
    user = content
    for attempt in range(2):
        try:
            data = await chat_json(system, user, max_tokens=8000, timeout_s=LLM_TIMEOUT_S,
                                   reasoning_effort="minimal")
            return _to_items(_ExtractedMenu.model_validate(data or {}))
        except ValidationError as e:
            logger.warning(f"[{name}] menu JSON invalid (attempt {attempt + 1}): {e.error_count()} errors")
            fix = f"Your previous JSON was invalid:\n{e}\nReturn corrected JSON only."
            user = f"{content}\n\n{fix}" if isinstance(content, str) else [*content, {"type": "text", "text": fix}]
        except Exception as e:
            logger.warning(f"[{name}] menu extraction LLM call failed: {e.__class__.__name__}: {e}")
            return None
    return None


async def _ocr_items(name: str, images: list[str]) -> list[MenuItem]:
    """Read menu images (or PDF pages) with the vision model, one call per image."""
    results = await asyncio.gather(*(
        _llm_items(name, [{"type": "text", "text": "Read this menu image."}, {"type": "image_url", "image_url": {"url": img}}])
        for img in images
    ))
    return [item for items in results if items for item in items]


def _priced(items: list[MenuItem]) -> int:
    return sum(i.price_per_person is not None for i in items)


async def _read_page(client: httpx.AsyncClient, name: str, page: Page) -> tuple[list[MenuItem], str]:
    """(items, how they were read) for one page: text first, then OCR of its menu images or PDF pages."""
    items: list[MenuItem] = []
    how = ""
    if len(page.text) >= 200:
        items = await _llm_items(name, page.text) or []
        how = "text"
    if _priced(items) < MIN_PRICED_ITEMS:
        images = page.images or await _download_images(client, page.image_urls)
        if images:
            ocr = await _ocr_items(name, images)
            if _priced(ocr) > _priced(items):
                items, how = ocr, f"OCR of {len(images)} image{'s' if len(images) != 1 else ''}"
    logger.info(f"[{slugify(name)}] {page.url}: {_priced(items)} priced items ({how or 'nothing readable'})")
    return items, how


async def extract_menu_with_reason(
    url: str, name: Optional[str] = None, place_id: Optional[str] = None,
    phone: Optional[str] = None, email: Optional[str] = None, agent_search: Optional[AgentSearch] = None, **extra,
) -> tuple[VendorMenu, str]:
    """Return (menu, reason). reason explains an "estimated" menu, or is "" when extracted."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        first = await _fetch(client, url)
        name = name or re.split(r"\s[|\-–]\s", first.title)[0].strip() or url
        menu = VendorMenu(name=name, slug=slugify(name), source_url=url, place_id=place_id or "",
                          phone=phone or "", email=email or "", photo_url=first.og_image, **extra)
        pages = [first]
        if first.kind == "html":
            pages += await asyncio.gather(*(_fetch(client, u) for u in menu_links(url, first)))

        best: list[MenuItem] = []
        names: list[MenuItem] = []  # the longest menu without prices, still worth showing
        names_url = ""

        async def read(candidates: list[Page], check_business: bool = False) -> None:
            nonlocal best, names, names_url
            for page in candidates:
                if page.error or _priced(best) >= MIN_PRICED_ITEMS:
                    continue
                if check_business and not same_business(page.text, menu.address, menu.phone):
                    logger.info(f"[{menu.slug}] {page.url}: skipped, doesn't show this business's phone or address")
                    continue
                items, _ = await _read_page(client, name, page)
                if _priced(items) > _priced(best):
                    best, menu.source_url = items, page.url
                elif not _priced(best) and len(items) > len(names):
                    names, names_url = items, page.url

        await read(pages)
        if _priced(best) < MIN_PRICED_ITEMS and (menu.phone or menu.address):
            # The business's own site has no prices: look for its menu on menu platforms.
            found = await find_menu_pages(name, menu.address, menu.phone, url, agent_search)
            more = await asyncio.gather(*(_fetch(client, u) for u in found))
            pages += more
            await read(list(more), check_business=True)

    if _priced(best) < MIN_PRICED_ITEMS:
        menu.items = best or names
        menu.source_url = (menu.source_url if best else names_url) or url
        readable = [p for p in pages if not p.error]
        if best:
            detail = f"only {_priced(best)} items with a per-person price"
        elif readable:
            detail = f"no prices on the {len(readable)} page{'s' if len(readable) != 1 else ''} we could read"
        else:
            detail = next((p.error for p in pages if p.error), "no menu found")
        return menu, f"{detail} (checked {len(pages)} page{'s' if len(pages) != 1 else ''})"
    menu.items = best
    menu.extraction_status = "extracted"
    return menu, ""


async def extract_menu(url: str, name: Optional[str] = None, place_id: Optional[str] = None,
                       phone: Optional[str] = None, email: Optional[str] = None) -> VendorMenu:
    menu, reason = await extract_menu_with_reason(url, name, place_id, phone, email)
    if reason:
        logger.info(f"[{menu.slug}] estimated: {reason}")
    return menu
