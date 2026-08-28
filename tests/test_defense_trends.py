import pandas as pd
import pytest

from lib.defense_trends import (
    build_position_detail_table,
    filter_defense_reporting,
    league_position_weekly_average,
    pivot_matrix,
    sort_table,
    weekly_series_with_bye_gaps,
)


def _dr_row(defense_team, position, games=5, points_allowed=15.0, league_avg=15.0,
            rank=1, percentile=80.0, last3=16.0, sample="full_sample",
            trend_label="stable"):
    return {
        "defense_team": defense_team, "position": position, "games_in_sample": games,
        "fantasy_points_allowed_per_game": points_allowed,
        "league_avg_points_allowed_for_position": league_avg,
        "matchup_index": points_allowed / league_avg * 100 if league_avg else None,
        "matchup_delta": points_allowed - league_avg,
        "position_rank_most_favorable": rank,
        "position_percentile_most_favorable": percentile,
        "last_3_games_count": 3, "last_3_games_points_allowed_per_game": last3,
        "last_3_games_matchup_index": 100.0, "last_3_games_matchup_delta": 1.0,
        "dvp_recent_trend_delta": last3 - points_allowed,
        "dvp_trend_label": trend_label, "sample_size_label": sample,
    }


# ---------------------------------------------------------------------------
# filter_defense_reporting
# ---------------------------------------------------------------------------
def test_filter_by_position_team_min_games_and_sample():
    df = pd.DataFrame([
        _dr_row("KC", "WR", games=5, sample="full_sample"),
        _dr_row("KC", "QB", games=5, sample="full_sample"),
        _dr_row("BUF", "WR", games=1, sample="insufficient_sample"),
    ])
    out = filter_defense_reporting(df, positions=["WR"])
    assert set(out["defense_team"]) == {"KC", "BUF"}
    assert set(out["position"]) == {"WR"}

    out2 = filter_defense_reporting(df, teams=["KC"])
    assert set(out2["defense_team"]) == {"KC"}

    out3 = filter_defense_reporting(df, min_games=3)
    assert "BUF" not in set(out3["defense_team"])

    out4 = filter_defense_reporting(df, include_insufficient_sample=False)
    assert "BUF" not in set(out4["defense_team"])


def test_filter_empty_input_returns_empty():
    assert filter_defense_reporting(pd.DataFrame()).empty


# ---------------------------------------------------------------------------
# pivot_matrix - independence across positions
# ---------------------------------------------------------------------------
def test_pivot_matrix_shape_and_position_columns():
    df = pd.DataFrame([
        _dr_row("KC", "QB", percentile=90.0),
        _dr_row("KC", "RB", percentile=10.0),
        _dr_row("BUF", "QB", percentile=40.0),
    ])
    mat = pivot_matrix(df, "position_percentile_most_favorable")
    assert list(mat.columns) == ["QB", "RB", "WR", "TE"]
    assert mat.loc["KC", "QB"] == pytest.approx(90.0)
    assert mat.loc["KC", "RB"] == pytest.approx(10.0)
    assert pd.isna(mat.loc["BUF", "RB"])  # no data for BUF/RB - null, not 0


def test_pivot_matrix_empty_input():
    mat = pivot_matrix(pd.DataFrame(), "matchup_index")
    assert mat.empty


# ---------------------------------------------------------------------------
# sort_table
# ---------------------------------------------------------------------------
def test_sort_table_respects_direction_and_missing_column():
    df = pd.DataFrame([_dr_row("KC", "WR", points_allowed=10), _dr_row("BUF", "WR", points_allowed=20)])
    desc = sort_table(df, "fantasy_points_allowed_per_game", ascending=False)
    assert desc["defense_team"].tolist() == ["BUF", "KC"]
    same = sort_table(df, "not_a_real_column")
    assert same["defense_team"].tolist() == df["defense_team"].tolist()


# ---------------------------------------------------------------------------
# build_position_detail_table - formatting
# ---------------------------------------------------------------------------
def test_build_position_detail_table_formats_nulls_and_labels():
    df = pd.DataFrame([_dr_row("KC", "WR", trend_label="becoming_more_favorable")])
    df.loc[0, "last_3_games_points_allowed_per_game"] = None
    table = build_position_detail_table(df)
    assert table.loc[0, "Last-3 Pts Allowed"] == "—"
    assert table.loc[0, "Recent Trend"] == "🔺 Becoming More Favorable"
    assert table.loc[0, "Defense"] == "KC"


def test_build_position_detail_table_empty_input():
    out = build_position_detail_table(pd.DataFrame())
    assert out.empty


# ---------------------------------------------------------------------------
# weekly_series_with_bye_gaps / league_position_weekly_average
# ---------------------------------------------------------------------------
def _weekly_row(defense, position, week, points):
    return {"season": 2025, "defense_team": defense, "position": position, "week": week, "fantasy_points_allowed": points}


def test_weekly_series_has_a_real_gap_at_the_bye_week():
    weekly = pd.DataFrame([
        _weekly_row("KC", "WR", 1, 20), _weekly_row("KC", "WR", 2, 22),
        # week 3 bye - absent
        _weekly_row("KC", "WR", 4, 25),
    ])
    series = weekly_series_with_bye_gaps(weekly, "KC", "WR", max_week=4)
    assert series["week"].tolist() == [1, 2, 3, 4]
    assert pd.isna(series.loc[series["week"] == 3, "fantasy_points_allowed"].iloc[0])
    assert series.loc[series["week"] == 4, "fantasy_points_allowed"].iloc[0] == pytest.approx(25)


def test_weekly_series_empty_or_none_max_week():
    assert weekly_series_with_bye_gaps(pd.DataFrame(), "KC", "WR", 4).empty
    weekly = pd.DataFrame([_weekly_row("KC", "WR", 1, 20)])
    assert weekly_series_with_bye_gaps(weekly, "KC", "WR", None).empty


def test_league_position_weekly_average_scoped_to_position():
    weekly = pd.DataFrame([
        _weekly_row("KC", "WR", 1, 20), _weekly_row("BUF", "WR", 1, 10),
        _weekly_row("KC", "QB", 1, 100),  # must not leak into the WR average
    ])
    avg = league_position_weekly_average(weekly, "WR", max_week=1)
    assert avg.loc[avg["week"] == 1, "fantasy_points_allowed"].iloc[0] == pytest.approx(15.0)
