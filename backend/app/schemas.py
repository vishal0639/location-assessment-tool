from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Verdict = Literal["pursue", "review", "reject"]


class AssessmentCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def exactly_one_location(self):
        self.label = self.label.strip()
        self.address = (self.address or "").strip() or None
        has_coords = self.lat is not None and self.lon is not None
        if not self.label:
            raise ValueError("label must not be blank")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("provide both lat and lon, or neither")
        if not self.address and not has_coords:
            raise ValueError("provide an address or a lat/lon pair")
        if self.address and has_coords:
            raise ValueError("provide either an address or a lat/lon pair, not both")
        return self


class OverrideCreate(BaseModel):
    verdict: Verdict
    reason: str = Field(min_length=1, max_length=5000)
    analyst: str = Field(min_length=1, max_length=100)
    # The run the analyst was looking at. Defaults to the latest run.
    run_id: int | None = None

    @model_validator(mode="after")
    def not_blank(self):
        self.reason, self.analyst = self.reason.strip(), self.analyst.strip()
        if not self.reason or not self.analyst:
            raise ValueError("reason and analyst must not be blank")
        return self


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OverrideOut(ORM):
    id: int
    run_id: int | None
    verdict: str
    reason: str
    analyst: str
    created_at: datetime
    # Machine result of the run the override was made against.
    run_score: int | None = None
    run_machine_verdict: str | None = None


class RunSummary(ORM):
    id: int
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    error: str | None
    lat: float | None
    lon: float | None
    matched_address: str | None
    scoring_version: str | None
    score: int | None
    points_earned: float | None
    points_available: float | None
    points_possible: float | None
    machine_verdict: str | None
    verdict_reason: str | None


class FetchOut(ORM):
    id: int
    source: str
    status: str
    error_kind: str | None
    error_detail: str | None
    http_status: int | None
    request_url: str
    raw_payload: Any
    fetched_at: datetime
    duration_ms: int


class FactorOut(BaseModel):
    factor: str
    label: str
    status: str
    raw_value: Any
    derived_value: str | None
    points: float | None
    max_points: float
    explanation: str
    source: str
    source_fetch_id: int | None
    fetched_at: datetime | None


class RunDetail(RunSummary):
    factors: list[FactorOut]
    fetches: list[FetchOut]


class AssessmentListItem(BaseModel):
    id: int
    label: str
    location: str
    created_at: datetime
    run_count: int
    status: str | None
    score: int | None
    machine_verdict: str | None
    override_verdict: str | None
    effective_verdict: str | None


class AssessmentPage(BaseModel):
    items: list[AssessmentListItem]
    total: int
    limit: int
    offset: int


class AssessmentDetail(BaseModel):
    id: int
    label: str
    input_address: str | None
    input_lat: float | None
    input_lon: float | None
    created_at: datetime
    latest_run_id: int | None
    runs: list[RunSummary]
    run: RunDetail | None  # the run being viewed (latest unless ?run_id=)
    current_override: OverrideOut | None
    overrides: list[OverrideOut]
