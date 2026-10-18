"""parse_selection(): json.loads first, regex fallback for rfq_id / vendor_id / action.

Card replies arrive two ways: as a JSON string (user @mentioned the agent) or as
prose (ASI:One's planner routed the user and narrates the selection).

Owner: Person A
"""

import json
import re

# Every action any Haggle card can send back.
KNOWN_ACTIONS = (
    "submit_event", "toggle_vendor", "select_vendors", "select_all", "pick_quote", "request_quote", "book", "negotiate", "confirm",
    "cancel", "back", "edit",
)


def _as_dict(text: str) -> dict | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def parse_selection(text: str) -> dict:
    text = (text or "").strip()
    data = _as_dict(text)
    if data is None:
        # JSON with something around it, e.g. a leading "@haggle".
        m = re.search(r"\{.*\}", text, re.S)
        data = _as_dict(m.group(0)) if m else None
    if data is not None:
        return data

    sel: dict = {}
    m = re.search(r"\b(rfq_[A-Za-z0-9]+)\b", text)
    if m:
        sel["rfq_id"] = m.group(1)
    m = re.search(r"\bvendor[_ ]id\W+([A-Za-z0-9_-]+)", text, re.I)
    if m:
        sel["vendor_id"] = m.group(1)
    for action in KNOWN_ACTIONS:
        if re.search(rf"\b{action}\b", text, re.I):
            sel["action"] = action
            break
    return sel
