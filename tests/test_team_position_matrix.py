import pandas as pd
import pytest

from lib.team_position_matrix import (
    build_matrix,
    build_matrix_display_table,
    build_sample_count_display_table,
    filter_offense_reporting,
    percentile_to_rgb,
    required_matrix_columns,
)


def _off_row(team, position, season_pts=15.0, season_pctile=80.0, season_games=3,
             recent_pts=16.0, recent_pctile=70.0, recent_games=3):
    return {
        "team": team, "position": position,
        "season_offense_points_per_team_game": season_pts, "season_offense_percentile": season_pctile,
        "season_offense_games_recorded": season_games,
        "recent_offense_points_per_team_game": recent_pts, "recent_offense_percentile": recent_pctile,
        "recent_offense_games_recorded": recent_games,
    }


def test_required_matrix_columns_includes_both_windows():
    cols = required_matrix_columns()
    assert "season_offense_percentile" in cols
    assert "recent_offense_percentile" in cols
    assert "team" in cols and "position" in cols


def test_filter_offense_reporting_by_team_and_position():
    df = pd.DataFrame([_off_row("KC", "RB"), _off_row("KC", "WR"), _off_row("DEN", "RB")])
    out = filter_offense_reporting(df, teams=["KC"], positions=["RB"])
    assert len(out) == 1
    assert out.iloc[0]["team"] == "KC"
    assert out.iloc[0]["position"] == "RB"


def test_build_matrix_rows_are_teams_columns_are_positions():
    df = pd.DataFrame([_off_row("KC", "RB", season_pts=20.0), _off_row("DEN", "WR", season_pts=10.0)])
    matrix = build_matrix(df, "Season", "points")
    assert list(matrix.columns) == ["QB", "RB", "WR", "TE"]
    assert matrix.loc["KC", "RB"] == pytest.approx(20.0)
    assert pd.isna(matrix.loc["KC", "WR"])
    assert matrix.loc["DEN", "WR"] == pytest.approx(10.0)


def test_build_matrix_percentile_vs_points_are_different_values():
    df = pd.DataFrame([_off_row("KC", "RB", season_pts=20.0, season_pctile=95.0)])
    points_matrix = build_matrix(df, "Season", "points")
    pctile_matrix = build_matrix(df, "Season", "percentile")
    assert points_matrix.loc["KC", "RB"] == pytest.approx(20.0)
    assert pctile_matrix.loc["KC", "RB"] == pytest.approx(95.0)


def test_build_matrix_recent_window_uses_recent_columns():
    df = pd.DataFrame([_off_row("KC", "RB", season_pts=20.0, recent_pts=5.0)])
    season_matrix = build_matrix(df, "Season", "points")
    recent_matrix = build_matrix(df, "Recent", "points")
    assert season_matrix.loc["KC", "RB"] == pytest.approx(20.0)
    assert recent_matrix.loc["KC", "RB"] == pytest.approx(5.0)


def test_build_matrix_empty_input_returns_empty_with_position_columns():
    out = build_matrix(pd.DataFrame(), "Season", "points")
    assert out.empty
    assert list(out.columns) == ["QB", "RB", "WR", "TE"]


def test_build_matrix_display_table_renders_dash_for_missing_never_zero():
    df = pd.DataFrame([_off_row("KC", "RB", season_pts=20.0)])
    display = build_matrix_display_table(df, "Season", "points")
    assert display.loc["KC", "WR"] == "—"
    assert "0" not in display.loc["KC", "WR"]
    assert display.loc["KC", "RB"] == "20.0"


def test_build_sample_count_display_table_all_strings_no_mixed_types():
    df = pd.DataFrame([_off_row("KC", "RB", season_games=4)])
    display = build_sample_count_display_table(df, "Season")
    assert display.loc["KC", "RB"] == "4"
    assert display.loc["KC", "WR"] == "—"
    assert all(isinstance(v, str) for v in display.values.flatten())


def test_percentile_to_rgb_direction_green_for_high_red_for_low():
    low = percentile_to_rgb(0.0)
    high = percentile_to_rgb(100.0)
    assert low != high
    assert "rgb(214,39,40)" == low
    assert "rgb(26,150,65)" == high


def test_percentile_to_rgb_missing_is_neutral_gray_not_a_fabricated_color():
    assert percentile_to_rgb(None) == "rgb(224,224,224)"
    assert percentile_to_rgb(float("nan")) == "rgb(224,224,224)"


def test_percentile_to_rgb_never_crashes_on_out_of_range_values():
    percentile_to_rgb(-5.0)
    percentile_to_rgb(150.0)
