"""Fake upstreams for tests.

Census, USGS and Open-Meteo shapes are trimmed from live responses seen during
the build. FEMA was unreachable from the build machine all day, so its shapes
follow the documented ArcGIS REST query format and NFHL field names instead.
"""
import asyncio
from collections.abc import Callable

import httpx

CENSUS_OK = {
    "result": {
        "addressMatches": [{
            "matchedAddress": "1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500",
            "coordinates": {"x": -77.0365, "y": 38.8977},
            "geographies": {"Counties": [{"NAME": "District of Columbia"}]},
        }]
    }
}
CENSUS_NO_MATCH = {"result": {"addressMatches": []}}
USGS_OK = {"location": {"x": -77.0365, "y": 38.8977}, "value": 60.36, "resolution": 1}
USGS_NO_DATA = {"value": -1000000}


def open_meteo_ok(hot_days: int = 5, daily_precip: float = 3.0) -> dict:
    tmax = [36.0] * hot_days + [20.0] * (365 - hot_days)
    return {
        "daily": {
            "time": ["2025-01-01"] + [""] * 363 + ["2025-12-31"],
            "temperature_2m_max": tmax,
            "precipitation_sum": [daily_precip] * 365,
        }
    }


FEMA_ZONE_X = {"features": [{"attributes": {
    "FLD_ZONE": "X", "ZONE_SUBTY": "AREA OF MINIMAL FLOOD HAZARD", "SFHA_TF": "F"}}]}
FEMA_ZONE_AE = {"features": [{"attributes": {"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T"}}]}

HOSTS = {
    "census_geocoder": "geocoding.geo.census.gov",
    "usgs_epqs": "epqs.nationalmap.gov",
    "open_meteo": "archive-api.open-meteo.com",
    "fema_nfhl": "hazards.fema.gov",
}

Behaviour = dict | Callable[[httpx.Request], httpx.Response]


def healthy() -> dict[str, Behaviour]:
    return {
        "census_geocoder": CENSUS_OK,
        "usgs_epqs": USGS_OK,
        "open_meteo": open_meteo_ok(),
        "fema_nfhl": FEMA_ZONE_X,
    }


def down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection reset by peer", request=request)


def status(code: int, body: str = "", headers: dict | None = None):
    return lambda request: httpx.Response(code, text=body, headers=headers or {})


def slow(seconds: float, then: dict):
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return httpx.Response(200, json=then)
    return handler


def client_for(behaviours: dict[str, Behaviour]) -> httpx.AsyncClient:
    by_host = {HOSTS[source]: b for source, b in behaviours.items()}

    async def handler(request: httpx.Request) -> httpx.Response:
        behaviour = by_host.get(request.url.host)
        if behaviour is None:
            raise AssertionError(f"unexpected call to {request.url}")
        if isinstance(behaviour, dict):
            return httpx.Response(200, json=behaviour)
        response = behaviour(request)
        if asyncio.iscoroutine(response):
            response = await response
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
