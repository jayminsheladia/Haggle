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


def picker_carousel(rfq_id: str, location: str, vendors: list[dict]) -> dict:
    """Caterer picker as ASI:One's horizontal carousel (the one card kind that slides sideways). Each card's
    "Select" adds or removes that caterer; the Concierge answers with one short line and does NOT re-send the
    carousel, so the user keeps swiping and selecting on this same card. The last card is "Get quotes".

    vendors: [{"vendor_id", "name", "photo_url", "meta", "connected"}]
    """
    def sel(action: str, vendor_id: str = "") -> dict:
        return {"action": action, "rfq_id": rfq_id, "vendor_id": vendor_id}

    items = [{
        "id": v["vendor_id"],
        **({"image": v["photo_url"]} if v["photo_url"] else {}),
        "title": v["name"],
        "subtitle": v["meta"] or "Los Angeles caterer",
        "badges": [{"label": "Connected", "variant": "success"} if v["connected"]
                   else {"label": "Not connected", "variant": "warning"}],
        "secondary_text": "Tap Select to add or remove",
        "primary_cta": {"label": "Select", "selection": sel("toggle_vendor", v["vendor_id"])},
    } for v in vendors]
    items.append({
        "id": "get_quotes",
        "title": "Get quotes",
        "subtitle": "From every caterer you selected",
        "secondary_text": "Nobody is contacted until you tap this",
        "primary_cta": {"label": "Get quotes", "selection": sel("select_vendors")},
    })
    items.append({
        "id": "quote_all",
        "title": "Quote all",
        "subtitle": f"All {len(vendors)} caterers",
        "secondary_text": "Quick option",
        "primary_cta": {"label": "Quote all", "selection": sel("select_all")},
    })
    return {"title": f"Caterers near {location}", "subtitle": "Swipe, tap Select on each one you want, then Get quotes",
            "items": items}



def picker_page(rfq_id: str, location: str, vendors: list[dict], page: int, pages: int, picked: list[str]) -> dict:
    """One page of the caterer picker: one row of up to 3 photo tiles (choice_grid, multi=true) with
    Previous / Next buttons, like a slider. ASI:One stacks carousels vertically and can't update a card in place,
    so each arrow tap sends the next page as a new card. Picks live in the session: the buttons carry this page's
    ticks (selection["vendors"]); ticking a caterer that is already "Selected" removes it.

    vendors: this page only, [{"vendor_id", "name", "photo_url", "meta", "connected"}]; picked: names picked so far.
    """
    def label(v: dict) -> str:
        meta = v["meta"].replace(" away", "")
        text = " · ".join(x for x in (v["name"], meta, "✓ Connected" if v["connected"] else "") if x)
        return f"✅ {text}" if v.get("picked") else text

    def button(text: str, action: str, primary: bool = False, to_page: int = 0) -> dict:
        sel = {"action": action, "rfq_id": rfq_id, "vendor_id": ""}
        if action == "picker_page":
            sel["page"] = str(to_page)
        return {"type": "button", "label": text, "primary": primary, "action": {"selection": sel}}

    n = len(picked)
    chosen = (f"✅ Selected ({n}): {', '.join(picked)}. Tick a ✅ one to remove it" if n
              else "Tick the caterers you want, then Get quotes. Next ▶ for more")
    buttons = [
        *([button("◀ Prev", "picker_page", to_page=page - 1)] if page > 0 else []),
        button("Get quotes", "select_vendors", primary=True),
        button("Quote all", "select_all"),
        *([button("Next ▶", "picker_page", to_page=page + 1)] if page < pages - 1 else []),
    ]
    return {
        "root": {
            "type": "section",
            "title": f"Caterers near {location.split(',')[0]} · {page + 1}/{pages}",
            "subtitle": f"{chosen}.",
            "children": [
                {
                    "type": "choice_grid",
                    "name": "vendors",
                    "multi": True,
                    "choices": [
                        {"value": v["vendor_id"], "label": label(v),
                         **({"image": v["photo_url"]} if v["photo_url"] else {})}
                        for v in vendors
                    ],
                },
                {"type": "group", "direction": "row", "gap": 8, "children": buttons},
            ],
        }
    }
