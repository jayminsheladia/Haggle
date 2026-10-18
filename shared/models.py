"""Shared message models: RFQ, Quote, Counter, Accept, Decline, Onboard*, approvals. Every model carries a `trace` field.

Owner: Person C
"""

from typing import List, Optional

from uagents import Model


# ---- Discovery (Person B) ---------------------------------------------------
# Proposed in team chat; Person C owns this file.


class DiscoveryRequest(Model):
    """Concierge -> Discovery: find vendors near the venue."""
    rfq_id: str
    category: str = "catering"
    location: str = ""  # venue or address as the user wrote it; geocoded when lat/lng are missing
    lat: Optional[float] = None
    lng: Optional[float] = None
    radius_mi: float = 5.0
    limit: int = 10
    trace: str = ""


class VendorInfo(Model):
    name: str
    source: str  # "places" or "agentverse"
    place_id: str = ""
    agent_address: str = ""  # set when the vendor already has an agent that speaks HaggleNegotiation
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    photo_url: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance_mi: float = 0.0  # 0.0 for Agentverse vendors (no location yet)


class DiscoveryResult(Model):
    """Discovery -> Concierge. `error` is set when a source failed; `vendors` still holds what the others found."""
    rfq_id: str
    vendors: List[VendorInfo] = []
    resolved_location: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    error: str = ""
    trace: str = ""
