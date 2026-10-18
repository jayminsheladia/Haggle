"""Fetch.ai Payment Protocol, seller role, for the Concierge's booking deposit (Stripe embedded Checkout).

Flow: Concierge -> RequestPayment (metadata["stripe"] = embedded checkout) -> ASI:One shows the checkout
-> user pays -> CommitPayment (transaction_id = Checkout Session id) -> Concierge forwards BookingConfirmed
to the Booker, which verifies with Stripe -> Concierge sends CompletePayment (or CancelPayment).

Usage in an agent:
    agent.include(build_payment_proto(on_commit, on_reject), publish_manifest=True)

Owner: Person D
"""

from typing import Awaitable, Callable

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

DEADLINE_S = 900  # how long ASI:One keeps the payment request open

CommitHandler = Callable[[Context, str, CommitPayment], Awaitable[None]]
RejectHandler = Callable[[Context, str, RejectPayment], Awaitable[None]]


def build_payment_proto(on_commit: CommitHandler, on_reject: RejectHandler) -> Protocol:
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def _on_commit(ctx: Context, sender: str, msg: CommitPayment):
        await on_commit(ctx, sender, msg)

    @proto.on_message(RejectPayment)
    async def _on_reject(ctx: Context, sender: str, msg: RejectPayment):
        await on_reject(ctx, sender, msg)

    return proto


def deposit_request(ctx: Context, rfq_id: str, amount_usd: float, checkout: dict, description: str) -> RequestPayment:
    """RequestPayment for a deposit; `checkout` comes from stripe_client.create_deposit_checkout()."""
    return RequestPayment(
        accepted_funds=[Funds(currency="USD", amount=f"{amount_usd:.2f}", payment_method="stripe")],
        recipient=str(ctx.agent.address),
        deadline_seconds=DEADLINE_S,
        reference=rfq_id,
        description=description,
        metadata={"stripe": checkout, "service": "haggle_deposit", "rfq_id": rfq_id},
    )
