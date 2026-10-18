"""Email bodies (HTML + plain text). Every amount shown here was computed in code upstream.

Owner: Person D
"""

from html import escape

from shared.models import BookingConfirmed, QuoteRequest


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


def _html(title: str, rows: list[tuple[str, str]], intro: str = "", footer: str = "") -> str:
    trs = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#555">{escape(k)}</td>'
        f'<td style="padding:4px 0"><b>{escape(v)}</b></td></tr>' for k, v in rows
    )
    return ('<div style="font-family:system-ui,sans-serif;font-size:14px">'
            f'<h2 style="margin:0 0 12px">{escape(title)}</h2>'
            + (f"<p>{escape(intro)}</p>" if intro else "")
            + f"<table>{trs}</table>"
            + (f'<p style="color:#555">{footer}</p>' if footer else "")
            + '<p style="color:#777;font-size:12px">Sent by Haggle. During development every email goes to the team inbox.</p>'
            "</div>")


def vendor_order(b: BookingConfirmed) -> tuple[str, str, str]:
    """(subject, html, text): the order sent to the caterer once the customer's deposit is paid."""
    rows = [
        ("Order", b.rfq_id), ("When", f"{b.event_date} {b.event_time}".strip()), ("Delivery to", b.location),
        ("Guests", str(b.headcount)), ("Dietary", b.dietary_summary or "none"),
        ("Agreed total", _usd(b.total_usd)), ("Deposit collected by Haggle", _usd(b.deposit_usd)),
        ("Balance due from the customer", _usd(round(b.total_usd - b.deposit_usd, 2))),
    ]
    subject = f"New catering order via Haggle: {b.headcount} guests, {b.event_date} {b.event_time}"
    intro = f"Hi {b.vendor_name} team, a customer booked you through Haggle and paid a deposit."
    text = intro + "\n\n" + "\n".join(f"{k}: {v}" for k, v in rows)
    return subject, _html(f"New order for {b.vendor_name}", rows, intro), text


def quote_request(q: QuoteRequest, claim_url: str, reply_by: str) -> tuple[str, str, str]:
    """(subject, html, text): build plan 3A's quote request to an unclaimed caterer."""
    budget = _usd(q.budget_usd) if q.budget_usd else "no fixed budget"
    estimate = (f"based on your public menu we estimated {_usd(q.estimate_usd)}" if q.estimate_usd
                else "your public menu has no prices, so we couldn't estimate it")
    rows = [
        ("Date and time", f"{q.event_date} {q.event_time}".strip()), ("Delivery to", q.location),
        ("Guests", f"{q.headcount}" + (f" ({q.dietary_summary})" if q.dietary_summary else "")),
        ("Their budget", f"{budget} ({estimate})"), ("Please reply by", reply_by),
    ]
    subject = f"Catering request: {q.headcount} guests, {q.event_date} {q.event_time} (via Haggle)"
    intro = f"Hi {q.vendor_name} team, a customer is looking for catering and asked us to request a quote."
    footer = (f'Reply to this email with your price, or <a href="{escape(claim_url)}">claim your free Haggle agent</a> '
              "to quote and negotiate automatically (message it \"claim\"). Prices shown to the customer are marked "
              "as estimates until you confirm.")
    text = (intro + "\n\n" + "\n".join(f"- {k}: {v}" for k, v in rows)
            + f"\n\nReply to this email with your price, or claim your free Haggle agent: {claim_url}\n"
            "Prices shown to the customer are marked as estimates until you confirm.")
    return subject, _html("Catering quote request", rows, intro, footer), text
