"""
Reusable, non-UI transformations for the Position Explorer page
(pages/1_Position_Explorer.py). Kept separate from the page so the exact
per-position column sets, filtering, sample-size labeling, and CSV export
are directly testable and so the page never re-runs an aggregation on a
widget interaction - all of that already happened once in the pipeline
(dfs_data_pipeline.build_players_current) and is just read, filtered, and
formatted here.
"""

import pandas as pd

# Players with fewer played games than this are flagged, not hidden by default.
LOW_SAMPLE_GAMES = 3
# At or below this many played games, momentum/trend reflect an early, small
# sample - not established form - and should be labeled as such rather than
# read with the same confidence as a mature sample.
EARLY_SAMPLE_GAMES = 2

# Base identity/fantasy-output columns shared by every position's table.
BASE_COLUMNS = {
    "player_display_name": "Player",
    "team": "Team",
    "last_opponent": "Last Opp",
    "games_played": "GP",
    "avg_fantasy_points": "Season Avg",
    "consistency_score": "Consistency",
    "momentum_score": "Momentum",
    "momentum_games_used": "Games Used",
    "opportunity_trend": "Trend",
}

# QB's column set is unchanged by this pass - QB reporting is out of scope
# (see the Position Explorer enhancement's title/scope: RB, WR, TE only).
QB_COLUMNS = {
    "completion_pct": "Comp %",
    "passing_yards_per_attempt": "Yds/Att",
    "total_passing_yards": "Pass Yds",
    "total_passing_tds": "Pass TDs",
    "total_rushing_yards": "Rush Yds",
    "yards_per_carry": "Rush YPC",
    "total_rushing_tds": "Rush TDs",
}

# Exact RB column order/list, per the Position Explorer workload/yardage
# enhancement spec - identity/role -> fantasy output -> volume -> yardage ->
# efficiency/opportunity -> trend/sample context.
RB_COLUMNS = {
    "player_display_name": "Player",
    "team": "Team",
    "last_opponent": "Last Opponent",
    "games_played": "Games Played",
    "avg_fantasy_points": "Season FPPG",
    "latest_game_fantasy_points": "Last Game FPPG",
    "momentum_score": "Momentum",
    "total_carries": "Carries",
    "carries_per_game": "Carries/Game",
    "total_targets": "Targets",
    "total_receptions": "Receptions",
    "total_touches": "Touches",
    "touches_per_game": "Touches/Game",
    "total_rushing_yards": "Rushing Yards",
    "total_receiving_yards": "Receiving Yards",
    "total_yards": "Total Yards",
    "total_yards_per_game": "Total Yards/Game",
    "yards_per_carry": "YPC",
    "yards_per_target": "Yards/Target",
    "catch_rate": "Catch Rate",
    "yards_per_touch": "Yards/Touch",
    "points_per_touch": "Points/Touch",
    "target_share_pct": "Target Share %",
    "carries_wow_change": "WoW Carries",
    "targets_wow_change": "WoW Targets",
    "touches_wow_change": "WoW Touches",
    "opportunity_trend": "Trend",
}

# Exact WR/TE column order/list, per the same spec (shared by both positions).
WR_TE_COLUMNS = {
    "player_display_name": "Player",
    "team": "Team",
    "last_opponent": "Last Opponent",
    "games_played": "Games Played",
    "avg_fantasy_points": "Season FPPG",
    "latest_game_fantasy_points": "Last Game FPPG",
    "momentum_score": "Momentum",
    "total_targets": "Targets",
    "targets_per_game": "Targets/Game",
    "total_receptions": "Receptions",
    "receptions_per_game": "Receptions/Game",
    "total_receiving_yards": "Receiving Yards",
    "receiving_yards_per_game": "Receiving Yards/Game",
    "total_receiving_air_yards": "Air Yards",
    "receiving_air_yards_per_game": "Air Yards/Game",
    "target_share_pct": "Target Share %",
    "air_yards_share_pct": "Air Yards Share %",
    "catch_rate": "Catch Rate",
    "yards_per_target": "Yards/Target",
    "yac_per_reception": "YAC/Reception",
    "points_per_touch": "Points/Touch",
    "targets_wow_change": "WoW Targets",
    "receiving_yards_wow_change": "WoW Receiving Yards",
    "target_share_wow_change": "WoW Target Share",
    "opportunity_trend": "Trend",
}


def columns_for_position(position: str) -> dict:
    """The {raw_column: display_label} map, in display order, for one position."""
    if position == "RB":
        return RB_COLUMNS
    if position in ("WR", "TE"):
        return WR_TE_COLUMNS
    return {**BASE_COLUMNS, **QB_COLUMNS}


def filter_table(df: pd.DataFrame, name_filter: str = "", hide_low_sample: bool = False) -> pd.DataFrame:
    """Apply the page's name-filter / hide-low-sample controls. Never mutates `df`."""
    out = df
    if name_filter:
        out = out[out["player_display_name"].str.contains(name_filter, case=False, na=False)]
    if hide_low_sample:
        out = out[out["games_played"] >= LOW_SAMPLE_GAMES]
    return out


def build_display_table(df: pd.DataFrame, position: str) -> pd.DataFrame:
    """The position's renamed, ordered, display-ready column subset."""
    columns = columns_for_position(position)
    available = [c for c in columns if c in df.columns]
    return df[available].rename(columns=columns)


def early_sample_warning(df: pd.DataFrame) -> str | None:
    """
    A caption to surface when the currently-visible table includes players
    at or below EARLY_SAMPLE_GAMES played games, so Momentum/Trend for those
    rows is read as an early/small sample rather than established form.
    Returns None when there's nothing to flag (empty table, or every visible
    player already has a mature sample).
    """
    if df.empty or "games_played" not in df.columns:
        return None
    early = df[df["games_played"] <= EARLY_SAMPLE_GAMES]
    if early.empty:
        return None
    count = len(early)
    plural = "player has" if count == 1 else "players have"
    return (
        f"⚠ {count} {plural} only {EARLY_SAMPLE_GAMES} or fewer games played - "
        "their Momentum and Trend reflect an early, small sample, not established form."
    )


def build_csv_export(df: pd.DataFrame, position: str) -> pd.DataFrame:
    """
    Everything needed for an audit-ready CSV export of the currently-filtered
    table: every user-visible column for this position (raw field names, not
    the pretty display labels, so it's directly cross-referenceable with the
    parquet/pipeline output) plus the stable player_id.
    """
    columns = columns_for_position(position)
    export_cols = ["player_id"] + [c for c in columns if c in df.columns and c != "player_id"]
    return df[export_cols].reset_index(drop=True)


def weekly_volume_for_player(weekly_df: pd.DataFrame, player_name: str) -> pd.DataFrame:
    """RB weekly workload (carries, targets, touches) by completed week, for one player."""
    cols = ["week", "carries", "targets", "touches"]
    out = weekly_df[weekly_df["player_display_name"] == player_name]
    available = [c for c in cols if c in out.columns]
    return out[available].sort_values("week").reset_index(drop=True)


def weekly_receiving_for_player(weekly_df: pd.DataFrame, player_name: str) -> pd.DataFrame:
    """WR/TE weekly receiving workload (targets, receptions, receiving yards) by completed week."""
    cols = ["week", "targets", "receptions", "receiving_yards"]
    out = weekly_df[weekly_df["player_display_name"] == player_name]
    available = [c for c in cols if c in out.columns]
    return out[available].sort_values("week").reset_index(drop=True)
