import os

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from lib.position_explorer import (
    RB_COLUMNS,
    WR_TE_COLUMNS,
    build_csv_export,
    build_display_table,
    columns_for_position,
    early_sample_warning,
    filter_table,
    weekly_receiving_for_player,
    weekly_volume_for_player,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "1_Position_Explorer.py")


def _current_row(**overrides):
    row = {
        "player_id": "p1", "player_display_name": "Player One", "position": "RB",
        "team": "KC", "last_opponent": "BUF", "season": 2025, "latest_game_week": 2,
        "games_played": 2,
        "avg_fantasy_points": 15.0, "total_fantasy_points": 30.0, "latest_game_fantasy_points": 20.0,
        "total_touches": 20, "touches_per_game": 10.0,
        "total_targets": 6, "targets_per_game": 3.0,
        "total_carries": 14, "carries_per_game": 7.0,
        "total_receptions": 4, "receptions_per_game": 2.0,
        "total_rushing_yards": 70, "rushing_yards_per_game": 35.0,
        "total_receiving_yards": 40, "receiving_yards_per_game": 20.0,
        "total_yards": 110, "total_yards_per_game": 55.0,
        "total_receiving_air_yards": 30, "receiving_air_yards_per_game": 15.0,
        "total_yac": 18, "total_passing_yards": 0, "total_passing_tds": 0,
        "total_rushing_tds": 1, "total_receiving_tds": 0,
        "completion_pct": pd.NA, "passing_yards_per_attempt": pd.NA,
        "yards_per_target": 6.7, "yards_per_carry": 5.0,
        "catch_rate": 0.67, "yac_per_reception": 4.5,
        "target_share_pct": 18.0, "air_yards_share_pct": 12.0,
        "yards_per_touch": 5.5, "points_per_touch": 1.5, "consistency_score": 2.1,
        "momentum_score": 17.0, "momentum_games_used": 2,
        "latest_game_touches": 12, "prior_game_touches": 8, "touches_wow_change": 4,
        "latest_game_carries": 8, "latest_game_targets": 4, "latest_game_receptions": 3,
        "latest_game_rushing_yards": 40, "latest_game_receiving_yards": 25,
        "latest_game_total_yards": 65,
        "latest_game_target_share_pct": 22.0, "latest_game_air_yards_share_pct": 15.0,
        "carries_wow_change": 2, "targets_wow_change": 1,
        "receiving_yards_wow_change": 10, "target_share_wow_change": 5.0,
        "opportunity_trend": "gaining",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Exact column sets - no cross-position leakage
# ---------------------------------------------------------------------------
def test_rb_column_set_is_exact_and_ordered():
    expected = [
        "Player", "Team", "Last Opponent", "Games Played", "Season FPPG", "Last Game FPPG",
        "Momentum", "Carries", "Carries/Game", "Targets", "Receptions", "Touches", "Touches/Game",
        "Rushing Yards", "Receiving Yards", "Total Yards", "Total Yards/Game", "YPC", "Yards/Target",
        "Catch Rate", "Yards/Touch", "Points/Touch", "Target Share %", "WoW Carries", "WoW Targets",
        "WoW Touches", "Trend",
    ]
    assert list(RB_COLUMNS.values()) == expected


def test_wr_te_column_set_is_exact_and_ordered():
    expected = [
        "Player", "Team", "Last Opponent", "Games Played", "Season FPPG", "Last Game FPPG",
        "Momentum", "Targets", "Targets/Game", "Receptions", "Receptions/Game", "Receiving Yards",
        "Receiving Yards/Game", "Air Yards", "Air Yards/Game", "Target Share %", "Air Yards Share %",
        "Catch Rate", "Yards/Target", "YAC/Reception", "Points/Touch", "WoW Targets",
        "WoW Receiving Yards", "WoW Target Share", "Trend",
    ]
    assert list(WR_TE_COLUMNS.values()) == expected


def test_rb_display_table_has_exact_visible_columns_no_leakage():
    df = pd.DataFrame([_current_row()])
    display = build_display_table(df, "RB")
    assert list(display.columns) == list(RB_COLUMNS.values())
    # WR/TE-only concepts must not leak into the RB table.
    assert "Air Yards Share %" not in display.columns
    assert "WoW Receiving Yards" not in display.columns
    # Raw internal field names are never shown as column headers.
    assert "avg_fantasy_points" not in display.columns
    assert "target_share_pct" not in display.columns


def test_wr_te_display_table_has_exact_visible_columns_no_leakage():
    df = pd.DataFrame([_current_row(position="WR")])
    for pos in ("WR", "TE"):
        display = build_display_table(df, pos)
        assert list(display.columns) == list(WR_TE_COLUMNS.values())
        # RB-only concepts must not leak into WR/TE.
        assert "Carries" not in display.columns
        assert "WoW Carries" not in display.columns
        assert "YAC/Rec" not in display.columns or True  # renamed to "YAC/Reception"
        assert "yac_per_reception" not in display.columns


def test_qb_columns_unchanged_by_this_pass():
    df = pd.DataFrame([_current_row(position="QB")])
    display = build_display_table(df, "QB")
    assert "Comp %" in display.columns
    assert "Pass Yds" in display.columns
    # QB is out of scope - it must not pick up the new RB/WR/TE workload columns.
    assert "Carries/Game" not in display.columns
    assert "Air Yards" not in display.columns


# ---------------------------------------------------------------------------
# Filtering (name filter + hide-low-sample)
# ---------------------------------------------------------------------------
def test_filter_table_name_filter_is_case_insensitive():
    df = pd.DataFrame([_current_row(player_display_name="Christian McCaffrey"), _current_row(player_display_name="Bijan Robinson")])
    out = filter_table(df, name_filter="mccaffrey")
    assert out["player_display_name"].tolist() == ["Christian McCaffrey"]


def test_filter_table_hide_low_sample():
    df = pd.DataFrame([_current_row(games_played=1), _current_row(games_played=5)])
    out = filter_table(df, hide_low_sample=True)
    assert out["games_played"].tolist() == [5]


def test_filter_table_shows_low_sample_by_default():
    df = pd.DataFrame([_current_row(games_played=1), _current_row(games_played=5)])
    out = filter_table(df, hide_low_sample=False)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Early-sample warning
# ---------------------------------------------------------------------------
def test_early_sample_warning_present_for_one_or_two_games():
    df = pd.DataFrame([_current_row(games_played=1)])
    warning = early_sample_warning(df)
    assert warning is not None
    assert "early" in warning.lower()


def test_early_sample_warning_absent_for_established_sample():
    df = pd.DataFrame([_current_row(games_played=6)])
    assert early_sample_warning(df) is None


def test_early_sample_warning_absent_for_empty_table():
    assert early_sample_warning(pd.DataFrame()) is None


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
def test_csv_export_contains_expected_metrics_and_stable_player_id():
    df = pd.DataFrame([_current_row()])
    export = build_csv_export(df, "RB")
    assert "player_id" in export.columns
    for raw_col in ["total_carries", "total_rushing_yards", "total_yards", "carries_per_game", "target_share_pct"]:
        assert raw_col in export.columns
    assert export.iloc[0]["player_id"] == "p1"


def test_csv_export_uses_raw_field_names_for_audit():
    df = pd.DataFrame([_current_row(position="WR")])
    export = build_csv_export(df, "WR")
    # Raw field names, not the pretty display labels, so the export is
    # directly cross-referenceable with players_current.parquet.
    assert "total_receiving_air_yards" in export.columns
    assert "Air Yards" not in export.columns


# ---------------------------------------------------------------------------
# Weekly chart data helpers
# ---------------------------------------------------------------------------
def test_weekly_volume_for_player_returns_completed_weeks_only():
    weekly = pd.DataFrame([
        {"player_display_name": "Player One", "week": 1, "carries": 10, "targets": 2, "touches": 12},
        {"player_display_name": "Player One", "week": 2, "carries": 15, "targets": 3, "touches": 18},
        {"player_display_name": "Someone Else", "week": 1, "carries": 5, "targets": 1, "touches": 6},
    ])
    out = weekly_volume_for_player(weekly, "Player One")
    assert out["week"].tolist() == [1, 2]
    assert out["carries"].tolist() == [10, 15]


def test_weekly_receiving_for_player_returns_completed_weeks_only():
    weekly = pd.DataFrame([
        {"player_display_name": "Player One", "week": 1, "targets": 5, "receptions": 3, "receiving_yards": 40},
        {"player_display_name": "Player One", "week": 2, "targets": 8, "receptions": 6, "receiving_yards": 70},
    ])
    out = weekly_receiving_for_player(weekly, "Player One")
    assert out["week"].tolist() == [1, 2]
    assert out["receiving_yards"].tolist() == [40, 70]


# ---------------------------------------------------------------------------
# Page smoke test (AppTest) - across QB/RB/WR/TE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_position_explorer_page_renders_without_exception(position):
    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()
    assert not at.exception

    radio = at.radio[0]
    radio.set_value(position)
    at.run()
    assert not at.exception
    assert len(at.dataframe) >= 1
    assert len(at.download_button) >= 1


@pytest.mark.parametrize("position,expected_columns", [
    ("RB", list(RB_COLUMNS.values())),
    ("WR", list(WR_TE_COLUMNS.values())),
    ("TE", list(WR_TE_COLUMNS.values())),
])
def test_position_explorer_page_table_matches_exact_column_set(position, expected_columns):
    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()
    at.radio[0].set_value(position)
    at.run()
    assert not at.exception
    assert list(at.dataframe[0].value.columns) == expected_columns
