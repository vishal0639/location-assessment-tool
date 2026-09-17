import pytest

from app import scoring
from app.scoring import Unavailable, score_all

FEMA_X = {"zone": "X", "zone_subtype": "AREA OF MINIMAL FLOOD HAZARD", "sfha": False}
IDEAL = {
    "fema_nfhl": FEMA_X,
    "usgs_epqs": {"elevation_ft": 600},
    "open_meteo": {"hot_days": 3, "annual_precip_mm": 900},
}


def factor(card, key):
    return next(f for f in card.factors if f.factor == key)


def test_weights_sum_to_100():
    assert scoring.POINTS_POSSIBLE == 100


def test_all_factors_available_scores_complete():
    card = score_all(IDEAL)
    assert card.score == 100
    assert card.verdict == "pursue"
    assert not card.is_partial


def test_missing_factor_is_none_not_zero_and_leaves_the_denominator():
    card = score_all({**IDEAL, "fema_nfhl": Unavailable("fema_nfhl timeout: no response")})

    flood = factor(card, "flood_zone")
    assert flood.available is False
    assert flood.points is None  # the whole point: not 0
    assert "timeout" in flood.explanation
    assert card.is_partial
    assert card.points_available == 65
    # 65/65 on what we know, rather than 65/100 as if flood had scored zero.
    assert card.score == 100


def test_partial_data_cannot_auto_pursue_when_missing_factor_could_change_it():
    # Known factors perfect (65 pts). Missing flood could land total at 65 or 100.
    card = score_all({**IDEAL, "fema_nfhl": Unavailable("down")})
    assert card.verdict == "review"
    assert "65 to 100" in card.verdict_reason


def test_partial_data_still_commits_when_verdict_is_certain():
    # Known factors earn 0 of 65; even a perfect flood score gives 35 -> reject.
    card = score_all({
        "fema_nfhl": Unavailable("down"),
        "usgs_epqs": {"elevation_ft": 2},
        "open_meteo": {"hot_days": 120, "annual_precip_mm": 50},
    })
    assert card.verdict == "reject"
    assert card.is_partial


def test_too_little_data_gives_no_score_at_all():
    card = score_all({
        "fema_nfhl": Unavailable("down"),
        "usgs_epqs": Unavailable("down"),
        "open_meteo": {"hot_days": 3, "annual_precip_mm": 900},  # 40 of 100 pts
    })
    assert card.score is None
    assert card.verdict == "review"
    assert "Not enough data" in card.verdict_reason


def test_source_absent_from_input_counts_as_unavailable():
    card = score_all({"usgs_epqs": {"elevation_ft": 600}})
    assert factor(card, "flood_zone").points is None
    assert card.score is None


def test_high_risk_flood_zone_knocks_out_regardless_of_other_factors():
    card = score_all({**IDEAL, "fema_nfhl": {"zone": "AE", "zone_subtype": None, "sfha": True}})
    assert card.score == 65
    assert card.verdict == "reject"
    assert card.knockout


def test_unrecognised_flood_code_is_unavailable_not_guessed():
    card = score_all({**IDEAL, "fema_nfhl": {"zone": "Q9", "zone_subtype": None, "sfha": False}})
    flood = factor(card, "flood_zone")
    assert flood.points is None
    assert flood.raw_value["zone"] == "Q9"  # raw value kept for the analyst


def test_one_source_feeding_two_factors_fails_both():
    card = score_all({**IDEAL, "open_meteo": Unavailable("rate_limited")})
    assert factor(card, "hot_days").points is None
    assert factor(card, "precipitation").points is None
    assert factor(card, "elevation").points == 25


@pytest.mark.parametrize("ft,points", [
    (-5, 0), (9.9, 0), (10, 12), (49.9, 12), (50, 25), (6999, 25), (7000, 10),
])
def test_elevation_bands(ft, points):
    assert scoring.rule_elevation(ft).points == points


@pytest.mark.parametrize("days,points", [(0, 25), (7, 25), (8, 15), (30, 15), (31, 5), (60, 5), (61, 0)])
def test_hot_day_bands(days, points):
    assert scoring.rule_hot_days(days).points == points


@pytest.mark.parametrize("mm,points", [
    (149, 0), (150, 7), (299, 7), (300, 15), (1500, 15), (1501, 7), (2500, 7), (2501, 0),
])
def test_precipitation_bands(mm, points):
    assert scoring.rule_precipitation(mm).points == points


@pytest.mark.parametrize("score,verdict", [(70, "pursue"), (69, "review"), (40, "review"), (39, "reject")])
def test_verdict_thresholds(score, verdict):
    assert scoring.band(score) == verdict
