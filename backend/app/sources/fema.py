"""FEMA National Flood Hazard Layer (ArcGIS REST): flood zone at a point.

Layer 28 is "Flood Hazard Zones". ArcGIS reports many errors as HTTP 200 with
an {"error": ...} body, so a 200 alone proves nothing.
"""
from typing import Any

import httpx

from app import config
from app.sources.base import BadPayload, NoMatch, SourceResult, fetch_json

SOURCE = "fema_nfhl"


def parse(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadPayload("response is not a JSON object")
    if "error" in payload:
        err = payload["error"] or {}
        raise BadPayload(f"ArcGIS error {err.get('code')}: {err.get('message')}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise BadPayload("response has no features list")
    if not features:
        raise NoMatch("no mapped flood zone at this point (area may be unmapped)")

    attrs = features[0].get("attributes") or {}
    zone = attrs.get("FLD_ZONE")
    if not zone or not isinstance(zone, str):
        raise BadPayload("feature has no FLD_ZONE attribute")
    return {
        "zone": zone.strip().upper(),
        "zone_subtype": attrs.get("ZONE_SUBTY"),
        "sfha": attrs.get("SFHA_TF") == "T",
        "feature_count": len(features),
    }


async def fetch(client: httpx.AsyncClient, lat: float, lon: float) -> SourceResult:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
        "returnGeometry": "false",
        "f": "json",
    }
    return await fetch_json(client, SOURCE, config.FEMA_NFHL_URL, params, parse)
