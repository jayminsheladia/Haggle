"""Concierge @haggle: Chat Protocol, intake checklist, cards, session state, Payment Protocol (seller).

Run from the repo root:  python -m agents.concierge

Owner: Person A
"""

import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.contrib.protocols.payment import CancelPayment, CommitPayment, CompletePayment, RejectPayment

from integrations.stripe_client import charge_test_deposit, create_deposit_checkout, mock_payments, session_status
from shared import observability
from shared.cards import (
    DIETS, card_message, event_form, picker_carousel, quote_detail, quotes_carousel, review_card, text_message, usd,
)
from shared.llm import chat_json
from shared.models import (
    BookingConfirmed, BookingResult, CounterRequest, CounterResult, DiscoveryRequest, DiscoveryResult,
    NegotiationRequest, NegotiationResult, OnboardRequest, OnboardResult, Quote, QuoteRequest, QuoteRequestSent,
    RFQ, UserNotice, UserReply, VendorRef,
)
from shared.menu_schema import slugify
from shared.payment import build_payment_proto, deposit_request
from shared.selection import parse_selection

load_dotenv()

LA = ZoneInfo("America/Los_Angeles")

DISCOVERY_ADDRESS = os.getenv(
    "DISCOVERY_ADDRESS", "agent1qvrfzf7mnxhz6zmzgn2q6htwjec6fs5rg0wnz6ucj4cjhqlaefwr6l26g2g"
)
NEGOTIATOR_ADDRESS = os.getenv(
    "NEGOTIATOR_ADDRESS", "agent1qg68ah5xmm3t9lcecua0dvqqknkqhqv4scrpf3cjjhx64lrqkpvd7erglhl"
)
FACTORY_ADDRESS = os.getenv("FACTORY_ADDRESS", "agent1qdzh8nw69qe2x0rw3tc7rg8ddt9e6lda8x7y6hqfjwfaldk2k7kzukj7rh5")
FOLLOWUP_ADDRESS = os.getenv("FOLLOWUP_ADDRESS", "agent1qf5hzpsg0xxmmm5dkjvkpvfget7n7es0vyyc47sk359g7c2u82a8yl4m2xa")
FOLLOWUP_ACTIONS = ("submit_leftovers", "pick_rescue")  # replies to Follow-up cards, relayed back to it
BOOKER_ADDRESS = os.getenv("BOOKER_ADDRESS", "agent1qfq9dyy5q5t5dg7tzuhkuqztzqy9q806dw4qd8v6sg6znu87rmx8s3deu0f")
ONBOARD_TIMEOUT_S = 75  # the Factory reads menus in parallel; usually well under a minute
DEPOSIT_PCT = 0.20  # deposit taken at booking (build plan)
NO_PRICES = "no public menu prices"

# ---- Intake checklist ------------------------------------------------------
# status: missing | needs_confirm | filled | offer | declined

REQUIRED = ("date", "time", "location", "headcount", "budget_usd", "dietary", "allergies")
OPTIONAL = ("cuisine", "service_style", "utensils_plates", "drinks", "dessert", "setup_cleanup")

FIELD_NAMES = {
    "date": "date",
    "time": "start time",
    "location": "delivery location",
    "headcount": "number of guests",
    "budget_usd": "budget",
    "dietary": "dietary needs",
    "allergies": "allergies",
}

NEAR_ME = re.compile(r"\b(near me|nearby|around here|close to me|my location)\b", re.I)
RESET_WORDS = {"start over", "reset", "new request"}


def new_session() -> dict:
    checklist = {f: {"value": None, "status": "missing"} for f in REQUIRED}
    checklist.update({f: {"value": None, "status": "offer"} for f in OPTIONAL})
    return {
        "stage": "new",
        "rfq_id": f"rfq_{uuid4().hex[:10]}",
        "version": 1,  # bumped when the request or the vendor shortlist changes
        "checklist": checklist,
        "asked": [],
    }


def fingerprint(checklist: dict) -> str:
    return json.dumps({f: checklist[f]["value"] for f in REQUIRED}, sort_keys=True)


def open_fields(checklist: dict) -> dict[str, str]:
    """Required fields still missing or waiting for confirmation, in checklist order."""
    return {
        f: checklist[f]["status"]
        for f in REQUIRED
        if checklist[f]["status"] in ("missing", "needs_confirm")
    }


# ---- Validation (code, never the LLM, decides what counts as a value) ------

_INVALID = object()


def _to_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d[\d,]*(\.\d+)?", str(value or ""))
    return float(m.group(0).replace(",", "")) if m else None


def _truthy(value) -> bool:
    return value is True or str(value).strip().lower() in {"true", "on", "yes", "1", "checked"}


def clean(field: str, value):
    """Normalize one checklist value, or return _INVALID."""
    if field == "date":
        try:
            d = date.fromisoformat(str(value).strip())
        except ValueError:
            return _INVALID
        return d.isoformat() if d >= datetime.now(LA).date() else _INVALID

    if field == "time":
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            return _INVALID
        return f"{int(m[1]):02d}:{m[2]}"

    if field == "location":
        v = str(value).strip()
        return v if v and not NEAR_ME.search(v) else _INVALID

    if field == "headcount":
        n = _to_number(value)
        return int(n) if n and n == int(n) and 0 < n <= 5000 else _INVALID

    if field == "budget_usd":
        # None means "no fixed budget", which is a valid answer.
        s = str(value).strip().lower()
        if s in {"no_budget", "none"} or (not re.search(r"\d", s) and re.search(r"\bno\b|flexible", s)):
            return None
        n = _to_number(value)
        return round(n, 2) if n and n > 0 else _INVALID

    if field == "dietary":
        if isinstance(value, str) and value.strip().lower() == "none":
            return {}
        if not isinstance(value, dict):
            return _INVALID
        out = {}
        for k, v in value.items():
            k = str(k).strip().lower().replace("-", "_").replace(" ", "_")
            n = _to_number(v)
            if k in DIETS and n and n > 0:
                out[k] = int(n)
        return out or _INVALID

    if field == "allergies":
        if isinstance(value, str):
            if value.strip().lower() in {"none", "no", "n/a", "no allergies"}:
                return []
            value = re.split(r",|;|/|\band\b", value)
        if not isinstance(value, list):
            return _INVALID
        out = [str(a).strip().lower() for a in value if str(a).strip()]
        return out or _INVALID

    return _INVALID


def apply_extraction(checklist: dict, data: dict, from_form: bool = False) -> None:
    """Merge extracted {field: {"value", "source"}} into the checklist. Never clears a field."""
    for f in REQUIRED:
        item = data.get(f)
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        v = clean(f, item["value"])
        if v is _INVALID:
            continue
        stated = from_form or item.get("source") == "stated"
        # "none" for dietary or allergies must be said, never assumed.
        if f in ("dietary", "allergies") and not v and not stated:
            continue
        checklist[f] = {"value": v, "status": "filled" if stated else "needs_confirm"}


# ---- LLM parsing -----------------------------------------------------------

EXTRACT_PROMPT = """You extract catering event details for Haggle, a group catering service in Los Angeles.
Today is {today} ({weekday}), timezone America/Los_Angeles.

Return ONLY a JSON object with exactly these keys. Each key is either null or
{{"value": <value>, "source": "stated" | "inferred"}}.
- "date": "YYYY-MM-DD". Resolve weekday names and "tomorrow" to the next upcoming date.
- "time": "HH:MM", 24-hour.
- "location": the venue or address as the user wrote it. null for "near me", "nearby" or similar.
- "headcount": integer number of guests.
- "budget_usd": the maximum total budget in US dollars as a number, or "no_budget" if the user says there is no fixed budget.
- "dietary": guest counts using only the keys vegetarian, vegan, halal, kosher, gluten_free, e.g. {{"vegetarian": 10, "halal": 3}};
  or "none" ONLY if the user explicitly says nobody has dietary restrictions.
- "allergies": a list of allergens, e.g. ["peanuts"]; or "none" ONLY if the user explicitly says there are no allergies.

Rules:
- Use null for anything the user did not mention. Never guess. Never assume "none" for dietary or allergies.
- "source" is "stated" when the user said it directly, "inferred" when you derived it (e.g. "dinner" -> 18:00).
  Resolving a weekday name or "tomorrow" to a date counts as "stated".
"""
STRICT_SUFFIX = "\nYour previous reply was not valid JSON. Reply with the JSON object only: no prose, no code fences."


async def extract_event(ctx: Context, text: str) -> dict | None:
    """LLM -> event fields. Retries once with a stricter prompt; None means fall back to the form."""
    now = datetime.now(LA)
    system = EXTRACT_PROMPT.format(today=now.date().isoformat(), weekday=now.strftime("%A"))
    for attempt in range(2):
        try:
            data = await chat_json(system + (STRICT_SUFFIX if attempt else ""), text, reasoning_effort="minimal")
        except Exception:
            ctx.logger.exception("Event extraction failed")
            data = None
        if data is not None:
            return data
    return None


async def apply_form(ctx: Context, checklist: dict, sel: dict, asked: list[str]) -> None:
    """Merge an intake form submission. Numbers and checkboxes are parsed in code;
    only free-text date/time goes through the LLM to be normalized."""
    updates: dict = {}
    for f in ("location", "headcount", "budget_usd", "allergies"):
        if sel.get(f) not in (None, ""):
            updates[f] = {"value": sel[f]}

    counts = {d: sel.get(f"diet_{d}") for d in DIETS if sel.get(f"diet_{d}") not in (None, "")}
    if any((_to_number(v) or 0) > 0 for v in counts.values()):
        updates["dietary"] = {"value": counts}
    elif _truthy(sel.get("diet_none")):
        updates["dietary"] = {"value": "none"}

    when = " ".join(
        f"{label}: {sel[f]}." for f, label in (("date", "Event date"), ("time", "Start time")) if sel.get(f)
    )
    if when:
        data = await extract_event(ctx, when) or {}
        for f in ("date", "time"):
            if sel.get(f) and isinstance(data.get(f), dict):
                updates[f] = data[f]

    apply_extraction(checklist, updates, from_form=True)

    # A needs_confirm field left blank in the form means "keep it".
    for f in asked:
        if checklist[f]["status"] == "needs_confirm" and f not in updates:
            checklist[f]["status"] = "filled"


# ---- Display ---------------------------------------------------------------

def display(field: str, value) -> str:
    if field == "date":
        d = date.fromisoformat(value)
        return f"{d:%a %b} {d.day}"
    if field == "time":
        t = datetime.strptime(value, "%H:%M")
        return t.strftime("%I:%M %p").lstrip("0")
    if field == "headcount":
        return f"{value} guests"
    if field == "budget_usd":
        return "no fixed budget" if value is None else f"up to ${value:,.0f}"
    if field == "dietary":
        return ", ".join(f"{n} {d.replace('_', '-')}" for d, n in value.items()) or "no dietary restrictions"
    if field == "allergies":
        return f"allergies: {', '.join(value)}" if value else "no allergies"
    return str(value)


def summary(checklist: dict, statuses=("filled",)) -> str:
    parts = []
    for f in REQUIRED:
        item = checklist[f]
        if item["status"] not in statuses:
            continue
        text = display(f, item["value"])
        if f == "time" and parts and checklist["date"]["status"] in statuses:
            parts[-1] = f"{parts[-1]}, {text}"
        else:
            parts.append(text)
    return " · ".join(parts)


def form_narration(checklist: dict, ask: dict[str, str]) -> str:
    have = summary(checklist)
    missing = [FIELD_NAMES[f] for f, st in ask.items() if st == "missing"]
    confirm = [FIELD_NAMES[f] for f, st in ask.items() if st == "needs_confirm"]
    lines = [f"Got it: {have}." if have else "Let's set up your event."]
    if missing:
        lines.append(f"I still need: {', '.join(missing)}.")
    if confirm:
        lines.append(f"Please confirm: {', '.join(confirm)}.")
    return " ".join(lines)


# ---- Agent -----------------------------------------------------------------

agent = Agent(
    name="haggle",
    seed=os.environ["CONCIERGE_SEED"],
    port=8001,
    mailbox=True,
    publish_agent_details=True,
    handle="haggle",
    readme_path=str(Path(__file__).with_name("concierge_README.md")),
    description="Group catering in Los Angeles: tell Haggle about your event and it finds caterers, "
                "collects quotes, negotiates and books.",
)

chat = Protocol(spec=chat_protocol_spec)


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(f"Concierge @haggle address: {agent.address}")


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    started = time.monotonic()
    sent_at = msg.timestamp if msg.timestamp.tzinfo else msg.timestamp.replace(tzinfo=timezone.utc)
    lag = (datetime.now(timezone.utc) - sent_at).total_seconds()
    preview = "".join(c.text for c in msg.content if isinstance(c, TextContent))[:80]
    ctx.logger.info(f"chat from {sender[:16]} (sent {lag:.1f}s ago): {preview!r}")
    try:
        await _on_chat(ctx, sender, msg)
    finally:
        ctx.logger.info(f"chat from {sender[:16]} handled in {time.monotonic() - started:.1f}s")


async def _on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    text = re.sub(r"^(@[\w.-]+[\s,:]*)+", "", text).strip()  # ASI:One keeps the "@haggle" mention
    if not text:
        return

    s = ctx.storage.get(sender) or new_session()
    if text.lower().strip(" .!") in RESET_WORDS:
        s = new_session()
        ctx.storage.set(sender, s)
        await ctx.send(sender, text_message("Starting a new request. Tell me about your event."))
        return

    sel = parse_selection(text)
    action = sel.get("action")
    if action in FOLLOWUP_ACTIONS:  # after-event cards belong to a booked (often earlier) request
        await ctx.send(FOLLOWUP_ADDRESS, UserReply(rfq_id=sel.get("rfq_id", ""), user_address=sender,
                                                   selection=json.dumps(sel), text=text[:2000],
                                                   trace=s.get("trace", "")))
        return
    if sel.get("rfq_id") and sel["rfq_id"] != s["rfq_id"]:
        await ctx.send(sender, text_message("That card is from an earlier request. Here's where we are now."))
        await show_current(ctx, sender, s)
        return
    if action == "toggle_vendor" and s.get("vendors"):
        await toggle_vendor(ctx, sender, s, sel, text)
        ctx.storage.set(sender, s)
        return
    if action in ("select_vendors", "select_all") and s.get("vendors"):
        await pick_vendors(ctx, sender, s, sel, text)
        ctx.storage.set(sender, s)
        return
    if action in ("pick_quote", "negotiate", "back", "book", "request_quote", "confirm", "cancel") and s.get("quotes"):
        await quote_action(ctx, sender, s, action, sel, text)
        ctx.storage.set(sender, s)
        return

    checklist = s["checklist"]
    if action == "submit_event":
        await apply_form(ctx, checklist, sel, s.get("asked", []))
    else:
        data = await extract_event(ctx, text)
        if data:
            apply_extraction(checklist, data)
        # If the LLM failed, nothing is guessed: the form below asks for everything still open.

    ask = open_fields(checklist)
    if ask:
        s["stage"] = "collecting"
        s["asked"] = list(ask)
        have = {f: display(f, checklist[f]["value"]) for f, st in ask.items() if st == "needs_confirm"}
        await ctx.send(
            sender,
            card_message(form_narration(checklist, ask), "form", event_form(s["rfq_id"], ask, have)),
        )
    elif s.get("searched_fp") == fingerprint(checklist):
        await show_current(ctx, sender, s)  # nothing changed: don't search again
    else:
        # TODO(Phase 2): extras card, then the confirmation review card.
        if s.get("searched_fp"):
            s["version"] += 1  # the request changed: old quotes must not be mixed with new ones
        s["searched_fp"] = fingerprint(checklist)
        for k in ("vendors", "quotes", "ranked", "fits", "declined", "list_prices", "shortlist", "countering",
                  "picked", "negolog"):
            s.pop(k, None)
        s["stage"] = "discovering"
        s["asked"] = []
        await ctx.send(
            sender,
            text_message(f"Here's your request: {summary(checklist)}. "
                         "Looking for caterers nearby. No caterers are contacted until you choose."),
        )
        # Searching is allowed before the user chooses; contacting vendors is not.
        s.setdefault("trace", observability.new_trace())
        ctx.storage.set(f"rfq:{s['rfq_id']}", sender)
        await ctx.send(
            DISCOVERY_ADDRESS,
            DiscoveryRequest(rfq_id=s["rfq_id"], location=checklist["location"]["value"], trace=s["trace"]),
        )
        ctx.logger.info(f"[{s['rfq_id']}] discovery request sent to {DISCOVERY_ADDRESS}")
    ctx.storage.set(sender, s)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


async def show_current(ctx: Context, user: str, s: dict) -> None:
    """Resend whatever the user should be looking at now."""
    if s.get("quotes"):
        await send_carousel(ctx, user, s, "Here are your quotes.")
    elif s.get("vendors"):
        await send_picker(ctx, user, s)
    elif s.get("stage") in ("discovering", "quoting"):
        await ctx.send(user, text_message("Still working on it. I'll message you as soon as I have results."))
    else:
        await ctx.send(user, text_message("Tell me about your event, or say \"start over\"."))


def _session_for(ctx: Context, rfq_id: str) -> tuple[str | None, dict | None]:
    user = ctx.storage.get(f"rfq:{rfq_id}")
    s = ctx.storage.get(user) if user else None
    if not s or s.get("rfq_id") != rfq_id:
        return None, None
    return user, s


# ---- Discovery -> caterer picker ------------------------------------------

discovery = Protocol(name="HaggleDiscoveryClient", version="0.1.0")


def vendor_entry(v) -> dict:
    """VendorInfo -> what the session keeps. vendor_id is the vendor agent's name: the business's slug."""
    d = v.dict()
    if v.agent_address and v.name.startswith("agent1"):
        vid, name = v.agent_address, "Haggle vendor agent"  # search had no name for this agent
    elif v.agent_address:
        vid, name = v.name, v.business_name or v.name.replace("-", " ").title()  # vendor agents are named by slug
    else:
        vid, name = slugify(v.name), v.name  # the slug the Factory will give its agent
    meta = []
    if v.rating is not None:
        meta.append(f"★ {v.rating}" + (f" ({v.rating_count})" if v.rating_count else ""))
    if v.source == "places":
        meta.append(f"{v.distance_mi} mi away")
    d.update(vendor_id=vid, display_name=name, live=bool(v.agent_address),
             meta=" · ".join(meta))
    return d


def merge_vendors(entries: list[dict]) -> list[dict]:
    """One entry per vendor_id: a Places listing and its agent from Agentverse search become one."""
    merged: dict[str, dict] = {}
    for e in entries:
        old = merged.get(e["vendor_id"])
        if not old:
            merged[e["vendor_id"]] = e
            continue
        agent, places = (old, e) if old["live"] else (e, old)
        merged[e["vendor_id"]] = {**places, "agent_address": agent["agent_address"], "live": True,
                                  "claimed": agent.get("claimed", False),
                                  "photo_url": places.get("photo_url") or agent.get("photo_url", "")}
    return list(merged.values())


async def send_picker(ctx: Context, user: str, s: dict) -> None:
    vendors = s["vendors"]
    live = sum(v["live"] for v in vendors)
    loc = s["location"]["resolved"]
    connected = sum(bool(v.get("claimed")) for v in vendors)
    note = (f"{connected} {'are' if connected != 1 else 'is'} connected to Haggle and can negotiate; for the others "
            "I'll use their public menus." if connected else "I'll get estimates from their public menus.")
    await ctx.send(user, card_message(
        f"I found {len(vendors)} caterers near {loc}. {note} Swipe through, tap Select on each one you want, "
        "then swipe to the end and tap Get quotes.",
        "carousel",
        picker_carousel(
            s["rfq_id"], loc,
            [{"vendor_id": v["vendor_id"], "name": v["display_name"], "photo_url": v["photo_url"],
              "meta": v["meta"], "connected": v.get("claimed", False)} for v in vendors],
        ),
    ))


async def toggle_vendor(ctx: Context, user: str, s: dict, sel: dict, text: str) -> None:
    """Add or remove one caterer. Answer in one line and don't re-send the carousel: the user keeps using it."""
    names = {v["vendor_id"]: v["display_name"] for v in s["vendors"]}
    vid = sel.get("vendor_id") if sel.get("vendor_id") in names else next(
        (k for k, n in names.items() if n.lower() in text.lower()), None)
    picked = s.setdefault("picked", [])
    if vid is None:
        await ctx.send(user, text_message("Tap Select on a caterer card."))
        return
    if vid in picked:
        picked.remove(vid)
        verb = "Removed"
    else:
        picked.append(vid)
        verb = "Added"
    chosen = ", ".join(names[x] for x in picked) or "none yet"
    await ctx.send(user, text_message(
        f"{verb} {names[vid]}. Selected ({len(picked)}): {chosen}. "
        "Keep selecting, or swipe to the last card and tap Get quotes."))


@discovery.on_message(DiscoveryResult)
async def on_discovery_result(ctx: Context, sender: str, result: DiscoveryResult):
    if sender != DISCOVERY_ADDRESS:
        ctx.logger.warning(f"Ignoring DiscoveryResult from unknown sender {sender}")
        return
    observability.tag(result.trace, result.rfq_id)
    user, s = _session_for(ctx, result.rfq_id)
    if not s:
        ctx.logger.warning(f"[{result.rfq_id}] DiscoveryResult for an unknown or replaced request")
        return
    ctx.logger.info(
        f"[{result.rfq_id}] {len(result.vendors)} vendors from Discovery"
        + (f"; error: {result.error}" if result.error else "")
    )

    if (result.lat is None or result.lng is None) and "could not find location" not in result.error:
        # Search itself failed (Places down, crash): keep the request, let the user retry.
        await ctx.send(user, text_message(
            "Caterer search is having trouble right now. Send any message in a minute and I'll try again."
        ))
        s["stage"] = "collecting"
        s.pop("searched_fp", None)
        ctx.storage.set(user, s)
        return

    if result.lat is None or result.lng is None:
        # Location couldn't be resolved: ask for it again instead of guessing.
        checklist = s["checklist"]
        checklist["location"] = {"value": None, "status": "missing"}
        ask = open_fields(checklist)
        s["stage"], s["asked"] = "collecting", list(ask)
        s.pop("searched_fp", None)
        ctx.storage.set(user, s)
        await ctx.send(user, card_message(
            "I couldn't find that location in Los Angeles. Could you give me a full address or venue name?",
            "form",
            event_form(s["rfq_id"], ask, {}),
        ))
        return

    s["location"] = {"resolved": result.resolved_location, "lat": result.lat, "lng": result.lng}
    vendors = merge_vendors([vendor_entry(v) for v in result.vendors])
    s["vendors"] = vendors
    s["stage"] = "picking"
    ctx.storage.set(user, s)
    if vendors:
        await send_picker(ctx, user, s)
    else:
        await ctx.send(user, text_message(
            f"I couldn't find caterers near {result.resolved_location} right now. "
            "Try a nearby neighborhood, or say \"start over\"."
        ))


# ---- Picker -> Factory -> Negotiator ------------------------------------

def build_rfq(s: dict) -> RFQ:
    cl = s["checklist"]
    loc = s.get("location", {})
    return RFQ(
        rfq_id=s["rfq_id"],
        version=s["version"],
        event_date=cl["date"]["value"],
        event_time=cl["time"]["value"],
        location=loc.get("resolved") or cl["location"]["value"],
        lat=loc.get("lat"),
        lng=loc.get("lng"),
        headcount=cl["headcount"]["value"],
        budget_usd=cl["budget_usd"]["value"] or 0.0,  # 0 = no fixed budget
        dietary=cl["dietary"]["value"],
        allergies=cl["allergies"]["value"],
        trace=s["trace"],
    )


async def pick_vendors(ctx: Context, user: str, s: dict, sel: dict, text: str) -> None:
    vendors = s["vendors"]
    if sel.get("action") == "select_all":
        idx = list(range(len(vendors)))
    else:
        picks = sel.get("vendors") or []
        if isinstance(picks, str):  # a list, or "a,b" depending on how the drawer serializes it
            try:
                picks = json.loads(picks)
            except ValueError:
                picks = [x.strip() for x in picks.split(",")]
        picks = set(picks if isinstance(picks, list) else [picks])
        idx = [i for i, v in enumerate(vendors) if v["vendor_id"] in picks]
        idx = idx or [i for i, v in enumerate(vendors) if v["vendor_id"] in s.get("picked", [])]
        if not idx:  # planner prose: "picked pick_0 and pick_3" or caterer names
            idx = sorted({int(m) for m in re.findall(r"\bpick_(\d+)\b", text) if int(m) < len(vendors)}
                         | {i for i, v in enumerate(vendors) if v["display_name"].lower() in text.lower()})
    if not idx:
        await ctx.send(user, text_message("Tap at least one caterer, or tap \"Quote all\"."))
        await send_picker(ctx, user, s)
        return

    chosen = [vendors[i] for i in idx]
    shortlist = sorted(v["vendor_id"] for v in chosen)
    if s.get("shortlist") == shortlist:
        # Same picks again (double click or replay): never re-send anything.
        if s.get("quotes"):
            await send_carousel(ctx, user, s, "Here are your quotes.")
        else:
            await ctx.send(user, text_message("Still working on quotes from those caterers."))
        return
    if s.get("shortlist"):
        s["version"] += 1  # a new shortlist is a new round of quotes
    for k in ("quotes", "ranked", "fits", "declined", "list_prices", "countering", "onboarding", "onboard_failed",
              "negolog"):
        s.pop(k, None)
    s["shortlist"] = shortlist
    ctx.storage.set(f"rfq:{s['rfq_id']}", user)

    offline = [v for v in chosen if not v["live"]]
    if not offline:
        await start_negotiation(ctx, user, s)
        return

    # Caterers without an agent go to the Factory first; quotes start when their agents are live.
    s["stage"] = "onboarding"
    s["onboarding"] = {v["vendor_id"]: "pending" for v in offline}
    s["onboard_deadline"] = (datetime.now(timezone.utc) + timedelta(seconds=ONBOARD_TIMEOUT_S)).isoformat()
    for v in offline:
        waiting = ctx.storage.get(f"onboard:{v['vendor_id']}") or []
        ctx.storage.set(f"onboard:{v['vendor_id']}", sorted(set(waiting) | {user}))
        await ctx.send(FACTORY_ADDRESS, OnboardRequest(
            business_name=v["display_name"], website=v.get("website", ""), place_id=v.get("place_id", ""),
            phone=v.get("phone", ""), address=v.get("address", ""), trace=s["trace"]))
    users = ctx.storage.get("onboarding_users") or []
    ctx.storage.set("onboarding_users", sorted(set(users) | {user}))
    ctx.logger.info(f"[{s['rfq_id']}] onboarding {list(s['onboarding'])}")
    n = len(offline)
    await ctx.send(user, text_message(
        f"Setting up {n} caterer{'s' if n > 1 else ''} that {'don’t' if n > 1 else 'doesn’t'} have a Haggle agent yet: "
        "reading their public menus. This takes up to a minute, then I'll get quotes from everyone you picked."))


def dedupe_by_address(s: dict) -> None:
    """A business onboarded just now may also have come back from Agentverse search under its address:
    keep the entry with the Places details."""
    keep: dict[str, dict] = {}
    for v in s["vendors"]:
        addr = v.get("agent_address")
        if not addr:
            keep[v["vendor_id"]] = v
            continue
        other = next((k for k, o in keep.items() if o.get("agent_address") == addr), None)
        if other is None:
            keep[v["vendor_id"]] = v
        elif v.get("place_id") and not keep[other].get("place_id"):
            del keep[other]
            keep[v["vendor_id"]] = v
            if other in s["shortlist"]:
                s["shortlist"] = sorted((set(s["shortlist"]) - {other}) | {v["vendor_id"]})
    s["vendors"] = list(keep.values())
    s["shortlist"] = [vid for vid in s["shortlist"] if vid in keep]


async def start_negotiation(ctx: Context, user: str, s: dict) -> None:
    """Send the RFQ to every shortlisted vendor that has a live agent."""
    s.pop("onboarding", None)
    dedupe_by_address(s)
    live = [v for v in s["vendors"] if v["vendor_id"] in s["shortlist"] and v["live"]]
    failed = s.get("onboard_failed", {})
    lines = []
    if failed:
        names = ", ".join(_vendor(s, vid).get("display_name", vid) for vid in failed)
        lines.append(f"I couldn't set up {names}, so I've skipped {'them' if len(failed) > 1 else 'it'}.")
    if not live:
        s["stage"] = "picking"
        lines.append("None of the caterers you picked can quote right now. Pick others, or say \"start over\".")
        await ctx.send(user, text_message(" ".join(lines)))
        return
    s["stage"] = "quoting"
    refs = [VendorRef(vendor_id=v["vendor_id"], name=v["display_name"], address=v["agent_address"],
                      distance_mi=v["distance_mi"], claimed=v.get("claimed", False), rating=v["rating"])
            for v in live]
    await ctx.send(NEGOTIATOR_ADDRESS, NegotiationRequest(rfq=build_rfq(s), vendors=refs, trace=s["trace"]))
    ctx.logger.info(f"[{s['rfq_id']}] v{s['version']} negotiation request for {[r.vendor_id for r in refs]}")
    lines.append(f"Asking {len(live)} caterer{'s' if len(live) > 1 else ''} for quotes. This takes up to 10 seconds.")
    await ctx.send(user, text_message(" ".join(lines)))


# ---- Factory onboarding ----------------------------------------------------

onboarding = Protocol(name="HaggleFactoryClient", version="0.1.0")


@onboarding.on_message(OnboardResult)
async def on_onboard_result(ctx: Context, sender: str, result: OnboardResult):
    if sender != FACTORY_ADDRESS:
        ctx.logger.warning(f"Ignoring OnboardResult from unknown sender {sender}")
        return
    observability.tag(result.trace, vendor_id=result.vendor_id)
    vid = result.vendor_id
    ctx.logger.info(f"[{vid}] onboarded: {result.status} {result.agent_address or result.message}")
    users = ctx.storage.get(f"onboard:{vid}") or []
    ctx.storage.set(f"onboard:{vid}", [])
    for user in users:
        s = ctx.storage.get(user)
        if not s or s.get("stage") != "onboarding" or vid not in s.get("onboarding", {}):
            continue
        if result.agent_address:
            for v in s["vendors"]:
                if v["vendor_id"] == vid:
                    v.update(agent_address=result.agent_address, live=True, claimed=result.claimed)
            s["onboarding"][vid] = "done"
        else:
            s["onboarding"][vid] = "failed"
            s.setdefault("onboard_failed", {})[vid] = result.message or result.error
        if "pending" not in s["onboarding"].values():
            await start_negotiation(ctx, user, s)
        ctx.storage.set(user, s)


@agent.on_interval(period=5)
async def onboarding_timeouts(ctx: Context):
    """Don't wait forever on the Factory: after ONBOARD_TIMEOUT_S, quote whoever is ready."""
    users = ctx.storage.get("onboarding_users") or []
    if not users:
        return
    still = []
    for user in users:
        s = ctx.storage.get(user)
        if not s or s.get("stage") != "onboarding":
            continue
        if datetime.fromisoformat(s["onboard_deadline"]) > datetime.now(timezone.utc):
            still.append(user)
            continue
        for vid, state in s["onboarding"].items():
            if state == "pending":
                s.setdefault("onboard_failed", {})[vid] = "timed out"
        await start_negotiation(ctx, user, s)
        ctx.storage.set(user, s)
    ctx.storage.set("onboarding_users", still)


# ---- Quotes carousel, detail, counter -------------------------------------

negotiation = Protocol(name="HaggleNegotiatorClient", version="0.1.0")


def _vendor(s: dict, vid: str) -> dict:
    return next((v for v in s.get("vendors", []) if v["vendor_id"] == vid), {})


def _store_ranked(s: dict, ranked) -> None:
    s["quotes"], s["ranked"], s["fits"] = {}, [], {}
    for r in ranked:
        vid = r.quote.vendor_id
        s["quotes"][vid] = json.loads(r.quote.json())
        s["list_prices"].setdefault(vid, r.quote.total_usd)
        s["ranked"].append(vid)
        s["fits"][vid] = r.fits_budget
    s["ranked"].sort(key=lambda vid: s["quotes"][vid]["status"] == "price_on_request")  # priced quotes first


def can_negotiate(q: dict) -> bool:
    return not q["estimate"] and q["round"] < 2 and q["status"] not in ("accepted", "needs_approval")


def on_request(q: dict) -> bool:
    return q["status"] == "price_on_request"


def quote_view(s: dict, vid: str) -> dict:
    q, v = s["quotes"][vid], _vendor(s, vid)
    totals = [s["quotes"][x]["total_usd"] for x in s["ranked"] if not on_request(s["quotes"][x])]
    ratings = [_vendor(s, x).get("rating") for x in s["ranked"]]
    badges = []
    if len(totals) > 1 and not on_request(q) and q["total_usd"] == min(totals):
        badges.append(("Cheapest", "success"))
    if v.get("rating") and v["rating"] == max(r for r in ratings if r):
        badges.append(("Top rated", "info"))
    saved = s["list_prices"].get(vid, q["total_usd"]) - q["total_usd"]
    if saved >= 0.01:
        badges.append((f"Negotiated −{usd(saved)}", "success"))
    if s["checklist"]["dietary"]["value"] or s["checklist"]["allergies"]["value"]:
        badges.append(("Fits all diets", "info") if q.get("dietary_ok", True) else ("Diets to confirm", "warning"))
    if on_request(q):
        badges = [b for b in badges if b[0] not in ("Fits all diets", "Diets to confirm")]
        badges.append(("Price on request", "info"))
    elif not s["fits"].get(vid, True):
        badges.append(("Over budget", "warning"))
    if q["estimate"] and not on_request(q):
        badges.append(("Estimate · not confirmed", "warning"))
    badges.insert(0, ("Connected", "success") if q["claimed"] else ("Not connected", "warning"))
    if q["status"] == "needs_approval":
        badges.append(("Needs owner approval", "warning"))
    sub = [f"#{s['ranked'].index(vid) + 1}"]
    if v.get("rating") is not None:
        sub.append(f"★ {v['rating']}")
    if v.get("source") == "places":
        sub.append(f"{v['distance_mi']} mi away")
    return {
        "vendor_id": vid,
        "name": q["vendor_name"],
        "photo_url": v.get("photo_url") or q.get("photo_url", ""),
        "total_usd": q["total_usd"],
        "per_head_usd": q["per_head_usd"],
        "price_text": "Price on request" if on_request(q) else "",
        "subtitle": " · ".join(sub),
        "badges": badges,
    }


async def send_carousel(ctx: Context, user: str, s: dict, narration: str) -> None:
    n = len(s["ranked"])
    await ctx.send(user, card_message(
        narration,
        "carousel",
        quotes_carousel(s["rfq_id"], f"{n} quote{'s' if n != 1 else ''} for {summary(s['checklist'])}",
                        [quote_view(s, vid) for vid in s["ranked"]]),
    ))


def detail_rows(s: dict, vid: str) -> list[tuple[str, str]]:
    q, v = s["quotes"][vid], _vendor(s, vid)
    if on_request(q):
        rows = [("Price", "not published: request a quote")]
    else:
        rows = [("Total", usd(q["total_usd"])), ("Per person", usd(q["per_head_usd"]))]
    if q["delivery_fee_usd"]:
        rows.append(("Delivery fee", usd(q["delivery_fee_usd"])))
    for item in q["items"]:
        label = "Standard" if item["group"] == "standard" else item["group"].replace("_", "-").capitalize()
        rows.append((f"{label} · {item['package']}",
                     f"{item['headcount']} × {usd(item['per_head_usd'])} = {usd(item['subtotal_usd'])}"))
    if v.get("rating") is not None:
        rows.append(("Rating", f"★ {v['rating']} ({v.get('rating_count') or 0} reviews)"))
    if v.get("source") == "places":
        rows.append(("Distance", f"{v['distance_mi']} mi"))
    if q.get("conflicts"):
        rows.append(("Can't confirm", "; ".join(q["conflicts"])))
    for speaker, said in s.get("negolog", {}).get(vid, []):
        rows.append((f"💬 {speaker}", said))
    for line in q.get("menu_preview", []):
        name, _, price = line.partition(" — ")
        rows.append((f"Menu · {name}", price or "on the menu"))
    if q.get("menu_url"):
        rows.append(("Full menu", q["menu_url"]))
    rows.append(("Status", "Price on request" if on_request(q) else "Estimate · not confirmed" if q["estimate"]
                 else "Waiting on owner approval" if q["status"] == "needs_approval" else "Confirmed quote"))
    if q["note"]:
        rows.append(("Note", q["note"]))
    return rows


async def quote_action(ctx: Context, user: str, s: dict, action: str, sel: dict, text: str) -> None:
    vid = sel.get("vendor_id")
    if vid not in s["quotes"]:  # planner prose: find the vendor by id or name
        vid = next((x for x in s["ranked"] if x in text or s["quotes"][x]["vendor_name"].lower() in text.lower()),
                   None)
    if action == "back" or vid is None:
        await send_carousel(ctx, user, s, "Here are your quotes.")
        return

    q = s["quotes"][vid]
    if action == "pick_quote":
        await send_detail(ctx, user, s, vid)
        return
    if action in ("book", "request_quote", "confirm", "cancel"):
        await booking_action(ctx, user, s, action, vid)
        return

    # negotiate
    if s.get("countering"):
        await ctx.send(user, text_message("Already negotiating. I'll show you the new price in a moment."))
        return
    if not can_negotiate(q):
        reason = ("it's an estimate from an unclaimed caterer, so there's nothing to negotiate yet"
                  if q["estimate"] else "the owner is still deciding on the last offer"
                  if q["status"] == "needs_approval" else "this caterer has already given its best price")
        await ctx.send(user, text_message(f"I can't negotiate with {q['vendor_name']}: {reason}."))
        return
    s["countering"] = vid
    await ctx.send(NEGOTIATOR_ADDRESS, CounterRequest(rfq_id=s["rfq_id"], vendor_id=vid, trace=s["trace"]))
    ctx.logger.info(f"[{s['rfq_id']}] counter request for {vid}")
    await ctx.send(user, text_message(f"Asking {q['vendor_name']} for a better price..."))


async def send_detail(ctx: Context, user: str, s: dict, vid: str, narration: str = "") -> None:
    q = s["quotes"][vid]
    view = quote_view(s, vid) | {"rows": detail_rows(s, vid)}
    narration = narration or f"{q['vendor_name']}: " + (
        "no public prices yet, but here's their menu." if on_request(q) else
        f"{usd(q['total_usd'])} for {s['checklist']['headcount']['value']} guests.")
    await ctx.send(user, card_message(narration, "detail", quote_detail(s["rfq_id"], view, can_negotiate(q), next_step(q))))


def next_step(q: dict) -> str:
    if q["status"] == "needs_approval":
        return ""
    return "request_quote" if q["estimate"] else "book"


async def booking_action(ctx: Context, user: str, s: dict, action: str, vid: str) -> None:
    """Book (claimed) or request a confirmed quote (unclaimed): a review card first, then confirm.
    Booking takes the deposit through the Payment Protocol; the Booker verifies it and confirms."""
    q, cl = s["quotes"][vid], s["checklist"]
    when = summary(cl).split(" · ")[0]
    paid = s.get("booking", {}).get("paid") or s.get("booking", {}).get("committed")
    if paid:  # never replace or cancel a booking whose deposit is in
        b = s["booking"]
        await ctx.send(user, text_message(
            f"You're already booked with {s['quotes'][b['vendor_id']]['vendor_name']} (deposit "
            f"{usd(b['deposit_usd'])} {'paid' if b.get('paid') else 'being confirmed'}). "
            "Say \"start over\" for a new event."))
        return
    if action in ("book", "request_quote"):
        if next_step(q) != action:
            await ctx.send(user, text_message("That isn't available for this caterer right now."))
            return
        rows = [("Caterer", q["vendor_name"]), ("When", when),
                ("Where", s.get("location", {}).get("resolved") or cl["location"]["value"]),
                ("Guests", display("headcount", cl["headcount"]["value"])),
                ("Dietary", display("dietary", cl["dietary"]["value"]) + " · " + display("allergies", cl["allergies"]["value"]))]
        if action == "book":
            deposit = round(q["total_usd"] * DEPOSIT_PCT, 2)
            s["booking"] = {"vendor_id": vid, "kind": "book", "total_usd": q["total_usd"], "deposit_usd": deposit}
            rows += [("Total", usd(q["total_usd"])), (f"Deposit now ({DEPOSIT_PCT:.0%})", usd(deposit)),
                     ("Balance to the caterer", usd(round(q["total_usd"] - deposit, 2))),
                     ("Cancellation", "per the caterer's terms, confirmed in the booking email")]
            card = review_card(s["rfq_id"], vid, "Confirm your booking", rows, f"Pay {usd(deposit)} deposit")
            text = f"Review your booking with {q['vendor_name']}."
        else:
            target = cl["budget_usd"]["value"]
            s["booking"] = {"vendor_id": vid, "kind": "request_quote", "total_usd": q["total_usd"]}
            rows += [("Their estimate", "not published" if on_request(q) else f"{usd(q['total_usd'])} (not confirmed)"),
                     ("Your target", usd(target) if target else "no fixed budget"),
                     ("Sent to", "the business by email, with a link to claim its Haggle agent")]
            card = review_card(s["rfq_id"], vid, "Request a confirmed quote", rows, "Send request")
            text = f"{q['vendor_name']} hasn't confirmed its prices. I can ask them for a real quote."
        await ctx.send(user, card_message(text, "review", card))
        return

    booking = s.get("booking")
    if not booking or booking["vendor_id"] != vid:
        await send_carousel(ctx, user, s, "Pick a caterer first.")
        return
    if action == "cancel":
        s.pop("booking", None)
        await send_carousel(ctx, user, s, "No problem, nothing was booked. Here are your quotes again.")
        return
    booking["confirmed"] = True  # idempotent: confirming twice changes nothing
    s["stage"] = "awaiting_payment" if booking["kind"] == "book" else "quote_requested"
    ctx.logger.info(f"[{s['rfq_id']}] {booking['kind']} confirmed with {vid}: {booking}")
    if booking["kind"] == "book":
        await request_deposit(ctx, user, s, q, when)
    else:
        await request_quote(ctx, user, s, vid)


# ---- Deposit: Payment Protocol (seller) -> Booker ---------------------------

def booking_message(s: dict, user: str) -> BookingConfirmed:
    b, cl = s["booking"], s["checklist"]
    return BookingConfirmed(
        rfq_id=s["rfq_id"], vendor_id=b["vendor_id"], vendor_name=s["quotes"][b["vendor_id"]]["vendor_name"],
        vendor_address=_vendor(s, b["vendor_id"]).get("agent_address", ""), version=s["version"],
        user_address=user, event_date=cl["date"]["value"], event_time=cl["time"]["value"],
        location=s.get("location", {}).get("resolved") or cl["location"]["value"],
        headcount=cl["headcount"]["value"],
        dietary_summary=display("dietary", cl["dietary"]["value"]) + "; " + display("allergies", cl["allergies"]["value"]),
        total_usd=b["total_usd"], deposit_usd=b["deposit_usd"],
        checkout_session_id=b["checkout"]["checkout_session_id"],
        lat=s.get("location", {}).get("lat"), lng=s.get("location", {}).get("lng"), trace=s["trace"],
    )


async def request_deposit(ctx: Context, user: str, s: dict, q: dict, when: str) -> None:
    """Send RequestPayment for the deposit. Reuses an open Checkout Session and never creates a second one
    once a session is paid, so tapping "Pay" twice can't charge twice."""
    b = s["booking"]
    if mock_payments():
        await mock_deposit(ctx, user, s, q, when)
        return
    checkout = b.get("checkout")
    if checkout:
        try:
            status = await asyncio.to_thread(session_status, checkout["checkout_session_id"])
        except Exception:
            ctx.logger.exception(f"[{s['rfq_id']}] Stripe status check failed")
            await ctx.send(user, text_message("I couldn't reach Stripe just now. Nothing new was charged; "
                                              "tap the pay button again in a minute."))
            return
        if status == "complete":  # paid, but the CommitPayment was lost: let the Booker confirm it
            b["committed"] = checkout["checkout_session_id"]
            await ctx.send(BOOKER_ADDRESS, booking_message(s, user))
            await ctx.send(user, text_message("Your deposit is already paid. Confirming with the caterer..."))
            return
        if status != "open":
            checkout = None  # expired unpaid: start a fresh one
    if not checkout:
        try:
            checkout = await asyncio.to_thread(
                create_deposit_checkout, rfq_id=s["rfq_id"], vendor_id=b["vendor_id"], vendor_name=q["vendor_name"],
                amount_usd=b["deposit_usd"], user_address=user, chat_session_id=str(ctx.session),
                description=f"{DEPOSIT_PCT:.0%} deposit, {when}, {q['vendor_name']}")
        except Exception:
            ctx.logger.exception(f"[{s['rfq_id']}] couldn't create the Stripe checkout")
            await ctx.send(user, text_message("I couldn't start the deposit payment right now, so nothing was "
                                              "charged. Your booking is saved; tap the pay button again in a minute."))
            return
        b["checkout"] = checkout
    s["stage"] = "awaiting_payment"
    b["requested_at"] = datetime.now(timezone.utc).isoformat()
    ctx.storage.set("awaiting_payment", sorted(set(ctx.storage.get("awaiting_payment") or []) | {user}))
    await ctx.send(user, deposit_request(ctx, s["rfq_id"], b["deposit_usd"], checkout,
                                         f"{usd(b['deposit_usd'])} deposit to book {q['vendor_name']} for {when}"))
    await ctx.send(user, text_message(
        f"Pay the {usd(b['deposit_usd'])} deposit above to book {q['vendor_name']} for {when}. "
        "I'll confirm with the caterer as soon as it goes through."))
    ctx.logger.info(f"[{s['rfq_id']}] RequestPayment {usd(b['deposit_usd'])} ({checkout['checkout_session_id']})")


async def mock_deposit(ctx: Context, user: str, s: dict, q: dict, when: str) -> None:
    """STRIPE_MOCK_PAYMENTS=1: charge the deposit on Stripe's test card (real test-mode charge, no checkout UI),
    then hand the booking to the Booker exactly as a CommitPayment would."""
    b = s["booking"]
    try:
        intent = await asyncio.to_thread(
            charge_test_deposit, rfq_id=s["rfq_id"], vendor_id=b["vendor_id"], vendor_name=q["vendor_name"],
            amount_usd=b["deposit_usd"], user_address=user,
            description=f"{DEPOSIT_PCT:.0%} deposit, {when}, {q['vendor_name']}")
    except Exception:
        ctx.logger.exception(f"[{s['rfq_id']}] Stripe test charge failed")
        await ctx.send(user, text_message("I couldn't take the test deposit on Stripe just now, so nothing was "
                                          "charged. Tap Book again in a minute."))
        return
    b["checkout"], b["committed"], b["mock"] = {"checkout_session_id": intent}, intent, True
    s["stage"] = "awaiting_payment"
    ctx.storage.set(user, s)
    ctx.logger.info(f"[{s['rfq_id']}] mock deposit {usd(b['deposit_usd'])} charged on Stripe test mode ({intent})")
    await ctx.send(BOOKER_ADDRESS, booking_message(s, user))
    await ctx.send(user, text_message(
        f"Test deposit of {usd(b['deposit_usd'])} charged through Stripe (test mode, Stripe's test card; "
        f"payment {intent}). Confirming with {q['vendor_name']}..."))


async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
    s = ctx.storage.get(sender)
    b = (s or {}).get("booking") or {}
    txn = msg.transaction_id
    if msg.funds.payment_method != "stripe" or not txn or txn != b.get("checkout", {}).get("checkout_session_id"):
        ctx.logger.warning(f"CommitPayment {txn} from {sender[:16]} matches no open booking")
        await ctx.send(sender, CancelPayment(transaction_id=txn, reason="This payment doesn't match an open Haggle booking."))
        return
    if b.get("paid"):  # replay
        await ctx.send(sender, CompletePayment(transaction_id=txn))
        return
    if b.get("committed") == txn:
        return  # the Booker is already on it
    b["committed"] = txn
    ctx.storage.set(sender, s)
    await ctx.send(BOOKER_ADDRESS, booking_message(s, sender))
    ctx.logger.info(f"[{s['rfq_id']}] CommitPayment {txn} -> Booker")
    await ctx.send(sender, text_message("Payment received. Confirming your booking with the caterer..."))


async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
    s = ctx.storage.get(sender)
    if not s or not s.get("booking") or s["booking"].get("committed"):
        return
    s.pop("booking", None)
    s["stage"] = "choosing"
    ctx.storage.set(sender, s)
    await send_carousel(ctx, sender, s, "No deposit was taken" + (f" ({msg.reason})" if msg.reason else "")
                        + ". Here are your quotes again.")


booker = Protocol(name="HaggleBookerClient", version="0.1.0")


async def request_quote(ctx: Context, user: str, s: dict, vid: str) -> None:
    """Ask the Booker to email an unclaimed caterer for a confirmed quote (once per caterer and request)."""
    q, cl, v = s["quotes"][vid], s["checklist"], _vendor(s, vid)
    sent = s.setdefault("quote_requests", {})
    if vid in sent:
        await ctx.send(user, text_message(f"I've already asked {q['vendor_name']} for a quote ({sent[vid]})."))
        return
    ctx.storage.set(f"rfq:{s['rfq_id']}", user)
    await ctx.send(BOOKER_ADDRESS, QuoteRequest(
        rfq_id=s["rfq_id"], vendor_id=vid, vendor_name=q["vendor_name"], vendor_address=v.get("agent_address", ""),
        vendor_phone=v.get("phone", ""), event_date=cl["date"]["value"], event_time=cl["time"]["value"],
        location=s.get("location", {}).get("resolved") or cl["location"]["value"], headcount=cl["headcount"]["value"],
        dietary_summary=display("dietary", cl["dietary"]["value"]) + "; " + display("allergies", cl["allergies"]["value"]),
        budget_usd=cl["budget_usd"]["value"] or 0.0, estimate_usd=0.0 if on_request(q) else q["total_usd"],
        trace=s["trace"]))
    await ctx.send(user, text_message(f"Asking {q['vendor_name']} for a confirmed quote..."))


@booker.on_message(QuoteRequestSent)
async def on_quote_request_sent(ctx: Context, sender: str, r: QuoteRequestSent):
    if sender != BOOKER_ADDRESS:
        ctx.logger.warning(f"Ignoring QuoteRequestSent from unknown sender {sender}")
        return
    user, s = _session_for(ctx, r.rfq_id)
    if not s or r.vendor_id not in s.get("quotes", {}):
        return
    name = s["quotes"][r.vendor_id]["vendor_name"]
    if r.sent:
        s.setdefault("quote_requests", {})[r.vendor_id] = f"sent, reply by {r.reply_by}"
        ctx.storage.set(user, s)
        await ctx.send(user, text_message(
            f"Request sent to {name}. They have until {r.reply_by} to reply; I'll tell you when they do. "
            "(During testing the email goes to the team inbox.) You can still book a connected caterer meanwhile."))
    else:
        await ctx.send(user, text_message(f"I couldn't send the request to {name} just now ({r.detail}). "
                                          "Tap Send request again in a minute."))


@agent.on_interval(period=60)
async def expire_payment_requests(ctx: Context):
    """CancelPayment when a deposit request has waited longer than its deadline without a CommitPayment."""
    from shared.payment import DEADLINE_S
    for user in ctx.storage.get("awaiting_payment") or []:
        s = ctx.storage.get(user)
        b = (s or {}).get("booking") or {}
        if not b.get("requested_at") or b.get("committed") or b.get("paid"):
            continue
        if datetime.now(timezone.utc) - datetime.fromisoformat(b["requested_at"]) > timedelta(seconds=DEADLINE_S):
            await ctx.send(user, CancelPayment(transaction_id=b["checkout"]["checkout_session_id"],
                                               reason="The deposit wasn't paid in time."))
            b.pop("requested_at", None)
            ctx.storage.set(user, s)
            await ctx.send(user, text_message("The deposit request expired, so nothing was charged. "
                                              "Tap Book again whenever you're ready."))
    ctx.storage.set("awaiting_payment", [u for u in (ctx.storage.get("awaiting_payment") or [])
                                         if (ctx.storage.get(u) or {}).get("booking", {}).get("requested_at")])


@booker.on_message(BookingResult)
async def on_booking_result(ctx: Context, sender: str, result: BookingResult):
    if sender != BOOKER_ADDRESS:
        ctx.logger.warning(f"Ignoring BookingResult from unknown sender {sender}")
        return
    observability.tag(result.trace, result.rfq_id, result.vendor_id)
    user, s = _session_for(ctx, result.rfq_id)
    b = (s or {}).get("booking") or {}
    txn = b.get("committed")
    if not txn or b.get("vendor_id") != result.vendor_id:
        ctx.logger.warning(f"[{result.rfq_id}] BookingResult for an unknown booking")
        return
    name = s["quotes"][b["vendor_id"]]["vendor_name"]
    ctx.logger.info(f"[{result.rfq_id}] booking {result.status}: {result.detail}")

    if result.status == "confirmed":
        b["paid"], s["stage"] = True, "booked"
        ctx.storage.set(user, s)
        if not b.get("mock"):
            await ctx.send(user, CompletePayment(transaction_id=txn))
        cl = s["checklist"]
        await ctx.send(user, text_message(
            f"You're booked! {name}, {summary(cl).split(' · ')[0]}, {display('headcount', cl['headcount']['value'])}. "
            f"Deposit paid: {usd(b['deposit_usd'])}; balance to the caterer: {usd(round(b['total_usd'] - b['deposit_usd'], 2))}."
            + (" A confirmation email is on its way." if result.email_sent else "")))
        return

    if result.status == "vendor_declined":
        if not b.get("mock"):
            await ctx.send(user, CancelPayment(transaction_id=txn, reason=result.detail))
        s.pop("booking", None)
        s["stage"] = "choosing"
        ctx.storage.set(user, s)
        await send_carousel(ctx, user, s, f"Sorry: {result.detail}. Pick another caterer.")
        return

    # payment_failed or error: nothing booked; the user can tap pay again (the same checkout is reused).
    b.pop("committed", None)
    ctx.storage.set(user, s)
    if result.status == "payment_failed":
        if not b.get("mock"):
            await ctx.send(user, CancelPayment(transaction_id=txn, reason=result.detail))
        await ctx.send(user, text_message(f"Stripe hasn't confirmed the deposit ({result.detail}). "
                                          "Finish the checkout, then tap the pay button again."))
    else:
        await ctx.send(user, text_message("I couldn't confirm the payment with Stripe just now. You won't be "
                                          "charged twice: tap the pay button again in a minute."))


@negotiation.on_message(NegotiationResult)
async def on_negotiation_result(ctx: Context, sender: str, result: NegotiationResult):
    if sender != NEGOTIATOR_ADDRESS:
        ctx.logger.warning(f"Ignoring NegotiationResult from unknown sender {sender}")
        return
    observability.tag(result.trace, result.rfq_id)
    user, s = _session_for(ctx, result.rfq_id)
    if not s or s.get("stage") != "quoting":
        ctx.logger.warning(f"[{result.rfq_id}] NegotiationResult for an unknown or replaced request")
        return
    ctx.logger.info(f"[{result.rfq_id}] {len(result.ranked)} quotes, {len(result.declined)} declined")

    s["quotes"], s["list_prices"], s["declined"] = {}, {}, dict(result.declined)
    _store_ranked(s, result.ranked)
    cl = s["checklist"]
    asked = f"Quote request: {display('headcount', cl['headcount']['value'])}, {summary(cl).split(' · ')[0]}"
    s["negolog"] = {}
    for vid in s["ranked"]:
        q = s["quotes"][vid]
        answer = "No public prices: menu only" if on_request(q) else f"{usd(q['total_usd'])}" + (f" ({q['note']})" if q["note"] else "")
        s["negolog"][vid] = [["Haggle", asked], [q["vendor_name"], answer]]
    no_prices = [_vendor(s, vid).get("display_name", vid) for vid, why in s["declined"].items() if NO_PRICES in why]
    declined = "; ".join(f"{_vendor(s, vid).get('display_name', vid)}: {why}"
                         for vid, why in s["declined"].items() if NO_PRICES not in why)
    if no_prices:
        declined = "; ".join(filter(None, [declined, f"no public prices yet ({', '.join(no_prices)})"]))
    if not s["ranked"]:
        s["stage"] = "picking"
        ctx.storage.set(user, s)
        await ctx.send(user, text_message(
            "None of those caterers could quote" + (f" ({declined})" if declined else "") +
            ". Pick others from the list, or say \"start over\"."
        ))
        return
    s["stage"] = "choosing"
    ctx.storage.set(user, s)
    n = len(s["ranked"])
    await send_carousel(ctx, user, s, f"{n} quote{'s' if n != 1 else ''} in." +
                        (f" Couldn't quote: {declined}." if declined else ""))


@negotiation.on_message(CounterResult)
async def on_counter_result(ctx: Context, sender: str, result: CounterResult):
    if sender != NEGOTIATOR_ADDRESS:
        ctx.logger.warning(f"Ignoring CounterResult from unknown sender {sender}")
        return
    observability.tag(result.trace, result.rfq_id, result.vendor_id)
    user, s = _session_for(ctx, result.rfq_id)
    if not s or not s.get("quotes") or result.vendor_id not in s["quotes"]:
        ctx.logger.warning(f"[{result.rfq_id}] CounterResult for an unknown request or vendor")
        return
    s.pop("countering", None)
    vid = result.vendor_id
    name = s["quotes"][vid]["vendor_name"]
    before, was = s["quotes"][vid]["total_usd"], s["quotes"][vid]["status"]
    if result.ranked or not result.quote:
        _store_ranked(s, result.ranked)
    else:
        s["quotes"][vid] = json.loads(result.quote.json())
    after = s["quotes"].get(vid, {}).get("total_usd", before)
    status = s["quotes"].get(vid, {}).get("status", "declined")
    ctx.logger.info(f"[{result.rfq_id}] counter {vid}: {before} -> {after} {status} {result.declined_reason}")

    if status == "needs_approval":
        narration = f"{name} is checking with the owner. I'll update you as soon as they answer."
    elif was == "needs_approval" and status != "declined":
        narration = (f"The owner of {name} answered: {usd(after)}"
                     + (f" (was {usd(before)})." if after != before else ".")
                     + (f" {result.declined_reason}." if result.declined_reason else ""))
    elif after < before:
        narration = f"{name} came down from {usd(before)} to {usd(after)}."
        if status == "accepted":
            narration += " They accepted our offer."
    elif status == "declined":
        narration = f"{name} can't do this one" + (f": {result.declined_reason}." if result.declined_reason else ".")
    else:
        narration = f"{name} won't go lower" + (f": {result.declined_reason}." if result.declined_reason else ".")
    log = s.setdefault("negolog", {}).setdefault(vid, [])
    lines = []
    if result.target_usd:
        ask = f"Could you do {usd(result.target_usd)}?" + (f" ({result.reason})" if result.reason else "")
        log.append(["Haggle", ask])
        lines.append(f"🤖 Haggle → {name}: {ask}")
        if status == "declined":
            said = result.declined_reason or "We can't do this one."
        elif status == "needs_approval":
            said = "That's below what I can agree to on my own. Let me check with the owner."
        else:
            said = (s["quotes"][vid].get("note") or "") + (f" New total: {usd(after)}." if after != before else "")
        log.append([name, said.strip()])
        lines.append(f"🏪 {name} → Haggle: {said.strip()}")
    else:  # the owner answered a pending approval
        said = (s["quotes"].get(vid, {}).get("note") or result.declined_reason or "") + (
            f" Price: {usd(after)}." if status != "declined" else "")
        log.append([f"{name} (owner)", said.strip()])
        lines.append(f"🏪 {name} (owner) → Haggle: {said.strip()}")
    ctx.storage.set(user, s)
    transcript = "Negotiation with " + name + ":\n" + "\n".join(lines) + "\n\n" + narration
    if vid not in s["quotes"]:
        await ctx.send(user, text_message(transcript))
        if s["ranked"]:
            await send_carousel(ctx, user, s, "Here are the other quotes.")
        else:
            await ctx.send(user, text_message("No quotes are left. Pick other caterers, or say \"start over\"."))
        return
    await send_detail(ctx, user, s, vid, transcript)


# ---- Follow-up relay -------------------------------------------------------

followup = Protocol(name="HaggleFollowupClient", version="0.1.0")


@followup.on_message(UserNotice)
async def on_user_notice(ctx: Context, sender: str, n: UserNotice):
    """Reminders, the after-event check-in and food rescue cards from the Follow-up, sent on to the user."""
    if sender != FOLLOWUP_ADDRESS:
        ctx.logger.warning(f"Ignoring UserNotice from unknown sender {sender}")
        return
    observability.tag(n.trace, n.rfq_id)
    if ctx.storage.get(f"rfq:{n.rfq_id}") != n.user_address:  # only the user who made this request
        ctx.logger.warning(f"[{n.rfq_id}] UserNotice for a user who didn't make this request")
        return
    try:
        msg = card_message(n.text, n.card_kind, json.loads(n.card_payload)) if n.card_kind else text_message(n.text)
    except ValueError:
        ctx.logger.exception(f"[{n.rfq_id}] bad Follow-up card; sending the text only")
        msg = text_message(n.text)
    await ctx.send(n.user_address, msg)


agent.include(chat, publish_manifest=True)
agent.include(discovery)
agent.include(negotiation)
agent.include(onboarding)
agent.include(booker)
agent.include(followup)
agent.include(build_payment_proto(on_commit, on_reject), publish_manifest=True)

if __name__ == "__main__":
    observability.init("concierge")
    agent.run()
