"""Discovery: Agentverse search by protocol digest, Google Places, review summary tool.

Receives a DiscoveryRequest (category + location) and replies with a DiscoveryResult:
vendor agents on Agentverse that speak HaggleNegotiation, plus real businesses from Google Places.

Run from the repo root:  python -m agents.discovery

Owner: Person B
"""

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from uagents import Agent, Context, Protocol

from integrations import places
from shared import observability
from shared.models import DiscoveryRequest, DiscoveryResult, VendorInfo
from vendors.template import PROTOCOL_DIGEST

load_dotenv()

AGENTVERSE_SEARCH_URL = "https://agentverse.ai/v1/search/agents"
HTTP_TIMEOUT_S = 10


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
                   claimed="✓ Verified" in (a.get("description") or ""))  # set by vendors/template.py
        for a in r.json().get("agents", [])
        if a.get("status") == "active"
    ]


async def places_vendors(
    req: DiscoveryRequest, lat: float, lng: float, client: httpx.AsyncClient
) -> list[VendorInfo]:
    found = await places.text_search(req.category, lat, lng, req.radius_mi, req.limit, client)
    return [VendorInfo(source="places", **p) for p in found]


async def discover(req: DiscoveryRequest) -> DiscoveryResult:
    """Run both sources; if one fails, return what the other found plus the error."""
    errors: list[str] = []
    lat, lng, resolved = req.lat, req.lng, req.location

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        if lat is None or lng is None:
            geo = None
            if req.location:
                try:
                    geo = await places.geocode(req.location, client)
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
            sources.append(places_vendors(req, lat, lng, client))
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


# ---- Agent -----------------------------------------------------------------

agent = Agent(
    name="haggle-discovery",
    seed=os.environ["DISCOVERY_SEED"],
    port=8002,
    mailbox=True,
    publish_agent_details=True,
    readme_path=str(Path(__file__).with_name("discovery_README.md")),
    description="Haggle Discovery: finds real Los Angeles caterers near a venue and vendor agents that can "
                "negotiate quotes.",
)

discovery = Protocol(name="HaggleDiscovery", version="0.1.0")


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


agent.include(discovery, publish_manifest=True)

if __name__ == "__main__":
    observability.init("discovery")
    agent.run()
