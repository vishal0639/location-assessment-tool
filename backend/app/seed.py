"""Seed data.

  python -m app.seed                  queue a handful of real demo locations
                                      (the worker enriches them against live APIs)
  python -m app.seed --synthetic 10000
                                      bulk-insert clearly-labelled fake rows
                                      to exercise list pagination/sorting at scale.
                                      These have scores but no factors and never
                                      touch an upstream API.
"""
import argparse

from sqlalchemy import text

from app.db import SessionLocal
from app.init_db import init_db
from app.models import Assessment, EnrichmentRun

DEMO = [
    ("DC - baseline", "1600 Pennsylvania Ave NW, Washington, DC 20500", None, None),
    ("Miami Beach - coastal", "1700 Convention Center Dr, Miami Beach, FL 33139", None, None),
    ("Phoenix - hot/dry", "200 W Washington St, Phoenix, AZ 85003", None, None),
    ("Denver - office", "1437 Bannock St, Denver, CO 80202", None, None),
    ("Leadville - high altitude (coords)", None, 39.2508, -106.2925),
    ("Typo address - should fail geocoding", "123 Nowhere Lane, Faketown, ZZ 00000", None, None),
]

SYNTHETIC_SQL = text(
    """
    WITH new_assessments AS (
        INSERT INTO assessments (label, input_lat, input_lon, created_at)
        SELECT 'synthetic-' || lpad(g::text, 5, '0'),
               25 + random() * 23, -124 + random() * 57,
               now() - (random() * interval '180 days')
          FROM generate_series(1, :n) g
        RETURNING id, created_at
    ), new_runs AS (
        INSERT INTO enrichment_runs (assessment_id, status, created_at, finished_at, attempts,
                                     scoring_version, score, machine_verdict, verdict_reason)
        SELECT id, s.status, created_at, created_at + interval '5 seconds', 1, 'synthetic',
               CASE WHEN s.status = 'failed' THEN NULL ELSE s.score END,
               CASE WHEN s.status = 'failed' THEN NULL
                    WHEN s.score >= 70 THEN 'pursue' WHEN s.score < 40 THEN 'reject' ELSE 'review' END,
               'synthetic row for scale testing - not a real assessment'
          FROM new_assessments,
               LATERAL (SELECT (ARRAY['complete','complete','complete','partial','failed'])
                               [1 + floor(random() * 5)::int] AS status,
                               floor(random() * 101)::int AS score) s
        RETURNING id, assessment_id
    )
    UPDATE assessments a SET latest_run_id = r.id FROM new_runs r WHERE a.id = r.assessment_id
    """
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", type=int, default=0)
    args = parser.parse_args()
    init_db()

    with SessionLocal() as session, session.begin():
        if args.synthetic:
            session.execute(SYNTHETIC_SQL, {"n": args.synthetic})
            print(f"inserted {args.synthetic} synthetic assessments")
            return
        for label, address, lat, lon in DEMO:
            a = Assessment(label=label, input_address=address, input_lat=lat, input_lon=lon)
            session.add(a)
            session.flush()
            run = EnrichmentRun(assessment_id=a.id, status="pending")
            session.add(run)
            session.flush()
            a.latest_run_id = run.id
        print(f"queued {len(DEMO)} demo assessments; the worker will enrich them now")


if __name__ == "__main__":
    main()
