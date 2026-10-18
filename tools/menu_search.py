"""Find a caterer's menu on other sites when its own website has no prices.

Searches the web for the business's menu and keeps results on known menu platforms (SinglePlatform, Toast,
ezCater, AllMenus, ...). Search provider, first one configured: SERPER_API_KEY (Google results, serper.dev),
TAVILY_API_KEY (tavily.com), else DuckDuckGo's HTML page (no key, but it rate-limits after a few queries). A platform page is only used if it
shows this business's own phone number or street address, so another location's prices are never used.

Owner: Person B
"""

import asyncio
import html
import logging
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
SERPER_URL = "https://google.serper.dev/search"
TAVILY_URL = "https://api.tavily.com/search"
SEARCH_TIMEOUT_S = 10
MAX_RESULTS = 6
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Sites that publish restaurant/catering menus with prices, best first.
MENU_PLATFORMS = (
    "singleplatform.com", "toasttab.com", "ezcater.com", "allmenus.com", "menupages.com", "restaurantji.com",
    "grubhub.com", "seamless.com", "order.online", "chownow.com", "menufy.com", "beyondmenu.com", "zmenu.com",
    "sirved.com", "menuism.com", "clover.com", "square.site", "popmenu.com", "yelp.com",
)

logger = logging.getLogger("menu_search")


def _result_urls(page: str) -> list[str]:
    urls = []
    for href in re.findall(r'class="result__a" href="([^"]+)"', page):
        href = html.unescape(href)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        urls.append(href)
    return urls


MIN_GAP_S = 2.5  # DuckDuckGo rate-limits bursts (it answers 202): space queries out across the process
_lock = asyncio.Lock()
_last = 0.0


async def _search(client: httpx.AsyncClient, query: str) -> list[str]:
    try:
        if os.getenv("SERPER_API_KEY"):
            r = await client.post(SERPER_URL, headers={"X-API-KEY": os.environ["SERPER_API_KEY"]},
                                  json={"q": query, "gl": "us", "num": 10})
            r.raise_for_status()
            return [o["link"] for o in r.json().get("organic", []) if o.get("link")]
        if os.getenv("TAVILY_API_KEY"):
            r = await client.post(TAVILY_URL, headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                                  json={"query": query, "max_results": 10})
            r.raise_for_status()
            return [o["url"] for o in r.json().get("results", []) if o.get("url")]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        logger.warning(f"search API failed for {query!r}: {e.__class__.__name__}; falling back to DuckDuckGo")
    return await _duckduckgo(client, query)


async def _duckduckgo(client: httpx.AsyncClient, query: str) -> list[str]:
    global _last
    async with _lock:
        for attempt in range(3):
            wait = _last + MIN_GAP_S * (attempt + 1) - asyncio.get_running_loop().time()
            if wait > 0:
                await asyncio.sleep(wait)
            _last = asyncio.get_running_loop().time()
            try:
                r = await client.post(SEARCH_URL, data={"q": query})
            except httpx.HTTPError as e:
                logger.warning(f"search failed for {query!r}: {e.__class__.__name__}")
                return []
            if r.status_code == 200:
                return _result_urls(r.text)
    logger.warning(f"search rate-limited for {query!r}")
    return []


def _platform_rank(url: str) -> int | None:
    host = urlparse(url).netloc.lower()
    return next((i for i, p in enumerate(MENU_PLATFORMS) if host == p or host.endswith("." + p)), None)


async def find_menu_pages(name: str, address: str = "", phone: str = "", own_site: str = "") -> list[str]:
    """Menu-platform URLs that probably carry this business's menu, best first."""
    street = address.split(",")[0] if address else ""
    queries = [f"{name} {street or 'Los Angeles'} menu", f"{name} Los Angeles catering menu prices"]
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        results = []
        for q in queries:  # one at a time: DuckDuckGo rate-limits bursts
            results += await _search(client, q)
    own_host = urlparse(own_site).netloc.removeprefix("www.") if own_site else ""
    ranked = {}
    for url in results:
        rank = _platform_rank(url)
        if rank is not None and url not in ranked and urlparse(url).netloc.removeprefix("www.") != own_host:
            ranked[url] = rank
    found = sorted(ranked, key=ranked.get)[:MAX_RESULTS]
    logger.info(f"[{name}] menu search: {len(results)} results, {len(found)} on menu platforms")
    return found


def same_business(page_text: str, address: str = "", phone: str = "") -> bool:
    """Does this page show the business's own phone number or street address?"""
    digits = re.sub(r"\D", "", phone)[-10:]
    if len(digits) == 10 and digits in re.sub(r"\D", "", page_text):
        return True
    street = address.split(",")[0].strip().lower() if address else ""
    number = street.split(" ")[0] if street else ""
    if number.isdigit():
        words = [w for w in street.split(" ")[1:] if len(w) > 2][:1]
        text = page_text.lower()
        return number in text and all(w in text for w in words)
    return False
