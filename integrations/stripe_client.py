"""Create + verify Stripe Checkout Sessions.

Adapted from Fetch's stripe-horoscope-agent: an embedded Checkout Session whose details go into the
Payment Protocol's RequestPayment metadata["stripe"]; ASI:One renders the checkout in the chat and
CommitPayment comes back with the session id as transaction_id.

The amount is always computed in code by the caller; this module never decides a price. The secret
key never leaves this module (only the publishable key goes into metadata).

Owner: Person D
"""

import os
import time

import stripe

CURRENCY = "usd"
# Stripe requires expires_at at least 30 min out; clamp to [30 min, 24 h].
EXPIRES_S = max(1800, min(24 * 3600, int(os.getenv("STRIPE_CHECKOUT_EXPIRES_SECONDS", "1800"))))


class StripeNotConfigured(RuntimeError):
    pass


def _stripe():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set")
    if not key.startswith(("sk_test_", "rk_test_")) and os.getenv("STRIPE_ALLOW_LIVE") != "1":
        raise StripeNotConfigured("refusing a live Stripe key (set STRIPE_ALLOW_LIVE=1 to allow)")
    stripe.api_key = key
    return stripe


def _meta(obj) -> dict:
    """Stripe metadata as a plain dict (stripe-python returns a StripeObject, not a dict)."""
    meta = getattr(obj, "metadata", None)
    return meta.to_dict() if hasattr(meta, "to_dict") else dict(meta or {})


def to_cents(amount_usd: float) -> int:
    return int(round(amount_usd * 100))


def create_deposit_checkout(*, rfq_id: str, vendor_id: str, vendor_name: str, amount_usd: float,
                            user_address: str, chat_session_id: str = "", description: str = "") -> dict:
    """Create an embedded Checkout Session for a booking deposit; returns the RequestPayment metadata["stripe"]."""
    s = _stripe()
    cents = to_cents(amount_usd)
    if cents < 50:  # Stripe's minimum charge in USD
        raise ValueError(f"deposit ${amount_usd:.2f} is below Stripe's $0.50 minimum")
    return_url = (os.getenv("STRIPE_SUCCESS_URL", "https://asi1.ai")
                  + f"?session_id={{CHECKOUT_SESSION_ID}}&rfq_id={rfq_id}")
    session = s.checkout.Session.create(
        ui_mode="embedded_page",  # Stripe's current name for "embedded"; metadata below keeps ASI:One's "embedded"
        redirect_on_completion="if_required",  # payment methods come from the Stripe Dashboard settings
        mode="payment",
        return_url=return_url,
        expires_at=int(time.time()) + EXPIRES_S,
        client_reference_id=rfq_id,
        line_items=[{
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": f"Haggle deposit · {vendor_name}",
                                 "description": description or f"Catering deposit for {rfq_id}"},
                "unit_amount": cents,
            },
            "quantity": 1,
        }],
        metadata={"rfq_id": rfq_id, "vendor_id": vendor_id, "user_address": user_address,
                  "session_id": chat_session_id, "service": "haggle_deposit"},
    )
    return {
        "client_secret": session.client_secret,
        "id": session.id,  # some UIs read `id`, others `checkout_session_id`
        "checkout_session_id": session.id,
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        "currency": CURRENCY,
        "amount_cents": cents,
        "ui_mode": "embedded",
    }


def mock_payments() -> bool:
    """STRIPE_MOCK_PAYMENTS=1: no checkout UI; the deposit is a real Stripe *test-mode* charge on Stripe's test card."""
    return os.getenv("STRIPE_MOCK_PAYMENTS") == "1"


def charge_test_deposit(*, rfq_id: str, vendor_id: str, vendor_name: str, amount_usd: float, user_address: str,
                        description: str = "", attempt: int = 1) -> str:
    """Mock checkout: charge Stripe's test card (pm_card_visa) in test mode. Returns the PaymentIntent id.
    Idempotent on rfq_id + vendor + amount + attempt, so a repeated tap never charges twice; `attempt` goes up
    only after a refunded booking, so booking again takes a new deposit instead of reusing the refunded one."""
    s = _stripe()  # refuses live keys
    cents = to_cents(amount_usd)
    if cents < 50:
        raise ValueError(f"deposit ${amount_usd:.2f} is below Stripe's $0.50 minimum")
    intent = s.PaymentIntent.create(
        amount=cents, currency=CURRENCY, payment_method="pm_card_visa", confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        description=description or f"Haggle deposit · {vendor_name}",
        metadata={"rfq_id": rfq_id, "vendor_id": vendor_id, "user_address": user_address,
                  "service": "haggle_deposit", "mock_checkout": "1"},
        idempotency_key=f"haggle-deposit-{rfq_id}-{vendor_id}-{cents}-{attempt}",
    )
    return intent.id


def session_status(checkout_session_id: str) -> str:
    """open (can still be paid: reuse it) | complete (paid: never create another) | expired."""
    return _stripe().checkout.Session.retrieve(checkout_session_id).status


def verify_deposit(checkout_session_id: str, rfq_id: str, amount_usd: float) -> tuple[bool, str]:
    """(paid, reason). Paid only if Stripe says so AND the session is for this rfq_id and amount.
    Accepts a Checkout Session id (cs_...) or, for mock payments, a PaymentIntent id (pi_...)."""
    if checkout_session_id.startswith("pi_"):
        intent = _stripe().PaymentIntent.retrieve(checkout_session_id)
        if intent.status != "succeeded":
            return False, "payment not completed"
        if _meta(intent).get("rfq_id") != rfq_id:
            return False, "payment belongs to another request"
        if intent.amount != to_cents(amount_usd):
            return False, "amount paid does not match the deposit"
        return True, "paid"
    session = _stripe().checkout.Session.retrieve(checkout_session_id)
    if getattr(session, "payment_status", None) != "paid":
        return False, "payment not completed"
    if _meta(session).get("rfq_id") != rfq_id:
        return False, "checkout session belongs to another request"
    if getattr(session, "amount_total", None) != to_cents(amount_usd):
        return False, "amount paid does not match the deposit"
    return True, "paid"


def refund_deposit(checkout_session_id: str, rfq_id: str) -> str:
    """Refund a paid deposit in full (vendor couldn't take the booking). Idempotent per payment. Returns the refund id."""
    s = _stripe()
    intent = (checkout_session_id if checkout_session_id.startswith("pi_")
              else s.checkout.Session.retrieve(checkout_session_id).payment_intent)
    refund = s.Refund.create(payment_intent=intent, reason="requested_by_customer",
                             metadata={"rfq_id": rfq_id}, idempotency_key=f"haggle-refund-{intent}")
    return refund.id
