"""US Census Geocoder: address -> coordinates (+ county, for context)."""
from typing import Any

import httpx

from app import config
from app.sources.base import BadPayload, NoMatch, SourceResult, fetch_json

SOURCE = "census_geocoder"


def parse(payload: Any) -> dict[str, Any]:
    try:
        matches = payload["result"]["addressMatches"]
    except (KeyError, TypeError):
        raise BadPayload("response has no result.addressMatches")
    if not matches:
        # The most common cause in practice is a city or place name ("New York"):
        # this geocoder only matches street addresses, so say so.
        raise NoMatch(
            "the Census geocoder found no match for this address. It only matches full "
            "street addresses (house number, street, city, state - e.g. "
            "'350 5th Ave, New York, NY 10118'); a city or place name on its own won't "
            "match. Check the address, or submit latitude/longitude instead"
        )

    best = matches[0]
    try:
        lon = float(best["coordinates"]["x"])
        lat = float(best["coordinates"]["y"])
    except (KeyError, TypeError, ValueError):
        raise BadPayload("match has no usable coordinates")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise BadPayload(f"coordinates out of range: {lat}, {lon}")

    counties = (best.get("geographies") or {}).get("Counties") or []
    return {
        "lat": lat,
        "lon": lon,
        "matched_address": best.get("matchedAddress"),
        "county": counties[0].get("NAME") if counties else None,
        "match_count": len(matches),
    }


async def geocode(client: httpx.AsyncClient, address: str) -> SourceResult:
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "Counties",
        "format": "json",
    }
    return await fetch_json(client, SOURCE, config.CENSUS_GEOCODER_URL, params, parse)
