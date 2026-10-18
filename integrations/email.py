"""Resend email client. During development all mail goes to the team inbox.

Owner: Person D
"""

import logging
import os

import httpx

logger = logging.getLogger("email")

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Haggle <onboarding@resend.dev>"  # Resend's test sender; works without a verified domain


class EmailError(RuntimeError):
    pass


async def send_email(subject: str, html: str, text: str, idempotency_key: str = "") -> str:
    """Send to TEAM_INBOX_EMAIL (dev: never to a customer or a business). Returns the Resend email id."""
    key = os.getenv("RESEND_API_KEY", "")
    to = os.getenv("TEAM_INBOX_EMAIL", "")
    if not key or not to:
        raise EmailError("RESEND_API_KEY or TEAM_INBOX_EMAIL is not set")
    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key  # Resend dedupes retries for 24 h
    body = {"from": os.getenv("RESEND_FROM") or DEFAULT_FROM, "to": [to], "subject": subject,
            "html": html, "text": text}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(RESEND_URL, headers=headers, json=body)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise EmailError(f"Resend {e.response.status_code}: {e.response.text[:200]}") from e
    except httpx.HTTPError as e:
        raise EmailError(f"Resend unreachable: {e}") from e
    email_id = r.json().get("id", "")
    logger.info(f"email {email_id} sent to {to}: {subject}")
    return email_id
