"""Open-Meteo historical archive: last full calendar year of daily weather.

One call feeds two factors (hot days, annual precipitation). We use a fixed
calendar year rather than "the last 365 days" so a re-run in the same year
sees the same inputs.
"""
from datetime import date
from typing import Any

import httpx

from app import config
from app.sources.base import BadPayload, SourceResult, fetch_json

SOURCE = "open_meteo"
HOT_DAY_THRESHOLD_C = 35.0
# The archive lags a few days and occasionally has gaps; demand most of a year.
MIN_DAYS_WITH_DATA = 330


def parse(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BadPayload("response is not a JSON object")
    if payload.get("error"):
        raise BadPayload(f"Open-Meteo error: {payload.get('reason')}")
    daily = payload.get("daily") or {}
    tmax = daily.get("temperature_2m_max")
    precip = daily.get("precipitation_sum")
    if not isinstance(tmax, list) or not isinstance(precip, list):
        raise BadPayload("response is missing daily temperature/precipitation arrays")

    tmax_values = [t for t in tmax if isinstance(t, (int, float))]
    precip_values = [p for p in precip if isinstance(p, (int, float))]
    if len(tmax_values) < MIN_DAYS_WITH_DATA or len(precip_values) < MIN_DAYS_WITH_DATA:
        raise BadPayload(
            f"too few days with data ({len(tmax_values)} temperature, "
            f"{len(precip_values)} precipitation; need {MIN_DAYS_WITH_DATA})"
        )
    if any(t < -90 or t > 60 for t in tmax_values) or any(p < 0 for p in precip_values):
        raise BadPayload("physically implausible values in daily series")

    times = daily.get("time") or []
    return {
        "period": f"{times[0]}..{times[-1]}" if times else None,
        "days_with_data": len(tmax_values),
        "hot_days": sum(1 for t in tmax_values if t >= HOT_DAY_THRESHOLD_C),
        "annual_precip_mm": round(sum(precip_values), 1),
    }


async def fetch(
    client: httpx.AsyncClient, lat: float, lon: float, year: int | None = None
) -> SourceResult:
    year = year or date.today().year - 1
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "daily": "temperature_2m_max,precipitation_sum",
        "timezone": "UTC",
    }
    return await fetch_json(client, SOURCE, config.OPEN_METEO_URL, params, parse)
