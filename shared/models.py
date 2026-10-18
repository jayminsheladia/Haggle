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
    category: str = "catering"
    lat: Optional[float] = None  # venue
    lng: Optional[float] = None
    extras: List[str] = []  # e.g. ["drinks", "dessert", "plates"]
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
    status: str = "quoted"  # quoted | accepted | countered | needs_approval | price_on_request
    note: str = ""
    version: int = 1
    dietary_ok: bool = True  # every dietary group and allergy can be served
    conflicts: List[str] = []  # what can't be served, e.g. "no halal items"
    expires_in_s: int = 0  # 0 = no expiry given
    menu_preview: List[str] = []  # a few menu items with prices, for the quote's detail card
    menu_url: str = ""  # the public menu page the prices come from
    photo_url: str = ""
    trace: str = ""


class Counter(Model):
    """Negotiator -> vendor."""
    rfq_id: str
    vendor_id: str
    target_usd: float
    round: int = 1
    version: int = 1
    reason: str = ""
    trace: str = ""


class Accept(Model):
    rfq_id: str
    vendor_id: str
    final_usd: float
    version: int = 1
    deposit_txn: str = ""
    trace: str = ""


class Decline(Model):
    """Vendor -> Negotiator (can't serve / won't go lower), or Negotiator -> vendor (user passed)."""
    rfq_id: str
    vendor_id: str
    reason: str
    version: int = 1
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
    target_usd: float = 0.0  # what the Negotiator asked for in this round (0 = no counter was sent)
    reason: str = ""  # why it asked for that, as said to the vendor
    trace: str = ""


# Owner approval flow (claimed vendors, anything outside the owner's rules)
class ApprovalRequest(Model):
    rfq_id: str
    vendor_id: str
    summary: str
    proposed_usd: float
    deadline: str = ""
    trace: str = ""


class PendingApproval(Model):
    """A request waiting on the owner (stored by the vendor agent; the Negotiator sees Quote status "needs_approval")."""
    rfq_id: str
    vendor_id: str
    summary: str
    proposed_usd: float
    deadline_iso: str
    approval_id: str = ""
    trace: str = ""


class OwnerReply(Model):
    """POST /owner-reply on a vendor agent (from the Photon bridge): the owner's YES / NO / price."""
    vendor_id: str
    approval_id: str
    text: str


class OwnerReplyResult(Model):
    ok: bool
    detail: str = ""


class ApprovalDecision(Model):
    rfq_id: str
    vendor_id: str
    decision: str  # approve | counter | decline
    counter_usd: float = 0.0
    trace: str = ""


# Vendor Agent Factory onboarding
class OnboardRequest(Model):
    business_name: str
    website: str = ""  # menu page URL
    place_id: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    trace: str = ""


class OnboardResult(Model):
    vendor_id: str
    agent_address: str
    claimed: bool = False
    menu_items: int = 0  # item count
    error: str = ""
    status: str = "live"  # live (menu extracted) | estimated (agent live, no usable menu) | failed
    message: str = ""
    trace: str = ""


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
    claimed: bool = False  # the business set up (claimed) its agent: "Connected"
    business_name: str = ""  # display name for agent vendors (their agent name is the slug)


class DiscoveryResult(Model):
    """Discovery -> Concierge. `error` is set when a source failed; `vendors` still holds what the others found."""
    rfq_id: str
    vendors: List[VendorInfo] = []
    resolved_location: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    error: str = ""
    trace: str = ""


# ---- Booking (Person D) -----------------------------------------------------


class BookingConfirmed(Model):
    """Concierge -> Booker once the deposit's CommitPayment arrives. The Booker re-verifies with Stripe
    before confirming anything. Idempotent on rfq_id: a replay gets the stored BookingResult."""
    rfq_id: str
    vendor_id: str
    vendor_name: str
    vendor_address: str = ""  # vendor agent address (Accept is sent here once paid)
    version: int = 1
    user_address: str = ""
    event_date: str = ""  # YYYY-MM-DD
    event_time: str = ""  # HH:MM
    location: str = ""
    headcount: int = 0
    dietary_summary: str = ""
    total_usd: float
    deposit_usd: float
    checkout_session_id: str  # Stripe Checkout Session id = CommitPayment.transaction_id
    trace: str = ""


class BookingResult(Model):
    """Booker -> Concierge. status: confirmed | payment_failed | error."""
    rfq_id: str
    vendor_id: str
    status: str
    detail: str = ""
    email_sent: bool = False
    trace: str = ""


class QuoteRequest(Model):
    """Concierge -> Booker: email an UNCLAIMED caterer for a confirmed quote (with a claim link). Idempotent on
    rfq_id + vendor_id. During development the email goes to the team inbox, never to the business."""
    rfq_id: str
    vendor_id: str
    vendor_name: str
    vendor_address: str = ""  # its Haggle vendor agent (the claim link points at it)
    vendor_phone: str = ""
    event_date: str = ""
    event_time: str = ""
    location: str = ""
    headcount: int = 0
    dietary_summary: str = ""
    budget_usd: float = 0.0  # 0 = no fixed budget
    estimate_usd: float = 0.0  # 0 = no public prices
    trace: str = ""


class QuoteRequestSent(Model):
    """Booker -> Concierge."""
    rfq_id: str
    vendor_id: str
    email_to: str = ""
    sent_at: str = ""
    reply_by: str = ""
    sent: bool = False
    detail: str = ""
    trace: str = ""
