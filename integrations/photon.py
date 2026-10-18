"""Photon (iMessage / SMS) over its REST API: https://api.photon.codes/v1 (no bridge needed).

send_text(phone, text): finds or creates the Photon user for the phone, the conversation (space), and sends
the message from our line. Needs PHOTON_API_KEY, PHOTON_PROJECT_ID and PHOTON_SENDER_ID (our line's user id
in the Photon dashboard). During development only test numbers are ever texted (see integrations/notify.py).

Owner: Person B
"""

import logging
import os

import httpx

API = "https://api.photon.codes/v1"
TIMEOUT_S = 10
logger = logging.getLogger("photon")


class PhotonNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return all(os.getenv(k) for k in ("PHOTON_API_KEY", "PHOTON_PROJECT_ID", "PHOTON_SENDER_ID"))


async def send_text(phone: str, text: str, platform: str | None = None) -> str:
    """Text `phone`; returns the Photon message id. Raises on any failure (callers fall back to email)."""
    if not configured():
        raise PhotonNotConfigured("PHOTON_API_KEY, PHOTON_PROJECT_ID or PHOTON_SENDER_ID is not set")
    platform = platform or os.getenv("PHOTON_PLATFORM", "imessage")
    project = os.environ["PHOTON_PROJECT_ID"]
    headers = {"Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}"}
    async with httpx.AsyncClient(base_url=f"{API}/projects/{project}", headers=headers, timeout=TIMEOUT_S) as c:
        user = (await c.post("/users", json={"handle": phone, "platform": platform})).raise_for_status().json()
        space = (await c.post("/spaces", json={"participantIds": [user["id"]], "platform": platform})).raise_for_status().json()
        msg = (await c.post("/messages", json={"spaceId": space["id"], "senderId": os.environ["PHOTON_SENDER_ID"],
                                               "content": {"type": "text", "format": "plain", "text": text}})).raise_for_status().json()
    logger.info(f"photon message {msg.get('id')} sent to {phone}")
    return msg.get("id", "")
