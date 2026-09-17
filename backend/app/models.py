"""Relational model.

assessment 1-* enrichment_run 1-* source_fetch
                              1-* factor_result (-> the source_fetch it came from)
assessment 1-* verdict_override

- An assessment is what the analyst submitted. It never changes.
- A run is one attempt to enrich + score it. Re-running creates a new run, so
  "why did we reject that one in March?" is answered by the run from March.
- A source_fetch is one HTTP call: the raw payload, or the reason there isn't one.
- A factor_result is one scored fact. points is NULL when unavailable: a
  missing value is never stored as 0 (enforced by a CHECK constraint).
- Overrides are append-only; the latest one is the current human verdict.
"""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

RUN_STATUSES = ("pending", "running", "complete", "partial", "failed")
VERDICTS = ("pursue", "review", "reject")


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    input_address: Mapped[str | None] = mapped_column(Text)
    input_lat: Mapped[float | None] = mapped_column(Float)
    input_lon: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Denormalised pointers so the list view is one indexed join, not a
    # "latest row per group" query over every run.
    latest_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("enrichment_runs.id", use_alter=True, name="fk_assessment_latest_run")
    )
    current_override_id: Mapped[int | None] = mapped_column(
        ForeignKey("verdict_overrides.id", use_alter=True, name="fk_assessment_current_override")
    )

    runs: Mapped[list["EnrichmentRun"]] = relationship(
        back_populates="assessment",
        foreign_keys="EnrichmentRun.assessment_id",
        order_by="EnrichmentRun.id.desc()",
    )
    latest_run: Mapped["EnrichmentRun | None"] = relationship(
        foreign_keys=[latest_run_id], post_update=True
    )
    overrides: Mapped[list["VerdictOverride"]] = relationship(
        foreign_keys="VerdictOverride.assessment_id",
        order_by="VerdictOverride.id.desc()",
    )
    current_override: Mapped["VerdictOverride | None"] = relationship(
        foreign_keys=[current_override_id], post_update=True
    )

    __table_args__ = (
        CheckConstraint(
            "input_address IS NOT NULL OR (input_lat IS NOT NULL AND input_lon IS NOT NULL)",
            name="assessment_has_location",
        ),
        Index("ix_assessments_created_at", "created_at"),
        Index("ix_assessments_label", "label"),
    )


class EnrichmentRun(Base):
    __tablename__ = "enrichment_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    # Where we decided the location is.
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    matched_address: Mapped[str | None] = mapped_column(Text)

    # Scoring output. score is NULL unless enough of the weight was available.
    scoring_version: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None] = mapped_column(Integer)
    points_earned: Mapped[float | None] = mapped_column(Float)
    points_available: Mapped[float | None] = mapped_column(Float)
    points_possible: Mapped[float | None] = mapped_column(Float)
    machine_verdict: Mapped[str | None] = mapped_column(String(20))
    verdict_reason: Mapped[str | None] = mapped_column(Text)

    assessment: Mapped[Assessment] = relationship(
        back_populates="runs", foreign_keys=[assessment_id]
    )
    fetches: Mapped[list["SourceFetch"]] = relationship(
        back_populates="run", order_by="SourceFetch.id"
    )
    factors: Mapped[list["FactorResult"]] = relationship(
        back_populates="run", order_by="FactorResult.id"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'complete', 'partial', 'failed')",
            name="run_status_valid",
        ),
        # The worker's claim query scans this.
        Index("ix_runs_status_created", "status", "created_at"),
        Index("ix_runs_assessment", "assessment_id"),
        Index("ix_runs_score", "score"),
    )


class SourceFetch(Base):
    __tablename__ = "source_fetches"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("enrichment_runs.id"))
    source: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))  # ok | error
    # timeout | rate_limited | http_error | network | bad_payload
    error_kind: Mapped[str | None] = mapped_column(String(30))
    error_detail: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    request_url: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict | list | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)

    run: Mapped[EnrichmentRun] = relationship(back_populates="fetches")

    __table_args__ = (Index("ix_fetches_run", "run_id"),)


class FactorResult(Base):
    __tablename__ = "factor_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("enrichment_runs.id"))
    factor: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))  # ok | unavailable
    raw_value: Mapped[dict | list | str | float | None] = mapped_column(JSONB)
    derived_value: Mapped[str | None] = mapped_column(Text)
    points: Mapped[float | None] = mapped_column(Float)
    max_points: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50))
    source_fetch_id: Mapped[int | None] = mapped_column(ForeignKey("source_fetches.id"))

    run: Mapped[EnrichmentRun] = relationship(back_populates="factors")
    source_fetch: Mapped[SourceFetch | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "(status = 'ok' AND points IS NOT NULL) OR (status = 'unavailable' AND points IS NULL)",
            name="factor_points_null_iff_unavailable",
        ),
        Index("ix_factors_run", "run_id"),
    )


class VerdictOverride(Base):
    __tablename__ = "verdict_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"))
    # The run the analyst was looking at, so the machine score they disagreed
    # with is recoverable even after a re-run.
    run_id: Mapped[int | None] = mapped_column(ForeignKey("enrichment_runs.id"))
    verdict: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    analyst: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "verdict IN ('pursue', 'review', 'reject')", name="override_verdict_valid"
        ),
        CheckConstraint("length(trim(reason)) > 0", name="override_reason_required"),
        Index("ix_overrides_assessment", "assessment_id"),
    )
