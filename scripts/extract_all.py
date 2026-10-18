"""Extract every caterer in vendors/sources.csv into vendors/data/<slug>.json and print a summary.

Run from the repo root:  python -m scripts.extract_all

Owner: Person B
"""

import asyncio
import csv
import logging
from pathlib import Path

from dotenv import load_dotenv

from tools.menu_extract import extract_menu_with_reason

SOURCES = Path("vendors/sources.csv")
DATA_DIR = Path("vendors/data")


async def main() -> None:
    rows = list(csv.DictReader(SOURCES.open()))
    results = await asyncio.gather(*(
        extract_menu_with_reason(
            r.get("catering_menu_url") or r.get("url") or r.get("website", ""),
            name=r["name"], place_id=r.get("place_id"), phone=r.get("phone"), email=r.get("email"),
            address=r.get("address", ""),
        )
        for r in rows
    ))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{'vendor':<34} {'status':<10} {'items':>5}  {'priced':>6}  price range / reason")
    for menu, reason in results:
        (DATA_DIR / f"{menu.slug}.json").write_text(menu.model_dump_json(indent=2) + "\n")
        prices = [i.price_per_person for i in menu.priced_items]
        detail = f"${min(prices):.2f}-${max(prices):.2f}/person" if prices else reason
        print(f"{menu.name[:34]:<34} {menu.extraction_status:<10} {len(menu.items):>5}  {len(prices):>6}  {detail}")


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
