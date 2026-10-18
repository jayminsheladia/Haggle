"""Dietary fit + compliant menu per vendor (used by Negotiator).

Owner: Person C

Menu package shape: {"name", "per_head_usd", "tags": ["vegetarian", ...], "allergens": ["nuts", ...]}.
All math is plain code; the LLM never touches prices.
"""
from typing import Dict, List, Optional, Tuple


def _norm(s: str) -> str:
    return s.strip().lower()


def match_menu(
    packages: List[dict],
    headcount: int,
    dietary: Dict[str, int],
    allergies: List[str],
) -> Tuple[Optional[List[dict]], List[str]]:
    """Cheapest compliant package per group.

    Groups: each dietary tag with its headcount, plus "standard" for everyone else.
    A package is compliant if it carries the group's tag and none of the listed allergens.
    Returns (line_items, missing_groups). line_items is None when any group can't be served.
    """
    avoid = {_norm(a) for a in allergies}
    safe = [p for p in packages if not avoid & {_norm(a) for a in p.get("allergens", [])}]

    groups: Dict[str, int] = {_norm(k): v for k, v in dietary.items() if v > 0}
    standard = headcount - sum(groups.values())
    if standard < 0:
        standard = 0
    if standard:
        groups = {"standard": standard, **groups}

    items: List[dict] = []
    missing: List[str] = []
    for group, n in groups.items():
        if group == "standard":  # regular menu first; dietary packages only if there is none
            fits = [p for p in safe if not p.get("tags")] or safe
        else:
            fits = [p for p in safe if group in {_norm(t) for t in p.get("tags", [])}]
        if not fits:
            missing.append(group)
            continue
        best = min(fits, key=lambda p: p["per_head_usd"])
        items.append({
            "group": group,
            "package": best["name"],
            "headcount": n,
            "per_head_usd": best["per_head_usd"],
            "subtotal_usd": round(best["per_head_usd"] * n, 2),
        })
    return (None if missing else items), missing


def dietary_fit(dietary: Dict[str, int], missing: List[str]) -> float:
    """Share of dietary-restricted guests the vendor can serve (1.0 = all)."""
    total = sum(v for v in dietary.values() if v > 0)
    if not total:
        return 1.0
    unserved = sum(v for k, v in dietary.items() if _norm(k) in missing)
    return round(1 - unserved / total, 3)
