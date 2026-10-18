"""Shared message models: RFQ, Quote, Counter, Accept, Decline, Onboard*, approvals. Every model carries a `trace` field.

Owner: Person C
"""
from typing import Dict, List, Optional

from uagents import Model


class RFQ(Model):
    """Request for quote, Negotiator -> vendor. `version` bumps when the user changes the request."""
    rfq_id: str
    version: int = 1
    event_date: str  # YYYY-MM-DD
    event_time: str = ""  # HH:MM, 24h
    location: str = ""
    distance_mi: float = 0.0  # vendor-to-venue distance, computed by Discovery
    headcount: int
    budget_usd: float = 0.0
    dietary: Dict[str, int] = {}  # e.g. {"vegetarian": 10, "halal": 3}
    allergies: List[str] = []  # explicit; empty list means "none" was confirmed
    trace: str = ""


class LineItem(Model):
    group: str  # "standard" or a dietary tag
    package: str
    headcount: int
    per_head_usd: float
    subtotal_usd: float


class Quote(Model):
    """Vendor -> Negotiator. Also the reply to a Counter (round >= 1)."""
    rfq_id: str
    vendor_id: str
    vendor_name: str
    total_usd: float
    per_head_usd: float
    delivery_fee_usd: float = 0.0
    items: List[LineItem] = []
    claimed: bool = False
    estimate: bool = False  # True for unclaimed vendors: "Estimate · not confirmed"
    round: int = 0
    status: str = "quoted"  # quoted | accepted | countered | needs_approval
    note: str = ""
    trace: str = ""


class Counter(Model):
    """Negotiator -> vendor."""
    rfq_id: str
    vendor_id: str
    target_usd: float
    round: int = 1
    trace: str = ""


class Accept(Model):
    rfq_id: str
    vendor_id: str
    final_usd: float
    trace: str = ""


class Decline(Model):
    """Vendor -> Negotiator (can't serve / won't go lower), or Negotiator -> vendor (user passed)."""
    rfq_id: str
    vendor_id: str
    reason: str
    trace: str = ""


class VendorRef(Model):
    vendor_id: str
    name: str
    address: str  # agent address
    distance_mi: float = 0.0
    claimed: bool = False
    rating: Optional[float] = None


class NegotiationRequest(Model):
    """Concierge -> Negotiator: quote these vendors for this event."""
    rfq: RFQ
    vendors: List[VendorRef]
    trace: str = ""


class RankedQuote(Model):
    rank: int
    quote: Quote
    fits_budget: bool
    score: float


class NegotiationResult(Model):
    """Negotiator -> Concierge. `declined` maps vendor_id -> reason."""
    rfq_id: str
    ranked: List[RankedQuote]
    declined: Dict[str, str] = {}
    trace: str = ""


class CounterRequest(Model):
    """Concierge -> Negotiator: run one counter round with one vendor."""
    rfq_id: str
    vendor_id: str
    target_usd: float = 0.0  # 0 = let the Negotiator pick the target
    trace: str = ""


class CounterResult(Model):
    rfq_id: str
    vendor_id: str
    quote: Optional[Quote] = None  # None if the vendor declined
    declined_reason: str = ""
    ranked: List[RankedQuote] = []  # refreshed ranking
    trace: str = ""


# Owner approval flow (claimed vendors, anything outside the owner's rules)
class ApprovalRequest(Model):
    rfq_id: str
    vendor_id: str
    summary: str
    proposed_usd: float
    deadline: str = ""
    trace: str = ""


class ApprovalDecision(Model):
    rfq_id: str
    vendor_id: str
    decision: str  # approve | counter | decline
    counter_usd: float = 0.0
    trace: str = ""


# Vendor Agent Factory onboarding
class OnboardRequest(Model):
    business_name: str
    website: str = ""
    place_id: str = ""
    trace: str = ""


class OnboardResult(Model):
    vendor_id: str
    agent_address: str
    claimed: bool = False
    menu_items: int = 0
    error: str = ""
    trace: str = ""
