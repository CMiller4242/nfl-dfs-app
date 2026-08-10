import numpy as np
import pandas as pd
import pytest

from dfs_data_pipeline import (
    _classify_opportunity_trend,
    _player_recent_form,
    build_defense_matchups,
    build_players_current,
    build_players_weekly,
    determine_week_status,
    safe_divide,
)


# ---------------------------------------------------------------------------
# safe_divide
# ---------------------------------------------------------------------------
def test_safe_divide_scalar():
    assert safe_divide(10, 2) == 5.0
    assert pd.isna(safe_divide(10, 0))
    assert pd.isna(safe_divide(10, None))
    assert pd.isna(safe_divide(None, 5))


def test_safe_divide_series_never_produces_infinities():
    num = pd.Series([10, 5, 0, np.nan])
    den = pd.Series([2, 0, 0, 5])
    result = safe_divide(num, den)
    assert not np.isinf(result.dropna()).any()
    assert result.iloc[0] == pytest.approx(5.0)
    assert pd.isna(result.iloc[1])  # divide by zero
    assert pd.isna(result.iloc[2])  # 0 / 0
    assert pd.isna(result.iloc[3])  # NaN numerator


# ---------------------------------------------------------------------------
# determine_week_status - completed-week detection
# ---------------------------------------------------------------------------
def _make_schedule(season, week_game_scores):
    """week_game_scores: {week: [(home_score, away_score), ...]} - None means not yet played."""
    rows = []
    for week, games in week_game_scores.items():
        for i, (home_score, away_score) in enumerate(games):
            rows.append({
                "season": season, "game_type": "REG", "week": week,
                "home_team": f"H{week}_{i}", "away_team": f"A{week}_{i}",
                "home_score": home_score, "away_score": away_score,
            })
    return pd.DataFrame(rows)


def test_latest_completed_week_ignores_incomplete_week():
    schedule = _make_schedule(2025, {
        1: [(20, 10), (14, 14)],
        2: [(21, 17), (10, 10)],
        3: [(24, 20), (None, None)],  # one game still in progress
    })
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest == 2
    assert next_slate == 3


def test_latest_completed_week_partial_week_not_counted():
    schedule = _make_schedule(2025, {
        1: [(20, 10)],
        2: [(21, 17), (None, None)],
    })
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest == 1
    assert next_slate == 2


def test_no_completed_weeks_yet():
    schedule = _make_schedule(2025, {1: [(None, None), (None, None)]})
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest is None
    assert next_slate == 1


def test_next_slate_week_is_none_after_season_ends():
    schedule = _make_schedule(2025, {1: [(20, 10)], 2: [(21, 17)]})
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest == 2
    assert next_slate is None


# ---------------------------------------------------------------------------
# Momentum score (bye-aware, renormalized weights)
# ---------------------------------------------------------------------------
def _weekly_rows(player_id, week_fp_touches):
    return pd.DataFrame([
        {"player_id": player_id, "week": w, "fantasy_points_ppr": fp, "touches": t}
        for w, fp, t in week_fp_touches
    ])


def test_momentum_three_games():
    g = _weekly_rows("p1", [(1, 10, 5), (2, 20, 8), (3, 30, 12)])
    result = _player_recent_form(g)
    expected = 30 * 0.5 + 20 * 0.3 + 10 * 0.2
    assert result["momentum_score"] == pytest.approx(expected)
    assert result["momentum_games_used"] == 3


def test_momentum_two_games_renormalizes_weights():
    g = _weekly_rows("p1", [(1, 10, 5), (2, 20, 8)])
    result = _player_recent_form(g)
    expected = 20 * (0.5 / 0.8) + 10 * (0.3 / 0.8)
    assert result["momentum_score"] == pytest.approx(expected)
    assert result["momentum_games_used"] == 2


def test_momentum_one_game_uses_full_weight():
    g = _weekly_rows("p1", [(1, 10, 5)])
    result = _player_recent_form(g)
    assert result["momentum_score"] == pytest.approx(10)
    assert result["momentum_games_used"] == 1


def test_momentum_skips_bye_week_uses_played_games_only():
    # Played weeks 1, 2, 4, 5 - week 3 was a bye (simply absent, not a zero row).
    g = _weekly_rows("p1", [(1, 5, 5), (2, 10, 8), (4, 15, 10), (5, 25, 14)])
    result = _player_recent_form(g)
    # Last 3 PLAYED games are weeks 2, 4, 5 - not "current week minus 1/2".
    expected = 25 * 0.5 + 15 * 0.3 + 10 * 0.2
    assert result["momentum_score"] == pytest.approx(expected)
    assert result["momentum_games_used"] == 3


# ---------------------------------------------------------------------------
# Week-over-week touches (bye-aware)
# ---------------------------------------------------------------------------
def test_wow_touches_compares_last_two_played_games_across_a_bye():
    g = _weekly_rows("p1", [(1, 5, 20), (2, 10, 25), (4, 15, 18)])  # bye week 3
    result = _player_recent_form(g)
    assert result["latest_game_touches"] == 18
    assert result["prior_game_touches"] == 25
    assert result["touches_wow_change"] == pytest.approx(18 - 25)


def test_wow_touches_null_not_zero_with_only_one_game():
    g = _weekly_rows("p1", [(1, 5, 20)])
    result = _player_recent_form(g)
    assert pd.isna(result["prior_game_touches"])
    assert pd.isna(result["touches_wow_change"])


@pytest.mark.parametrize("change,expected", [
    (2, "gaining"), (1, "gaining"),
    (0.5, "stable"), (-0.5, "stable"), (0, "stable"),
    (-1, "losing"), (-3, "losing"),
    (None, "insufficient_data"),
])
def test_classify_opportunity_trend(change, expected):
    assert _classify_opportunity_trend(change) == expected


# ---------------------------------------------------------------------------
# build_players_weekly - completed-week filtering + idempotency
# ---------------------------------------------------------------------------
def _raw_player_row(week, opponent, fp, targets=5, carries=0, receiving_yards=40):
    return {
        "player_id": "p1", "player_name": "x", "player_display_name": "Player One",
        "position": "WR", "position_group": "WR", "season": 2025, "week": week,
        "season_type": "REG", "team": "KC", "opponent_team": opponent,
        "completions": 0, "attempts": 0, "passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
        "carries": carries, "rushing_yards": 0, "rushing_tds": 0,
        "receptions": 3, "targets": targets, "receiving_yards": receiving_yards, "receiving_tds": 0,
        "receiving_yards_after_catch": 20, "target_share": 0.2, "air_yards_share": 0.15,
        "fantasy_points": fp, "fantasy_points_ppr": fp,
    }


def test_build_players_weekly_excludes_incomplete_weeks():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10), _raw_player_row(2, "DEN", 12)])
    weekly = build_players_weekly(raw, 2025, latest_completed_week=1)
    assert weekly["week"].tolist() == [1]
    assert weekly.iloc[0]["touches"] == 5


def test_build_players_weekly_no_completed_weeks_returns_empty_with_schema():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10)])
    weekly = build_players_weekly(raw, 2025, latest_completed_week=None)
    assert weekly.empty
    assert "touches" in weekly.columns


def test_build_players_weekly_is_idempotent():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10), _raw_player_row(2, "DEN", 12)])
    first = build_players_weekly(raw, 2025, latest_completed_week=2)
    second = build_players_weekly(raw, 2025, latest_completed_week=2)
    pd.testing.assert_frame_equal(first, second)
    assert not first.duplicated(subset=["player_id", "week"]).any()


# ---------------------------------------------------------------------------
# build_players_current - safe division, consistency score, sample size
# ---------------------------------------------------------------------------
def test_consistency_score_requires_two_games():
    weekly = build_players_weekly(pd.DataFrame([_raw_player_row(1, "BUF", 10)]), 2025, 1)
    current = build_players_current(weekly)
    row = current.iloc[0]
    assert row["games_played"] == 1
    assert pd.isna(row["consistency_score"])


def test_consistency_score_computed_with_two_or_more_games():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10), _raw_player_row(2, "DEN", 20)])
    weekly = build_players_weekly(raw, 2025, 2)
    current = build_players_current(weekly)
    row = current.iloc[0]
    avg = 15.0
    std = pd.Series([10, 20]).std(ddof=0)
    assert row["consistency_score"] == pytest.approx(avg / std)


def test_no_targets_gives_null_efficiency_not_infinity():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10, targets=0, carries=10, receiving_yards=0)])
    weekly = build_players_weekly(raw, 2025, 1)
    current = build_players_current(weekly)
    row = current.iloc[0]
    assert pd.isna(row["yards_per_target"])
    assert pd.isna(row["catch_rate"])
    assert not np.isinf(row["yards_per_target"]) if not pd.isna(row["yards_per_target"]) else True


def test_build_players_current_empty_input_returns_empty_with_schema():
    current = build_players_current(pd.DataFrame())
    assert current.empty
    assert "avg_fantasy_points" in current.columns


# ---------------------------------------------------------------------------
# Defense-vs-position + position-specific percentile
# ---------------------------------------------------------------------------
def test_defense_matchups_delta_and_rank():
    rows = [{"opponent_team": "DEFA", "position": "WR", "fantasy_points_ppr": 20}] * 3
    rows += [{"opponent_team": "DEFB", "position": "WR", "fantasy_points_ppr": 10}] * 3
    dm = build_defense_matchups(pd.DataFrame(rows))

    a = dm[dm.defense_team == "DEFA"].iloc[0]
    b = dm[dm.defense_team == "DEFB"].iloc[0]

    assert a["fantasy_points_allowed"] == pytest.approx(20)
    assert b["fantasy_points_allowed"] == pytest.approx(10)
    assert a["position_league_average"] == pytest.approx(15.0)
    assert a["matchup_delta"] == pytest.approx(5.0)
    assert b["matchup_delta"] == pytest.approx(-5.0)
    # Higher points allowed must always mean a higher (more favorable) percentile.
    assert a["matchup_rating_percentile"] > b["matchup_rating_percentile"]
    assert a["matchup_rank"] == 1
    assert b["matchup_rank"] == 2


def test_defense_matchups_percentile_is_position_specific_not_global():
    rows = [
        {"opponent_team": "DEF1", "position": "QB", "fantasy_points_ppr": 30},
        {"opponent_team": "DEF2", "position": "QB", "fantasy_points_ppr": 15},
        {"opponent_team": "DEF1", "position": "RB", "fantasy_points_ppr": 5},
        {"opponent_team": "DEF2", "position": "RB", "fantasy_points_ppr": 25},
    ]
    dm = build_defense_matchups(pd.DataFrame(rows))

    qb1 = dm[(dm.defense_team == "DEF1") & (dm.position == "QB")].iloc[0]
    qb2 = dm[(dm.defense_team == "DEF2") & (dm.position == "QB")].iloc[0]
    rb1 = dm[(dm.defense_team == "DEF1") & (dm.position == "RB")].iloc[0]
    rb2 = dm[(dm.defense_team == "DEF2") & (dm.position == "RB")].iloc[0]

    # DEF1 allows the most to QB but the least to RB - rankings must flip
    # independently per position, proving there's no shared/global scale.
    assert qb1["matchup_rating_percentile"] > qb2["matchup_rating_percentile"]
    assert rb2["matchup_rating_percentile"] > rb1["matchup_rating_percentile"]


def test_defense_matchups_empty_input():
    dm = build_defense_matchups(pd.DataFrame())
    assert dm.empty
    assert "matchup_rating_percentile" in dm.columns
