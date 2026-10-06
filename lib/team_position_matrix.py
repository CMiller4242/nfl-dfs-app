"""
Offensive Production Matrix - non-UI pivot/format/legend helpers (Part 3).

Pure transformations over `dfs_data_pipeline.build_offensive_position_reporting`'s
already-computed mart (`data/team_offense_position_reporting.parquet`, read
via `lib.data.load_team_offense_position_reporting`). This module computes
NOTHING new - no score, no cross-position ranking - it only reshapes an
already-built team/position table into a matrix for display, and formats
cells. See that pipeline function's docstring for the exact field
definitions and percentile-direction convention (higher = more production).
"""

import pandas as pd

POSITIONS = ["QB", "RB", "WR", "TE"]

WINDOW_VALUE_COLUMNS = {
    "Season": {
        "points": "season_offense_points_per_team_game",
        "percentile": "season_offense_percentile",
        "games": "season_offense_games_recorded",
    },
    "Recent": {
        "points": "recent_offense_points_per_team_game",
        "percentile": "recent_offense_percentile",
        "games": "recent_offense_games_recorded",
    },
}

LEGEND_TEXT = (
    "Cell color is this team's OFFENSIVE PRODUCTION PERCENTILE at this position, computed strictly "
    "within the position (QB vs QB, RB vs RB, WR vs WR, TE vs TE - never compared across positions). "
    "Higher percentile (and a warmer color) always means MORE positional fantasy production - never "
    "the reverse, and never a measure of defense. Cell numbers are the selectable raw "
    "points-per-team-game or percentile value. A blank/gray cell means no completed team-games with "
    "recorded data for that position yet, not a verified zero."
)


def required_matrix_columns() -> list:
    """Columns this module's functions read from the offense reporting
    mart - used by the page's schema guard before rendering."""
    cols = {"team", "position"}
    for spec in WINDOW_VALUE_COLUMNS.values():
        cols.update(spec.values())
    return sorted(cols)


def filter_offense_reporting(df: pd.DataFrame, teams=None, positions=None) -> pd.DataFrame:
    """Team/position filters for the matrix and its underlying table. Never
    mutates `df`."""
    if df.empty:
        return df
    out = df
    if teams:
        out = out[out["team"].isin(teams)]
    if positions:
        out = out[out["position"].isin(positions)]
    return out


def build_matrix(df: pd.DataFrame, window: str, value_kind: str) -> pd.DataFrame:
    """
    Team (rows) x Position (columns QB/RB/WR/TE) matrix.
    `window` is "Season" or "Recent"; `value_kind` is "points" or
    "percentile" (points = raw points-per-team-game, percentile = the
    within-position offensive production percentile). Missing production
    (no recorded completed games) renders as a real NaN cell - a matrix
    library's natural "no data" rendering - never a fabricated 0.
    """
    if df.empty or window not in WINDOW_VALUE_COLUMNS or value_kind not in ("points", "percentile"):
        return pd.DataFrame(columns=POSITIONS)
    value_col = WINDOW_VALUE_COLUMNS[window][value_kind]
    if value_col not in df.columns:
        return pd.DataFrame(columns=POSITIONS)
    pivoted = df.pivot(index="team", columns="position", values=value_col)
    return pivoted.reindex(columns=POSITIONS).sort_index()


def build_sample_count_matrix(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """The distinct-completed-team-games-recorded matrix for the same
    window - shown alongside the main matrix so a thin sample is always
    visible, never hidden behind a single color."""
    if df.empty or window not in WINDOW_VALUE_COLUMNS:
        return pd.DataFrame(columns=POSITIONS)
    games_col = WINDOW_VALUE_COLUMNS[window]["games"]
    if games_col not in df.columns:
        return pd.DataFrame(columns=POSITIONS)
    pivoted = df.pivot(index="team", columns="position", values=games_col)
    return pivoted.reindex(columns=POSITIONS).sort_index()


def _lerp_rgb(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def percentile_to_rgb(pct) -> str:
    """
    A red (low) -> yellow (mid) -> green (high) color for one percentile
    value, as a CSS `rgb(...)` string - a hand-rolled interpolation so the
    matrix's color legend never depends on matplotlib (an optional pandas
    Styler dependency this project doesn't otherwise require). Returns a
    neutral gray for a missing percentile (no recorded data) - never a
    color implying a real 0th-percentile value. Direction matches the
    documented convention: higher percentile = more production = greener.
    """
    if pct is None or (isinstance(pct, float) and pd.isna(pct)):
        return "rgb(224,224,224)"
    pct = max(0.0, min(100.0, float(pct)))
    red, yellow, green = (214, 39, 40), (255, 255, 140), (26, 150, 65)
    if pct <= 50:
        r, g, b = _lerp_rgb(red, yellow, pct / 50.0)
    else:
        r, g, b = _lerp_rgb(yellow, green, (pct - 50.0) / 50.0)
    return f"rgb({r},{g},{b})"


def _fmt(value, decimals=1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.{decimals}f}"


def build_matrix_display_table(df: pd.DataFrame, window: str, value_kind: str) -> pd.DataFrame:
    """Text-formatted version of `build_matrix` for a plain table fallback
    (never relying on color alone) - null cells render as '—', never '0'."""
    matrix = build_matrix(df, window, value_kind)
    decimals = 0 if value_kind == "percentile" else 1
    return matrix.apply(lambda col: col.map(lambda v: _fmt(v, decimals)))


def build_sample_count_display_table(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """Text-formatted version of `build_sample_count_matrix` - every cell a
    plain string (never a mixed float/'—' column), null as '—'."""
    matrix = build_sample_count_matrix(df, window)
    return matrix.apply(lambda col: col.map(lambda v: _fmt(v, 0)))
