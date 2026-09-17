"""Readiness scoring rules. Every rule lives in this file.

Pure functions only: no I/O, no database, no clock. The plain-English version
is in SCORING.md; if you change a number here, change it there and bump
SCORING_VERSION so old runs remain explainable.

Two principles the tests pin down:
  1. A missing fact is Unavailable, never 0. It earns no points AND takes its
     weight out of the denominator.
  2. The machine only commits to "pursue" or "reject" when the unavailable
     factors could not change that verdict, however they had turned out.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SCORING_VERSION = "v1"

PURSUE_AT_OR_ABOVE = 70
REJECT_BELOW = 40
# Below this share of the total weight we refuse to produce a number at all.
MIN_COVERAGE_FOR_SCORE = 0.5


@dataclass(frozen=True)
class Unavailable:
    reason: str


@dataclass(frozen=True)
class Rule:
    """The outcome of applying one factor's rule to a known value."""
    points: float
    derived: str
    explanation: str


@dataclass(frozen=True)
class FactorScore:
    factor: str
    label: str
    source: str
    max_points: float
    available: bool
    raw_value: Any
    derived_value: str | None
    points: float | None  # None iff not available
    explanation: str


@dataclass(frozen=True)
class ScoreCard:
    factors: list[FactorScore]
    score: int | None
    points_earned: float
    points_available: float
    points_possible: float
    verdict: str
    verdict_reason: str
    knockout: str | None

    @property
    def is_partial(self) -> bool:
        return any(not f.available for f in self.factors)


# ---------------------------------------------------------------- factor rules


def rule_flood_zone(fema: dict[str, Any]) -> Rule:
    zone = fema["zone"]
    subtype = (fema.get("zone_subtype") or "").upper()
    if fema.get("sfha") or zone.startswith("A") or zone.startswith("V"):
        return Rule(0, f"Zone {zone} (high risk)", "In a FEMA Special Flood Hazard Area (1% annual chance).")
    if zone == "X" and "0.2 PCT" in subtype:
        return Rule(20, "Zone X shaded (moderate)", "0.2% annual chance flood area.")
    if zone == "X":
        return Rule(35, "Zone X (minimal)", "Area of minimal flood hazard.")
    if zone == "D":
        return Rule(15, "Zone D (undetermined)", "Flood risk possible but not studied.")
    if zone == "OPEN WATER":
        return Rule(0, "Open water", "The point is in open water.")
    # An unknown code is not something to guess at; the caller turns this
    # into Unavailable rather than inventing a number.
    raise ValueError(f"unrecognised FEMA flood zone code {zone!r}")


def rule_elevation(elevation_ft: float) -> Rule:
    ft = elevation_ft
    if ft < 10:
        return Rule(0, f"{ft:,.0f} ft (very low)", "Under 10 ft: storm-surge and drainage exposure.")
    if ft < 50:
        return Rule(12, f"{ft:,.0f} ft (low)", "10-49 ft: some low-lying exposure.")
    if ft < 7000:
        return Rule(25, f"{ft:,.0f} ft", "50-6,999 ft: no elevation concern.")
    return Rule(10, f"{ft:,.0f} ft (high altitude)", "7,000 ft or more: access and construction difficulty.")


def rule_hot_days(hot_days: int) -> Rule:
    d = hot_days
    if d <= 7:
        return Rule(25, f"{d} days >= 35 C", "7 or fewer very hot days a year.")
    if d <= 30:
        return Rule(15, f"{d} days >= 35 C", "8-30 very hot days a year.")
    if d <= 60:
        return Rule(5, f"{d} days >= 35 C", "31-60 very hot days a year.")
    return Rule(0, f"{d} days >= 35 C", "More than 60 very hot days a year.")


def rule_precipitation(annual_mm: float) -> Rule:
    mm = annual_mm
    derived = f"{mm:,.0f} mm/year"
    if 300 <= mm <= 1500:
        return Rule(15, derived, "300-1,500 mm: moderate rainfall.")
    if 150 <= mm < 300 or 1500 < mm <= 2500:
        return Rule(7, derived, "150-299 mm (dry) or 1,501-2,500 mm (wet).")
    return Rule(0, derived, "Under 150 mm (arid) or over 2,500 mm (very wet).")


@dataclass(frozen=True)
class FactorDef:
    key: str
    label: str
    source: str
    max_points: float
    # Picks this factor's raw input out of the source's facts.
    extract: Callable[[dict[str, Any]], Any]
    rule: Callable[[Any], Rule]


FACTORS: list[FactorDef] = [
    FactorDef("flood_zone", "Flood zone", "fema_nfhl", 35,
              lambda f: {k: f.get(k) for k in ("zone", "zone_subtype", "sfha")}, rule_flood_zone),
    FactorDef("elevation", "Elevation", "usgs_epqs", 25,
              lambda f: f["elevation_ft"], rule_elevation),
    FactorDef("hot_days", "Very hot days (last full year)", "open_meteo", 25,
              lambda f: f["hot_days"], rule_hot_days),
    FactorDef("precipitation", "Annual precipitation (last full year)", "open_meteo", 15,
              lambda f: f["annual_precip_mm"], rule_precipitation),
]
POINTS_POSSIBLE = sum(f.max_points for f in FACTORS)  # 100


# -------------------------------------------------------------- whole scorecard


def score_factor(defn: FactorDef, source_facts: dict[str, Any] | Unavailable) -> FactorScore:
    def unavailable(reason: str, raw: Any = None) -> FactorScore:
        return FactorScore(defn.key, defn.label, defn.source, defn.max_points, False,
                           raw, None, None, f"Unavailable - {reason}")

    if isinstance(source_facts, Unavailable):
        return unavailable(source_facts.reason)
    try:
        raw = defn.extract(source_facts)
    except (KeyError, TypeError):
        return unavailable(f"{defn.source} response did not include this value")
    if raw is None:
        return unavailable(f"{defn.source} returned no value")
    try:
        rule = defn.rule(raw)
    except ValueError as exc:
        return unavailable(str(exc), raw)
    return FactorScore(defn.key, defn.label, defn.source, defn.max_points, True,
                       raw, rule.derived, rule.points, rule.explanation)


def band(score: float) -> str:
    if score >= PURSUE_AT_OR_ABOVE:
        return "pursue"
    if score < REJECT_BELOW:
        return "reject"
    return "review"


def score_all(facts_by_source: dict[str, dict[str, Any] | Unavailable]) -> ScoreCard:
    """facts_by_source maps a source name to its parsed facts or Unavailable.

    A source missing from the dict entirely is treated as Unavailable too.
    """
    factors = [
        score_factor(d, facts_by_source.get(d.source, Unavailable(f"{d.source} was not queried")))
        for d in FACTORS
    ]
    available = [f for f in factors if f.available]
    missing = [f for f in factors if not f.available]
    earned = sum(f.points for f in available)
    points_available = sum(f.max_points for f in available)
    possible = POINTS_POSSIBLE

    # Knock-out rule: a high-risk flood zone rejects regardless of the rest.
    flood = next(f for f in factors if f.factor == "flood_zone")
    knockout = None
    if flood.available and flood.points == 0 and flood.derived_value != "Open water":
        knockout = "Location is in a FEMA high-risk flood zone."

    score = None
    if points_available / possible >= MIN_COVERAGE_FOR_SCORE:
        score = round(earned / points_available * 100)

    # Bounds on the score had every missing factor come back worst / best.
    worst = earned / possible * 100
    best = (earned + sum(f.max_points for f in missing)) / possible * 100
    missing_names = ", ".join(f.label for f in missing)

    if knockout:
        verdict, reason = "reject", knockout
    elif score is None:
        verdict = "review"
        reason = (f"Not enough data to score: only {points_available:g} of {possible:g} "
                  f"points could be assessed (missing: {missing_names}).")
    elif not missing:
        verdict = band(score)
        reason = f"Score {score} with all factors available."
    elif band(worst) == band(best):
        verdict = band(worst)
        reason = (f"Score {score} on partial data, but the verdict holds whatever "
                  f"{missing_names} turns out to be (possible range {worst:.0f}-{best:.0f}).")
    else:
        verdict = "review"
        reason = (f"Partial data: score {score} on available factors, but the full score "
                  f"could be anywhere from {worst:.0f} to {best:.0f} depending on "
                  f"{missing_names}. Needs an analyst.")

    return ScoreCard(factors, score, earned, points_available, possible, verdict, reason, knockout)
