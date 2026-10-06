import pandas as pd
import pytest

from dfs_data_pipeline import (
    FANTASY_SCORING_BASIS,
    OFFENSE_DEFENSE_POSITION_RECENT_FORM_GAMES,
    POSITIONS,
    build_defensive_position_points_allowed,
    build_offensive_position_reporting,
    build_team_game_position_totals,
    build_upcoming_schedule,
    detect_unresolved_position_production,
)


def _row(team, opp, pos, week, pid, fp, carries=0.0, targets=0.0, receptions=0.0, season=2026):
    return {
        "season": season, "team": team, "opponent_team": opp, "position": pos, "week": week,
        "player_id": pid, "player_display_name": pid, "fantasy_points_ppr": fp,
        "carries": carries, "targets": targets, "receptions": receptions,
    }


# ---------------------------------------------------------------------------
# Part 1 - multiple players aggregate into one team-position game total
# ---------------------------------------------------------------------------
def test_multiple_rbs_aggregate_into_one_team_position_game_total():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", 10.0, carries=15),
        _row("KC", "BUF", "RB", 1, "p2", 8.0, carries=5),
    ])
    totals = build_team_game_position_totals(weekly, 2026, "team")
    row = totals[(totals.team == "KC") & (totals.position == "RB") & (totals.week == 1)].iloc[0]
    assert row["team_position_fantasy_points"] == pytest.approx(18.0)
    assert row["team_position_carries"] == pytest.approx(20.0)
    assert row["contributing_player_count"] == 2
    assert row["data_status"] == "recorded"


def test_player_totals_reconcile_with_team_position_reporting_mean():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", 10.0),
        _row("KC", "BUF", "RB", 1, "p2", 8.0),
        _row("KC", "BAL", "RB", 2, "p1", 12.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    kc_rb = off[(off.team == "KC") & (off.position == "RB")].iloc[0]
    # (18 + 12) / 2 games == 15.0, exactly the team-game-total mean - never
    # computed as a per-player-appearance average (which would be 10.
    assert kc_rb["season_offense_points_per_team_game"] == pytest.approx(15.0)
    assert kc_rb["season_offense_games_recorded"] == 2


# ---------------------------------------------------------------------------
# Verified zero vs. unavailable (never treat missing as a genuine zero)
# ---------------------------------------------------------------------------
def test_verified_zero_counts_as_a_real_recorded_zero():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "TE", 1, "p1", 0.0, targets=1),  # played, recorded, scored 0
        _row("KC", "BUF", "QB", 1, "p2", 20.0),  # proves the team-week is real
    ])
    totals = build_team_game_position_totals(weekly, 2026, "team")
    te_row = totals[(totals.team == "KC") & (totals.position == "TE") & (totals.week == 1)].iloc[0]
    assert te_row["data_status"] == "recorded"
    assert te_row["team_position_fantasy_points"] == 0.0


def test_unavailable_position_never_treated_as_zero():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "QB", 1, "p1", 20.0),  # team played; no TE row exists this week
    ])
    totals = build_team_game_position_totals(weekly, 2026, "team")
    te_row = totals[(totals.team == "KC") & (totals.position == "TE") & (totals.week == 1)].iloc[0]
    assert te_row["data_status"] == "unavailable"
    assert pd.isna(te_row["team_position_fantasy_points"])

    # And the season summary must never average an unavailable week in as 0.
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    te_summary = off[(off.team == "KC") & (off.position == "TE")]
    if not te_summary.empty:
        assert pd.isna(te_summary.iloc[0]["season_offense_points_per_team_game"])


def test_bye_week_never_counted_as_a_game():
    # KC plays weeks 1 and 3 only (week 2 is a bye - simply absent).
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", 10.0),
        _row("KC", "DEN", "RB", 3, "p1", 14.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    kc_rb = off[(off.team == "KC") & (off.position == "RB")].iloc[0]
    assert kc_rb["season_offense_games_recorded"] == 2  # not 3 - the bye is never a counted game


# ---------------------------------------------------------------------------
# Season/recent windows use the intended completed games
# ---------------------------------------------------------------------------
def test_recent_window_uses_last_n_recorded_games_only():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", w, "p1", 10.0 + w) for w in range(1, 6)
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    kc_rb = off[(off.team == "KC") & (off.position == "RB")].iloc[0]
    assert kc_rb["recent_offense_games_recorded"] == OFFENSE_DEFENSE_POSITION_RECENT_FORM_GAMES
    # weeks 3,4,5 -> points 13,14,15 -> mean 14.0
    assert kc_rb["recent_offense_points_per_team_game"] == pytest.approx(14.0)
    # season uses all 5 weeks -> mean of 11..15 == 13.0
    assert kc_rb["season_offense_points_per_team_game"] == pytest.approx(13.0)


# ---------------------------------------------------------------------------
# Percentile direction, ties, and never across mixed positions
# ---------------------------------------------------------------------------
def test_percentile_direction_higher_production_is_higher_percentile():
    weekly = pd.DataFrame([
        _row("LOW", "X", "WR", 1, "p1", 5.0),
        _row("MID", "X", "WR", 1, "p2", 15.0),
        _row("HIGH", "X", "WR", 1, "p3", 25.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    by_team = off[off.position == "WR"].set_index("team")["season_offense_percentile"]
    assert by_team["LOW"] < by_team["MID"] < by_team["HIGH"]


def test_percentile_ties_share_the_same_value():
    weekly = pd.DataFrame([
        _row("A", "X", "WR", 1, "p1", 10.0),
        _row("B", "X", "WR", 1, "p2", 10.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    pctiles = off[off.position == "WR"]["season_offense_percentile"]
    assert pctiles.nunique() == 1


def test_missing_production_has_no_percentile_not_a_zero_percentile():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "QB", 1, "p1", 20.0),  # KC has no TE row at all
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    te_row = off[(off.team == "KC") & (off.position == "TE")]
    if not te_row.empty:
        assert pd.isna(te_row.iloc[0]["season_offense_percentile"])


def test_percentiles_never_computed_across_mixed_positions():
    # A huge QB total must never affect a tiny-looking RB's percentile -
    # percentiles are computed independently per position.
    weekly = pd.DataFrame([
        _row("KC", "X", "QB", 1, "p1", 300.0),
        _row("KC", "X", "RB", 1, "p2", 5.0),
        _row("DEN", "X", "RB", 1, "p3", 20.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    kc_rb_pct = off[(off.team == "KC") & (off.position == "RB")].iloc[0]["season_offense_percentile"]
    den_rb_pct = off[(off.team == "DEN") & (off.position == "RB")].iloc[0]["season_offense_percentile"]
    assert kc_rb_pct < den_rb_pct  # KC's RB total (5) really is lower than DEN's (20)
    kc_qb_pct = off[(off.team == "KC") & (off.position == "QB")].iloc[0]["season_offense_percentile"]
    assert kc_qb_pct == 100.0  # QB's own, separate scale - only QB in the position


# ---------------------------------------------------------------------------
# Comparable defensive measure (Part 2) - distinct from legacy DvP
# ---------------------------------------------------------------------------
def test_defensive_points_allowed_is_team_game_total_not_row_average():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", 10.0),
        _row("KC", "BUF", "RB", 1, "p2", 8.0),  # BUF (as defense) allowed 18 total this game
    ])
    defn = build_defensive_position_points_allowed(weekly, 2026, "in_season")
    buf_rb = defn[(defn.defense_team == "BUF") & (defn.position == "RB")].iloc[0]
    assert buf_rb["season_points_allowed_per_defensive_game"] == pytest.approx(18.0)
    assert buf_rb["season_points_allowed_games_recorded"] == 1
    assert FANTASY_SCORING_BASIS == "PPR"


def test_defensive_points_allowed_percentile_direction_matches_legacy_convention():
    weekly = pd.DataFrame([
        _row("KC", "STINGY", "WR", 1, "p1", 5.0),
        _row("DEN", "LEAKY", "WR", 1, "p2", 30.0),
    ])
    defn = build_defensive_position_points_allowed(weekly, 2026, "in_season")
    stingy = defn[(defn.defense_team == "STINGY") & (defn.position == "WR")].iloc[0]["season_points_allowed_percentile"]
    leaky = defn[(defn.defense_team == "LEAKY") & (defn.position == "WR")].iloc[0]["season_points_allowed_percentile"]
    assert leaky > stingy  # allows MORE -> higher percentile, same direction as legacy DvP


def test_offensive_and_defensive_measures_use_distinct_completed_games():
    # Offense uses the team's OWN games; defense uses games where that team
    # was the OPPONENT - they must not be conflated into the same count.
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", 10.0),
        _row("KC", "DEN", "RB", 2, "p1", 12.0),
        _row("BUF", "SEA", "RB", 1, "p2", 9.0),
    ])
    off = build_offensive_position_reporting(weekly, 2026, "in_season")
    defn = build_defensive_position_points_allowed(weekly, 2026, "in_season")
    kc_offense_games = off[(off.team == "KC") & (off.position == "RB")].iloc[0]["season_offense_games_recorded"]
    kc_defense_games = defn[(defn.defense_team == "KC") & (defn.position == "RB")]
    assert kc_offense_games == 2
    assert kc_defense_games.empty or kc_defense_games.iloc[0]["season_points_allowed_games_recorded"] == 0 or pd.isna(
        kc_defense_games.iloc[0].get("season_points_allowed_games_recorded")
    ) or kc_defense_games.iloc[0]["season_points_allowed_games_recorded"] != kc_offense_games


# ---------------------------------------------------------------------------
# Unresolved position flagging (never silently dropped without report)
# ---------------------------------------------------------------------------
def test_unresolved_position_production_is_flagged_not_silently_dropped():
    raw = pd.DataFrame([
        {"season": 2026, "season_type": "REG", "week": 1, "position": "FB", "fantasy_points_ppr": 6.0},
        {"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "fantasy_points_ppr": 10.0},
    ])
    info = detect_unresolved_position_production(raw, 2026, 1)
    assert info["unresolved_position_rows"] == 1
    assert info["unresolved_position_points"] == pytest.approx(6.0)
    assert info["unresolved_positions"] == ["FB"]


def test_unresolved_position_production_empty_when_none_found():
    raw = pd.DataFrame([{"season": 2026, "season_type": "REG", "week": 1, "position": "RB", "fantasy_points_ppr": 10.0}])
    info = detect_unresolved_position_production(raw, 2026, 1)
    assert info["unresolved_position_rows"] == 0
    assert info["unresolved_positions"] == []


# ---------------------------------------------------------------------------
# Upcoming schedule - verified source, never fabricated
# ---------------------------------------------------------------------------
def test_upcoming_schedule_only_includes_not_yet_completed_games():
    schedule = pd.DataFrame([
        {"season": 2026, "week": 4, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "home_score": 20, "away_score": 17},
        {"season": 2026, "week": 5, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
         "home_score": None, "away_score": None},
    ])
    upcoming = build_upcoming_schedule(schedule, 2026, latest_completed_week=4)
    assert set(upcoming["week"].unique()) == {5}
    assert set(upcoming[upcoming["team"] == "KC"]["opponent_team"]) == {"DEN"}


def test_upcoming_schedule_each_team_appears_once_per_week_with_correct_opponent():
    schedule = pd.DataFrame([
        {"season": 2026, "week": 5, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
         "home_score": None, "away_score": None},
    ])
    upcoming = build_upcoming_schedule(schedule, 2026, latest_completed_week=4)
    assert len(upcoming) == 2
    kc_row = upcoming[upcoming["team"] == "KC"].iloc[0]
    den_row = upcoming[upcoming["team"] == "DEN"].iloc[0]
    assert kc_row["opponent_team"] == "DEN"
    assert den_row["opponent_team"] == "KC"


def test_upcoming_schedule_empty_when_schedule_missing_required_columns():
    schedule = pd.DataFrame([{"season": 2026, "week": 5}])  # no home_team/away_team
    upcoming = build_upcoming_schedule(schedule, 2026, 4)
    assert upcoming.empty


def test_upcoming_schedule_empty_when_schedule_source_empty():
    upcoming = build_upcoming_schedule(pd.DataFrame(), 2026, 4)
    assert upcoming.empty
