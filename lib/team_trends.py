"""
Reusable, non-UI transformations for the Team Trends page
(pages/4_Team_Trends.py). Kept separate from the page so filtering/sorting/
formatting is directly testable and so the page never re-runs a groupby or
rolling calculation on a widget interaction - all of that heavy lifting
already happened once in the pipeline (dfs_data_pipeline.build_team_reporting)
and is just read, filtered, and formatted here.
"""

import pandas as pd

METRIC_SCOPES = ["Season", "Last 3 Games", "Latest Game Change"]

# (column, display label) options per metric scope, used for both the
# sorting-metric selector and to decide which columns a scope's chart/table
# view emphasizes. Every column here is a real column in team_reporting.parquet.
SORT_METRIC_OPTIONS = {
    "Season": [
        ("season_total_yards_per_game", "Total Yards/Game"),
        ("season_passing_yards_per_game", "Pass Yards/Game"),
        ("season_rushing_yards_per_game", "Rush Yards/Game"),
        ("season_pass_rate_pct", "Pass Rate"),
        ("season_passing_epa_per_dropback_or_attempt", "Passing EPA Rate"),
    ],
    "Last 3 Games": [
        ("last_3_total_yards_per_game", "Last-3 Total Yards/Game"),
        ("last_3_passing_yards_per_game", "Last-3 Pass Yards/Game"),
        ("last_3_rushing_yards_per_game", "Last-3 Rush Yards/Game"),
        ("offensive_momentum_yards", "Offensive Momentum"),
        ("last_3_passing_epa_per_dropback_or_attempt", "Last-3 Passing EPA Rate"),
    ],
    "Latest Game Change": [
        ("wow_total_yards_change", "WoW Total Yards Change"),
        ("wow_passing_yards_change", "WoW Pass Yards Change"),
        ("wow_rushing_yards_change", "WoW Rush Yards Change"),
        ("wow_pass_rate_change", "WoW Pass Rate Change"),
    ],
}

# Icon + text, deliberately not color-only, for each label vocabulary.
RECENT_FORM_DISPLAY = {
    "heating_up": "🔺 Heating Up",
    "stable": "➖ Stable",
    "cooling_off": "🔻 Cooling Off",
    "insufficient_sample": "— Insufficient Sample",
    "not_applicable_preseason": "— N/A (Preseason)",
}

WOW_CHANGE_DISPLAY = {
    "increasing": "🔺 Increasing",
    "steady": "➖ Steady",
    "decreasing": "🔻 Decreasing",
    "insufficient_sample": "— Insufficient Sample",
    "not_applicable_preseason": "— N/A (Preseason)",
}

SAMPLE_SIZE_DISPLAY = {
    "insufficient_sample": "⚠ Insufficient Sample",
    "limited_sample": "◐ Limited Sample",
    "full_sample": "● Full Sample",
}

TABLE_COLUMNS = [
    ("team", "Team"),
    ("games_played", "Games"),
    ("season_total_yards_per_game", "Total Yards/Game"),
    ("season_passing_yards_per_game", "Pass Yards/Game"),
    ("season_rushing_yards_per_game", "Rush Yards/Game"),
    ("season_pass_rate_pct", "Pass Rate"),
    ("season_passing_epa_per_dropback_or_attempt", "Passing EPA Rate"),
    ("last_3_total_yards_per_game", "Last-3 Total Yards/Game"),
    ("offensive_momentum_yards", "Offensive Momentum"),
    ("wow_passing_yards_change", "WoW Pass Yards"),
    ("wow_rushing_yards_change", "WoW Rush Yards"),
    ("recent_form_display", "Recent Form"),
]


def filter_team_reporting(df: pd.DataFrame, teams=None, min_games=0, include_insufficient_sample=True) -> pd.DataFrame:
    """Apply the page's team-multiselect / min-games / sample-size controls. Never mutates `df`."""
    if df.empty:
        return df

    out = df
    if teams:
        out = out[out["team"].isin(teams)]
    if min_games:
        out = out[out["games_played"] >= min_games]
    if not include_insufficient_sample:
        out = out[out["sample_size_label"] != "insufficient_sample"]
    return out.reset_index(drop=True)


def compute_kpis(df: pd.DataFrame) -> dict:
    """
    League-average and standout-team KPIs from whatever rows are passed in
    (the page passes the already-filtered table, so KPIs respect the
    selected filters). Returns None for any KPI that can't be computed from
    an empty/all-null selection, never a fabricated 0.
    """
    if df.empty:
        return {
            "league_avg_total_yards_per_game": None,
            "league_avg_pass_rate_pct": None,
            "most_improved_team": None,
            "most_improved_momentum_yards": None,
            "highest_last_3_team": None,
            "highest_last_3_yards_per_game": None,
        }

    momentum = df.dropna(subset=["offensive_momentum_yards"])
    most_improved_team = None
    most_improved_value = None
    if not momentum.empty:
        top = momentum.loc[momentum["offensive_momentum_yards"].idxmax()]
        most_improved_team, most_improved_value = top["team"], top["offensive_momentum_yards"]

    last3 = df.dropna(subset=["last_3_total_yards_per_game"])
    highest_last3_team = None
    highest_last3_value = None
    if not last3.empty:
        top = last3.loc[last3["last_3_total_yards_per_game"].idxmax()]
        highest_last3_team, highest_last3_value = top["team"], top["last_3_total_yards_per_game"]

    return {
        "league_avg_total_yards_per_game": df["season_total_yards_per_game"].mean(),
        "league_avg_pass_rate_pct": df["season_pass_rate_pct"].mean(),
        "most_improved_team": most_improved_team,
        "most_improved_momentum_yards": most_improved_value,
        "highest_last_3_team": highest_last3_team,
        "highest_last_3_yards_per_game": highest_last3_value,
    }


def sort_table(df: pd.DataFrame, metric_col: str, ascending: bool = False) -> pd.DataFrame:
    if df.empty or metric_col not in df.columns:
        return df
    return df.sort_values(metric_col, ascending=ascending, na_position="last").reset_index(drop=True)


def with_display_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Add icon+text display columns for the label vocabularies - never
    color-only signaling. Safe to call on an empty frame."""
    out = df.copy()
    if out.empty:
        for col in ("recent_form_display", "wow_change_display", "sample_size_display"):
            out[col] = pd.Series(dtype="object")
        return out

    out["recent_form_display"] = out["recent_form_label"].map(RECENT_FORM_DISPLAY).fillna(out["recent_form_label"])
    out["wow_change_display"] = out["wow_change_label"].map(WOW_CHANGE_DISPLAY).fillna(out["wow_change_label"])
    out["sample_size_display"] = out["sample_size_label"].map(SAMPLE_SIZE_DISPLAY).fillna(out["sample_size_label"])
    return out


def _fmt(value, decimals=1, signed=False, suffix=""):
    """Format a number as text, or '—' for null - never a blank cell that
    could be misread as zero. Sign the value explicitly (+/-) for change
    metrics so direction never depends on color alone."""
    if value is None or pd.isna(value):
        return "—"
    fmt = f"{{:+.{decimals}f}}" if signed else f"{{:.{decimals}f}}"
    return fmt.format(value) + suffix


# Formatting spec per raw column: (decimals, signed, suffix). Columns not
# listed here (team, games_played, the *_display label columns) pass through
# as-is - sort_table already ran on the numeric values before this step.
NUMERIC_FORMAT_SPEC = {
    "season_total_yards_per_game": (1, False, ""),
    "season_passing_yards_per_game": (1, False, ""),
    "season_rushing_yards_per_game": (1, False, ""),
    "season_pass_rate_pct": (1, False, "%"),
    "season_passing_epa_per_dropback_or_attempt": (3, False, ""),
    "last_3_total_yards_per_game": (1, False, ""),
    "last_3_passing_yards_per_game": (1, False, ""),
    "last_3_rushing_yards_per_game": (1, False, ""),
    "last_3_pass_rate_pct": (1, False, "%"),
    "last_3_passing_epa_per_dropback_or_attempt": (3, False, ""),
    "offensive_momentum_yards": (1, True, ""),
    "wow_passing_yards_change": (0, True, ""),
    "wow_rushing_yards_change": (0, True, ""),
    "wow_total_yards_change": (0, True, ""),
    # season_pass_rate_pct/last_3_pass_rate_pct are absolute percentages
    # (suffix "%"); this is a DIFFERENCE of two such percentages, i.e.
    # percentage POINTS - "pts" avoids misreading "+6.2%" as a 6.2% relative
    # change.
    "wow_pass_rate_change": (1, True, " pts"),
}


def weekly_total_yards_by_team(raw_team_stats_df: pd.DataFrame, season, max_week) -> pd.DataFrame:
    """
    Week-by-week total yards (passing + rushing) per team, completed
    REG-season games only - the raw series behind the weekly trend chart.
    Never includes a week beyond `max_week` (an in-progress week's partial
    data is never charted as a completed data point). `season`/`max_week`
    are passed in by the caller (the page uses team_reporting's own season/
    latest_played_week) so this stays correct in both in_season and
    preseason_baseline mode without special-casing here.
    """
    empty = pd.DataFrame(columns=["season", "week", "team", "total_yards"])
    if raw_team_stats_df is None or raw_team_stats_df.empty or max_week is None:
        return empty

    df = raw_team_stats_df[
        (raw_team_stats_df["season"] == season)
        & (raw_team_stats_df["season_type"] == "REG")
        & (raw_team_stats_df["week"] <= max_week)
    ].copy()
    if df.empty:
        return empty

    df = df.sort_values(["team", "week"]).drop_duplicates(subset=["team", "week"], keep="last")
    df["total_yards"] = df["passing_yards"] + df["rushing_yards"]
    return df[["season", "week", "team", "total_yards"]].reset_index(drop=True)


def build_display_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    The main sortable table's renamed, display-ready column subset. Call
    `sort_table` on the raw numeric frame FIRST - this formats every numeric
    metric to text (with an explicit '—' for null and a signed +/- for
    change metrics), so row order must already be final before this runs.
    """
    labeled = with_display_labels(df)
    for col, (decimals, signed, suffix) in NUMERIC_FORMAT_SPEC.items():
        if col in labeled.columns:
            labeled[col] = labeled[col].apply(lambda v, d=decimals, s=signed, suf=suffix: _fmt(v, d, s, suf))

    cols = [c for c, _ in TABLE_COLUMNS if c in labeled.columns]
    rename = dict(TABLE_COLUMNS)
    return labeled[cols].rename(columns=rename)
