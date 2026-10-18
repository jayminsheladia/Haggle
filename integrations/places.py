"""Google Places (New): text search, photos, reviews.

Owner: Person B
"""

import asyncio
import math
import os

import httpx

BASE_URL = "https://places.googleapis.com/v1"
METERS_PER_MILE = 1609.344
MAX_BIAS_RADIUS_M = 50_000  # Places API limit for a locationBias circle

# Center of Los Angeles, used to bias geocoding toward LA.
LA_CENTER = (34.0522, -118.2437)

SEARCH_FIELDS = ",".join(
    f"places.{f}"
    for f in (
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "nationalPhoneNumber",
        "rating",
        "userRatingCount",
        "photos",
        "websiteUri",
        "businessStatus",
    )
)
GEOCODE_FIELDS = "places.formattedAddress,places.location"


def _headers(field_mask: str | None = None) -> dict[str, str]:
    headers = {"X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"]}
    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask
    return headers


def _circle(lat: float, lng: float, radius_m: float) -> dict:
    return {
        "circle": {
            "center": {"latitude": lat, "longitude": lng},
            "radius": min(radius_m, MAX_BIAS_RADIUS_M),
        }
    }


def distance_mi(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


async def geocode(text: str, client: httpx.AsyncClient) -> dict | None:
    """Turn a venue, address or neighborhood into {lat, lng, formatted_address}, biased toward LA."""
    body = {"textQuery": text, "pageSize": 1, "locationBias": _circle(*LA_CENTER, MAX_BIAS_RADIUS_M)}
    r = await client.post(f"{BASE_URL}/places:searchText", json=body, headers=_headers(GEOCODE_FIELDS))
    r.raise_for_status()
    places = r.json().get("places", [])
    if not places:
        return None
    loc = places[0]["location"]
    return {
        "lat": loc["latitude"],
        "lng": loc["longitude"],
        "formatted_address": places[0].get("formattedAddress"),
    }


async def photo_url(photo_name: str, client: httpx.AsyncClient, max_width_px: int = 800) -> str | None:
    """Resolve a Places photo to a public image URL that does not contain our API key.

    Cards are shown to users, so never put the key-bearing /media URL in them.
    """
    try:
        r = await client.get(
            f"{BASE_URL}/{photo_name}/media",
            params={"maxWidthPx": max_width_px, "skipHttpRedirect": "true"},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json().get("photoUri")
    except (httpx.HTTPError, ValueError):
        return None  # a missing photo should never fail the search


async def text_search(
    query: str,
    lat: float,
    lng: float,
    radius_mi: float,
    limit: int,
    client: httpx.AsyncClient,
) -> list[dict]:
    """Places near (lat, lng): name, address, phone, rating, photo URL and Place ID.

    Results outside radius_mi are dropped, since locationBias alone can return far-away places.
    """
    body = {
        "textQuery": query,
        "pageSize": 20,  # the max; many fall outside the radius and are dropped below
        "locationBias": _circle(lat, lng, radius_mi * METERS_PER_MILE),
    }
    r = await client.post(f"{BASE_URL}/places:searchText", json=body, headers=_headers(SEARCH_FIELDS))
    r.raise_for_status()

    places = []
    for p in r.json().get("places", []):
        if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        loc = p.get("location") or {}
        if "latitude" not in loc:
            continue
        dist = distance_mi(lat, lng, loc["latitude"], loc["longitude"])
        if dist > radius_mi:
            continue
        places.append((p, loc, dist))
        if len(places) == limit:
            break

    photos = await asyncio.gather(
        *(photo_url(p["photos"][0]["name"], client) if p.get("photos") else asyncio.sleep(0) for p, _, _ in places)
    )
    return [
        {
            "place_id": p["id"],
            "name": p.get("displayName", {}).get("text", ""),
            "address": p.get("formattedAddress"),
            "phone": p.get("nationalPhoneNumber"),
            "website": p.get("websiteUri"),
            "rating": p.get("rating"),
            "rating_count": p.get("userRatingCount"),
            "photo_url": photo,
            "lat": loc["latitude"],
            "lng": loc["longitude"],
            "distance_mi": round(dist, 1),
        }
        for (p, loc, dist), photo in zip(places, photos)
    ]
