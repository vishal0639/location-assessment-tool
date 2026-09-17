"""API tests against a real Postgres (the assess_test database in compose).

No worker runs here, so these also prove the API never calls an upstream.
"""
import os

import pytest

TEST_DB = os.getenv("TEST_DATABASE_URL")
if not TEST_DB:
    pytest.skip("TEST_DATABASE_URL not set (run tests via docker compose)", allow_module_level=True)


from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import EnrichmentRun  # noqa: E402
from app.worker import save_enrichment  # noqa: E402
from app.pipeline import Enrichment  # noqa: E402
from app import scoring  # noqa: E402


@pytest.fixture()
def client():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def test_submit_returns_pending_immediately(client, monkeypatch):
    import httpx

    def no_network(*args, **kwargs):
        raise AssertionError("the submit request must not call third parties")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)

    r = client.post("/api/assessments", json={"label": "Site A", "address": "1 Main St, Denver CO"})
    assert r.status_code == 202
    body = r.json()
    assert body["run"]["status"] == "pending"
    assert body["run"]["score"] is None


@pytest.mark.parametrize("payload", [
    {"label": "x"},                                          # no location
    {"label": "x", "address": "1 Main", "lat": 1, "lon": 2},  # both
    {"label": "x", "lat": 1},                                # half a pair
    {"label": "   ", "address": "1 Main"},                   # blank label
    {"label": "x", "lat": 91, "lon": 0},                     # out of range
])
def test_submit_validation(client, payload):
    assert client.post("/api/assessments", json=payload).status_code == 422


def _finish_run_with_partial_data(assessment_id: int) -> int:
    with SessionLocal() as s:
        run_id = s.query(EnrichmentRun).filter_by(assessment_id=assessment_id).one().id
    card = scoring.score_all({
        "usgs_epqs": {"elevation_ft": 600},
        "open_meteo": {"hot_days": 3, "annual_precip_mm": 900},
        "fema_nfhl": scoring.Unavailable("fema_nfhl network: connection reset"),
    })
    save_enrichment(run_id, Enrichment(status="partial", lat=39.7, lon=-105, card=card))
    return run_id


def test_partial_run_is_reported_as_partial_with_null_points(client):
    aid = client.post("/api/assessments", json={"label": "S", "lat": 39.7, "lon": -105}).json()["id"]
    _finish_run_with_partial_data(aid)

    detail = client.get(f"/api/assessments/{aid}").json()
    assert detail["run"]["status"] == "partial"
    flood = next(f for f in detail["run"]["factors"] if f["factor"] == "flood_zone")
    assert flood["status"] == "unavailable"
    assert flood["points"] is None

    listed = client.get("/api/assessments", params={"status": "partial"}).json()
    assert [i["id"] for i in listed["items"]] == [aid]


def test_override_keeps_machine_verdict_and_history(client):
    aid = client.post("/api/assessments", json={"label": "S", "lat": 39.7, "lon": -105}).json()["id"]
    run_id = _finish_run_with_partial_data(aid)

    client.post(f"/api/assessments/{aid}/overrides",
                json={"verdict": "pursue", "reason": "Site visit: flood risk is fine", "analyst": "ana"})
    detail = client.post(f"/api/assessments/{aid}/overrides",
                         json={"verdict": "reject", "reason": "Landlord pulled out", "analyst": "bo"}).json()

    assert detail["run"]["machine_verdict"] == "review"  # untouched
    assert detail["current_override"]["verdict"] == "reject"
    assert detail["current_override"]["run_id"] == run_id
    assert [o["verdict"] for o in detail["overrides"]] == ["reject", "pursue"]

    item = client.get("/api/assessments").json()["items"][0]
    assert (item["machine_verdict"], item["override_verdict"], item["effective_verdict"]) == \
        ("review", "reject", "reject")


def test_override_requires_reason(client):
    aid = client.post("/api/assessments", json={"label": "S", "lat": 1, "lon": 1}).json()["id"]
    r = client.post(f"/api/assessments/{aid}/overrides",
                    json={"verdict": "pursue", "reason": "  ", "analyst": "ana"})
    assert r.status_code == 422


def test_rerun_keeps_previous_runs(client):
    aid = client.post("/api/assessments", json={"label": "S", "lat": 39.7, "lon": -105}).json()["id"]
    assert client.post(f"/api/assessments/{aid}/runs").status_code == 409  # first still pending
    first = _finish_run_with_partial_data(aid)

    detail = client.post(f"/api/assessments/{aid}/runs").json()
    assert [r["status"] for r in detail["runs"]] == ["pending", "partial"]
    old = client.get(f"/api/assessments/{aid}", params={"run_id": first}).json()
    assert old["run"]["status"] == "partial" and len(old["run"]["factors"]) == 4


def test_diagnostics_names_the_failing_source(client, monkeypatch):
    from tests import fakes
    behaviours = {**fakes.healthy(), "fema_nfhl": fakes.down}
    monkeypatch.setattr("app.main.make_client", lambda: fakes.client_for(behaviours))

    body = client.get("/api/diagnostics/sources").json()
    assert body["all_ok"] is False
    assert body["failing"] == ["fema_nfhl"]
    fema = next(s for s in body["sources"] if s["source"] == "fema_nfhl")
    assert fema["error_kind"] == "network"


def test_list_sorts_unscored_last(client):
    for label in ("a", "b"):
        client.post("/api/assessments", json={"label": label, "lat": 39.7, "lon": -105})
    _finish_run_with_partial_data(1)
    for order in ("asc", "desc"):
        items = client.get("/api/assessments", params={"sort": "score", "order": order}).json()["items"]
        assert items[-1]["score"] is None
