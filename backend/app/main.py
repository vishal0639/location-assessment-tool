"""HTTP API. Every handler is a thin read or write against Postgres and never
calls a third-party service - that is what keeps submit fast. The one
deliberate exception is /api/diagnostics/sources, which exists to probe them."""
import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app import scoring
from app.db import get_session
from app.init_db import init_db
from app.models import Assessment, EnrichmentRun, FactorResult, VerdictOverride
from app.pipeline import POINT_SOURCES
from app.sources import census
from app.sources.base import make_client
from app.schemas import (
    AssessmentCreate,
    AssessmentDetail,
    AssessmentListItem,
    AssessmentPage,
    FactorOut,
    FetchOut,
    OverrideCreate,
    OverrideOut,
    RunDetail,
    RunSummary,
)

FACTOR_LABELS = {f.key: f.label for f in scoring.FACTORS}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Location Assessment API", lifespan=lifespan)


@app.get("/api/health")
def health(session: Session = Depends(get_session)):
    pending = session.scalar(
        select(func.count()).select_from(EnrichmentRun).where(EnrichmentRun.status == "pending")
    )
    oldest = session.scalar(
        select(func.min(EnrichmentRun.created_at)).where(EnrichmentRun.status == "pending")
    )
    return {"ok": True, "pending_runs": pending, "oldest_pending_at": oldest}


@app.get("/api/diagnostics/sources")
async def diagnose_sources(
    lat: float = Query(38.8977, ge=-90, le=90),
    lon: float = Query(-77.0365, ge=-180, le=180),
    address: str = "1600 Pennsylvania Ave NW, Washington, DC 20500",
):
    """Call every upstream right now and report which ones work. Saves nothing.

    Unlike the rest of the API this does call third parties, on purpose: it is
    the "which source is broken?" button. The lat/lon and address are probed
    independently, so a geocoder outage doesn't hide the other sources' state.
    """
    async with make_client() as client:
        results = await asyncio.gather(
            census.geocode(client, address),
            *(fetch(client, lat, lon) for _, fetch in POINT_SOURCES),
        )
    return {
        "all_ok": all(r.ok for r in results),
        "failing": [r.source for r in results if not r.ok],
        "sources": [
            {
                "source": r.source,
                "ok": r.ok,
                "error_kind": r.error_kind,
                "error_detail": r.error_detail,
                "http_status": r.http_status,
                "duration_ms": r.duration_ms,
                "facts": r.facts,
                "request_url": r.request_url,
            }
            for r in results
        ],
    }


# ------------------------------------------------------------------ assessments


@app.post("/api/assessments", status_code=status.HTTP_202_ACCEPTED, response_model=AssessmentDetail)
def create_assessment(body: AssessmentCreate, session: Session = Depends(get_session)):
    assessment = Assessment(
        label=body.label, input_address=body.address, input_lat=body.lat, input_lon=body.lon
    )
    session.add(assessment)
    session.flush()
    _enqueue_run(session, assessment)
    session.commit()
    return _detail(session, assessment.id)


@app.post("/api/assessments/{assessment_id}/runs", status_code=status.HTTP_202_ACCEPTED,
          response_model=AssessmentDetail)
def rerun_assessment(assessment_id: int, session: Session = Depends(get_session)):
    """Queue a fresh enrichment. Earlier runs are kept untouched."""
    assessment = _get_assessment(session, assessment_id)
    if assessment.latest_run and assessment.latest_run.status in ("pending", "running"):
        raise HTTPException(409, "a run is already in progress for this assessment")
    _enqueue_run(session, assessment)
    session.commit()
    return _detail(session, assessment_id)


def _enqueue_run(session: Session, assessment: Assessment) -> None:
    run = EnrichmentRun(assessment_id=assessment.id, status="pending")
    session.add(run)
    session.flush()
    assessment.latest_run_id = run.id


SortKey = Literal["created_at", "score", "label", "status"]


@app.get("/api/assessments", response_model=AssessmentPage)
def list_assessments(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="substring match on label or address"),
    status_: list[str] | None = Query(None, alias="status"),
    verdict: list[str] | None = Query(None, description="effective verdict (override wins)"),
    sort: SortKey = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    run = aliased(EnrichmentRun)
    override = aliased(VerdictOverride)
    effective = func.coalesce(override.verdict, run.machine_verdict)
    run_count = (
        select(func.count()).where(EnrichmentRun.assessment_id == Assessment.id)
        .correlate(Assessment).scalar_subquery()
    )

    base = (
        select(Assessment, run, override, effective.label("effective"), run_count.label("run_count"))
        .outerjoin(run, run.id == Assessment.latest_run_id)
        .outerjoin(override, override.id == Assessment.current_override_id)
    )
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Assessment.label.ilike(like), Assessment.input_address.ilike(like)))
    if status_:
        base = base.where(run.status.in_(status_))
    if verdict:
        base = base.where(effective.in_(verdict))

    total = session.scalar(select(func.count()).select_from(base.order_by(None).subquery()))

    column = {
        "created_at": Assessment.created_at,
        "score": run.score,
        "label": func.lower(Assessment.label),
        "status": run.status,
    }[sort]
    direction = asc if order == "asc" else desc
    # Unscored rows sort last either way: "no score" is not "lowest score".
    rows = session.execute(
        base.order_by(direction(column).nulls_last(), direction(Assessment.id)).limit(limit).offset(offset)
    ).all()

    items = [
        AssessmentListItem(
            id=a.id,
            label=a.label,
            location=a.input_address or f"{a.input_lat:.5f}, {a.input_lon:.5f}",
            created_at=a.created_at,
            run_count=count,
            status=r.status if r else None,
            score=r.score if r else None,
            machine_verdict=r.machine_verdict if r else None,
            override_verdict=o.verdict if o else None,
            effective_verdict=eff,
        )
        for a, r, o, eff, count in rows
    ]
    return AssessmentPage(items=items, total=total, limit=limit, offset=offset)


@app.get("/api/assessments/{assessment_id}", response_model=AssessmentDetail)
def get_assessment(
    assessment_id: int, run_id: int | None = None, session: Session = Depends(get_session)
):
    return _detail(session, assessment_id, run_id)


# -------------------------------------------------------------------- overrides


@app.post("/api/assessments/{assessment_id}/overrides", status_code=status.HTTP_201_CREATED,
          response_model=AssessmentDetail)
def create_override(assessment_id: int, body: OverrideCreate, session: Session = Depends(get_session)):
    assessment = _get_assessment(session, assessment_id)
    run_id = body.run_id or assessment.latest_run_id
    if run_id is not None:
        run = session.get(EnrichmentRun, run_id)
        if run is None or run.assessment_id != assessment_id:
            raise HTTPException(400, "run_id does not belong to this assessment")
    override = VerdictOverride(
        assessment_id=assessment_id, run_id=run_id,
        verdict=body.verdict, reason=body.reason, analyst=body.analyst,
    )
    session.add(override)
    session.flush()
    # The machine verdict on the run is never modified; the override sits beside it.
    assessment.current_override_id = override.id
    session.commit()
    return _detail(session, assessment_id)


# ---------------------------------------------------------------------- helpers


def _get_assessment(session: Session, assessment_id: int) -> Assessment:
    assessment = session.get(Assessment, assessment_id)
    if assessment is None:
        raise HTTPException(404, "assessment not found")
    return assessment


def _override_out(o: VerdictOverride, runs_by_id: dict[int, EnrichmentRun]) -> OverrideOut:
    out = OverrideOut.model_validate(o)
    run = runs_by_id.get(o.run_id)
    if run:
        out.run_score, out.run_machine_verdict = run.score, run.machine_verdict
    return out


def _detail(session: Session, assessment_id: int, run_id: int | None = None) -> AssessmentDetail:
    session.expire_all()
    assessment = session.scalar(
        select(Assessment).where(Assessment.id == assessment_id)
        .options(selectinload(Assessment.runs), selectinload(Assessment.overrides))
    )
    if assessment is None:
        raise HTTPException(404, "assessment not found")

    runs_by_id = {r.id: r for r in assessment.runs}
    view_id = run_id or assessment.latest_run_id
    if run_id is not None and run_id not in runs_by_id:
        raise HTTPException(404, "run not found for this assessment")

    run_detail = None
    if view_id is not None:
        run = session.scalar(
            select(EnrichmentRun).where(EnrichmentRun.id == view_id).options(
                selectinload(EnrichmentRun.fetches),
                selectinload(EnrichmentRun.factors).selectinload(FactorResult.source_fetch),
            )
        )
        run_detail = RunDetail(
            **RunSummary.model_validate(run).model_dump(),
            fetches=[FetchOut.model_validate(f) for f in run.fetches],
            factors=[
                FactorOut(
                    factor=f.factor, label=FACTOR_LABELS.get(f.factor, f.factor), status=f.status,
                    raw_value=f.raw_value, derived_value=f.derived_value, points=f.points,
                    max_points=f.max_points, explanation=f.explanation, source=f.source,
                    source_fetch_id=f.source_fetch_id,
                    fetched_at=f.source_fetch.fetched_at if f.source_fetch else None,
                )
                for f in run.factors
            ],
        )

    current = next((o for o in assessment.overrides if o.id == assessment.current_override_id), None)
    return AssessmentDetail(
        id=assessment.id,
        label=assessment.label,
        input_address=assessment.input_address,
        input_lat=assessment.input_lat,
        input_lon=assessment.input_lon,
        created_at=assessment.created_at,
        latest_run_id=assessment.latest_run_id,
        runs=[RunSummary.model_validate(r) for r in assessment.runs],
        run=run_detail,
        current_override=_override_out(current, runs_by_id) if current else None,
        overrides=[_override_out(o, runs_by_id) for o in assessment.overrides],
    )
