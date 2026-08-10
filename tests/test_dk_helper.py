import pandas as pd
import pytest

from lib.dk_helper import (
    FUZZY_MATCH_THRESHOLD,
    MATCHUP_ADJUSTMENT_WEIGHT,
    MOMENTUM_ADJUSTMENT_WEIGHT,
    best_value_by_position,
    compute_projections,
    match_dk_players,
    needs_review,
    parse_opponent,
)


# ---------------------------------------------------------------------------
# Game Info / opponent parsing
# ---------------------------------------------------------------------------
def test_parse_opponent_home_player():
    assert parse_opponent("LAC@KC 10/19/2025 04:05PM ET", "KC") == "LAC"


def test_parse_opponent_away_player():
    assert parse_opponent("LAC@KC 10/19/2025 04:05PM ET", "LAC") == "KC"


def test_parse_opponent_dk_alias_on_opponent_side():
    # DK's Game Info uses JAC; the player's own (canonical) team is JAX.
    assert parse_opponent("JAC@HOU 10/19/2025 01:00PM ET", "JAX") == "HOU"


def test_parse_opponent_dk_alias_on_own_team_side():
    assert parse_opponent("LAR@SF 09/07/2026 04:25PM ET", "LA") == "SF"


@pytest.mark.parametrize("game_info", [None, float("nan"), "", "Postponed", "no-at-symbol-here", "KC 10/19 ET"])
def test_parse_opponent_malformed_or_missing_never_crashes(game_info):
    assert parse_opponent(game_info, "KC") == ""


def test_parse_opponent_team_not_in_matchup_returns_empty():
    assert parse_opponent("LAC@KC 10/19/2025 04:05PM ET", "BUF") == ""


# ---------------------------------------------------------------------------
# Player matching
# ---------------------------------------------------------------------------
def _stats_df():
    return pd.DataFrame([
        {
            "player_id": "1", "player_display_name": "Puka Nacua", "team": "LA", "position": "WR",
            "last_opponent": "SF", "avg_fantasy_points": 18.0, "momentum_score": 20.0,
            "momentum_games_used": 3, "games_played": 10, "total_touches": 100,
            "touches_wow_change": 1, "opportunity_trend": "gaining", "points_per_touch": 1.1,
            "yards_per_target": 9.0, "yards_per_carry": None, "catch_rate": 0.7, "consistency_score": 1.4,
        },
        {
            "player_id": "2", "player_display_name": "CeeDee Lamb", "team": "DAL", "position": "WR",
            "last_opponent": "PHI", "avg_fantasy_points": 16.0, "momentum_score": 14.0,
            "momentum_games_used": 3, "games_played": 10, "total_touches": 90,
            "touches_wow_change": -1, "opportunity_trend": "stable", "points_per_touch": 1.0,
            "yards_per_target": 8.0, "yards_per_carry": None, "catch_rate": 0.6, "consistency_score": 1.1,
        },
    ])


def test_exact_name_team_position_match():
    dk = pd.DataFrame([{"Position": "WR", "Name": "Puka Nacua", "Salary": 8000, "TeamAbbrev": "LAR"}])
    result = match_dk_players(dk, _stats_df())
    row = result.iloc[0]
    assert row["match_method"] == "exact_name_team_position"
    assert row["match_score"] == 100.0
    assert row["matched_player_name"] == "Puka Nacua"


def test_fuzzy_match_restricted_to_team_and_position():
    dk = pd.DataFrame([{"Position": "WR", "Name": "Puka Nacau", "Salary": 8000, "TeamAbbrev": "LAR"}])  # letter swap
    result = match_dk_players(dk, _stats_df())
    row = result.iloc[0]
    assert row["match_method"] == "fuzzy_team_position"
    assert row["match_score"] >= FUZZY_MATCH_THRESHOLD
    assert row["matched_player_name"] == "Puka Nacua"


def test_low_confidence_is_unmatched_for_review_not_guessed():
    dk = pd.DataFrame([{"Position": "WR", "Name": "Someone Else Entirely", "Salary": 8000, "TeamAbbrev": "LAR"}])
    result = match_dk_players(dk, _stats_df())
    row = result.iloc[0]
    assert row["match_method"] == "unmatched"
    assert row["matched_player_name"] is None


def test_low_confidence_still_records_best_candidate_for_review():
    # A near-miss within the same team+position that scores below the
    # threshold (82.35 for this pair - verified below FUZZY_MATCH_THRESHOLD=88)
    # should still surface *which* player it almost matched, even though
    # it's correctly rejected as a confirmed match.
    dk = pd.DataFrame([{"Position": "WR", "Name": "Puk Nac", "Salary": 8000, "TeamAbbrev": "LAR"}])
    result = match_dk_players(dk, _stats_df())
    row = result.iloc[0]
    assert row["match_score"] < FUZZY_MATCH_THRESHOLD
    assert row["match_method"] == "unmatched"
    assert row["matched_player_name"] is None
    assert row["best_candidate_name"] == "Puka Nacua"


def test_never_falls_back_to_unrestricted_name_only_match():
    # Same name as a real player, but wrong team AND wrong position -
    # must NOT match even though the name alone would be a perfect fuzzy hit.
    dk = pd.DataFrame([{"Position": "RB", "Name": "Puka Nacua", "Salary": 8000, "TeamAbbrev": "KC"}])
    result = match_dk_players(dk, _stats_df())
    row = result.iloc[0]
    assert row["match_method"] == "unmatched"
    assert row["matched_player_name"] is None


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------
def _matched_row(**overrides):
    base = {
        "Name": "X", "Position": "WR", "TeamAbbrev": "LAR", "Salary": 8000,
        "Game Info": "LAR@SF 09/07/2026 04:25PM ET", "AvgPointsPerGame": 15.0,
        "match_method": "exact_name_team_position", "match_score": 100.0, "matched_player_name": "X",
        "stat_avg_fantasy_points": 18.0, "stat_momentum_score": 20.0,
    }
    base.update(overrides)
    return pd.DataFrame([base])


def test_projected_points_matches_documented_formula():
    defense = pd.DataFrame([{"defense_team": "SF", "position": "WR", "matchup_delta": 1.5}])
    proj = compute_projections(_matched_row(), defense)
    row = proj.iloc[0]

    expected_points = 18.0 + (20.0 - 18.0) * MOMENTUM_ADJUSTMENT_WEIGHT + 1.5 * MATCHUP_ADJUSTMENT_WEIGHT
    assert row["projected_points"] == pytest.approx(expected_points, abs=0.01)
    assert row["projected_value"] == pytest.approx(expected_points / (8000 / 1000), abs=0.01)
    assert row["projection_status"] == "ok"


def test_projection_missing_salary_is_labeled_and_not_computed():
    defense = pd.DataFrame([{"defense_team": "SF", "position": "WR", "matchup_delta": 1.5}])
    proj = compute_projections(_matched_row(Salary=0), defense)
    row = proj.iloc[0]
    assert pd.isna(row["projected_points"])
    assert pd.isna(row["projected_value"])
    assert row["projection_status"] == "no_salary"


def test_projection_unmatched_gets_no_fallback_projection():
    # No DK-average fallback of any kind - an unmatched/low-confidence row
    # gets a null projection, full stop.
    defense = pd.DataFrame([{"defense_team": "SF", "position": "WR", "matchup_delta": 1.5}])
    unmatched = _matched_row(
        match_method="unmatched", match_score=40.0, matched_player_name=None,
        stat_avg_fantasy_points=pd.NA, stat_momentum_score=pd.NA, AvgPointsPerGame=12.0,
    )
    proj = compute_projections(unmatched, defense)
    row = proj.iloc[0]
    assert row["projection_status"] == "review_required"
    assert pd.isna(row["projected_points"])
    assert pd.isna(row["projected_value"])
    assert pd.isna(row["player_avg"])
    assert pd.isna(row["momentum_score"])


def test_projection_review_required_takes_priority_over_no_salary():
    # An unmatched row with a missing salary must still read as
    # "review_required" (the match problem), not "no_salary".
    defense = pd.DataFrame([{"defense_team": "SF", "position": "WR", "matchup_delta": 1.5}])
    unmatched = _matched_row(
        match_method="unmatched", match_score=None, matched_player_name=None,
        stat_avg_fantasy_points=pd.NA, stat_momentum_score=pd.NA, Salary=0,
    )
    proj = compute_projections(unmatched, defense)
    assert proj.iloc[0]["projection_status"] == "review_required"


def test_projection_matchup_delta_independent_of_player_match():
    # Even an unmatched player still gets a real matchup_delta, since it's
    # looked up from the DK row's own Position + parsed opponent - a bad
    # name match doesn't have to cost matchup context in the review table.
    defense = pd.DataFrame([{"defense_team": "SF", "position": "WR", "matchup_delta": -3.0}])
    unmatched = _matched_row(match_method="unmatched", matched_player_name=None)
    proj = compute_projections(unmatched, defense)
    assert proj.iloc[0]["matchup_delta"] == pytest.approx(-3.0)


def test_projection_matchup_delta_null_when_unresolvable():
    # No matchup row for (opponent, position) at all -> null, not a
    # "neutral" 0.0 guess.
    defense = pd.DataFrame([{"defense_team": "SOME_OTHER_TEAM", "position": "WR", "matchup_delta": -3.0}])
    proj = compute_projections(_matched_row(), defense)
    assert pd.isna(proj.iloc[0]["matchup_delta"])


def test_projection_still_computed_for_matched_player_despite_unresolvable_matchup():
    # A confidently-matched player with a real salary/average should still
    # get a projection even when matchup_delta can't be resolved - the
    # matchup term just contributes 0, and matchup_delta itself stays null
    # for display rather than blocking the whole projection.
    defense = pd.DataFrame([{"defense_team": "SOME_OTHER_TEAM", "position": "WR", "matchup_delta": -3.0}])
    proj = compute_projections(_matched_row(), defense)
    row = proj.iloc[0]
    assert pd.isna(row["matchup_delta"])
    assert row["projection_status"] == "ok"
    expected_points = 18.0 + (20.0 - 18.0) * MOMENTUM_ADJUSTMENT_WEIGHT  # matchup term = 0
    assert row["projected_points"] == pytest.approx(expected_points, abs=0.01)


# ---------------------------------------------------------------------------
# needs_review()
# ---------------------------------------------------------------------------
def test_needs_review_excludes_only_ok_rows():
    df = pd.DataFrame([
        {"projection_status": "ok"},
        {"projection_status": "review_required"},
        {"projection_status": "no_salary"},
        {"projection_status": "no_player_average"},
    ])
    result = needs_review(df)
    assert set(result["projection_status"]) == {"review_required", "no_salary", "no_player_average"}
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Best-value-per-position filtering
# ---------------------------------------------------------------------------
def test_best_value_filters_position_before_ranking_and_excludes_ineligible_rows():
    df = pd.DataFrame([
        {"Position": "WR", "Name": "A", "projected_value": 5.0, "projection_status": "ok"},
        {"Position": "WR", "Name": "B", "projected_value": 10.0, "projection_status": "no_salary"},
        {"Position": "WR", "Name": "C", "projected_value": 3.0, "projection_status": "ok"},
        {"Position": "RB", "Name": "D", "projected_value": 9.0, "projection_status": "ok"},
        {"Position": "WR", "Name": "E", "projected_value": 8.0, "projection_status": "review_required"},
    ])
    result = best_value_by_position(df, positions=["WR", "RB"], top_n=2)
    assert result["WR"]["Name"].tolist() == ["A", "C"]
    assert result["RB"]["Name"].tolist() == ["D"]


def test_best_value_handles_empty_frame():
    df = pd.DataFrame(columns=["Position", "Name", "projected_value", "projection_status"])
    result = best_value_by_position(df, positions=["WR"], top_n=3)
    assert result["WR"].empty
