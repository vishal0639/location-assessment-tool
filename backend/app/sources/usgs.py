"""USGS Elevation Point Query Service: elevation (feet) at a point."""
from typing import Any

import httpx

from app import config
from app.sources.base import BadPayload, NoMatch, SourceResult, fetch_json

SOURCE = "usgs_epqs"

# Lowest and highest ground in the US, with some slack. Anything outside this
# is a sentinel or garbage, not an elevation. EPQS uses -1000000 for "no data".
MIN_PLAUSIBLE_FT = -300
MAX_PLAUSIBLE_FT = 21000


def parse(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or "value" not in payload:
        raise BadPayload("response has no 'value' field")
    value = payload["value"]
    if value is None:
        raise NoMatch("no elevation data at this point (outside USGS coverage?)")
    try:
        feet = float(value)
    except (TypeError, ValueError):
        raise BadPayload(f"elevation is not a number: {value!r}")
    if feet <= -1000000:
        raise NoMatch("USGS returned its no-data sentinel (outside coverage?)")
    if not (MIN_PLAUSIBLE_FT <= feet <= MAX_PLAUSIBLE_FT):
        raise BadPayload(f"implausible elevation {feet} ft")
    return {"elevation_ft": round(feet, 1)}


async def fetch(client: httpx.AsyncClient, lat: float, lon: float) -> SourceResult:
    params = {"x": lon, "y": lat, "wkid": 4326, "units": "Feet", "includeDate": "false"}
    return await fetch_json(client, SOURCE, config.USGS_EPQS_URL, params, parse)
