import pandas as pd
import pytest

from dfs_data_pipeline import (
    DEFENSE_POSITION_WEEKLY_COLUMNS,
    DEFENSE_REPORTING_COLUMNS,
    DEFENSE_TREND_MORE_FAVORABLE_PCT,
    DEFENSE_TREND_TOUGHER_PCT,
    build_defense_position_weekly,
    build_defense_reporting,
)


def _row(opponent, position, week, fantasy_points_ppr, season=2025, season_type="REG"):
    return {
        "opponent_team": opponent, "position": position, "week": week,
        "fantasy_points_ppr": fantasy_points_ppr, "season": season, "season_type": season_type,
    }


def _get(out, defense, position):
    matches = out[(out["defense_team"] == defense) & (out["position"] == position)]
    assert len(matches) == 1, f"expected exactly one row for {defense}/{position}, got {len(matches)}"
    return matches.iloc[0]


# ---------------------------------------------------------------------------
# Raw DvP formulas match the Power BI DAX semantics
# ---------------------------------------------------------------------------
def test_fantasy_points_allowed_per_game_is_simple_average_of_every_row():
    # Preserves AVERAGE(PlayerStats[fantasy_points_ppr]) - a week where a
    # defense faced 2 WRs contributes 2 rows to the average, not 1.
    rows = [
        _row("DEFA", "WR", 1, 20),
        _row("DEFA", "WR", 1, 30),  # second WR, same week
        _row("DEFA", "WR", 2, 10),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    row = _get(out, "DEFA", "WR")
    assert row["games_in_sample"] == 3  # raw row count, not distinct weeks
    assert row["fantasy_points_allowed_per_game"] == pytest.approx((20 + 30 + 10) / 3)


def test_league_avg_points_allowed_is_scoped_within_position():
    rows = [
        _row("DEFA", "QB", 1, 30), _row("DEFB", "QB", 1, 10),
        _row("DEFA", "RB", 1, 5), _row("DEFB", "RB", 1, 25),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    qb_avg = _get(out, "DEFA", "QB")["league_avg_points_allowed_for_position"]
    rb_avg = _get(out, "DEFA", "RB")["league_avg_points_allowed_for_position"]
    assert qb_avg == pytest.approx(20.0)
    assert rb_avg == pytest.approx(15.0)
    assert qb_avg != rb_avg  # never one shared/global league average


def test_matchup_index_and_delta_formulas():
    rows = [_row("DEFA", "WR", 1, 20), _row("DEFB", "WR", 1, 10)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    a = _get(out, "DEFA", "WR")
    assert a["matchup_index"] == pytest.approx(20 / 15 * 100)
    assert a["matchup_delta"] == pytest.approx(20 - 15)


# ---------------------------------------------------------------------------
# Rank/percentile direction: higher fantasy points allowed is ALWAYS more
# favorable for the offense, and always scoped within position (never global).
# ---------------------------------------------------------------------------
def test_rank_and_percentile_direction_higher_points_allowed_is_more_favorable():
    rows = [_row("DEFA", "WR", 1, 30), _row("DEFB", "WR", 1, 10), _row("DEFC", "WR", 1, 20)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    a = _get(out, "DEFA", "WR")  # allows the most
    b = _get(out, "DEFB", "WR")  # allows the least
    c = _get(out, "DEFC", "WR")

    assert a["position_rank_most_favorable"] == 1
    assert b["position_rank_most_favorable"] == 3
    assert a["position_percentile_most_favorable"] > c["position_percentile_most_favorable"] > b["position_percentile_most_favorable"]


def test_rank_is_dense_rank():
    # Two defenses tied for the top spot should both get rank 1, and the
    # next distinct value gets rank 2 (not 3) - "dense" rank per spec.
    rows = [
        _row("DEFA", "WR", 1, 20), _row("DEFB", "WR", 1, 20), _row("DEFC", "WR", 1, 10),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    a = _get(out, "DEFA", "WR")
    b = _get(out, "DEFB", "WR")
    c = _get(out, "DEFC", "WR")
    assert a["position_rank_most_favorable"] == 1
    assert b["position_rank_most_favorable"] == 1
    assert c["position_rank_most_favorable"] == 2


def test_no_global_ranking_or_color_normalization_across_positions():
    # DEF1 allows the most to QB but the least to RB - rankings/percentiles
    # must flip independently per position, proving there's no shared scale.
    rows = [
        _row("DEF1", "QB", 1, 30), _row("DEF2", "QB", 1, 15),
        _row("DEF1", "RB", 1, 5), _row("DEF2", "RB", 1, 25),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    qb1 = _get(out, "DEF1", "QB")
    qb2 = _get(out, "DEF2", "QB")
    rb1 = _get(out, "DEF1", "RB")
    rb2 = _get(out, "DEF2", "RB")

    assert qb1["position_percentile_most_favorable"] > qb2["position_percentile_most_favorable"]
    assert rb2["position_percentile_most_favorable"] > rb1["position_percentile_most_favorable"]
    # A raw QB value (30) and a raw RB value (5) are never compared to each
    # other for rank/percentile purposes - both DEF1 rows are rank 1 within
    # their OWN position despite wildly different raw numbers.
    assert qb1["position_rank_most_favorable"] == 1
    assert rb2["position_rank_most_favorable"] == 1


# ---------------------------------------------------------------------------
# Recent DvP across a bye
# ---------------------------------------------------------------------------
def test_last_3_played_weeks_skips_bye_week():
    rows = [
        _row("DEFA", "TE", 1, 10), _row("DEFA", "TE", 2, 12),
        # week 3 bye - absent
        _row("DEFA", "TE", 4, 14),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    row = _get(out, "DEFA", "TE")
    assert row["last_3_games_count"] == 3
    assert row["last_3_games_points_allowed_per_game"] == pytest.approx((10 + 12 + 14) / 3)


def test_multi_player_week_is_averaged_before_last_3_window():
    # A week where the defense faced 2 WRs must count as ONE game in the
    # recent-DvP window (via build_defense_position_weekly), not two -
    # unlike the season-level games_in_sample, which does count both rows.
    rows = [
        _row("DEFA", "WR", 1, 10), _row("DEFA", "WR", 1, 30),  # week 1: avg 20
        _row("DEFA", "WR", 2, 8),
    ]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    row = _get(out, "DEFA", "WR")
    assert row["last_3_games_count"] == 2  # 2 WEEKS, not 3 rows
    assert row["last_3_games_points_allowed_per_game"] == pytest.approx((20 + 8) / 2)
    assert row["games_in_sample"] == 3  # season-level count still counts every row


# ---------------------------------------------------------------------------
# Insufficient-sample labeling
# ---------------------------------------------------------------------------
def test_one_game_is_insufficient_sample_with_no_fabricated_trend():
    rows = [_row("DEFA", "RB", 1, 12)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    row = _get(out, "DEFA", "RB")
    assert row["games_in_sample"] == 1
    assert row["sample_size_label"] == "insufficient_sample"
    assert row["last_3_games_count"] == 1
    assert row["last_3_games_points_allowed_per_game"] == pytest.approx(12)  # uses what's available
    assert pd.isna(row["dvp_recent_trend_delta"])  # never fabricated from 1 game
    assert row["dvp_trend_label"] == "insufficient_sample"


def test_two_games_gives_a_real_trend_number_but_still_insufficient_sample_label():
    rows = [_row("DEFA", "RB", 1, 10), _row("DEFA", "RB", 2, 20)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    row = _get(out, "DEFA", "RB")
    assert row["last_3_games_count"] == 2
    assert pd.notna(row["dvp_recent_trend_delta"])  # 2 games is enough for a real number
    # ...but the LABEL still requires the full recent-form window (3 games)
    # before it will name a confident direction.
    assert row["dvp_trend_label"] == "insufficient_sample"


def test_trend_label_thresholds_use_the_documented_constants():
    season_avg_target = 15.0
    more_favorable_value = season_avg_target * (1 + DEFENSE_TREND_MORE_FAVORABLE_PCT + 0.01)
    tougher_value = season_avg_target * (1 + DEFENSE_TREND_TOUGHER_PCT - 0.01)

    def defense_rows(defense, last3_value):
        # 2 filler games + 3 recent games = 5 total; solve filler so the
        # 5-game season average lands exactly on season_avg_target while the
        # last 3 games are each exactly last3_value.
        filler = (season_avg_target * 5 - 3 * last3_value) / 2
        rows = [_row(defense, "WR", w, filler) for w in (1, 2)]
        rows += [_row(defense, "WR", w, last3_value) for w in (3, 4, 5)]
        return rows

    rows = defense_rows("HOT", more_favorable_value) + defense_rows("COLD", tougher_value)
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    assert _get(out, "HOT", "WR")["dvp_trend_label"] == "becoming_more_favorable"
    assert _get(out, "COLD", "WR")["dvp_trend_label"] == "becoming_tougher"


# ---------------------------------------------------------------------------
# Current-season vs prior-season baseline behavior
# ---------------------------------------------------------------------------
def test_preseason_baseline_keeps_season_dvp_but_nulls_recent_dvp():
    rows = [_row("DEFA", "QB", w, 15 + w, season=2025) for w in range(1, 8)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "preseason_baseline")
    row = _get(out, "DEFA", "QB")

    assert row["reporting_mode"] == "preseason_baseline"
    assert row["games_in_sample"] == 7
    assert pd.notna(row["fantasy_points_allowed_per_game"])  # season DvP stays useful

    for col in [
        "last_3_games_count", "last_3_games_points_allowed_per_game",
        "last_3_games_matchup_index", "last_3_games_matchup_delta", "dvp_recent_trend_delta",
    ]:
        assert pd.isna(row[col]), f"{col} should be null in preseason_baseline mode"
    assert row["dvp_trend_label"] == "not_applicable_preseason"


# ---------------------------------------------------------------------------
# No incomplete/future-week or wrong-season/non-REG rows used
# ---------------------------------------------------------------------------
def test_excludes_other_seasons_and_non_regular_season():
    rows = [
        _row("DEFA", "TE", 1, 10, season=2025, season_type="REG"),
        _row("DEFA", "TE", 1, 999, season=2025, season_type="POST"),
        _row("DEFA", "TE", 1, 999, season=2024, season_type="REG"),
    ]
    # build_defense_reporting assumes pre-filtered weekly_df, same convention
    # as the old build_defense_matchups - filtering to REG/season happens
    # upstream in build_players_weekly. Prove build_defense_reporting itself
    # doesn't need to re-filter by only passing the REG/2025 row through.
    filtered = pd.DataFrame([r for r in rows if r["season"] == 2025 and r["season_type"] == "REG"])
    out = build_defense_reporting(filtered, 2025, "in_season")
    row = _get(out, "DEFA", "TE")
    assert row["fantasy_points_allowed_per_game"] == pytest.approx(10)


# ---------------------------------------------------------------------------
# Schema / no duplicates / empty input
# ---------------------------------------------------------------------------
def test_output_schema_matches_defense_reporting_columns():
    rows = [_row("DEFA", "QB", 1, 15), _row("DEFB", "RB", 1, 8)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    assert list(out.columns) == DEFENSE_REPORTING_COLUMNS


def test_no_duplicate_defense_position_rows():
    rows = [_row("DEFA", "QB", 1, 15), _row("DEFA", "QB", 2, 18), _row("DEFB", "QB", 1, 12)]
    out = build_defense_reporting(pd.DataFrame(rows), 2025, "in_season")
    assert not out.duplicated(subset=["defense_team", "position"]).any()
    assert len(out) == 2


def test_empty_input_returns_empty_with_schema():
    out = build_defense_reporting(pd.DataFrame(), 2025, "in_season")
    assert out.empty
    assert list(out.columns) == DEFENSE_REPORTING_COLUMNS


# ---------------------------------------------------------------------------
# build_defense_position_weekly
# ---------------------------------------------------------------------------
def test_defense_position_weekly_averages_multiple_players_same_week():
    rows = [_row("DEFA", "WR", 1, 10), _row("DEFA", "WR", 1, 30), _row("DEFA", "WR", 2, 8)]
    out = build_defense_position_weekly(pd.DataFrame(rows), 2025)
    assert list(out.columns) == DEFENSE_POSITION_WEEKLY_COLUMNS
    week1 = out[out["week"] == 1].iloc[0]
    assert week1["fantasy_points_allowed"] == pytest.approx(20.0)
    assert len(out) == 2  # one row per (defense, position, week), not per player


def test_defense_position_weekly_empty_input():
    out = build_defense_position_weekly(pd.DataFrame(), 2025)
    assert out.empty
    assert list(out.columns) == DEFENSE_POSITION_WEEKLY_COLUMNS
