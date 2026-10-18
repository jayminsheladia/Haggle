"""Follow-up: reminders and leftover food rescue.

Concierge -> EventBooked (once the booking is confirmed) -> Follow-up schedules:
  - a reminder 24 h before the event (relayed to the organizer by the Concierge, plus a Photon text)
  - a check-in when the event ends (start + 2 h, + 15 min): the Concierge shows the leftovers form.
    Saying "event over" in the chat does the same at once (EventEnded) and skips the timed check-in.
Concierge -> LeftoverReport -> Follow-up matches food rescue organizations (data/food_rescue_orgs.json) in code:
  food type, food safety, pickup or drop-off, open now, distance from the venue -> top 3 -> RescueOptions.
Concierge -> RescueRequest -> Follow-up emails the organization (team inbox during development), texts the
  organizer -> RescueConfirmed with the address, directions and the estimated impact.

Everything is idempotent on rfq_id: one reminder, one check-in, one rescue email per event.

Run from the repo root:  python -m agents.followup

Owner: Person D
"""

import asyncio
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from integrations import photon
from integrations.email import EmailError, send_email
from integrations.email_templates import rescue_request
from shared import observability
from shared.models import (
    EventBooked, EventEnded, FollowupPrompt, LeftoverReport, RescueConfirmed, RescueOption, RescueOptions,
    RescueRequest,
)
from vendors.vendor_host import register_mailbox

load_dotenv()

CONCIERGE_ADDRESS = os.getenv(
    "CONCIERGE_ADDRESS", "agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k"
)
FOLLOWUP_PORT = int(os.getenv("FOLLOWUP_PORT", "8006"))
LA = ZoneInfo("America/Los_Angeles")
ORGS_PATH = Path(__file__).resolve().parent.parent / "data" / "food_rescue_orgs.json"
README_PATH = Path(__file__).with_name("followup_README.md")
DESCRIPTION = ("Haggle's follow-up agent: event reminders, a post-event check-in, and leftover food rescue "
               "with Los Angeles food banks and pantries.")

REMINDER_BEFORE = timedelta(hours=24)
EVENT_LENGTH = timedelta(hours=2)  # assumed: the request has a start time only
CHECKIN_AFTER_END = timedelta(minutes=15)
MAX_DISTANCE_MI = 30
TOP_N = 3
# Impact estimates (labeled as estimates everywhere): pounds per amount, ~1.2 lb per meal (Feeding America).
LBS = {"little": 6.0, "tray": 24.0, "several": 60.0}
LB_PER_MEAL = 1.2
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

UNSAFE_ADVICE = ("For safety, prepared food that wasn't kept hot (135°F or above) or cold (41°F or below), or sat "
                 "out more than 2 hours, can't be donated. Please compost it if you can. Any sealed, packaged "
                 "items can still be donated.")
NO_MATCH_ADVICE = {
    "prepared": "No rescue partner can take prepared food right now. Offer it to attendees in to-go boxes, and "
                "refrigerate anything left within 2 hours.",
    "packaged": "No pantry is open right now. Sealed food keeps: drop it at the campus pantry tomorrow "
                "(UCLA CPO Food Closet, Mon-Fri 9 AM-4 PM).",
    "produce": "No partner takes produce right now. Offer it to attendees, or drop it at a food bank tomorrow.",
}


def load_orgs() -> list[dict]:
    try:
        return json.loads(ORGS_PATH.read_text())
    except (OSError, ValueError):
        return []


# ---- Matching and estimates (code only, never the LLM) -------------------------

def category(food_type: str) -> str:
    return "prepared" if food_type in ("hot", "cold") else food_type


def miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def open_status(org: dict, now: datetime) -> str:
    """"open", "closed" or "unknown" (hours not published)."""
    hours = org.get("hours")
    if not hours:
        return "unknown"
    span = hours.get(DAYS[now.weekday()])
    if not span:
        return "closed"
    start, end = span.split("-")
    return "open" if start <= now.strftime("%H:%M") < end else "closed"


def directions_url(address: str) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={quote_plus(address)}"


def estimate(amount: str) -> tuple[float, int]:
    lbs = LBS.get(amount, 0.0)
    return lbs, round(lbs / LB_PER_MEAL)


def match_orgs(orgs: list[dict], r: LeftoverReport, lat: float | None, lng: float | None,
               now: datetime) -> tuple[list[RescueOption], str]:
    """(top options nearest first, advice when there are none)."""
    cat = category(r.food_type)
    if r.amount == "none" or not r.amount:
        return [], "No leftovers, nothing to rescue. Thanks for planning well!"
    if cat == "prepared" and r.kept_safe != "yes":
        return [], UNSAFE_ADVICE
    fits = [o for o in orgs if cat in o.get("accepts", [])]
    wanted = [o for o in fits if o.get("pickup" if r.handoff == "pickup" else "dropoff")]
    fits = wanted or fits  # nobody offers that handoff: show the others rather than nothing

    scored = []
    for o in fits:
        dist = miles(lat, lng, o["lat"], o["lng"]) if lat is not None and lng is not None else 0.0
        if dist > MAX_DISTANCE_MI:
            continue
        status = open_status(o, now)
        if status == "closed" and cat == "prepared":
            continue  # prepared food can't wait for tomorrow's opening hours
        scored.append(({"open": 0, "unknown": 1, "closed": 2}[status], dist, o, status))
    scored.sort(key=lambda t: t[:2])
    options = [RescueOption(
        org_id=o["org_id"], name=o["name"], address=o["address"], distance_mi=round(dist, 1),
        accepts=o.get("accepts", []), pickup=bool(o.get("pickup")),
        open_status={"open": "Open now", "closed": "Closed now", "unknown": "Hours not published · call first"}[status],
        hours_text=o.get("hours_text", ""), phone=o.get("phone", ""), directions_url=directions_url(o["address"]),
        source=o.get("source", ""),
    ) for _, dist, o, status in scored[:TOP_N]]
    return options, "" if options else NO_MATCH_ADVICE.get(cat, NO_MATCH_ADVICE["prepared"])


# ---- Agent ----------------------------------------------------------------------

proto = Protocol(name="HaggleFollowup", version="0.1.0")


def _events(ctx: Context) -> dict:
    return ctx.storage.get("events") or {}


def _save(ctx: Context, events: dict) -> None:
    ctx.storage.set("events", events)


def _start(e: dict) -> datetime:
    return datetime.fromisoformat(f"{e['event_date']}T{e.get('event_time') or '12:00'}").replace(tzinfo=LA)


async def _text(ctx: Context, e: dict, text: str, key: str) -> None:
    phone = os.getenv("TEAM_PHONE", "")  # dev: the organizer's texts go to the team phone only
    if not phone or not photon.configured():
        return
    try:
        with observability.span("photon.send", key, e.get("trace", ""), rfq_id=e["rfq_id"]):
            await photon.send_text(phone, text, idempotency_key=f"{key}-{e['rfq_id']}")
    except Exception as ex:
        ctx.logger.warning(f"[{e['rfq_id']}] Photon text not sent: {ex}")


async def checkin(ctx: Context, e: dict) -> None:
    e["checked_in"] = True
    await ctx.send(CONCIERGE_ADDRESS, FollowupPrompt(
        rfq_id=e["rfq_id"], kind="checkin", trace=e.get("trace", ""),
        text=f"How did it go with {e['vendor_name']}? Any leftovers? I can find a food bank or pantry nearby "
             "to rescue them."))
    ctx.logger.info(f"[{e['rfq_id']}] post-event check-in sent")


@proto.on_message(EventBooked)
async def on_event_booked(ctx: Context, sender: str, msg: EventBooked):
    if sender != CONCIERGE_ADDRESS:
        ctx.logger.warning(f"Ignoring EventBooked from unknown sender {sender}")
        return
    events = _events(ctx)
    if msg.rfq_id in events:  # replay: keep the schedule and what was already sent
        return
    e = json.loads(msg.json()) | {"booked_at": datetime.now(LA).isoformat()}
    events[msg.rfq_id] = e
    _save(ctx, events)
    start = _start(e)
    ctx.logger.info(f"[{msg.rfq_id}] scheduled: reminder {start - REMINDER_BEFORE:%a %b %d %H:%M}, "
                    f"check-in {start + EVENT_LENGTH + CHECKIN_AFTER_END:%a %b %d %H:%M} (LA)")


@proto.on_message(EventEnded)
async def on_event_ended(ctx: Context, sender: str, msg: EventEnded):
    if sender != CONCIERGE_ADDRESS:
        return
    events = _events(ctx)
    e = events.get(msg.rfq_id)
    if not e:
        ctx.logger.warning(f"[{msg.rfq_id}] EventEnded for an unknown event")
        return
    e["ended"] = e["checked_in"] = True  # the Concierge already shows the leftovers form
    _save(ctx, events)
    ctx.logger.info(f"[{msg.rfq_id}] organizer says the event is over")


@proto.on_interval(period=60)
async def due(ctx: Context):
    """Send reminders and check-ins that are due. Each is sent once per event."""
    now = datetime.now(LA)
    events = _events(ctx)
    for e in events.values():
        start = _start(e)
        booked = datetime.fromisoformat(e["booked_at"])
        if not e.get("reminded") and start - REMINDER_BEFORE <= now < start:
            e["reminded"] = True
            if booked > start - REMINDER_BEFORE:
                continue  # booked within a day of the event: the confirmation is the reminder
            text = (f"Reminder: {e['vendor_name']} caters your event tomorrow, {start:%a %b} {start.day} at "
                    f"{start:%I:%M %p}".replace(" 0", " ") + f", {e['headcount']} guests, at {e['location']}.")
            await ctx.send(CONCIERGE_ADDRESS, FollowupPrompt(rfq_id=e["rfq_id"], kind="reminder", text=text,
                                                             trace=e.get("trace", "")))
            await _text(ctx, e, f"Haggle: {text}", "reminder")
            ctx.logger.info(f"[{e['rfq_id']}] reminder sent")
        if not e.get("checked_in") and now >= start + EVENT_LENGTH + CHECKIN_AFTER_END:
            await checkin(ctx, e)
            await _text(ctx, e, f"Haggle: how did the event go? Any leftovers from {e['vendor_name']}? Reply in "
                                "ASI:One (@haggle) and I'll find a food bank nearby.", "checkin")
    _save(ctx, events)


@proto.on_message(LeftoverReport, replies=RescueOptions)
async def on_leftovers(ctx: Context, sender: str, r: LeftoverReport):
    if sender != CONCIERGE_ADDRESS:
        return
    events = _events(ctx)
    e = events.get(r.rfq_id)
    if not e:
        await ctx.send(sender, RescueOptions(rfq_id=r.rfq_id, trace=r.trace,
                                             advice="I don't have this booking on file, so I can't match it yet."))
        return
    with observability.span("followup.match", r.rfq_id, r.trace, rfq_id=r.rfq_id):
        options, advice = match_orgs(load_orgs(), r, e.get("lat"), e.get("lng"), datetime.now(LA))
    lbs, meals = estimate(r.amount) if options else (0.0, 0)
    e["report"], e["ended"], e["checked_in"] = json.loads(r.json()), True, True
    e["options"] = [json.loads(o.json()) for o in options]
    _save(ctx, events)
    ctx.logger.info(f"[{r.rfq_id}] leftovers {r.amount}/{r.food_type}/safe={r.kept_safe}/{r.handoff}, "
                    f"rating {r.rating or '-'}: {len(options)} matches" + (f" ({advice[:60]})" if advice else ""))
    await ctx.send(sender, RescueOptions(rfq_id=r.rfq_id, options=options, advice=advice, est_lbs=lbs,
                                         est_meals=meals, trace=r.trace))


@proto.on_message(RescueRequest, replies=RescueConfirmed)
async def on_rescue(ctx: Context, sender: str, req: RescueRequest):
    if sender != CONCIERGE_ADDRESS:
        return
    events = _events(ctx)
    e = events.get(req.rfq_id) or {}
    if e.get("rescue"):  # replay: one rescue per event, same answer
        await ctx.send(sender, RescueConfirmed.parse_raw(e["rescue"]))
        return
    org = next((o for o in e.get("options", []) if o["org_id"] == req.org_id), None)
    if not org or not e.get("report"):
        await ctx.send(sender, RescueConfirmed(rfq_id=req.rfq_id, org_id=req.org_id, trace=req.trace,
                                               detail="That option isn't available any more."))
        return
    r = LeftoverReport.parse_obj(e["report"])
    lbs, meals = estimate(r.amount)
    subject, html, text = rescue_request(org["name"], r, e.get("location", ""), e["vendor_name"], lbs, meals)
    email_sent = False
    try:  # dev: the team inbox stands in for the organization
        with observability.span("email.send", "rescue request", req.trace, rfq_id=req.rfq_id):
            await send_email(subject, html, text, idempotency_key=f"haggle-rescue-{req.rfq_id}")
        email_sent = True
    except EmailError as ex:
        ctx.logger.warning(f"[{req.rfq_id}] rescue email not sent: {ex}")
    how = "They'll arrange a pickup" if r.handoff == "pickup" and org["pickup"] else "Drop it off"
    await _text(ctx, e, f"Haggle food rescue: {how} with {org['name']}, {org['address']}. "
                        f"{org['phone'] or org['hours_text']}. Directions: {org['directions_url']}", "rescue")
    result = RescueConfirmed(
        rfq_id=req.rfq_id, org_id=org["org_id"], org_name=org["name"], address=org["address"],
        directions_url=org["directions_url"], phone=org["phone"], email_sent=email_sent, est_lbs=lbs,
        est_meals=meals, handoff="pickup" if r.handoff == "pickup" and org["pickup"] else "dropoff",
        detail=org["hours_text"], trace=req.trace)
    e["rescue"] = result.json()
    _save(ctx, events)
    ctx.logger.info(f"[{req.rfq_id}] rescue with {org['org_id']}: ~{lbs:.0f} lb, ~{meals} meals, "
                    f"email {'sent' if email_sent else 'NOT sent'}")
    await ctx.send(sender, result)


def build_agent(mailbox: bool) -> Agent:
    agent = Agent(
        name="haggle-followup",
        seed=os.environ["FOLLOWUP_SEED"],
        port=FOLLOWUP_PORT,
        publish_agent_details=True,
        readme_path=str(README_PATH),
        description=DESCRIPTION,
        **({"mailbox": True} if mailbox else {"endpoint": [f"http://127.0.0.1:{FOLLOWUP_PORT}/submit"]}),
    )

    @agent.on_event("startup")
    async def on_startup(ctx: Context):
        ctx.logger.info(f"Follow-up address: {agent.address} ({'mailbox' if mailbox else 'local endpoint'})")

    agent.include(proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    observability.init("followup")
    has_mailbox = asyncio.run(register_mailbox(os.environ["FOLLOWUP_SEED"], "haggle-followup", DESCRIPTION,
                                               README_PATH.read_text(), [proto.digest]))
    asyncio.set_event_loop(asyncio.new_event_loop())  # asyncio.run closed the previous one
    build_agent(has_mailbox).run()
