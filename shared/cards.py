"""Interactive Card builders: card_message() + form/carousel/detail/review/confirm.

Rules (from the ASI:One Interactive Cards docs):
- always include card_protocol_version "1" and requires_card_interaction "true"
- every metadata value is a string; the payload is json.dumps'd, never wrapped
- every selection carries rfq_id and vendor_id
- payload <= 64 KB

Owner: Person A
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

MAX_PAYLOAD_BYTES = 64 * 1024

DIETS = ("vegetarian", "vegan", "halal", "kosher", "gluten_free")



def usd(amount: float) -> str:
    return f"${amount:,.2f}"


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def card_message(narration: str, kind: str, payload: dict, terminal: bool = False) -> ChatMessage:
    body = json.dumps(payload)
    if len(body.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{kind} card payload is over 64 KB")
    meta = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": kind,
        "card_payload": body,
        "preferred_drawer_width_px": "540",
    }
    if terminal:
        meta["is_terminal"] = "true"
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[
            TextContent(type="text", text=narration),
            MetadataContent(type="metadata", metadata=meta),
        ],
    )


def _label(base: str, have: str | None) -> str:
    # Form fields can't be pre-filled, so inferred values are shown in the label.
    return f"{base} (I have: {have} - leave blank to keep)" if have else base


def event_form(rfq_id: str, ask: dict[str, str], have: dict[str, str]) -> dict:
    """Intake form card for the checklist fields that are missing or need confirming.

    ask:  field -> status ("missing" or "needs_confirm")
    have: field -> display value for needs_confirm fields
    """
    fields: list[dict] = []

    def add(name: str, kind: str, label: str) -> None:
        fields.append({
            "name": name,
            "kind": kind,
            "label": _label(label, have.get(name)),
            "required": ask[name] == "missing",
        })

    if "date" in ask:
        add("date", "text", "Event date (e.g. Fri Oct 23)")
    if "time" in ask:
        add("time", "text", "Start time (e.g. 6:00 PM)")
    if "location" in ask:
        add("location", "text", "Delivery address or venue in Los Angeles")
    if "headcount" in ask:
        add("headcount", "number", "Number of guests")
    if "budget_usd" in ask:
        add("budget_usd", "text", "Max budget in USD (or 'no fixed budget')")
    if "dietary" in ask:
        # Dietary is always asked explicitly: counts per diet, or tick "none".
        hint = have.get("dietary")
        for diet in DIETS:
            fields.append({
                "name": f"diet_{diet}",
                "kind": "number",
                "label": f"{diet.replace('_', '-').capitalize()} guests",
                "required": False,
            })
        fields.append({
            "name": "diet_none",
            "kind": "checkbox",
            "label": _label("No dietary restrictions", hint),
            "required": False,
        })
    if "allergies" in ask:
        add("allergies", "text", "Allergies (type 'none' if there are none)")

    return {
        "title": "Event details",
        "fields": fields,
        "submit_cta": {
            "label": "Continue",
            "selection": {"action": "submit_event", "rfq_id": rfq_id, "vendor_id": ""},
        },
    }


def quotes_carousel(rfq_id: str, title: str, quotes: list[dict]) -> dict:
    """One card per quote, in rank order.

    quotes: [{"vendor_id", "name", "photo_url", "total_usd", "per_head_usd", "subtitle", "badges": [(label, variant)]}]
    """
    return {
        "title": title,
        "subtitle": "Tap a caterer for details or to negotiate.",
        "items": [
            {
                "id": q["vendor_id"],
                **({"image": q["photo_url"]} if q["photo_url"] else {}),
                "title": q["name"],
                "subtitle": q["subtitle"],
                "badges": [{"label": label, "variant": variant} for label, variant in q["badges"]],
                "secondary_text": q.get("price_text") or f"{usd(q['total_usd'])} · {usd(q['per_head_usd'])}/person",
                "primary_cta": {
                    "label": "View",
                    "selection": {"action": "pick_quote", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]},
                },
            }
            for q in quotes
        ],
    }


def quote_detail(rfq_id: str, q: dict, can_negotiate: bool, next_step: str = "") -> dict:
    """Detail card for one quote. q: carousel item fields plus "rows": [(label, value)].

    next_step: "book" (claimed vendor), "request_quote" (unclaimed estimate) or "" (nothing to do yet).
    """
    ctas = []
    if next_step:
        ctas.append({
            "label": "Book" if next_step == "book" else "Request a confirmed quote",
            "primary": True,
            "selection": {"action": next_step, "rfq_id": rfq_id, "vendor_id": q["vendor_id"]},
        })
    if can_negotiate:
        ctas.append({
            "label": "Negotiate lower",
            "primary": not next_step,
            "selection": {"action": "negotiate", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]},
        })
    ctas.append({"label": "Back", "selection": {"action": "back", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]}})
    return {
        "title": q["name"],
        **({"hero_image": q["photo_url"]} if q["photo_url"] else {}),
        "summary_rows": [{"label": label, "value": value} for label, value in q["rows"]],
        "ctas": ctas,
    }


def owner_rules_form(vendor_id: str, rules: dict) -> dict:
    """Form card a vendor owner fills in after claiming: their negotiation rules.

    rules: current OwnerRules as a dict; shown in labels since form fields can't be pre-filled.
    """
    def field(name: str, kind: str, label: str) -> dict:
        current = rules.get(name)
        if isinstance(current, list):
            current = ", ".join(current) or "none"
        return {"name": name, "kind": kind, "label": f"{label} (now: {current} - leave blank to keep)",
                "required": False}

    return {
        "title": "Your Haggle rules",
        "fields": [
            field("floor_discount_pct", "number", "Lowest discount you'll go to, in % off list price"),
            field("min_headcount", "number", "Minimum guests"),
            field("max_headcount", "number", "Maximum guests"),
            field("lead_time_hours", "number", "Notice you need, in hours"),
            field("delivery_radius_mi", "number", "Delivery radius in miles"),
            field("blackout_dates", "text", "Dates you're closed (YYYY-MM-DD, comma separated)"),
            field("auto_approve_limit_usd", "number", "Orders above this total ($) need your approval"),
            field("delivery_fee_usd", "number", "Delivery fee ($)"),
        ],
        "submit_cta": {
            "label": "Save rules",
            "selection": {"action": "save_rules", "rfq_id": "", "vendor_id": vendor_id},
        },
    }


def review_card(rfq_id: str, vendor_id: str, title: str, rows: list[tuple[str, str]], approve_label: str) -> dict:
    """Confirm / cancel before anything is sent or charged."""
    sel = {"rfq_id": rfq_id, "vendor_id": vendor_id}
    return {
        "title": title,
        "summary_rows": [{"label": label, "value": value} for label, value in rows],
        "approve_cta": {"label": approve_label, "primary": True, "selection": {"action": "confirm", **sel}},
        "reject_cta": {"label": "Cancel", "selection": {"action": "cancel", **sel}},
    }


def picker_slider(rfq_id: str, location: str, vendors: list[dict]) -> dict:
    """Caterer picker in ONE card: a horizontal row of caterer cards (swipe through them), each with a "Select"
    checkbox the drawer keeps, then "Get quotes" sends every pick at once. Nothing is re-sent while picking.

    vendors: [{"vendor_id", "name", "photo_url", "meta", "connected"}]; checkbox i is named pick_<i>.
    """
    def card(i: int, v: dict) -> dict:
        status = ({"type": "badge", "label": "Connected", "variant": "success"} if v["connected"]
                  else {"type": "badge", "label": "Not connected", "variant": "warning"})
        return {
            "type": "section",
            "title": v["name"],
            "children": [
                *([{"type": "image", "src": v["photo_url"], "alt": v["name"], "aspect_ratio": "4:3"}]
                  if v["photo_url"] else []),
                *([{"type": "text", "value": v["meta"], "style": "muted"}] if v["meta"] else []),
                status,
                {"type": "input", "name": f"pick_{i}", "kind": "checkbox", "label": "Select"},
            ],
        }

    def button(text: str, action: str, primary: bool = False) -> dict:
        return {"type": "button", "label": text, "primary": primary,
                "action": {"selection": {"action": action, "rfq_id": rfq_id, "vendor_id": ""}}}

    return {
        "root": {
            "type": "section",
            "title": f"Caterers near {location}",
            "subtitle": "Swipe through and tick every caterer you want quotes from, then Get quotes.",
            "children": [
                {"type": "group", "direction": "row", "gap": 12, "children": [card(i, v) for i, v in enumerate(vendors)]},
                {"type": "group", "direction": "row", "gap": 8, "children": [
                    button("Get quotes", "select_vendors", primary=True),
                    button("Quote all", "select_all"),
                ]},
            ],
        }
    }
