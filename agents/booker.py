"""Booker: Stripe verification, vendor confirmation, email, calendar invite, Photon texts.

Concierge -> BookingConfirmed (after the deposit's CommitPayment) -> Booker:
  1. verifies with Stripe that the Checkout Session is paid, for this rfq_id and this amount
  2. sends Accept to the vendor agent (refunds the deposit if the vendor can't take it)
  3. emails the confirmation with a calendar invite (.ics) and the order to the caterer (Resend; team inbox
     during development), and texts the organizer through Photon when it is configured
  -> BookingResult back to the Concierge.
Concierge -> QuoteRequest (an unclaimed caterer) -> Booker emails the caterer for a confirmed quote with a link
to claim its Haggle agent -> QuoteRequestSent.

Idempotent on rfq_id: a replayed BookingConfirmed never re-charges, re-books or re-emails; it gets the
stored result. A QuoteRequest is sent once per rfq_id + vendor_id.

Run from the repo root:  python -m agents.booker

Owner: Person D
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from integrations import photon
from integrations.calendar_invite import as_attachment, booking_invite
from integrations.email import EmailError, send_email
from integrations.email_templates import booking_confirmation, quote_request, vendor_order
from integrations.stripe_client import refund_deposit, verify_deposit
from shared import observability
from shared.models import (
    Accept, BookingConfirmed, BookingResult, Decline, FollowupSchedule, Quote, QuoteRequest, QuoteRequestSent,
)

load_dotenv()

CONCIERGE_ADDRESS = os.getenv(
    "CONCIERGE_ADDRESS", "agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k"
)
FOLLOWUP_ADDRESS = os.getenv(
    "FOLLOWUP_ADDRESS", "agent1qf5hzpsg0xxmmm5dkjvkpvfget7n7es0vyyc47sk359g7c2u82a8yl4m2xa"
)
VENDOR_TIMEOUT_S = 10
LA =ZoneInfo("America/Los_Angeles")
QUOTE_REPLY_HOURS = 48  # how long a caterer gets to answer a quote request (capped at 2 days before the event)

agent = Agent(
    name="haggle-booker",
    seed=os.getenv("BOOKER_SEED") or "haggle-booker-dev-seed",
    port=int(os.getenv("BOOKER_PORT", "8005")),
    mailbox=True,
    publish_agent_details=True,
    readme_path=str(Path(__file__).with_name("booker_README.md")),
    description="Haggle's booking agent: verifies the Stripe deposit, confirms with the caterer and sends "
                "the booking confirmation.",
)
proto = Protocol(name="HaggleBooker", version="0.1.0")


@agent.on_event("startup")
async def startup(ctx: Context):
    ctx.logger.info(f"Booker address: {agent.address}")


def _result(b: BookingConfirmed, status: str, detail: str = "", email_sent: bool = False) -> BookingResult:
    return BookingResult(rfq_id=b.rfq_id, vendor_id=b.vendor_id, status=status, detail=detail,
                         email_sent=email_sent, trace=b.trace)


async def confirm_with_vendor(ctx: Context, b: BookingConfirmed) -> tuple[bool, str]:
    """(ok, detail). ok=False only on an explicit Decline; no reply keeps the booking (the vendor agent
    is idempotent on rfq_id, so the Accept can be resent)."""
    if not b.vendor_address:
        return True, "no vendor agent address"
    accept = Accept(rfq_id=b.rfq_id, vendor_id=b.vendor_id, final_usd=b.total_usd, version=b.version,
                    deposit_txn=b.checkout_session_id, trace=b.trace)
    with observability.span("vendor.accept", b.vendor_id, b.trace, rfq_id=b.rfq_id):
        reply, status = await ctx.send_and_receive(b.vendor_address, accept, response_type={Quote, Decline},
                                                   timeout=VENDOR_TIMEOUT_S)
    if isinstance(reply, Decline):
        return False, reply.reason
    if isinstance(reply, Quote):
        return True, reply.note or "caterer confirmed"
    ctx.logger.warning(f"[{b.rfq_id}] no reply from vendor {b.vendor_id} in {VENDOR_TIMEOUT_S}s ({status})")
    return True, "caterer hasn't acknowledged yet"


@proto.on_message(BookingConfirmed, replies=BookingResult)
async def on_booking(ctx: Context, sender: str, b: BookingConfirmed):
    if sender != CONCIERGE_ADDRESS:
        ctx.logger.warning(f"Ignoring BookingConfirmed from unknown sender {sender}")
        return
    observability.tag(b.trace, b.rfq_id, b.vendor_id)
    key = f"booking:{b.rfq_id}"
    stored = ctx.storage.get(key)
    if stored and stored.get("status") == "in_progress":
        ctx.logger.info(f"[{b.rfq_id}] already being booked; ignoring the duplicate")
        return
    if stored:  # replay: same answer, nothing re-done
        ctx.logger.info(f"[{b.rfq_id}] replay -> stored {stored['status']}")
        await ctx.send(sender, BookingResult.parse_raw(stored["result"]))
        return

    ctx.storage.set(key, {"status": "in_progress"})  # before any await: a concurrent duplicate stops above

    # 1. Payment: Stripe is the source of truth, never the message.
    try:
        with observability.span("stripe.verify", b.checkout_session_id, b.trace, rfq_id=b.rfq_id):
            paid, reason = await asyncio.to_thread(verify_deposit, b.checkout_session_id, b.rfq_id, b.deposit_usd)
    except Exception as e:
        ctx.logger.exception(f"[{b.rfq_id}] Stripe verification failed")
        ctx.storage.remove(key)  # nothing done yet: a retry may try again
        await ctx.send(sender, _result(b, "error", f"couldn't reach Stripe: {e}"))
        return
    if not paid:
        ctx.logger.info(f"[{b.rfq_id}] payment not verified: {reason}")
        ctx.storage.remove(key)  # not final: the user can still finish paying
        await ctx.send(sender, _result(b, "payment_failed", reason))
        return

    # 2. Vendor confirmation; refund if the vendor can't take it.
    ok, vendor_detail = await confirm_with_vendor(ctx, b)
    if not ok:
        try:
            refund_id = await asyncio.to_thread(refund_deposit, b.checkout_session_id, b.rfq_id)
            detail = f"{b.vendor_name} couldn't take the booking ({vendor_detail}); deposit refunded ({refund_id})"
        except Exception as e:
            ctx.logger.exception(f"[{b.rfq_id}] refund failed")
            detail = f"{b.vendor_name} couldn't take the booking ({vendor_detail}); refund failed, team notified: {e}"
        result = _result(b, "vendor_declined", detail)
        ctx.storage.set(key, {"status": result.status, "result": result.json()})
        ctx.logger.info(f"[{b.rfq_id}] {detail}")
        await ctx.send(sender, result)
        return

    # 3. Confirmation + calendar invite, order to the caterer, text (a failed message never undoes a paid booking).
    email_sent = await send_booking_messages(ctx, b)

    result = _result(b, "confirmed", vendor_detail, email_sent)
    ctx.storage.set(key, {"status": result.status, "result": result.json()})
    ctx.logger.info(f"[{b.rfq_id}] booked {b.vendor_id} ${b.total_usd:.2f} (deposit ${b.deposit_usd:.2f}, "
                    f"email {'sent' if email_sent else 'NOT sent'}; {vendor_detail})")
    await ctx.send(sender, result)

    # 4. Reminders and the after-event check-in (the Follow-up is idempotent on rfq_id).
    if b.user_address and b.event_date:
        await ctx.send(FOLLOWUP_ADDRESS, FollowupSchedule(
            rfq_id=b.rfq_id, user_address=b.user_address, vendor_id=b.vendor_id, vendor_name=b.vendor_name,
            event_date=b.event_date, event_time=b.event_time, location=b.location, lat=b.lat, lng=b.lng,
            headcount=b.headcount, trace=b.trace))


async def send_booking_messages(ctx: Context, b: BookingConfirmed) -> bool:
    """Confirmation email with a .ics invite, the order email to the caterer, and a Photon text. Returns whether
    the confirmation email went out. Every channel fails independently and never blocks the booking."""
    subject, html, text = booking_confirmation(b)
    invite = booking_invite(b.rfq_id, f"Catering: {b.vendor_name}", f"{b.headcount} guests. {text}", b.location,
                            b.event_date, b.event_time)
    email_sent = False
    try:
        with observability.span("email.send", "confirmation", b.trace, rfq_id=b.rfq_id):
            await send_email(subject, html, text, idempotency_key=f"haggle-booking-{b.rfq_id}",
                             attachments=[as_attachment(invite)])
        email_sent = True
    except EmailError as e:
        ctx.logger.warning(f"[{b.rfq_id}] confirmation email not sent: {e}")

    subject, html, text = vendor_order(b)
    try:
        with observability.span("email.send", "vendor order", b.trace, rfq_id=b.rfq_id):
            await send_email(subject, html, text, idempotency_key=f"haggle-order-{b.rfq_id}",
                             to=os.getenv("VENDOR_TEST_EMAIL", ""))  # dev: stands in for the caterer
    except EmailError as e:
        ctx.logger.warning(f"[{b.rfq_id}] order email to the caterer not sent: {e}")

    phone = os.getenv("TEAM_PHONE", "")  # dev: the organizer's text goes to the team phone only
    if phone and photon.configured():
        try:
            with observability.span("photon.send", "booking text", b.trace, rfq_id=b.rfq_id):
                await photon.send_text(phone, f"Haggle: you're booked with {b.vendor_name} on {b.event_date} "
                                              f"{b.event_time} for {b.headcount} guests. Deposit paid: ${b.deposit_usd:,.2f}.")
        except Exception as e:
            ctx.logger.warning(f"[{b.rfq_id}] Photon text not sent: {e}")
    return email_sent


def _reply_by(event_date: str) -> datetime:
    """5 PM about QUOTE_REPLY_HOURS from now, but never later than 5 PM two days before the event."""
    now = datetime.now(LA)
    latest = (datetime.fromisoformat(event_date).replace(tzinfo=LA) - timedelta(days=2)).replace(hour=17, minute=0)
    wanted = (now + timedelta(hours=QUOTE_REPLY_HOURS)).replace(hour=17, minute=0, second=0, microsecond=0)
    return max(now + timedelta(hours=2), min(wanted, latest))


@proto.on_message(QuoteRequest, replies=QuoteRequestSent)
async def on_quote_request(ctx: Context, sender: str, q: QuoteRequest):
    if sender != CONCIERGE_ADDRESS:
        ctx.logger.warning(f"Ignoring QuoteRequest from unknown sender {sender}")
        return
    observability.tag(q.trace, q.rfq_id, q.vendor_id)
    key = f"quote-request:{q.rfq_id}:{q.vendor_id}"
    stored = ctx.storage.get(key)
    if stored:  # sent already: same answer, no second email
        await ctx.send(sender, QuoteRequestSent.parse_raw(stored))
        return
    reply_by = _reply_by(q.event_date)
    reply_by_text = f"{reply_by:%a %b} {reply_by.day}, {reply_by:%I:%M %p}".replace(" 0", " ")
    claim_url = f"https://agentverse.ai/agents/details/{q.vendor_address}/profile" if q.vendor_address else "https://asi1.ai"
    subject, html, text = quote_request(q, claim_url, reply_by_text)
    to = os.getenv("VENDOR_TEST_EMAIL") or os.getenv("TEAM_INBOX_EMAIL", "")  # dev: stands in for the business
    sent, detail = False, ""
    try:
        with observability.span("email.send", "quote request", q.trace, rfq_id=q.rfq_id):
            await send_email(subject, html, text, idempotency_key=f"haggle-quote-{q.rfq_id}-{q.vendor_id}", to=to)
        sent, detail = True, f"emailed ({to} stands in for the business during development)"
    except EmailError as e:
        detail = f"email not sent: {e}"
        ctx.logger.warning(f"[{q.rfq_id}] quote request to {q.vendor_id} not sent: {e}")
    result = QuoteRequestSent(rfq_id=q.rfq_id, vendor_id=q.vendor_id, email_to=to, sent=sent, detail=detail,
                              sent_at=datetime.now(timezone.utc).isoformat(), reply_by=reply_by_text, trace=q.trace)
    if sent:
        ctx.storage.set(key, result.json())
    ctx.logger.info(f"[{q.rfq_id}] quote request to {q.vendor_id}: {detail}")
    await ctx.send(sender, result)


agent.include(proto, publish_manifest=True)

if __name__ == "__main__":
    observability.init("booker")
    agent.run()
