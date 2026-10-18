"""Photon (iMessage / SMS) over its REST API: https://api.photon.codes/v1 (no bridge needed).

send_text(phone, text): finds or creates the Photon user for the phone, the conversation (space), and sends
the message from our line. Needs PHOTON_API_KEY, PHOTON_PROJECT_ID and PHOTON_SENDER_ID (our line's user id
in the Photon dashboard). During development only test numbers are ever texted (see integrations/notify.py).

Owner: Person B
"""

import logging
import os
from uuid import uuid4

import httpx

API = "https://api.photon.codes/v1"
TIMEOUT_S = 10
logger = logging.getLogger("photon")


class PhotonNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return all(os.getenv(k) for k in ("PHOTON_API_KEY", "PHOTON_PROJECT_ID", "PHOTON_SENDER_ID"))


async def send_text(phone: str, text: str, platform: str | None = None, idempotency_key: str = "") -> str:
    """Text `phone`; returns the Photon message id. Raises on any failure (callers fall back to email).
    idempotency_key (e.g. "booking-<rfq_id>") makes a retried send deliver only once."""
    if not configured():
        raise PhotonNotConfigured("PHOTON_API_KEY, PHOTON_PROJECT_ID or PHOTON_SENDER_ID is not set")
    platform = platform or os.getenv("PHOTON_PLATFORM", "imessage")
    project, sender = os.environ["PHOTON_PROJECT_ID"], os.environ["PHOTON_SENDER_ID"]
    headers = {"Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}"}

    def key(kind: str, value: str) -> dict:
        return {"Idempotency-Key": f"haggle-{kind}-{value}"[:200]}

    async with httpx.AsyncClient(base_url=f"{API}/projects/{project}", headers=headers, timeout=TIMEOUT_S) as c:
        user = (await c.post("/users", json={"handle": phone, "platform": platform},
                             headers=key("user", f"{platform}-{phone}"))).raise_for_status().json()
        space = (await c.post("/spaces", json={"participantIds": [sender, user["id"]], "platform": platform},
                              headers=key("space", f"{sender}-{user['id']}"))).raise_for_status().json()
        msg = (await c.post("/messages", json={"spaceId": space["id"], "senderId": sender,
                                               "content": {"type": "text", "format": "plain", "text": text}},
                            headers=key("msg", idempotency_key or str(uuid4())))).raise_for_status().json()
    logger.info(f"photon message {msg.get('id')} sent to {phone}")
    return msg.get("id", "")
