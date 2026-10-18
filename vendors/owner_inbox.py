"""Owner replies by text: reads texts sent to our Photon line and hands YES / NO / a counter price to the vendor
agent that asked for approval (its POST /owner-reply), then texts the owner the result.

Runs inside the vendor host (it knows every vendor agent and its port). Every POLL_S seconds:
  1. list texts received since the last poll (Photon REST, direction=received)
  2. keep only texts from a vendor owner's phone (OWNER_TEST_PHONE during development)
  3. find the approval: the id in the text ("YES A1234", "counter A1234 750"), else the only pending one
  4. POST it to that vendor agent's /owner-reply, which applies the decision exactly like a chat reply
A text with no pending approval is ignored: the same test phone also gets the organizer's booking texts.
Each text is handled once (ids kept in vendors/owner_inbox_state.json); texts sent before the first run are skipped.

Owner: Person D
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from integrations import photon
from shared import observability
from shared.models import OwnerReply

logger = logging.getLogger("owner_inbox")

POLL_S = 15
STATE_FILE = Path(__file__).parent / "owner_inbox_state.json"
APPROVAL_ID = re.compile(r"\b(a\d{4})\b", re.I)
KEEP_IDS = 200


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-10:]


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        # First run: start now, so old texts (or a "YES" to something else) are never replayed.
        return {"since": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "seen": []}


def _save_state(state: dict) -> None:
    state["seen"] = state["seen"][-KEEP_IDS:]
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def pending_approvals(host) -> list[tuple[str, dict]]:
    """(vendor slug, approval) for every approval still waiting on its owner, oldest first."""
    found = []
    for slug, agent in host.agents.items():
        for a in (agent.storage.get("approvals") or {}).values():
            if a.get("status") == "pending":
                found.append((slug, a))
    return sorted(found, key=lambda x: x[1].get("deadline_iso", ""))


def route(text: str, pending: list[tuple[str, dict]]) -> tuple[tuple[str, dict] | None, str]:
    """(the approval this text answers, or None; why not)."""
    m = APPROVAL_ID.search(text)
    if m:
        match = [p for p in pending if p[1]["approval_id"] == m.group(1).upper()]
        return (match[0], "") if match else (None, f"No pending request {m.group(1).upper()}.")
    if len(pending) == 1:
        return pending[0], ""
    if pending:
        ids = ", ".join(a["approval_id"] for _, a in pending)
        return None, f"Several requests are waiting ({ids}). Reply with the id, e.g. \"YES {pending[0][1]['approval_id']}\"."
    return None, ""  # nothing pending: not an owner reply


async def handle(host, msg: dict) -> None:
    pending = pending_approvals(host)
    target, why = route(msg["text"], pending)
    owner_phone = os.getenv("OWNER_TEST_PHONE", "")
    if not target:
        if why:
            await photon.send_text(owner_phone, f"Haggle: {why}", idempotency_key=f"owner-reply-{msg['id']}")
        logger.info(f"text {msg['id']} not routed ({why or 'no pending approvals'}): {msg['text'][:60]!r}")
        return
    slug, approval = target
    text = APPROVAL_ID.sub("", msg["text"]).strip() or msg["text"]
    with observability.span("owner.reply", f"{slug} {approval['approval_id']}", approval.get("trace", ""),
                            rfq_id=approval["rfq_id"], vendor_id=slug):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"http://127.0.0.1:{host.ports[slug]}/owner-reply",
                             json=json.loads(OwnerReply(vendor_id=slug, approval_id=approval["approval_id"],
                                                        text=text).json()))
            r.raise_for_status()
            result = r.json()
    logger.info(f"text {msg['id']} -> {slug} {approval['approval_id']}: {result}")
    reply = result.get("detail") or ("Done." if result.get("ok") else "Couldn't apply that.")
    await photon.send_text(owner_phone, f"Haggle: {reply}", idempotency_key=f"owner-reply-{msg['id']}")


async def poll_once(host, state: dict) -> None:
    owner = _digits(os.getenv("OWNER_TEST_PHONE", ""))
    messages = await photon.list_received(state["since"])
    for msg in messages:
        if msg["id"] in state["seen"]:
            continue
        state["seen"].append(msg["id"])
        if owner and _digits(msg["handle"]) == owner and msg["text"].strip():
            try:
                await handle(host, msg)
            except Exception as e:  # one bad text never stops the inbox
                logger.warning(f"text {msg['id']} failed: {e}")
    if messages:
        # createdSince is inclusive: step back a little and rely on `seen` to skip repeats.
        last = datetime.fromisoformat(messages[-1]["created_at"].replace("Z", "+00:00")) - timedelta(seconds=1)
        state["since"] = last.isoformat(timespec="seconds").replace("+00:00", "Z")
    _save_state(state)


async def run(host) -> None:
    """Poll forever. Without Photon or OWNER_TEST_PHONE it does nothing (owners can still reply in chat)."""
    if not photon.configured() or not os.getenv("OWNER_TEST_PHONE"):
        logger.info("owner inbox off: Photon or OWNER_TEST_PHONE not configured")
        return
    state = _load_state()
    _save_state(state)
    print(f"owner inbox: reading owner texts from Photon every {POLL_S}s", flush=True)
    while True:
        try:
            await poll_once(host, state)
        except Exception as e:
            logger.warning(f"owner inbox poll failed: {e}")
        await asyncio.sleep(POLL_S)
