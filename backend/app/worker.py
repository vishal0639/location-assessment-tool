"""Background worker: claims pending runs from Postgres and enriches them.

The queue is the enrichment_runs table itself. FOR UPDATE SKIP LOCKED lets
several workers run side by side without double-claiming, and a run whose
lease has expired (worker crashed mid-run) is picked up again.

Run with:  python -m app.worker
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app import config, scoring
from app.db import SessionLocal
from app.init_db import init_db
from app.models import EnrichmentRun, FactorResult, SourceFetch
from app.pipeline import Enrichment, enrich
from app.sources.base import make_client

log = logging.getLogger("worker")

CLAIM_SQL = text(
    """
    UPDATE enrichment_runs
       SET status = 'running',
           attempts = attempts + 1,
           locked_at = now(),
           started_at = COALESCE(started_at, now())
     WHERE id = (
        SELECT id FROM enrichment_runs
         WHERE status = 'pending'
            OR (status = 'running' AND locked_at < now() - make_interval(secs => :lease))
         ORDER BY created_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, attempts
    """
)


def claim_next_run() -> tuple[int, int] | None:
    with SessionLocal() as session, session.begin():
        row = session.execute(CLAIM_SQL, {"lease": config.RUN_LEASE_SECONDS}).first()
        return (row.id, row.attempts) if row else None


def save_enrichment(run_id: int, result: Enrichment) -> None:
    """Write fetches, factors and score for a run in one transaction."""
    with SessionLocal() as session, session.begin():
        run = session.get(EnrichmentRun, run_id)
        fetch_ids: dict[str, int] = {}
        for f in result.fetches:
            row = SourceFetch(
                run_id=run_id, source=f.source, status="ok" if f.ok else "error",
                error_kind=f.error_kind, error_detail=f.error_detail, http_status=f.http_status,
                request_url=f.request_url, raw_payload=f.payload, fetched_at=f.fetched_at,
                duration_ms=f.duration_ms,
            )
            session.add(row)
            session.flush()
            fetch_ids[f.source] = row.id

        card = result.card
        if card:
            for fs in card.factors:
                session.add(FactorResult(
                    run_id=run_id, factor=fs.factor, status="ok" if fs.available else "unavailable",
                    raw_value=fs.raw_value, derived_value=fs.derived_value, points=fs.points,
                    max_points=fs.max_points, explanation=fs.explanation, source=fs.source,
                    source_fetch_id=fetch_ids.get(fs.source),
                ))
            run.scoring_version = scoring.SCORING_VERSION
            run.score = card.score
            run.points_earned = card.points_earned
            run.points_available = card.points_available
            run.points_possible = card.points_possible
            run.machine_verdict = card.verdict
            run.verdict_reason = card.verdict_reason

        run.status = result.status
        run.error = result.error
        run.lat, run.lon, run.matched_address = result.lat, result.lon, result.matched_address
        run.finished_at = datetime.now(timezone.utc)
        run.locked_at = None


def mark_failed(run_id: int, error: str) -> None:
    with SessionLocal() as session, session.begin():
        run = session.get(EnrichmentRun, run_id)
        run.status = "failed"
        run.error = error
        run.finished_at = datetime.now(timezone.utc)
        run.locked_at = None


async def process(run_id: int, attempts: int, client) -> None:
    with SessionLocal() as session:
        run = session.get(EnrichmentRun, run_id)
        a = run.assessment
        address, lat, lon = a.input_address, a.input_lat, a.input_lon

    if attempts > config.MAX_RUN_ATTEMPTS:
        # Something about this run kills the worker repeatedly. Stop looping.
        mark_failed(run_id, f"gave up after {attempts - 1} attempts (worker crashed or timed out each time)")
        return
    try:
        result = await enrich(client, address=address, lat=lat, lon=lon)
        save_enrichment(run_id, result)
        log.info("run %s -> %s (score=%s)", run_id, result.status,
                 result.card.score if result.card else None)
    except Exception as exc:
        log.exception("run %s crashed", run_id)
        mark_failed(run_id, f"internal error: {type(exc).__name__}: {exc}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    await asyncio.to_thread(init_db)
    log.info("worker started")
    async with make_client() as client:
        while True:
            try:
                claimed = await asyncio.to_thread(claim_next_run)
            except Exception:
                # Database blip: keep the worker alive and try again shortly.
                log.exception("could not claim a run")
                await asyncio.sleep(5)
                continue
            if claimed is None:
                await asyncio.sleep(config.WORKER_POLL_SECONDS)
                continue
            # One run at a time per worker process: at a few hundred runs a
            # day that is plenty, and it keeps us polite to the free APIs.
            await process(*claimed, client)


if __name__ == "__main__":
    asyncio.run(main())
