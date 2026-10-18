"""Email bodies (HTML + plain text). Every amount shown here was computed in code upstream.

Owner: Person D
"""

from html import escape

from shared.models import BookingConfirmed


def _usd(amount: float) -> str:
    return f"${amount:,.2f}"


def booking_confirmation(b: BookingConfirmed) -> tuple[str, str, str]:
    """(subject, html, text) for a paid booking."""
    balance = round(b.total_usd - b.deposit_usd, 2)
    rows = [
        ("Booking", b.rfq_id),
        ("Caterer", b.vendor_name),
        ("When", f"{b.event_date} {b.event_time}".strip()),
        ("Where", b.location),
        ("Guests", str(b.headcount)),
        ("Dietary", b.dietary_summary or "none"),
        ("Total", _usd(b.total_usd)),
        ("Deposit paid", _usd(b.deposit_usd)),
        ("Balance to the caterer", _usd(balance)),
        ("Stripe payment", b.checkout_session_id),
    ]
    subject = f"Haggle booking confirmed: {b.vendor_name}, {b.event_date}"
    text = "Your Haggle booking is confirmed.\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    trs = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#555">{escape(k)}</td>'
        f'<td style="padding:4px 0"><b>{escape(v)}</b></td></tr>' for k, v in rows
    )
    html = (
        '<div style="font-family:system-ui,sans-serif;font-size:14px">'
        "<h2 style=\"margin:0 0 12px\">Your Haggle booking is confirmed</h2>"
        f"<table>{trs}</table>"
        '<p style="color:#777;font-size:12px">Stripe test mode. Sent to the team inbox during development.</p>'
        "</div>"
    )
    return subject, html, text
