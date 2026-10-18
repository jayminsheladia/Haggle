"""Booker: Stripe verification, vendor confirmation, email, calendar invite, Photon texts.

Concierge -> BookingConfirmed (after the deposit's CommitPayment) -> Booker:
  1. verifies with Stripe that the Checkout Session is paid, for this rfq_id and this amount
  2. sends Accept to the vendor agent (refunds the deposit if the vendor can't take it)
  3. emails the confirmation (Resend, team inbox during development)
  -> BookingResult back to the Concierge.

Idempotent on rfq_id: a replayed BookingConfirmed never re-charges, re-books or re-emails; it gets the
stored result. Calendar invite and Photon texts: not built yet.

Run from the repo root:  python -m agents.booker

Owner: Person D
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from integrations.email import EmailError, send_email
from integrations.email_templates import booking_confirmation
from integrations.stripe_client import refund_deposit, verify_deposit
from shared import observability
from shared.models import Accept, BookingConfirmed, BookingResult, Decline, Quote

load_dotenv()

CONCIERGE_ADDRESS = os.getenv(
    "CONCIERGE_ADDRESS", "agent1qtczn7424hmp43jqetnl9pydsmqhcfzfg8muzyl5ajxwyep0mt7lxxewe9k"
)
VENDOR_TIMEOUT_S = 10

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

    # 3. Confirmation email (a failed email never undoes a paid booking).
    subject, html, text = booking_confirmation(b)
    email_sent = False
    try:
        with observability.span("email.send", "resend", b.trace, rfq_id=b.rfq_id):
            await send_email(subject, html, text, idempotency_key=f"haggle-booking-{b.rfq_id}")
        email_sent = True
    except EmailError as e:
        ctx.logger.warning(f"[{b.rfq_id}] confirmation email not sent: {e}")

    result = _result(b, "confirmed", vendor_detail, email_sent)
    ctx.storage.set(key, {"status": result.status, "result": result.json()})
    ctx.logger.info(f"[{b.rfq_id}] booked {b.vendor_id} ${b.total_usd:.2f} (deposit ${b.deposit_usd:.2f}, "
                    f"email {'sent' if email_sent else 'NOT sent'}; {vendor_detail})")
    await ctx.send(sender, result)


agent.include(proto, publish_manifest=True)

if __name__ == "__main__":
    observability.init("booker")
    agent.run()
