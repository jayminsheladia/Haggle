"""Photon (iMessage / SMS) over its REST API: https://api.photon.codes/v1 (no bridge needed).

send_text(phone, text): finds or creates the Photon user for the phone, the conversation (space), and sends
the message from our line. Needs PHOTON_API_KEY, PHOTON_PROJECT_ID and PHOTON_SENDER_ID (our line's user id
in the Photon dashboard). During development only test numbers are ever texted (see integrations/notify.py).
list_received(since): texts people sent to our line (vendors/owner_inbox.py reads owner replies from it).

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


async def list_received(since_iso: str, page_size: int = 50) -> list[dict]:
    """Texts received on our line since `since_iso` (RFC 3339, inclusive), oldest first:
    [{"id", "handle", "text", "created_at"}]. Raises on any failure."""
    if not configured():
        raise PhotonNotConfigured("PHOTON_API_KEY, PHOTON_PROJECT_ID or PHOTON_SENDER_ID is not set")
    headers = {"Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
        r = await c.get(f"{API}/projects/{os.environ['PHOTON_PROJECT_ID']}/messages", headers=headers,
                        params={"direction": "received", "createdSince": since_iso, "order": "asc",
                                "pageSize": page_size})
        r.raise_for_status()
    return [{"id": m["id"], "handle": m.get("sentBy", {}).get("handle", ""), "created_at": m.get("createdAt", ""),
             "text": (m.get("content") or {}).get("text", "")}
            for m in r.json().get("items", []) if (m.get("content") or {}).get("type") == "text"]
