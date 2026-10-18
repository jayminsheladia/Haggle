"""Follow-up: on_interval reminders and leftover food rescue.

Booker -> FollowupSchedule (a confirmed booking) -> Follow-up stores the event, then on its own schedule:
  - REMINDER_BEFORE_H before the start: a reminder (skipped if the booking was made later than that)
  - CHECKIN_AFTER_END_MIN after the assumed end: "How did it go?" form card with a rating and the leftover
    food check-in (build plan 3C)
Leftovers -> nearby open food recovery organizations that accept that food (carousel) -> the user picks one ->
pickup / drop-off email to the organization (team inbox during development) and an impact estimate.

The Follow-up can't post into the user's ASI:One chat itself: every message goes through the Concierge as a
UserNotice, and the user's card replies come back as UserReply. Matching, safety rules and the impact estimate
are code (tools/food_rescue.py), never the LLM. Idempotent on rfq_id.

Run from the repo root:  python -m agents.followup

Owner: Person D
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from integrations import photon
from integrations.email import EmailError, send_email
from integrations.email_templates import rescue_request
from shared import observability
from shared.cards import leftover_form, rescue_carousel
from shared.models import FollowupSchedule, UserNotice, UserReply
from tools import food_rescue

load_dotenv()

CONCIERGE_ADDRESS = os.getenv(
    "CONCIERGE_ADDRESS", "agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k"
)
BOOKER_ADDRESS = os.getenv("BOOKER_ADDRESS", "agent1qfq9dyy5q5t5dg7tzuhkuqztzqy9q806dw4qd8v6sg6znu87rmx8s3deu0f")
LA = ZoneInfo("America/Los_Angeles")

EVENT_HOURS = 3  # assumed event length: intake only asks for the start time
CHECKIN_AFTER_END_MIN = 15
REMINDER_BEFORE_H = 24
GIVE_UP_AFTER_H = 48  # stop waiting for an answer to the check-in
CHECK_PERIOD_S = 30
DEMO_DELAY_S = int(os.getenv("FOLLOWUP_DEMO_DELAY_S") or 0)  # > 0: check in this long after booking (demo only)

AMOUNT_LABELS = {"little": "A little", "tray": "About a tray", "trays": "Several trays"}
FOOD_LABELS = {"hot": "Hot prepared food", "cold": "Cold prepared food", "packaged": "Packaged food"}
SAFE_OPTIONS = ("Safe options: share it with attendees to take home now, or keep sealed packaged food for a "
                "campus pantry tomorrow.")

agent = Agent(
    name="haggle-followup",
    seed=os.getenv("FOLLOWUP_SEED") or "haggle-followup-dev-seed",
    port=int(os.getenv("FOLLOWUP_PORT", "8006")),
    mailbox=True,
    publish_agent_details=True,
    readme_path=str(Path(__file__).with_name("followup_README.md")),
    description="Haggle's follow-up agent: event reminders, a check-in after the event, and leftover food "
                "rescue to nearby food recovery organizations.",
)
proto = Protocol(name="HaggleFollowup", version="0.1.0")


@agent.on_event("startup")
async def startup(ctx: Context):
    n = len(food_rescue.load_orgs())
    ctx.logger.info(f"Follow-up address: {agent.address}")
    ctx.logger.info(f"{n} food rescue organizations loaded" + ("" if n else " (data/food_rescue_orgs.json is empty)"))


def _key(rfq_id: str) -> str:
    return f"event:{rfq_id}"


def event_start(ev: dict) -> datetime:
    return datetime.fromisoformat(f"{ev['event_date']}T{ev.get('event_time') or '12:00'}").replace(tzinfo=LA)


def _ampm(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


async def tell(ctx: Context, ev: dict, text: str, kind: str = "", payload: dict | None = None) -> None:
    await ctx.send(CONCIERGE_ADDRESS, UserNotice(
        rfq_id=ev["rfq_id"], user_address=ev["user_address"], text=text, card_kind=kind,
        card_payload=json.dumps(payload) if payload else "", trace=ev.get("trace", "")))


# ---- Scheduling ------------------------------------------------------------

@proto.on_message(FollowupSchedule)
async def on_schedule(ctx: Context, sender: str, m: FollowupSchedule):
    if sender != BOOKER_ADDRESS:
        ctx.logger.warning(f"Ignoring FollowupSchedule from unknown sender {sender}")
        return
    observability.tag(m.trace, m.rfq_id, m.vendor_id)
    if ctx.storage.get(_key(m.rfq_id)):
        ctx.logger.info(f"[{m.rfq_id}] already scheduled")
        return
    now = datetime.now(timezone.utc)
    ev = {**m.dict(), "stage": "scheduled", "reminded": False, "scheduled_at": now.isoformat()}
    start = event_start(ev)
    remind = start - timedelta(hours=REMINDER_BEFORE_H)
    ev["remind_at"] = remind.isoformat() if remind > now else ""  # booked inside the window: no reminder
    checkin = (now + timedelta(seconds=DEMO_DELAY_S) if DEMO_DELAY_S
               else start + timedelta(hours=EVENT_HOURS, minutes=CHECKIN_AFTER_END_MIN))
    ev["checkin_at"] = checkin.isoformat()
    ctx.storage.set(_key(m.rfq_id), ev)
    ctx.storage.set("events", sorted(set(ctx.storage.get("events") or []) | {m.rfq_id}))
    ctx.logger.info(f"[{m.rfq_id}] scheduled: reminder {ev['remind_at'] or 'none'}, check-in {ev['checkin_at']}")


@agent.on_interval(period=CHECK_PERIOD_S)
async def run_due(ctx: Context):
    now = datetime.now(timezone.utc)
    still = []
    for rfq_id in ctx.storage.get("events") or []:
        ev = ctx.storage.get(_key(rfq_id))
        if not ev or ev["stage"] in ("rescued", "closed"):
            continue
        still.append(rfq_id)
        try:
            await _run_event(ctx, ev, now)
        except Exception:
            ctx.logger.exception(f"[{rfq_id}] follow-up step failed; will retry")
    ctx.storage.set("events", still)


async def _run_event(ctx: Context, ev: dict, now: datetime) -> None:
    start = event_start(ev)
    if not ev["reminded"] and ev["remind_at"] and datetime.fromisoformat(ev["remind_at"]) <= now < start:
        ev["reminded"] = True  # set before sending: a reminder goes out at most once
        ctx.storage.set(_key(ev["rfq_id"]), ev)
        await send_reminder(ctx, ev, start)

    if ev["stage"] == "scheduled" and datetime.fromisoformat(ev["checkin_at"]) <= now:
        ev["stage"] = "checkin_sent"
        ctx.storage.set(_key(ev["rfq_id"]), ev)
        ctx.storage.set(f"user:{ev['user_address']}", ev["rfq_id"])  # replies narrated without the rfq_id
        await tell(ctx, ev, f"How did your event with {ev['vendor_name']} go? If there's leftover food, I can "
                            "find a nearby food recovery organization to take it.",
                   "form", leftover_form(ev["rfq_id"], ev["vendor_id"], ev["vendor_name"]))
        ctx.logger.info(f"[{ev['rfq_id']}] check-in sent")
    elif ev["stage"] in ("checkin_sent", "options_sent") and \
            now > datetime.fromisoformat(ev["checkin_at"]) + timedelta(hours=GIVE_UP_AFTER_H):
        ev["stage"] = "closed"
        ctx.storage.set(_key(ev["rfq_id"]), ev)
        ctx.logger.info(f"[{ev['rfq_id']}] no answer to the check-in; closed")


async def send_reminder(ctx: Context, ev: dict, start: datetime) -> None:
    today = datetime.now(LA).date()
    day = ("today" if start.date() == today else "tomorrow" if start.date() == today + timedelta(days=1)
           else f"on {start:%a %b} {start.day}")
    text = (f"Reminder: {ev['vendor_name']} is catering your event {day} at {_ampm(start)}, "
            f"{ev['headcount']} guests at {ev['location']}.")
    await tell(ctx, ev, text)
    phone = os.getenv("TEAM_PHONE", "")  # dev: texts go to the team phone only
    if phone and photon.configured():
        try:
            await photon.send_text(phone, f"Haggle: {text}")
        except Exception as e:
            ctx.logger.warning(f"[{ev['rfq_id']}] reminder text not sent: {e}")
    ctx.logger.info(f"[{ev['rfq_id']}] reminder sent")


# ---- Replies: leftovers -> options -> rescue -------------------------------

@proto.on_message(UserReply)
async def on_reply(ctx: Context, sender: str, r: UserReply):
    if sender != CONCIERGE_ADDRESS:
        ctx.logger.warning(f"Ignoring UserReply from unknown sender {sender}")
        return
    try:
        sel = json.loads(r.selection or "{}")
    except ValueError:
        sel = {}
    rfq_id = r.rfq_id or sel.get("rfq_id") or ctx.storage.get(f"user:{r.user_address}") or ""
    ev = ctx.storage.get(_key(rfq_id)) if rfq_id else None
    if not ev or ev["user_address"] != r.user_address:
        ctx.logger.warning(f"[{rfq_id}] reply from {r.user_address[:16]} matches no follow-up")
        return
    observability.tag(ev.get("trace", ""), rfq_id, ev["vendor_id"])
    action = sel.get("action")
    if action == "submit_leftovers":
        await on_leftovers(ctx, ev, sel)
    elif action == "pick_rescue":
        await on_pick(ctx, ev, sel, r.text)
    ctx.storage.set(_key(rfq_id), ev)


async def on_leftovers(ctx: Context, ev: dict, sel: dict) -> None:
    if ev["stage"] in ("rescued", "rescuing"):
        await tell(ctx, ev, "Your leftovers are already on their way to a food recovery organization. Thank you!")
        return
    rating = str(sel.get("rating", "")).strip()
    if rating in {"1", "2", "3", "4", "5"}:
        ev["rating"] = int(rating)
    thanks = "Thanks for the rating! " if "rating" in ev else "Thanks! "

    amount = sel.get("amount")
    if amount not in food_rescue.AMOUNT_LBS:
        await resend_form(ctx, ev, "Tell me how much food is left (or pick None).")
        return
    if amount == "none":
        ev["stage"] = "closed"
        await tell(ctx, ev, thanks + "Glad it went well. Nothing left to rescue, so we're all done.")
        return
    food, safe, handoff = sel.get("food_type"), sel.get("kept_safe"), sel.get("handoff")
    if food not in FOOD_LABELS or safe not in ("yes", "no", "unsure") or handoff not in ("pickup", "dropoff"):
        await resend_form(ctx, ev, "To find an organization I need the kind of food, whether it's been kept hot "
                                   "or refrigerated, and pickup or drop-off.")
        return
    if safe != "yes":  # food safety rail: never route food that may not have been kept at a safe temperature
        ev["stage"] = "closed"
        await tell(ctx, ev, thanks + "For safety I can't send food for donation unless it's been kept hot or "
                                     "refrigerated the whole time. " + SAFE_OPTIONS)
        return

    ev["leftovers"] = {"amount": amount, "food_type": food, "handoff": handoff,
                       "contact": str(sel.get("contact") or "").strip()[:200]}
    now = datetime.now(LA)
    lbs, meals = food_rescue.impact(amount)
    orgs = food_rescue.load_orgs()
    options = food_rescue.match(orgs, food, handoff, ev.get("lat"), ev.get("lng"), now, lbs)
    note = ""
    if not options and handoff == "pickup":
        options = food_rescue.match(orgs, food, "dropoff", ev.get("lat"), ev.get("lng"), now, lbs)
        if options:
            ev["leftovers"]["handoff"] = "dropoff"
            note = "Nobody open right now can pick up this amount, but these take drop-offs. "
    if not options:
        ev["stage"] = "closed"
        await tell(ctx, ev, thanks + f"I couldn't find a food recovery organization that's open now and takes "
                                     f"{FOOD_LABELS[food].lower()}. " + SAFE_OPTIONS)
        ctx.logger.info(f"[{ev['rfq_id']}] no rescue options for {food}/{handoff}")
        return
    ev["stage"], ev["options"] = "options_sent", options
    await tell(ctx, ev, thanks + note + f"{AMOUNT_LABELS[amount]} of {FOOD_LABELS[food].lower()} could be about "
                                        f"{meals} meals (an estimate). Pick where it should go.",
               "carousel", rescue_carousel(ev["rfq_id"], ev["vendor_id"],
                                           f"{len(options)} open nearby", options))
    ctx.logger.info(f"[{ev['rfq_id']}] {len(options)} rescue options sent")


async def resend_form(ctx: Context, ev: dict, why: str) -> None:
    await tell(ctx, ev, why, "form", leftover_form(ev["rfq_id"], ev["vendor_id"], ev["vendor_name"]))


async def on_pick(ctx: Context, ev: dict, sel: dict, text: str) -> None:
    if ev["stage"] == "rescued":
        await tell(ctx, ev, confirmation(ev))
        return
    if ev["stage"] != "options_sent":
        await tell(ctx, ev, "Tell me about the leftovers first, using the form above.")
        return
    options = ev["options"]
    org = next((o for o in options if o["org_id"] == sel.get("org_id")), None) or \
        next((o for o in options if o["name"].lower() in (text or "").lower()), None)
    if not org:
        await tell(ctx, ev, "Pick one of these organizations.", "carousel",
                   rescue_carousel(ev["rfq_id"], ev["vendor_id"], f"{len(options)} open nearby", options))
        return

    ev["stage"] = "rescuing"  # a double tap while the email is sending stops at the check above
    ctx.storage.set(_key(ev["rfq_id"]), ev)
    left = ev["leftovers"]
    lbs, meals = food_rescue.impact(left["amount"])
    subject, html, body = rescue_request(org, ev["location"], FOOD_LABELS[left["food_type"]],
                                         AMOUNT_LABELS[left["amount"]], left["handoff"], left["contact"],
                                         lbs, meals, ev["rfq_id"])
    try:
        with observability.span("email.send", "rescue request", ev.get("trace", ""), rfq_id=ev["rfq_id"]):
            await send_email(subject, html, body, idempotency_key=f"haggle-rescue-{ev['rfq_id']}-{org['org_id']}")
    except EmailError as e:
        ctx.logger.warning(f"[{ev['rfq_id']}] rescue email to {org['org_id']} not sent: {e}")
        ev["stage"] = "options_sent"  # let the user try again or pick another
        await tell(ctx, ev, f"I couldn't reach {org['name']} by email just now. Try again in a minute, or pick "
                            f"another organization. Their address: {org.get('address', '')}.")
        return

    ev["stage"] = "rescued"
    ev["impact"] = {"org_id": org["org_id"], "org_name": org["name"], "lbs_est": lbs, "meals_est": meals,
                    "handoff": left["handoff"], "address": org.get("address", ""), "status": "requested"}
    total = ctx.storage.get("impact_total") or {"events": 0, "lbs_est": 0, "meals_est": 0}
    total = {"events": total["events"] + 1, "lbs_est": total["lbs_est"] + lbs, "meals_est": total["meals_est"] + meals}
    ctx.storage.set("impact_total", total)
    ctx.logger.info(f"[{ev['rfq_id']}] rescue requested from {org['org_id']}: ~{lbs} lbs ≈ {meals} meals "
                    f"(all events: ~{total['meals_est']} meals)")
    await tell(ctx, ev, confirmation(ev))


def confirmation(ev: dict) -> str:
    i = ev["impact"]
    where = ("They'll contact you to arrange the pickup." if i["handoff"] == "pickup" else
             f"Drop it off at {i['address']}. Directions: {food_rescue.directions_url(i['address'])}")
    return (f"Sent! {i['org_name']} has your offer: ~{i['lbs_est']} lbs rescued ≈ {i['meals_est']} meals "
            f"(an estimate). {where} The organization makes the final call on what it accepts. Thank you!")


agent.include(proto, publish_manifest=True)

if __name__ == "__main__":
    observability.init("followup")
    agent.run()
