"""Discovery: Agentverse search by protocol digest, Google Places, review summary tool.

Receives a DiscoveryRequest (category + location) and replies with a DiscoveryResult:
vendor agents on Agentverse that speak HaggleNegotiation, plus real businesses from Google Places.
Also chattable in ASI:One as @haggle-discovery ("caterers near Ackerman Union").

Run from the repo root:  python -m agents.discovery

Owner: Person B
"""

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from integrations import places
from shared import observability
from shared.models import DiscoveryRequest, DiscoveryResult, VendorInfo
from vendors.template import PROTOCOL_DIGEST

load_dotenv()

AGENTVERSE_SEARCH_URL = "https://agentverse.ai/v1/search/agents"
HTTP_TIMEOUT_S = 10

# Results cached per area (build plan): repeat searches cost no Places quota.
GEOCODE_TTL_S = 24 * 3600  # venues don't move
SEARCH_TTL_S = 3600  # photo URLs and opening status can change
_geocode_cache: dict[str, tuple[float, dict]] = {}
_search_cache: dict[tuple, tuple[float, list[dict]]] = {}


def _cached(cache: dict, key, ttl_s: float):
    hit = cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl_s else None


async def geocode(location: str, client: httpx.AsyncClient) -> dict | None:
    key = " ".join(location.lower().split())
    geo = _cached(_geocode_cache, key, GEOCODE_TTL_S)
    if geo is None:
        geo = await places.geocode(location, client)
        if geo:
            _geocode_cache[key] = (time.monotonic(), geo)
    return geo


def _business_name(description: str) -> str:
    """vendors/template.py descriptions start "<Name> ✓ Verified: ..." or "<Name> (unofficial · ...): ..."."""
    head = description.split(":")[0]
    return head.replace(" ✓ Verified", "").split(" (unofficial")[0].strip()


async def agentverse_vendors(limit: int, client: httpx.AsyncClient) -> list[VendorInfo]:
    """Active vendor agents on Agentverse that speak HaggleNegotiation.

    NEGOTIATION_PROTOCOL_DIGEST in .env overrides the digest from vendors/template.py.
    """
    digest = os.environ.get("NEGOTIATION_PROTOCOL_DIGEST") or PROTOCOL_DIGEST
    r = await client.post(
        AGENTVERSE_SEARCH_URL,
        json={
            "search_text": "",  # the digest filter alone selects Haggle vendors
            "filters": {"protocol_digest": [digest]},
            "offset": 0,
            "limit": limit,
        },
    )
    r.raise_for_status()
    return [
        VendorInfo(name=a.get("name") or a["address"], source="agentverse", agent_address=a["address"],
                   claimed="✓ Verified" in (a.get("description") or ""),  # set by vendors/template.py
                   business_name=_business_name(a.get("description") or ""),
                   photo_url=a.get("avatar_href") or "")
        for a in r.json().get("agents", [])
        if a.get("status") == "active"
    ]


async def places_vendors(
    req: DiscoveryRequest, lat: float, lng: float, client: httpx.AsyncClient, with_photos: bool
) -> list[VendorInfo]:
    key = (round(lat, 3), round(lng, 3), req.category.lower(), req.radius_mi, req.limit, with_photos)
    found = _cached(_search_cache, key, SEARCH_TTL_S)
    if found is None:
        found = await places.text_search(req.category, lat, lng, req.radius_mi, req.limit, client, with_photos)
        _search_cache[key] = (time.monotonic(), found)
    return [VendorInfo(source="places", **p) for p in found]


async def discover(req: DiscoveryRequest, with_photos: bool = True) -> DiscoveryResult:
    """Run both sources; if one fails, return what the other found plus the error."""
    errors: list[str] = []
    lat, lng, resolved = req.lat, req.lng, req.location

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        if lat is None or lng is None:
            geo = None
            if req.location:
                try:
                    geo = await geocode(req.location, client)
                except httpx.HTTPError as e:
                    errors.append(f"geocoding failed: {e}")
            if geo:
                lat, lng, resolved = geo["lat"], geo["lng"], geo["formatted_address"]
            elif not req.location:
                errors.append("no location given")
            elif not errors:
                errors.append(f"could not find location: {req.location!r}")

        sources = [agentverse_vendors(req.limit, client)]
        if lat is not None and lng is not None:
            sources.append(places_vendors(req, lat, lng, client, with_photos))
        results = await asyncio.gather(*sources, return_exceptions=True)

    vendors: list[VendorInfo] = []
    for name, res in zip(("agentverse search", "places search"), results):
        if isinstance(res, Exception):
            errors.append(f"{name} failed: {res}")
        else:
            vendors.extend(res)  # agents first: they can quote and negotiate right away

    return DiscoveryResult(
        rfq_id=req.rfq_id,
        vendors=vendors,
        resolved_location=resolved,
        lat=lat,
        lng=lng,
        error="; ".join(errors),
        trace=req.trace,
    )


# ---- Chat (ASI:One) ----------------------------------------------------------

CHAT_LIMIT = 5
LOCATION_RE = re.compile(r"\b(?:near|in|at|around|by|close to)\s+(.+)", re.I)
TRAILING_RE = re.compile(r"\s+(?:for|on|this|next|tomorrow|tonight|with|under|budget)\b.*$", re.I)
HELP = ("I find real caterers near any venue in Los Angeles. Tell me where your event is, "
        "e.g. \"caterers near Ackerman Union, UCLA\" or \"catering in Santa Monica\".")


def location_from_text(text: str) -> str | None:
    text = re.sub(r"@\S+", " ", text)
    m = LOCATION_RE.search(text)
    if not m:
        return None
    return TRAILING_RE.sub("", m.group(1)).strip(" .?!,") or None


def chat_reply(result: DiscoveryResult) -> str:
    places_found = sorted((v for v in result.vendors if v.source == "places"), key=lambda v: v.distance_mi)
    if not places_found:
        if "429" in result.error:
            return "I've reached today's search limit. Please try again after midnight Pacific time."
        if result.error:
            return f"I couldn't find that place. {HELP}"
        return "I didn't find any caterers within 5 miles of there. Try a nearby neighborhood."
    lines = [f"Caterers near {result.resolved_location}:", ""]
    for i, v in enumerate(places_found[:CHAT_LIMIT], 1):
        rating = f" · ★ {v.rating} ({v.rating_count})" if v.rating else ""
        phone = f" · {v.phone}" if v.phone else ""
        maps = f"https://www.google.com/maps/search/?api=1&query={quote(v.name)}&query_place_id={v.place_id}"
        lines.append(f"{i}. [{v.name}]({maps}) · {v.distance_mi} mi{rating}{phone}")
    lines += ["", "To get quotes, negotiate and book, chat with @haggle."]
    return "\n".join(lines)


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


# ---- Agent -----------------------------------------------------------------

agent = Agent(
    name="haggle-discovery",
    seed=os.environ["DISCOVERY_SEED"],
    port=8002,
    mailbox=True,
    publish_agent_details=True,
    handle="haggle-discovery",
    readme_path=str(Path(__file__).with_name("discovery_README.md")),
    description="Haggle Discovery: finds real Los Angeles caterers near a venue and vendor agents that can "
                "negotiate quotes.",
)

discovery = Protocol(name="HaggleDiscovery", version="0.1.0")
chat = Protocol(spec=chat_protocol_spec)


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(f"Discovery address: {agent.address}")


@discovery.on_message(DiscoveryRequest, replies=DiscoveryResult)
async def on_discovery_request(ctx: Context, sender: str, req: DiscoveryRequest):
    observability.tag(req.trace, req.rfq_id)
    where = req.location or f"{req.lat},{req.lng}"
    ctx.logger.info(f"[{req.rfq_id}] {req.category} within {req.radius_mi} mi of {where}")
    try:
        result = await discover(req)
    except Exception as e:  # always reply, so the Concierge never waits on a crashed search
        ctx.logger.exception(f"[{req.rfq_id}] discovery crashed")
        result = DiscoveryResult(rfq_id=req.rfq_id, error=f"discovery failed: {e}", trace=req.trace)
    ctx.logger.info(
        f"[{req.rfq_id}] {len(result.vendors)} vendors" + (f"; error: {result.error}" if result.error else "")
    )
    await ctx.send(sender, result)


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent)).strip()
    location = location_from_text(text)
    ctx.logger.info(f"[chat] {sender[:16]}… asked {text!r} -> location {location!r}")
    if not location:
        await ctx.send(sender, text_message(HELP))
        return
    req = DiscoveryRequest(rfq_id=f"chat_{str(msg.msg_id)[:8]}", location=location, limit=CHAT_LIMIT * 2)
    try:
        result = await discover(req, with_photos=False)  # chat replies are text; photos would waste quota
    except Exception as e:
        ctx.logger.exception("[chat] discovery crashed")
        result = DiscoveryResult(rfq_id=req.rfq_id, error=f"discovery failed: {e}")
    await ctx.send(sender, text_message(chat_reply(result)))


@chat.on_message(ChatAcknowledgement)
async def on_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(discovery, publish_manifest=True)
agent.include(chat, publish_manifest=True)

if __name__ == "__main__":
    observability.init("discovery")
    agent.run()
