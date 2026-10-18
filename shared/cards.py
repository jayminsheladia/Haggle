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
from urllib.parse import quote_plus
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

MAX_PAYLOAD_BYTES = 64 * 1024

DIETS = ("vegetarian", "vegan", "halal", "kosher", "gluten_free")

# Shown when a vendor has no Places photo (e.g. agent-only vendors).
PLACEHOLDER_IMAGE = "https://placehold.co/600x400/png?text={}"


def _image(url: str, name: str) -> str:
    return url or PLACEHOLDER_IMAGE.format(quote_plus(name))


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


def vendor_picker(rfq_id: str, location: str, vendors: list[dict]) -> dict:
    """Custom card: every discovered caterer with a checkbox, then "Get quotes".

    vendors: [{"vendor_id", "name", "photo_url", "meta", "live"}] where `live` means the vendor
    has a Haggle agent that can quote now; the others are unclaimed (quote request by email).
    """
    rows: list[dict] = []
    for i, v in enumerate(vendors):
        badge = (
            {"type": "badge", "label": "Live quote", "variant": "success"}
            if v["live"]
            else {"type": "badge", "label": "Quote by email", "variant": "info"}
        )
        rows.append({
            "type": "group",
            "direction": "row",
            "gap": 12,
            "children": [
                {"type": "image", "src": _image(v["photo_url"], v["name"]), "alt": v["name"], "aspect_ratio": "1:1"},
                {
                    "type": "group",
                    "direction": "column",
                    "gap": 4,
                    "children": [
                        {"type": "heading", "value": v["name"], "level": 3},
                        {"type": "text", "value": v["meta"], "style": "muted"},
                        badge,
                        {"type": "input", "name": f"pick_{i}", "kind": "checkbox", "label": "Get a quote"},
                    ],
                },
            ],
        })
        rows.append({"type": "divider"})

    def button(label: str, action: str, primary: bool = False) -> dict:
        return {
            "type": "button",
            "label": label,
            "primary": primary,
            "action": {"selection": {"action": action, "rfq_id": rfq_id, "vendor_id": ""}},
        }

    rows.append({
        "type": "group",
        "direction": "row",
        "gap": 8,
        "children": [
            button("Get quotes from selected", "select_vendors", primary=True),
            button("Get quotes from all", "select_all"),
        ],
    })
    return {
        "root": {
            "type": "section",
            "title": f"Caterers near {location}",
            "subtitle": "Tick the ones you want quotes from. Nobody is contacted until you choose.",
            "children": rows,
        }
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
                "image": _image(q["photo_url"], q["name"]),
                "title": q["name"],
                "subtitle": q["subtitle"],
                "badges": [{"label": label, "variant": variant} for label, variant in q["badges"]],
                "secondary_text": f"{usd(q['total_usd'])} · {usd(q['per_head_usd'])}/person",
                "primary_cta": {
                    "label": "View",
                    "selection": {"action": "pick_quote", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]},
                },
            }
            for q in quotes
        ],
    }


def quote_detail(rfq_id: str, q: dict, can_negotiate: bool) -> dict:
    """Detail card for one quote. q: carousel item fields plus "rows": [(label, value)]."""
    ctas = []
    if can_negotiate:
        ctas.append({
            "label": "Negotiate lower",
            "primary": True,
            "selection": {"action": "negotiate", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]},
        })
    ctas.append({"label": "Back", "selection": {"action": "back", "rfq_id": rfq_id, "vendor_id": q["vendor_id"]}})
    return {
        "title": q["name"],
        "hero_image": _image(q["photo_url"], q["name"]),
        "summary_rows": [{"label": label, "value": value} for label, value in q["rows"]],
        "ctas": ctas,
    }
