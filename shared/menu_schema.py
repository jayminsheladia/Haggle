"""Vendor menu data: what the Factory extracts and every vendor agent prices from.

For UNCLAIMED vendors the rules are Haggle's defaults and are never used to make binding offers.

Owner: Person B
"""

import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

DietaryTag = Literal["vegetarian", "vegan", "halal", "kosher", "gluten-free", "contains-nuts", "contains-dairy"]

MAX_SLUG_LEN = 32  # the slug is the vendor agent's name, and Agentverse names are at most 32 characters


def slugify(name: str) -> str:
    """Lowercase-hyphen name, cut at a whole word so it fits an Agentverse agent name."""
    words = re.findall(r"[a-z0-9]+", name.lower().replace("'", ""))
    slug = ""
    for w in words:
        if len(slug) + len(w) + (1 if slug else 0) > MAX_SLUG_LEN:
            break
        slug = f"{slug}-{w}" if slug else w
    return slug or (words[0][:MAX_SLUG_LEN] if words else "vendor")


class MenuItem(BaseModel):
    name: str
    price_per_person: Optional[float] = Field(None, ge=0)  # USD; None when the page gives no usable price
    description: Optional[str] = None
    dietary_tags: list[DietaryTag] = []
    serves: Optional[int] = Field(None, ge=1)


class OwnerRules(BaseModel):
    floor_discount_pct: float = Field(12, ge=0, le=90)
    min_headcount: int = Field(10, ge=1)
    max_headcount: int = Field(200, ge=1)
    lead_time_hours: int = Field(48, ge=0)
    delivery_radius_mi: float = Field(10, ge=0)
    blackout_dates: list[str] = []  # ISO dates
    auto_approve_limit_usd: float = Field(2000, ge=0)
    delivery_fee_usd: float = Field(0, ge=0)


class VendorMenu(BaseModel):
    name: str
    slug: str
    source_url: str = ""
    place_id: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    items: list[MenuItem] = []
    claimed: bool = False
    owner_contact: Optional[str] = None  # the owner's ASI:One agent address once claimed
    rules: OwnerRules = OwnerRules()
    extraction_status: Literal["extracted", "estimated"] = "estimated"
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rating: Optional[float] = None
    photo_url: str = ""  # from the business's website (og:image) or Google Places

    @property
    def display_name(self) -> str:
        return f"{self.name} ✓ Verified" if self.claimed else f"{self.name} (unofficial · built from public menu)"

    @property
    def priced_items(self) -> list[MenuItem]:
        return [i for i in self.items if i.price_per_person is not None]
