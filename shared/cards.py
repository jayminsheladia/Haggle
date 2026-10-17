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
