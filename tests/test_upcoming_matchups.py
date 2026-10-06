import pandas as pd
import pytest

from lib.upcoming_matchups import (
    apply_discovery_filters,
    build_discovery_table,
    matchup_uid,
    resolve_shortlist_uids,
    schedule_is_available,
)


def _offense_row(team, position, pts=15.0, pctile=80.0, games=3):
    return {
        "team": team, "position": position,
        "season_offense_points_per_team_game": pts, "season_offense_percentile": pctile,
        "season_offense_games_recorded": games,
        "recent_offense_points_per_team_game": pts, "recent_offense_percentile": pctile,
        "recent_offense_games_recorded": games,
    }


def _defense_row(defense_team, position, pts=14.0, pctile=70.0, games=3):
    return {
        "defense_team": defense_team, "position": position,
        "season_points_allowed_per_defensive_game": pts, "season_points_allowed_percentile": pctile,
        "season_points_allowed_games_recorded": games,
        "recent_points_allowed_per_defensive_game": pts, "recent_points_allowed_percentile": pctile,
        "recent_points_allowed_games_recorded": games,
    }


def _schedule_row(team, opponent, week=5):
    return {"season": 2026, "week": week, "team": team, "opponent_team": opponent, "is_home": True, "game_type": "REG"}


def test_schedule_is_available_false_for_empty_or_none():
    assert schedule_is_available(None) is False
    assert schedule_is_available(pd.DataFrame()) is False
    assert schedule_is_available(pd.DataFrame([{"a": 1}])) is True


def test_build_discovery_table_empty_when_schedule_unavailable():
    offense = pd.DataFrame([_offense_row("KC", "RB")])
    defense = pd.DataFrame([_defense_row("DEN", "RB")])
    out = build_discovery_table(offense, defense, pd.DataFrame())
    assert out.empty


def test_build_discovery_table_joins_offense_defense_via_schedule():
    offense = pd.DataFrame([_offense_row("KC", "RB", pts=20.0, pctile=90.0, games=4)])
    defense = pd.DataFrame([_defense_row("DEN", "RB", pts=18.0, pctile=85.0, games=5)])
    schedule = pd.DataFrame([_schedule_row("KC", "DEN")])
    out = build_discovery_table(offense, defense, schedule, window="Season")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["team"] == "KC"
    assert row["opponent_team"] == "DEN"
    assert row["offense_points_per_team_game"] == pytest.approx(20.0)
    assert row["offense_percentile"] == pytest.approx(90.0)
    assert row["offense_games_recorded"] == 4
    assert row["points_allowed_per_defensive_game"] == pytest.approx(18.0)
    assert row["defense_percentile"] == pytest.approx(85.0)
    assert row["defense_games_recorded"] == 5
    assert row["sample_warning"] is None


def test_build_discovery_table_both_percentiles_kept_separate_never_averaged():
    offense = pd.DataFrame([_offense_row("KC", "RB", pctile=90.0)])
    defense = pd.DataFrame([_defense_row("DEN", "RB", pctile=10.0)])
    schedule = pd.DataFrame([_schedule_row("KC", "DEN")])
    out = build_discovery_table(offense, defense, schedule)
    row = out.iloc[0]
    assert row["offense_percentile"] == 90.0
    assert row["defense_percentile"] == 10.0
    assert "offense_defense_blended_score" not in out.columns
    assert "matchup_score" not in out.columns


def test_build_discovery_table_flags_missing_defense_sample():
    offense = pd.DataFrame([_offense_row("KC", "RB")])
    defense = pd.DataFrame([_defense_row("DEN", "WR")])  # no RB row for DEN
    schedule = pd.DataFrame([_schedule_row("KC", "DEN")])
    out = build_discovery_table(offense, defense, schedule)
    row = out[out.position == "RB"].iloc[0]
    assert pd.isna(row["defense_games_recorded"])
    assert "no recorded defensive games" in row["sample_warning"]


def test_matchup_uid_distinguishes_week_and_position():
    a = {"team": "KC", "opponent_team": "DEN", "position": "RB", "week": 5}
    b = {"team": "KC", "opponent_team": "DEN", "position": "WR", "week": 5}
    c = {"team": "KC", "opponent_team": "DEN", "position": "RB", "week": 6}
    assert matchup_uid(a) != matchup_uid(b)
    assert matchup_uid(a) != matchup_uid(c)


def test_apply_discovery_filters_independent_thresholds_never_blended():
    df = pd.DataFrame([
        {"team": "A", "position": "RB", "offense_percentile": 90.0, "defense_percentile": 40.0,
         "offense_games_recorded": 3, "defense_games_recorded": 3},
        {"team": "B", "position": "RB", "offense_percentile": 60.0, "defense_percentile": 90.0,
         "offense_games_recorded": 3, "defense_games_recorded": 3},
    ])
    only_offense = apply_discovery_filters(df, min_offense_percentile=75.0)
    assert set(only_offense["team"]) == {"A"}
    only_defense = apply_discovery_filters(df, min_defense_percentile=75.0)
    assert set(only_defense["team"]) == {"B"}
    both = apply_discovery_filters(df, min_offense_percentile=75.0, min_defense_percentile=75.0)
    assert both.empty


def test_apply_discovery_filters_null_percentile_never_passes_minimum():
    df = pd.DataFrame([
        {"team": "A", "position": "RB", "offense_percentile": pd.NA, "defense_percentile": 90.0,
         "offense_games_recorded": 0, "defense_games_recorded": 3},
    ])
    out = apply_discovery_filters(df, min_offense_percentile=1.0)
    assert out.empty


def test_apply_discovery_filters_min_sample_requires_both_sides():
    df = pd.DataFrame([
        {"team": "A", "position": "RB", "offense_percentile": 90.0, "defense_percentile": 90.0,
         "offense_games_recorded": 3, "defense_games_recorded": 0},
    ])
    out = apply_discovery_filters(df, min_sample_games=1)
    assert out.empty


def test_resolve_shortlist_uids_splits_valid_and_stale():
    df = pd.DataFrame([{"matchup_uid": "KC|DEN|RB|5"}])
    valid, stale = resolve_shortlist_uids(df, ["KC|DEN|RB|5", "OLD|TEAM|WR|3"])
    assert valid == ["KC|DEN|RB|5"]
    assert stale == ["OLD|TEAM|WR|3"]
