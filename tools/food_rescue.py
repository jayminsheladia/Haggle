"""Leftover food rescue: match an event's leftovers to open food recovery organizations (build plan 3C).

Everything here is plain code (no LLM): food-type matching, opening hours, distance, the impact estimate.

data/food_rescue_orgs.json is a list of:
  {"org_id", "name", "address", "lat", "lng",
   "hours": {"mon": [["09:00", "17:00"]], ...},   # America/Los_Angeles; a missing day = closed
   "accepts": ["prepared", "packaged", "produce"], "email", "pickup": true|false, "source": "<url>"}

Owner: Person D
"""

import json
import math
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

ORGS_PATH = Path(__file__).resolve().parent.parent / "data" / "food_rescue_orgs.json"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Leftover form choices -> what an organization must accept.
FOOD_TYPES = {"hot": "prepared", "cold": "prepared", "packaged": "packaged"}

# Rough weight per amount choice. Stated assumptions, shown to the user as estimates.
AMOUNT_LBS = {"none": 0, "little": 3, "tray": 8, "trays": 24}
LBS_PER_MEAL = 1.2  # a common food bank estimate

MAX_OPTIONS = 3


def load_orgs(path: Path = ORGS_PATH) -> list[dict]:
    try:
        orgs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [o for o in orgs if isinstance(o, dict) and o.get("org_id") and o.get("name")]


def distance_mi(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def open_now(org: dict, now: datetime) -> bool:
    """`now` is a local (America/Los_Angeles) datetime."""
    hhmm = now.strftime("%H:%M")
    return any(start <= hhmm < end for start, end in org.get("hours", {}).get(DAYS[now.weekday()], []))


def hours_today(org: dict, now: datetime) -> str:
    spans = org.get("hours", {}).get(DAYS[now.weekday()], [])
    if not spans:
        return "closed today"
    return ", ".join(f"{_ampm(s)}–{_ampm(e)}" for s, e in spans)


def _ampm(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0").replace(":00", "")


def match(orgs: list[dict], food_type: str, handoff: str, lat: float | None, lng: float | None,
          now: datetime) -> list[dict]:
    """Up to MAX_OPTIONS organizations that accept this food, are open now and fit the hand-off, nearest first.

    handoff: "pickup" (the org must offer pickup) or "dropoff". Returns copies with distance_mi (None if
    the event has no coordinates) and hours_today added.
    """
    needs = FOOD_TYPES.get(food_type)
    if not needs:
        return []
    found = []
    for o in orgs:
        if needs not in o.get("accepts", []) or not open_now(o, now):
            continue
        if handoff == "pickup" and not o.get("pickup"):
            continue
        d = None
        if lat is not None and lng is not None and o.get("lat") is not None and o.get("lng") is not None:
            d = round(distance_mi(lat, lng, o["lat"], o["lng"]), 1)
        found.append({**o, "distance_mi": d, "hours_today": hours_today(o, now)})
    found.sort(key=lambda o: (o["distance_mi"] is None, o["distance_mi"] or 0, o["name"]))
    return found[:MAX_OPTIONS]


def impact(amount: str) -> tuple[int, int]:
    """(lbs, meals) estimate for an amount choice."""
    lbs = AMOUNT_LBS.get(amount, 0)
    return lbs, round(lbs / LBS_PER_MEAL)


def directions_url(address: str) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={quote_plus(address)}"
