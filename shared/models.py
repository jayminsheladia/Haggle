"""Shared message models: RFQ, Quote, Counter, Accept, Decline, Onboard*, approvals. Every model carries a `trace` field.

Owner: Person C
"""

from uagents import Model


# ---- Discovery (Person B) ---------------------------------------------------
# Concierge -> Discovery -> Concierge. Proposed in team chat; Person C owns this file.


class DiscoveryRequest(Model):
    rfq_id: str
    category: str = "catering"
    location: str | None = None  # venue or address as the user wrote it; geocoded if lat/lng are missing
    lat: float | None = None
    lng: float | None = None
    radius_mi: float = 5.0
    limit: int = 10
    trace: dict[str, str] | None = None


class VendorInfo(Model):
    name: str
    source: str  # "places" or "agentverse"
    place_id: str | None = None
    agent_address: str | None = None  # set when the vendor already has an agent that speaks HaggleNegotiation
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    rating_count: int | None = None
    photo_url: str | None = None
    lat: float | None = None
    lng: float | None = None
    distance_mi: float | None = None


class DiscoveryResult(Model):
    rfq_id: str
    vendors: list[VendorInfo]
    resolved_location: str | None = None
    lat: float | None = None
    lng: float | None = None
    error: str | None = None  # set when a source failed; vendors holds whatever the other sources found
    trace: dict[str, str] | None = None
