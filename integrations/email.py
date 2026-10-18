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


async def send_email(subject: str, html: str, text: str, idempotency_key: str = "",
                     attachments: list[dict] | None = None, to: str = "") -> str:
    """Send to `to` (a development test contact, never a real customer or business; default TEAM_INBOX_EMAIL).
    Returns the Resend email id.

    Resend's test sender only delivers to the Resend account owner: if it refuses `to`, the email goes to
    TEAM_INBOX_EMAIL instead with "[meant for <to>]" in the subject, so nothing is lost.
    attachments: [{"filename": ..., "content": <base64 str>}] (Resend's format), e.g. a calendar invite.
    """
    key = os.getenv("RESEND_API_KEY", "")
    team = os.getenv("TEAM_INBOX_EMAIL", "")
    to = to or team
    if not key or not to:
        raise EmailError("RESEND_API_KEY or TEAM_INBOX_EMAIL is not set")
    try:
        return await _send(key, to, subject, html, text, idempotency_key, attachments)
    except EmailError as e:
        if to == team or not team or "only send testing emails" not in str(e):
            raise
        logger.info(f"Resend test sender can't reach {to}; sending to the team inbox instead")
        return await _send(key, team, f"[meant for {to}] {subject}", html, text,
                           f"{idempotency_key}-team" if idempotency_key else "", attachments)


async def _send(key: str, to: str, subject: str, html: str, text: str, idempotency_key: str,
                attachments: list[dict] | None) -> str:
    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key  # Resend dedupes retries for 24 h
    body = {"from": os.getenv("RESEND_FROM") or DEFAULT_FROM, "to": [to], "subject": subject,
            "html": html, "text": text, **({"attachments": attachments} if attachments else {})}
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
