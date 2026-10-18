"""Concierge @haggle: Chat Protocol, intake checklist, cards, session state, Payment Protocol (seller).

Run from the repo root:  python -m agents.concierge

Owner: Person A
"""

import os
import re
from datetime import date, datetime, timezone
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

from shared.cards import DIETS, card_message, event_form, text_message
from shared.llm import chat_json
from shared.selection import parse_selection

load_dotenv()

LA = ZoneInfo("America/Los_Angeles")

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
    return {"stage": "new", "rfq_id": f"rfq_{uuid4().hex[:10]}", "checklist": checklist, "asked": []}


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
            data = await chat_json(system + (STRICT_SUFFIX if attempt else ""), text)
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
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    if not text:
        return

    s = ctx.storage.get(sender) or new_session()
    if text.lower().strip(" .!") in RESET_WORDS:
        s = new_session()
        ctx.storage.set(sender, s)
        await ctx.send(sender, text_message("Starting a new request. Tell me about your event."))
        return

    checklist = s["checklist"]
    sel = parse_selection(text)
    if sel.get("action") == "submit_event":
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
    else:
        # TODO(Phase 2): extras card, then the confirmation review card.
        s["stage"] = "confirming"
        s["asked"] = []
        await ctx.send(
            sender,
            text_message(f"Here's your request: {summary(checklist)}. "
                         "No caterers are contacted until you confirm."),
        )
    ctx.storage.set(sender, s)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(chat, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
