"""
Reusable, non-UI transformations for the Defense vs Position page
(pages/2_Defense_Matchups.py). Kept separate from the page so filtering/
pivoting/formatting is directly testable and so the page never re-runs a
groupby on a widget interaction - all of that heavy lifting already happened
once in the pipeline (dfs_data_pipeline.build_defense_reporting /
build_defense_position_weekly) and is just read, filtered, and formatted
here. Mirrors lib/team_trends.py's established pattern deliberately (same
filter/sort/format shape) rather than reinventing one for defense.
"""

import pandas as pd

from dfs_data_pipeline import POSITIONS
from lib.team_trends import SAMPLE_SIZE_DISPLAY  # noqa: F401 - reused as-is, not redefined

METRIC_SCOPES = ["Season", "Last 3 Games", "Recent Trend"]

SORT_METRIC_OPTIONS = {
    "Season": [
        ("fantasy_points_allowed_per_game", "Fantasy Points Allowed"),
        ("matchup_index", "Matchup Index"),
        ("matchup_delta", "Matchup Delta"),
        ("position_percentile_most_favorable", "Percentile"),
    ],
    "Last 3 Games": [
        ("last_3_games_points_allowed_per_game", "Last-3 Points Allowed"),
        ("last_3_games_matchup_index", "Last-3 Matchup Index"),
        ("last_3_games_matchup_delta", "Last-3 Matchup Delta"),
    ],
    "Recent Trend": [
        ("dvp_recent_trend_delta", "Recent Trend Delta"),
    ],
}

# Icon + text, deliberately not color-only. Higher points allowed = more
# favorable for the OFFENSE, so "becoming_more_favorable" is the "good for
# DFS" direction here, not a statement about the defense playing better.
DVP_TREND_DISPLAY = {
    "becoming_more_favorable": "🔺 Becoming More Favorable",
    "stable": "➖ Stable",
    "becoming_tougher": "🔻 Becoming Tougher",
    "insufficient_sample": "— Insufficient Sample",
    "not_applicable_preseason": "— N/A (Preseason)",
}

MATRIX_VIEWS = {
    "Raw Points Allowed": "fantasy_points_allowed_per_game",
    "Matchup Index": "matchup_index",
    "Percentile": "position_percentile_most_favorable",
}

DETAIL_TABLE_COLUMNS = [
    ("defense_team", "Defense"),
    ("games_in_sample", "Games"),
    ("fantasy_points_allowed_per_game", "Pts Allowed"),
    ("league_avg_points_allowed_for_position", "League Avg"),
    ("matchup_index", "Matchup Index"),
    ("matchup_delta", "Matchup Delta"),
    ("position_rank_most_favorable", "Most-Favorable Rank"),
    ("position_percentile_most_favorable", "Percentile"),
    ("last_3_games_points_allowed_per_game", "Last-3 Pts Allowed"),
    ("dvp_trend_display", "Recent Trend"),
]


def filter_defense_reporting(df: pd.DataFrame, positions=None, teams=None, min_games=0,
                              include_insufficient_sample=True) -> pd.DataFrame:
    """Apply the page's position/team/min-games/sample-size controls. Never mutates `df`."""
    if df.empty:
        return df

    out = df
    if positions:
        out = out[out["position"].isin(positions)]
    if teams:
        out = out[out["defense_team"].isin(teams)]
    if min_games:
        out = out[out["games_in_sample"] >= min_games]
    if not include_insufficient_sample:
        out = out[out["sample_size_label"] != "insufficient_sample"]
    return out.reset_index(drop=True)


def pivot_matrix(df: pd.DataFrame, value_col: str, positions=None) -> pd.DataFrame:
    """
    Defense x position matrix for one value column - rows sorted
    alphabetically by defense, columns in POSITIONS order. Every value in
    this matrix is already computed independently within its own position
    column by the pipeline (see build_defense_reporting) - pivoting never
    introduces a cross-position comparison that wasn't already safe.
    """
    positions = positions or POSITIONS
    if df.empty or value_col not in df.columns:
        return pd.DataFrame(columns=positions)
    pivoted = df.pivot(index="defense_team", columns="position", values=value_col)
    return pivoted.reindex(columns=positions).sort_index()


def sort_table(df: pd.DataFrame, metric_col: str, ascending: bool = False) -> pd.DataFrame:
    if df.empty or metric_col not in df.columns:
        return df
    return df.sort_values(metric_col, ascending=ascending, na_position="last").reset_index(drop=True)


def with_display_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Add icon+text display columns for the label vocabularies - never
    color-only signaling. Safe to call on an empty frame."""
    out = df.copy()
    if out.empty:
        for col in ("dvp_trend_display", "sample_size_display"):
            out[col] = pd.Series(dtype="object")
        return out

    out["dvp_trend_display"] = out["dvp_trend_label"].map(DVP_TREND_DISPLAY).fillna(out["dvp_trend_label"])
    out["sample_size_display"] = out["sample_size_label"].map(SAMPLE_SIZE_DISPLAY).fillna(out["sample_size_label"])
    return out


def _fmt(value, decimals=1, signed=False, suffix=""):
    """Format a number as text, or '—' for null - never a blank cell that
    could be misread as zero."""
    if value is None or pd.isna(value):
        return "—"
    fmt = f"{{:+.{decimals}f}}" if signed else f"{{:.{decimals}f}}"
    return fmt.format(value) + suffix


NUMERIC_FORMAT_SPEC = {
    "fantasy_points_allowed_per_game": (2, False, ""),
    "league_avg_points_allowed_for_position": (2, False, ""),
    "matchup_index": (1, False, ""),
    "matchup_delta": (1, True, ""),
    "position_rank_most_favorable": (0, False, ""),
    "position_percentile_most_favorable": (1, False, ""),
    "last_3_games_points_allowed_per_game": (2, False, ""),
    "last_3_games_matchup_index": (1, False, ""),
    "last_3_games_matchup_delta": (1, True, ""),
    "dvp_recent_trend_delta": (2, True, ""),
}


def build_position_detail_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    The Position Detail section's renamed, display-ready table for a single
    (already position-filtered, already-sorted) slice of defense_reporting.
    Call `sort_table` FIRST - this formats every numeric metric to text
    (with an explicit '—' for null and a signed +/- for delta metrics), so
    row order must already be final before this runs.
    """
    labeled = with_display_labels(df)
    for col, (decimals, signed, suffix) in NUMERIC_FORMAT_SPEC.items():
        if col in labeled.columns:
            labeled[col] = labeled[col].apply(lambda v, d=decimals, s=signed, suf=suffix: _fmt(v, d, s, suf))

    cols = [c for c, _ in DETAIL_TABLE_COLUMNS if c in labeled.columns]
    rename = dict(DETAIL_TABLE_COLUMNS)
    return labeled[cols].rename(columns=rename)


def weekly_series_with_bye_gaps(weekly_df: pd.DataFrame, defense_team, position, max_week) -> pd.DataFrame:
    """
    One defense+position's weekly points-allowed series, reindexed across
    the full 1..max_week range with a real NaN for any week absent from the
    source (a bye) - so a line chart breaks there instead of drawing a
    misleading straight line across the bye.
    """
    empty = pd.DataFrame(columns=["week", "fantasy_points_allowed"])
    if weekly_df.empty or max_week is None:
        return empty

    subset = weekly_df[(weekly_df["defense_team"] == defense_team) & (weekly_df["position"] == position)]
    full_weeks = pd.DataFrame({"week": range(1, int(max_week) + 1)})
    merged = full_weeks.merge(subset[["week", "fantasy_points_allowed"]], on="week", how="left")
    return merged


def league_position_weekly_average(weekly_df: pd.DataFrame, position, max_week) -> pd.DataFrame:
    """Per-week average fantasy points allowed to `position`, across every
    defense that played that week - the reference line for the recent-trend
    chart. Reindexed the same way as weekly_series_with_bye_gaps for a
    consistent x-axis, though a league-wide average is very unlikely to have
    a truly empty week."""
    empty = pd.DataFrame(columns=["week", "fantasy_points_allowed"])
    if weekly_df.empty or max_week is None:
        return empty

    subset = weekly_df[weekly_df["position"] == position]
    weekly_avg = subset.groupby("week")["fantasy_points_allowed"].mean().reset_index()
    full_weeks = pd.DataFrame({"week": range(1, int(max_week) + 1)})
    return full_weeks.merge(weekly_avg, on="week", how="left")
