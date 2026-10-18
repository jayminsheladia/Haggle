"""notify_owner(menu, text): reach a vendor's owner. Photon (if integrations/photon.py has send_text)
-> email via Resend (if RESEND_API_KEY) -> log only.

During development every message goes to OWNER_TEST_PHONE / OWNER_TEST_EMAIL, never to the business.

Owner: Person D
"""

import logging
import os

import httpx

from integrations import photon
from shared.menu_schema import VendorMenu

logger = logging.getLogger("notify")

RESEND_URL = "https://api.resend.com/emails"
RESEND_FROM = "Haggle <onboarding@resend.dev>"


async def notify_owner(menu: VendorMenu, text: str, subject: str = "Haggle") -> str:
    """Send `text` to the owner; returns the channel used ("photon", "email" or "log")."""
    phone = os.getenv("OWNER_TEST_PHONE", "")  # dev: test contacts only, never menu.phone / menu.email
    email = os.getenv("OWNER_TEST_EMAIL", "")

    send_text = getattr(photon, "send_text", None)
    if send_text and phone:
        try:
            await send_text(phone, text)
            return "photon"
        except Exception as e:
            logger.warning(f"[{menu.slug}] Photon failed, falling back: {e}")

    key = os.getenv("RESEND_API_KEY")
    if key and email:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(RESEND_URL, headers={"Authorization": f"Bearer {key}"},
                                      json={"from": RESEND_FROM, "to": [email], "subject": subject, "text": text})
                r.raise_for_status()
            return "email"
        except httpx.HTTPError as e:
            logger.warning(f"[{menu.slug}] email failed, falling back to log: {e}")

    logger.info(f"[{menu.slug}] OWNER MESSAGE (log only): {text}")
    return "log"
