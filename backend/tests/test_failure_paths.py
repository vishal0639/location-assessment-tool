"""Every way an upstream can misbehave must end as an honest partial result."""
import asyncio
import time

import httpx
import pytest

from app import config
from app.pipeline import enrich
from app.sources import fema, open_meteo, usgs
from tests import fakes


def run(coro):
    return asyncio.run(coro)


async def enrich_with(behaviours, **location):
    async with fakes.client_for(behaviours) as client:
        return await enrich(client, **location)


# ------------------------------------------------ classification of one source


@pytest.mark.parametrize("behaviour,kind", [
    (fakes.down, "network"),
    (fakes.status(429, headers={"Retry-After": "60"}), "rate_limited"),
    (fakes.status(503, "Service Unavailable"), "http_error"),
    (fakes.status(200, "<html>maintenance</html>"), "bad_payload"),
    ({"value": "abc"}, "bad_payload"),
    ({"value": 99999}, "bad_payload"),        # nonsense elevation
    (fakes.USGS_NO_DATA, "no_match"),         # EPQS sentinel, not -1,000,000 ft
])
def test_usgs_failures_are_classified(behaviour, kind):
    async def go():
        async with fakes.client_for({"usgs_epqs": behaviour}) as client:
            return await usgs.fetch(client, 38.9, -77.0)

    result = run(go())
    assert result.ok is False
    assert result.error_kind == kind
    assert result.facts == {}


def test_transient_502_is_retried_once_then_succeeds():
    calls = []

    def flaky(request):
        calls.append(request)
        return httpx.Response(502) if len(calls) == 1 else httpx.Response(200, json=fakes.USGS_OK)

    async def go():
        async with fakes.client_for({"usgs_epqs": flaky}) as client:
            return await usgs.fetch(client, 38.9, -77.0)

    assert run(go()).ok
    assert len(calls) == 2


def test_rate_limit_is_not_retried():
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429)

    async def go():
        async with fakes.client_for({"usgs_epqs": limited}) as client:
            return await usgs.fetch(client, 38.9, -77.0)

    assert run(go()).error_kind == "rate_limited"
    assert len(calls) == 1


def test_arcgis_error_inside_http_200_is_not_success():
    body = {"error": {"code": 500, "message": "Unable to complete operation."}}

    async def go():
        async with fakes.client_for({"fema_nfhl": body}) as client:
            return await fema.fetch(client, 38.9, -77.0)

    result = run(go())
    assert result.ok is False
    assert result.error_kind == "bad_payload"
    assert result.payload == body  # kept for audit


def test_open_meteo_with_mostly_null_days_is_rejected():
    payload = fakes.open_meteo_ok()
    payload["daily"]["temperature_2m_max"] = [None] * 300 + [20.0] * 65

    async def go():
        async with fakes.client_for({"open_meteo": payload}) as client:
            return await open_meteo.fetch(client, 38.9, -77.0)

    assert run(go()).error_kind == "bad_payload"


def test_slow_source_times_out(monkeypatch):
    monkeypatch.setitem(config.TIMEOUTS, "usgs_epqs", 0.2)

    async def go():
        async with fakes.client_for({"usgs_epqs": fakes.slow(2, fakes.USGS_OK)}) as client:
            return await usgs.fetch(client, 38.9, -77.0)

    started = time.monotonic()
    result = run(go())
    assert result.error_kind == "timeout"
    assert time.monotonic() - started < 1


# ------------------------------------------------------- whole pipeline


def test_happy_path_is_complete():
    result = run(enrich_with(fakes.healthy(), address="1600 Pennsylvania Ave NW"))
    assert result.status == "complete"
    assert result.card.score == 100
    assert (result.lat, result.lon) == (38.8977, -77.0365)
    assert len(result.fetches) == 4


def test_fema_down_still_completes_as_partial_with_flood_unavailable():
    behaviours = {**fakes.healthy(), "fema_nfhl": fakes.down}
    result = run(enrich_with(behaviours, address="1600 Pennsylvania Ave NW"))

    assert result.status == "partial"
    flood = next(f for f in result.card.factors if f.factor == "flood_zone")
    assert flood.available is False and flood.points is None
    assert "fema_nfhl network" in flood.explanation
    # The failed call is still recorded, so the report can say what happened.
    fema_fetch = next(f for f in result.fetches if f.source == "fema_nfhl")
    assert fema_fetch.ok is False and fema_fetch.error_kind == "network"
    # Other sources were unaffected.
    assert next(f for f in result.card.factors if f.factor == "elevation").points == 25


def test_every_point_source_down_is_partial_with_no_score():
    behaviours = {"usgs_epqs": fakes.down, "open_meteo": fakes.status(500), "fema_nfhl": fakes.down}
    result = run(enrich_with(behaviours, lat=38.9, lon=-77.0))
    assert result.status == "partial"
    assert result.card.score is None
    assert all(f.points is None for f in result.card.factors)


def test_coordinates_input_skips_geocoder():
    behaviours = fakes.healthy()
    del behaviours["census_geocoder"]  # any call to it would raise in the fake
    result = run(enrich_with(behaviours, lat=38.9, lon=-77.0))
    assert result.status == "complete"


@pytest.mark.parametrize("geocoder,kind", [
    (fakes.CENSUS_NO_MATCH, "no_match"),
    (fakes.down, "network"),
])
def test_geocoding_failure_fails_the_run_without_a_score(geocoder, kind):
    result = run(enrich_with({"census_geocoder": geocoder}, address="nowhere"))
    assert result.status == "failed"
    assert result.card is None
    assert kind in result.error


def test_geocoder_no_match_tells_the_analyst_what_to_do():
    result = run(enrich_with({"census_geocoder": fakes.CENSUS_NO_MATCH}, address="New York"))
    assert "street address" in result.error
    assert "latitude/longitude" in result.error


def test_sources_are_called_in_parallel():
    behaviours = {
        "usgs_epqs": fakes.slow(0.4, fakes.USGS_OK),
        "open_meteo": fakes.slow(0.4, fakes.open_meteo_ok()),
        "fema_nfhl": fakes.slow(0.4, fakes.FEMA_ZONE_X),
    }
    started = time.monotonic()
    result = run(enrich_with(behaviours, lat=38.9, lon=-77.0))
    assert result.status == "complete"
    assert time.monotonic() - started < 1.0  # sequential would be >= 1.2s
