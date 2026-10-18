"""Vendor pricing: pure functions, no agents, no LLM. Every number a vendor agent sends comes from here.

Owner: Person C
"""

import math
import re
from datetime import datetime, timedelta
from statistics import mean
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from shared.menu_schema import MenuItem, OwnerRules, VendorMenu
from shared.models import RFQ, LineItem

LA = ZoneInfo("America/Los_Angeles")

# RFQ dietary keys -> menu tags that satisfy them.
DIET_TAGS = {
    "vegetarian": {"vegetarian", "vegan"},
    "vegan": {"vegan"},
    "halal": {"halal"},
    "kosher": {"kosher"},
    "gluten_free": {"gluten-free"},
}
# Allergy words -> the menu tag that marks an item as containing it.
ALLERGEN_TAGS = {
    "nut": "contains-nuts", "nuts": "contains-nuts", "peanut": "contains-nuts", "peanuts": "contains-nuts",
    "tree nuts": "contains-nuts", "dairy": "contains-dairy", "milk": "contains-dairy", "lactose": "contains-dairy",
}
# Extras -> words that identify menu items offering them.
EXTRA_WORDS = {
    "drinks": r"drinks?|beverages?|sodas?|water|lemonade|juices?|coffee|tea|agua fresca",
    "dessert": r"desserts?|cookies?|brownies?|cakes?|sweets|pastry|pastries|churros?|baklava",
    "plates": r"utensils?|cutlery|napkins?|tableware|paper goods|plasticware|disposables",
}


def _is_extra(item: MenuItem) -> Optional[str]:
    name = item.name.lower()
    return next((extra for extra, words in EXTRA_WORDS.items() if re.search(rf"\b({words})\b", name)), None)


def _label(group: str) -> str:
    return group.replace("_", "-")


def _package(items: list[MenuItem]) -> str:
    names = [i.name for i in items[:3]]
    return ", ".join(names) + (f" +{len(items) - 3} more" if len(items) > 3 else "")


UNLABELED = "options not labeled on the menu"
MAIN_SHARE = 0.5


def price_for(menu: VendorMenu, rfq: RFQ, unlabeled_ok: bool = False) -> tuple[float, list[LineItem], bool, list[str]]:
    """List price for an RFQ: (price_usd, line_items, dietary_ok, conflicts).

    Each dietary group (and "standard" for everyone else) is priced at the average per-person price of
    the menu items that fit it; extras only if the menu lists them; plus the delivery fee.
    unlabeled_ok: the menu has no dietary labels at all (public menus often don't), so a dietary group with no
    labeled items is priced from the whole menu and flagged "<diet> options not labeled on the menu".
    """
    conflicts: list[str] = []
    if not menu.priced_items:
        return 0.0, [], False, ["no public menu prices"]  # never invent a price
    mains = [i for i in menu.priced_items if not _is_extra(i)]
    # Cheap add-ons priced per person (a $2.50 side) would drag a meal's average down: a full meal is priced
    # from items at least MAIN_SHARE of the most expensive one. Items labeled for a diet still count as-is.
    top = max((i.price_per_person for i in mains), default=0)
    meals = [i for i in mains if i.price_per_person >= MAIN_SHARE * top]

    # Allergies: drop items the menu marks as containing them; flag allergens the menu can't tell us about.
    safe, safe_meals = mains, meals
    for allergy in rfq.allergies:
        tag = ALLERGEN_TAGS.get(allergy.strip().lower())
        if tag is None:
            conflicts.append(f"can't confirm {allergy}-free options")
            continue
        safe = [i for i in safe if tag not in i.dietary_tags]
        safe_meals = [i for i in safe_meals if tag not in i.dietary_tags]
        if mains and not safe:
            conflicts.append(f"all items contain {allergy}")

    groups = {k: n for k, n in rfq.dietary.items() if n > 0}
    standard = max(0, rfq.headcount - sum(groups.values()))
    if standard:
        groups = {"standard": standard, **groups}

    lines: list[LineItem] = []
    for group, n in groups.items():
        fits = (safe_meals or safe) if group == "standard" else [
            i for i in safe if DIET_TAGS.get(group, {group}) & set(i.dietary_tags)]
        if not fits and unlabeled_ok and safe:
            conflicts.append(f"{_label(group)} {UNLABELED}")
            fits = safe_meals or safe
        if not fits:
            conflicts.append(f"no {_label(group)} items")
            continue
        per, package = round(mean(i.price_per_person for i in fits), 2), _package(fits)
        lines.append(LineItem(group=group, package=package, headcount=n, per_head_usd=per,
                              subtotal_usd=round(per * n, 2)))

    for extra in rfq.extras:
        offered = [i for i in menu.priced_items if _is_extra(i) == extra]
        if offered:
            per = round(mean(i.price_per_person for i in offered), 2)
            lines.append(LineItem(group=extra, package=_package(offered), headcount=rfq.headcount,
                                  per_head_usd=per, subtotal_usd=round(per * rfq.headcount, 2)))
        else:
            lines.append(LineItem(group=extra, package="not offered", headcount=0, per_head_usd=0, subtotal_usd=0))

    total = round(sum(li.subtotal_usd for li in lines) + menu.rules.delivery_fee_usd, 2)
    return total, lines, not conflicts, conflicts


def distance_mi(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def check_rules(rules: OwnerRules, rfq: RFQ, now: Optional[datetime] = None,
                vendor_lat: Optional[float] = None, vendor_lng: Optional[float] = None) -> list[str]:
    """Every rule the RFQ breaks, as human-readable reasons (empty list = OK)."""
    violations = []
    if rfq.headcount < rules.min_headcount:
        violations.append(f"minimum headcount is {rules.min_headcount}")
    if rfq.headcount > rules.max_headcount:
        violations.append(f"maximum headcount is {rules.max_headcount}")
    if rfq.event_date in rules.blackout_dates:
        violations.append(f"unavailable on {rfq.event_date}")
    try:
        event = datetime.fromisoformat(f"{rfq.event_date}T{rfq.event_time or '12:00'}")
        now = now or datetime.now(LA).replace(tzinfo=None)
        if event - now < timedelta(hours=rules.lead_time_hours):
            violations.append(f"needs {rules.lead_time_hours}h notice")
    except ValueError:
        pass
    dist = rfq.distance_mi
    if not dist and None not in (rfq.lat, rfq.lng, vendor_lat, vendor_lng):
        dist = distance_mi(rfq.lat, rfq.lng, vendor_lat, vendor_lng)
    if dist and dist > rules.delivery_radius_mi:
        violations.append(f"outside delivery radius ({rules.delivery_radius_mi:g} mi)")
    return violations


def floor_price(list_price: float, rules: OwnerRules) -> float:
    return round(list_price * (1 - rules.floor_discount_pct / 100), 2)


CounterAction = Literal["accept", "counter", "decline"]


def counter_response(last_quote: float, target: float, floor: float, round_: int) -> tuple[CounterAction, float | str]:
    """The counter-offer rule: accept at the target if it's at or above the floor; otherwise meet halfway
    (never below the floor) for the first round; after that, decline."""
    if target >= floor:
        return "accept", round(min(target, last_quote), 2)
    if round_ < 2:
        return "counter", round(max(floor, (last_quote + target) / 2), 2)
    return "decline", "floor reached"
