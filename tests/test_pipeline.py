import os

import numpy as np
import pandas as pd
import pytest

from datetime import datetime

from dfs_data_pipeline import (
    _classify_opportunity_trend,
    _player_recent_form,
    build_defense_reporting,
    build_depth_chart_snapshot,
    build_players_current,
    build_players_weekly,
    build_prior_season_baseline,
    build_team_summary,
    determine_active_season,
    determine_app_mode,
    determine_week_status,
    safe_divide,
    write_snapshot_with_fallback,
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
# Defense-vs-position - see tests/test_defense_reporting.py for the full
# suite (build_defense_reporting / build_defense_position_weekly). This
# empty-input check stays here since it's exercised alongside the other
# builders in the season-lifecycle tests below.
# ---------------------------------------------------------------------------
def test_defense_reporting_empty_input():
    dm = build_defense_reporting(pd.DataFrame(), 2025, "in_season")
    assert dm.empty
    assert "position_percentile_most_favorable" in dm.columns


# ---------------------------------------------------------------------------
# Season-lifecycle edge cases: before Week 1, mid-Week-1, after the season ends
# ---------------------------------------------------------------------------
def test_before_week1_no_games_played_yet():
    # Full slate scheduled, nothing played - preseason/week-1-not-yet-kicked-off.
    schedule = _make_schedule(2025, {1: [(None, None), (None, None), (None, None)]})
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest is None
    assert next_slate == 1

    weekly = build_players_weekly(pd.DataFrame([_raw_player_row(1, "BUF", 10)]), 2025, latest)
    current = build_players_current(weekly)
    defense = build_defense_reporting(weekly, 2025, "in_season")
    assert weekly.empty and current.empty and defense.empty


def test_during_week1_partial_slate_is_not_completed():
    # Thursday night game final, Sunday/Monday games not yet played.
    schedule = _make_schedule(2025, {1: [(24, 17), (None, None), (None, None)]})
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest is None  # week 1 itself isn't done just because one game is
    assert next_slate == 1


def test_after_regular_season_ends_next_slate_is_none():
    schedule = _make_schedule(2025, {w: [(21, 14)] for w in range(1, 19)})
    latest, next_slate = determine_week_status(schedule, 2025)
    assert latest == 18
    assert next_slate is None
    # Metadata built from this state must clearly say "no more slates."
    assert next_slate is None and latest is not None


def test_players_weekly_duplicate_source_rows_collapse_to_one_and_assertion_holds():
    raw = pd.DataFrame([
        _raw_player_row(1, "BUF", 10),
        _raw_player_row(1, "BUF", 999),  # corrupted/duplicate re-publish for the same player+week
    ])
    weekly = build_players_weekly(raw, 2025, latest_completed_week=1)
    assert len(weekly) == 1
    assert not weekly.duplicated(subset=["player_id", "week"]).any()


# ---------------------------------------------------------------------------
# build_team_summary - completed weeks only, safe division, own-defense semantics
# ---------------------------------------------------------------------------
def _raw_team_row(team, week, passing_yards=200, rushing_yards=100, attempts=30, carries=25,
                   def_sacks=2, def_interceptions=1, def_fumbles_forced=1, def_qb_hits=3, opponent="OPP"):
    return {
        "season": 2025, "week": week, "team": team, "season_type": "REG", "opponent_team": opponent,
        "attempts": attempts, "passing_yards": passing_yards, "carries": carries, "rushing_yards": rushing_yards,
        "def_sacks": def_sacks, "def_interceptions": def_interceptions, "def_fumbles_forced": def_fumbles_forced,
        "def_qb_hits": def_qb_hits,
    }


def test_team_summary_before_any_completed_week_is_empty_with_schema():
    raw = pd.DataFrame([_raw_team_row("KC", 1)])
    summary = build_team_summary(raw, 2025, latest_completed_week=None)
    assert summary.empty
    assert "sacks_per_game" in summary.columns


def test_team_summary_computes_own_defense_production_and_pass_rate():
    raw = pd.DataFrame([
        _raw_team_row("KC", 1, passing_yards=300, rushing_yards=100, attempts=40, carries=20,
                       def_sacks=3, def_interceptions=2, def_fumbles_forced=0),
        _raw_team_row("KC", 2, passing_yards=200, rushing_yards=140, attempts=20, carries=20,
                       def_sacks=1, def_interceptions=0, def_fumbles_forced=1),
    ])
    summary = build_team_summary(raw, 2025, latest_completed_week=2)
    row = summary[summary.team == "KC"].iloc[0]

    assert row["games_played"] == 2
    assert row["pass_yards_per_game"] == pytest.approx((300 + 200) / 2)
    assert row["rush_yards_per_game"] == pytest.approx((100 + 140) / 2)
    assert row["pass_rate_pct"] == pytest.approx((40 + 20) / (40 + 20 + 20 + 20) * 100)
    # sacks_per_game/turnovers_forced_per_game are THIS team's own defense
    # (def_sacks / def_interceptions / def_fumbles_forced), not points allowed.
    assert row["sacks_per_game"] == pytest.approx((3 + 1) / 2)
    assert row["turnovers_forced_per_game"] == pytest.approx((2 + 0 + 0 + 1) / 2)
    assert "points_allowed" not in summary.columns


def test_team_summary_excludes_future_weeks():
    raw = pd.DataFrame([_raw_team_row("KC", 1), _raw_team_row("KC", 2)])
    summary = build_team_summary(raw, 2025, latest_completed_week=1)
    assert summary[summary.team == "KC"].iloc[0]["games_played"] == 1


def test_defensive_pressure_events_per_game_is_sacks_plus_qb_hits_over_games():
    raw = pd.DataFrame([
        _raw_team_row("KC", 1, def_sacks=3, def_qb_hits=5),
        _raw_team_row("KC", 2, def_sacks=1, def_qb_hits=7),
    ])
    summary = build_team_summary(raw, 2025, latest_completed_week=2)
    row = summary[summary.team == "KC"].iloc[0]
    # Old Power BI measure called this a "rate"; it's an events-PER-GAME
    # count (sacks + qb_hits, divided by games played), never a per-play rate.
    assert row["defensive_pressure_events_per_game"] == pytest.approx((3 + 5 + 1 + 7) / 2)


def test_defensive_pressure_events_per_game_omitted_gracefully_without_source_column():
    raw = pd.DataFrame([
        {"season": 2025, "week": 1, "team": "KC", "season_type": "REG", "opponent_team": "OPP",
         "attempts": 30, "passing_yards": 200, "carries": 25, "rushing_yards": 100,
         "def_sacks": 2, "def_interceptions": 1, "def_fumbles_forced": 1},  # no def_qb_hits
    ])
    summary = build_team_summary(raw, 2025, latest_completed_week=1)
    row = summary[summary.team == "KC"].iloc[0]
    assert pd.isna(row["defensive_pressure_events_per_game"])


# ---------------------------------------------------------------------------
# Week 1 / preseason baseline mode: active season, app_mode selection
# ---------------------------------------------------------------------------
def test_determine_active_season_before_march_is_prior_calendar_year():
    assert determine_active_season(datetime(2026, 1, 15)) == 2025


def test_determine_active_season_march_or_later_is_current_calendar_year():
    assert determine_active_season(datetime(2026, 8, 10)) == 2026
    assert determine_active_season(datetime(2026, 3, 1)) == 2026


def test_app_mode_before_week1_is_preseason_baseline():
    app_mode, source_season = determine_app_mode(active_season=2026, latest_completed_week=None)
    assert app_mode == "preseason_week_1_baseline"
    assert source_season == 2025  # immediately preceding season


def test_app_mode_after_week1_completes_is_in_season():
    app_mode, source_season = determine_app_mode(active_season=2026, latest_completed_week=1)
    assert app_mode == "in_season"
    assert source_season == 2026  # same season, not prior


def test_app_mode_mid_season_stays_in_season():
    app_mode, source_season = determine_app_mode(active_season=2026, latest_completed_week=9)
    assert app_mode == "in_season"
    assert source_season == 2026


# ---------------------------------------------------------------------------
# build_prior_season_baseline
# ---------------------------------------------------------------------------
def test_prior_season_baseline_has_no_momentum_or_wow_fields():
    raw = pd.DataFrame([_raw_player_row(1, "BUF", 10), _raw_player_row(2, "DEN", 20)])
    weekly = build_players_weekly(raw, 2025, latest_completed_week=2)
    baseline = build_prior_season_baseline(weekly)

    for forbidden in ["momentum_score", "momentum_games_used", "touches_wow_change",
                       "opportunity_trend", "latest_game_touches", "prior_game_touches"]:
        assert forbidden not in baseline.columns

    row = baseline.iloc[0]
    assert row["historical_team"] == "KC"
    assert row["games_played"] == 2
    assert row["avg_fantasy_points"] == pytest.approx(15.0)


def test_prior_season_baseline_empty_input_has_schema():
    baseline = build_prior_season_baseline(pd.DataFrame())
    assert baseline.empty
    assert "historical_team" in baseline.columns
    assert "avg_fantasy_points" in baseline.columns
    assert "momentum_score" not in baseline.columns


# ---------------------------------------------------------------------------
# build_depth_chart_snapshot
# ---------------------------------------------------------------------------
def _raw_depth_chart_row(gsis_id, espn_id, name, team, pos_abb, pos_rank, dt="2026-09-01T00:00:00Z"):
    return {
        "dt": dt, "team": team, "player_name": name, "espn_id": espn_id, "gsis_id": gsis_id,
        "pos_grp_id": 1, "pos_grp": "Offense", "pos_id": 1, "pos_name": pos_abb,
        "pos_abb": pos_abb, "pos_slot": pos_rank, "pos_rank": pos_rank,
    }


def test_build_depth_chart_snapshot_stamps_season_and_week():
    raw = pd.DataFrame([_raw_depth_chart_row("00-1", "1", "Some Guy", "KC", "QB", 1)])
    snapshot = build_depth_chart_snapshot(raw, season=2026, week=1)
    assert len(snapshot) == 1
    assert snapshot.iloc[0]["season"] == 2026
    assert snapshot.iloc[0]["week"] == 1
    assert snapshot.iloc[0]["player_id"] == "00-1"


def test_build_depth_chart_snapshot_empty_input_has_schema():
    snapshot = build_depth_chart_snapshot(pd.DataFrame(), season=2026, week=1)
    assert snapshot.empty
    assert "season" in snapshot.columns
    assert "week" in snapshot.columns
    assert "player_id" in snapshot.columns


# ---------------------------------------------------------------------------
# write_snapshot_with_fallback - never overwrite last-known-good data with
# an empty/failed refresh
# ---------------------------------------------------------------------------
def test_write_snapshot_with_fallback_writes_valid_data(tmp_path):
    path = str(tmp_path / "snapshot.parquet")
    df = pd.DataFrame([{"a": 1}])
    written_fresh = write_snapshot_with_fallback(df, path, label="test")
    assert written_fresh is True
    assert pd.read_parquet(path)["a"].tolist() == [1]


def test_write_snapshot_with_fallback_writes_empty_frame_when_nothing_existed_before(tmp_path):
    path = str(tmp_path / "snapshot.parquet")
    df = pd.DataFrame(columns=["a"])
    written_fresh = write_snapshot_with_fallback(df, path, label="test")
    assert written_fresh is True
    assert os.path.exists(path)
    assert pd.read_parquet(path).empty


def test_write_snapshot_with_fallback_preserves_prior_good_file_on_empty_refresh(tmp_path):
    path = str(tmp_path / "snapshot.parquet")
    good_df = pd.DataFrame([{"a": 1}, {"a": 2}])
    write_snapshot_with_fallback(good_df, path, label="test")

    empty_df = pd.DataFrame(columns=["a"])
    written_fresh = write_snapshot_with_fallback(empty_df, path, label="test")

    assert written_fresh is False
    # The original good data must still be there, untouched.
    assert pd.read_parquet(path)["a"].tolist() == [1, 2]


def test_write_snapshot_with_fallback_preserves_prior_good_file_on_none(tmp_path):
    path = str(tmp_path / "snapshot.parquet")
    good_df = pd.DataFrame([{"a": 1}])
    write_snapshot_with_fallback(good_df, path, label="test")

    written_fresh = write_snapshot_with_fallback(None, path, label="test")

    assert written_fresh is False
    assert pd.read_parquet(path)["a"].tolist() == [1]
