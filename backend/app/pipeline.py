"""Enrich one location: resolve coordinates, query sources in parallel, score.

No database access here, so the whole failure path is testable with a fake
HTTP transport. The worker persists what this returns.
"""
import asyncio
from dataclasses import dataclass, field

import httpx

from app import scoring
from app.sources import census, fema, open_meteo, usgs
from app.sources.base import SourceResult

# Adding a source = one module with fetch(client, lat, lon) + a FactorDef.
POINT_SOURCES = [
    (usgs.SOURCE, usgs.fetch),
    (open_meteo.SOURCE, open_meteo.fetch),
    (fema.SOURCE, fema.fetch),
]


@dataclass
class Enrichment:
    status: str  # complete | partial | failed
    fetches: list[SourceResult] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    matched_address: str | None = None
    card: scoring.ScoreCard | None = None
    error: str | None = None


async def enrich(
    client: httpx.AsyncClient,
    *,
    address: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Enrichment:
    out = Enrichment(status="failed")

    if lat is None or lon is None:
        geo = await census.geocode(client, address or "")
        out.fetches.append(geo)
        if not geo.ok:
            # Without coordinates there is nothing to score. This is a failed
            # run, not a zero score; a re-run can retry the geocoder.
            out.error = f"Could not resolve address to coordinates - {geo.unavailable_reason()}"
            return out
        lat, lon = geo.facts["lat"], geo.facts["lon"]
        out.matched_address = geo.facts.get("matched_address")
    out.lat, out.lon = lat, lon

    # Sources are independent, so call them concurrently. Each is bounded by
    # its own timeout inside fetch_json, so the slowest source caps the run.
    results = await asyncio.gather(
        *(fetch(client, lat, lon) for _, fetch in POINT_SOURCES), return_exceptions=True
    )
    facts_by_source: dict[str, dict | scoring.Unavailable] = {}
    for (source, _), res in zip(POINT_SOURCES, results):
        if isinstance(res, BaseException):
            # fetch_json should never raise; if it does, that's our bug, but it
            # still must not sink the other sources.
            facts_by_source[source] = scoring.Unavailable(f"internal error: {res!r}")
            continue
        out.fetches.append(res)
        facts_by_source[res.source] = (
            res.facts if res.ok else scoring.Unavailable(res.unavailable_reason())
        )

    out.card = scoring.score_all(facts_by_source)
    out.status = "partial" if out.card.is_partial else "complete"
    return out
