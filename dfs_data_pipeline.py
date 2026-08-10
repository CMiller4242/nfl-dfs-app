"""
NFL DFS data pipeline.

Pulls current-season player and team stats from nflreadpy and writes two
kinds of output to /data:

  - "raw" tables (players_weekly.parquet, team_stats.parquet): one row per
    player/team per COMPLETED week, straight from the source with minimal
    shaping.
  - "derived" tables (players_current.parquet, defense_matchups.parquet,
    team_summary.parquet): season-to-date aggregates and analytics computed
    from the raw tables (rolling form, momentum, matchup quality, etc).

The pipeline is a pure function of nflreadpy's source data: every run
recomputes all outputs from scratch and overwrites the Parquet files, so
re-running it never produces duplicate rows (idempotent by construction).

The active season, its latest fully-completed week, and the next DFS slate
week are all auto-detected from the schedule (using final scores, not just
whether nflreadpy has *any* rows for a week) so nobody has to hand-edit a
week number, and a week with any game still in progress is never treated as
complete.

If the active season (as the calendar says "now") has no completed week yet
- before Week 1, or Week 1 itself still in progress - the pipeline switches
to `app_mode = "preseason_week_1_baseline"`: `players_weekly`/
`players_current`/`defense_matchups`/`team_summary` are written empty (there
is no current-season data), and `players_prior_season_baseline.parquet` is
populated instead, from the immediately preceding (fully-completed) season.
This is a clearly-labeled, separate output - never silently blended into
current-season numbers. See `determine_app_mode`.
"""

import json
import os
from datetime import datetime, timezone

import nflreadpy as nfl
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

POSITIONS = ["QB", "RB", "WR", "TE"]

# Momentum weights, most-recent game first. If a player has fewer than 3
# played games available, only the leading weights are used and renormalized
# to sum to 1.0 (see `_player_recent_form`).
MOMENTUM_WEIGHTS = (0.5, 0.3, 0.2)

# Columns pulled from nflreadpy's player-week stats. `receiving_yards_after_catch`,
# `target_share`, and `air_yards_share` are used for yac_per_reception /
# target_share_pct / air_yards_share_pct in players_current when present.
PLAYER_STAT_COLUMNS = [
    "player_id", "player_name", "player_display_name", "position", "position_group",
    "season", "week", "season_type", "team", "opponent_team",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "receptions", "targets", "receiving_yards", "receiving_tds", "receiving_yards_after_catch",
    "target_share", "air_yards_share",
    "fantasy_points", "fantasy_points_ppr",
]

# Columns pulled from nflreadpy's team-week stats. def_sacks / def_interceptions /
# def_fumbles_forced are the team's own defensive production (not the
# opponent's), so sacks_per_game / turnovers_forced_per_game below are real
# source fields, not derived guesses. nflreadpy's team_stats has no
# points-allowed column, so we deliberately don't compute one.
TEAM_STAT_COLUMNS = [
    "season", "week", "team", "season_type", "opponent_team",
    "attempts", "passing_yards", "carries", "rushing_yards",
    "def_sacks", "def_interceptions", "def_fumbles_forced",
]

FANTASY_COLUMNS_TO_ZERO_FILL = [
    "fantasy_points", "fantasy_points_ppr", "targets", "carries", "attempts",
    "receptions", "receiving_yards", "rushing_yards", "receiving_yards_after_catch",
]

PLAYERS_CURRENT_EMPTY_COLUMNS = [
    "player_id", "player_display_name", "position", "team", "last_opponent",
    "season", "latest_game_week", "games_played",
    "avg_fantasy_points", "total_touches",
    "total_targets", "total_carries", "total_receptions",
    "total_passing_yards", "total_rushing_yards", "total_receiving_yards",
    "total_passing_tds", "total_rushing_tds", "total_receiving_tds",
    "completion_pct", "passing_yards_per_attempt",
    "yards_per_target", "yards_per_carry",
    "catch_rate", "yac_per_reception", "target_share_pct", "air_yards_share_pct",
    "yards_per_touch", "points_per_touch", "consistency_score",
    "momentum_score", "momentum_games_used",
    "latest_game_touches", "prior_game_touches", "touches_wow_change", "opportunity_trend",
]

PRIOR_SEASON_BASELINE_EMPTY_COLUMNS = [
    "player_id", "player_display_name", "position", "historical_team", "season",
    "games_played", "avg_fantasy_points", "total_touches",
    "total_targets", "total_carries", "total_receptions",
    "total_passing_yards", "total_rushing_yards", "total_receiving_yards",
    "total_passing_tds", "total_rushing_tds", "total_receiving_tds",
    "completion_pct", "passing_yards_per_attempt",
    "yards_per_target", "yards_per_carry",
    "catch_rate", "yac_per_reception", "target_share_pct", "air_yards_share_pct",
    "yards_per_touch", "points_per_touch", "consistency_score",
]

DEFENSE_MATCHUPS_EMPTY_COLUMNS = [
    "defense_team", "position", "fantasy_points_allowed", "games",
    "position_league_average", "matchup_delta", "matchup_rating_percentile", "matchup_rank",
]

TEAM_SUMMARY_EMPTY_COLUMNS = [
    "team", "games_played", "pass_yards_per_game", "rush_yards_per_game",
    "pass_rate_pct", "sacks_per_game", "turnovers_forced_per_game",
]


def safe_divide(numerator, denominator):
    """
    Elementwise division that returns NaN (never +/-inf) whenever the
    denominator is zero, negative-zero, or missing. Works with pandas
    Series/arrays or plain Python scalars.
    """
    is_scalar = pd.api.types.is_scalar(numerator) and pd.api.types.is_scalar(denominator)
    num = pd.to_numeric(pd.Series([numerator] if is_scalar else numerator), errors="coerce")
    den = pd.to_numeric(pd.Series([denominator] if is_scalar else denominator), errors="coerce")
    num, den = num.align(den) if not is_scalar else (num, den)
    result = (num / den).where(den.notna() & (den != 0))
    return result.iloc[0] if is_scalar else result.reset_index(drop=True)


def determine_active_season(now=None):
    """
    The season the calendar says is "current" - NFL seasons span Sep-Feb, so
    before March, "this year" still means last year's season (e.g. Jan 2026
    games belong to the 2025 season). This is independent of whether that
    season actually HAS any data yet from nflreadpy (see `determine_app_mode`).
    """
    now = now or datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def determine_app_mode(active_season, latest_completed_week):
    """
    Decide whether the app has real current-season data to work with.

      - `latest_completed_week` is not None -> "in_season": the active
        season has at least one fully-completed week; source_season ==
        active_season.
      - `latest_completed_week` is None -> "preseason_week_1_baseline": no
        completed week exists yet for the active season (before Week 1, or
        Week 1 itself still in progress); source_season == the immediately
        preceding season, used as a historical baseline.

    Pure decision function - no I/O - so mode selection is directly testable
    without mocking nflreadpy.
    """
    if latest_completed_week is not None:
        return "in_season", active_season
    return "preseason_week_1_baseline", active_season - 1


def _try_load_player_stats(season):
    try:
        return nfl.load_player_stats([season]).to_pandas()
    except Exception as exc:
        print(f"Season {season}: player stats unavailable ({exc})")
        return pd.DataFrame()


def _try_load_schedule(season):
    try:
        return nfl.load_schedules([season]).to_pandas()
    except Exception as exc:
        print(f"Season {season}: schedule unavailable ({exc})")
        return pd.DataFrame()


def determine_week_status(schedule_df, season):
    """
    A week is "completed" only if every REG-season game scheduled for it has
    a final score. Returns (latest_completed_week, next_slate_week), either
    of which may be None (e.g. before any games have finished this season).
    """
    games = schedule_df[(schedule_df["season"] == season) & (schedule_df["game_type"] == "REG")].copy()
    if games.empty:
        return None, None

    games["is_complete"] = games["home_score"].notna() & games["away_score"].notna()
    by_week = games.groupby("week")["is_complete"].agg(total="count", done="sum")
    fully_completed_weeks = by_week[by_week["done"] == by_week["total"]].index.tolist()
    latest_completed_week = max(fully_completed_weeks) if fully_completed_weeks else None

    all_weeks = sorted(games["week"].unique().tolist())
    if latest_completed_week is not None:
        remaining = [w for w in all_weeks if w > latest_completed_week]
    else:
        remaining = all_weeks
    next_slate_week = remaining[0] if remaining else None

    return latest_completed_week, next_slate_week


def load_raw_team_stats(season):
    try:
        df = nfl.load_team_stats([season]).to_pandas()
    except Exception as exc:
        print(f"Warning: could not load team stats: {exc}")
        return pd.DataFrame()
    cols = [c for c in TEAM_STAT_COLUMNS if c in df.columns]
    return df[cols].copy()


def load_raw_depth_charts(season):
    """
    Raw depth-chart rows from nflreadpy - a real, roughly-daily-updated feed
    covering all 32 teams, with each row carrying BOTH nflreadpy's own
    `gsis_id` and ESPN's `espn_id` together (a verified identity crosswalk,
    not a name guess). See lib/player_identity.py for how this gets turned
    into the app's canonical identity crosswalk. NOT the same thing as the
    old root-level `nfl_depth_chart.py`, which is a hardcoded, static Python
    list frozen at some past roster snapshot and is no longer used by the
    pipeline.
    """
    try:
        return nfl.load_depth_charts([season]).to_pandas()
    except Exception as exc:
        print(f"Warning: could not load depth charts: {exc}")
        return pd.DataFrame()


def build_depth_chart_snapshot(raw_depth_chart_df, season, week):
    """
    The app's normalized depth-chart snapshot: canonical player identity
    (`player_id` = gsis, `espn_id`), canonical team, fantasy position group
    (QB/RB/WR/TE only), source-specific position, depth rank, source
    timestamp, plus the season/week this snapshot is being used for (the
    raw feed itself is just "current as of `dt`," not week-indexed - this
    stamps which slate we're treating it as informing).

    Depth rank means different things by position group and this is
    deliberately NOT smoothed over: QB rank is normally a direct read of who
    starts; RB/WR/TE rank is role CONTEXT (who's ahead of whom on the depth
    chart), not a fabricated snap-share or target-share projection. Source
    position and depth rank are kept as their own visible columns
    specifically so this stays auditable rather than papered over.
    """
    from lib.player_identity import IDENTITY_CROSSWALK_COLUMNS, build_identity_crosswalk

    crosswalk = build_identity_crosswalk(raw_depth_chart_df)
    empty_columns = IDENTITY_CROSSWALK_COLUMNS + ["season", "week"]
    if crosswalk.empty:
        return pd.DataFrame(columns=empty_columns)

    crosswalk = crosswalk.copy()
    crosswalk["season"] = season
    crosswalk["week"] = week
    return crosswalk[empty_columns]


def write_snapshot_with_fallback(df, path, label):
    """
    Write `df` to `path` UNLESS it's empty/invalid and a prior good file
    already exists there - in that case the prior file is left untouched
    (a failed refresh must never overwrite last-known-good depth-chart or
    injury data with nothing). Returns True if `path` now holds a freshly
    written snapshot, False if a prior snapshot was kept instead.
    """
    is_valid = df is not None and not df.empty
    if is_valid:
        df.to_parquet(path, index=False)
        return True

    if os.path.exists(path):
        print(f"Warning: new {label} snapshot is empty/invalid - keeping the existing file at {path} unchanged.")
        return False

    # Nothing valid ever existed here either - write the empty-but-correctly
    # -shaped frame so downstream loaders see a consistent (if empty) file
    # rather than a missing one.
    df.to_parquet(path, index=False)
    return True


def build_players_weekly(raw_player_df, season, latest_completed_week):
    """One row per player per COMPLETED week this season. Raw stats only -
    no rolling averages or ratios live here, just the observed box score
    plus `touches` (targets + carries), which is a straight sum, not a rate."""
    if latest_completed_week is None:
        return pd.DataFrame(columns=PLAYER_STAT_COLUMNS + ["touches"])

    df = raw_player_df[
        (raw_player_df["season"] == season)
        & (raw_player_df["season_type"] == "REG")
        & (raw_player_df["week"] <= latest_completed_week)
    ].copy()
    df = df[df["position"].isin(POSITIONS)].copy()

    cols = [c for c in PLAYER_STAT_COLUMNS if c in df.columns]
    df = df[cols].copy()
    for col in FANTASY_COLUMNS_TO_ZERO_FILL:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    df["touches"] = df[["targets", "carries"]].fillna(0).sum(axis=1)

    # Uniqueness: one row per player per week. `player_id` is a stable,
    # globally-unique id in nflreadpy's schema (not team-scoped), and a
    # player is only ever on one team for a given week's game, so
    # (player_id, week) - not (player_id, team, week) - is the correct grain;
    # collapsing on it also self-heals the rare case of a player_id somehow
    # carrying two different team values for the same week.
    dup_mask = df.duplicated(subset=["player_id", "week"], keep=False)
    if dup_mask.any():
        print(
            f"Warning: {int(dup_mask.sum())} duplicate player-week rows found in source "
            f"data (same player_id + week); keeping one row per player-week."
        )

    df = (
        df.sort_values(["player_id", "week"])
        .drop_duplicates(subset=["player_id", "week"], keep="last")
        .reset_index(drop=True)
    )
    assert not df.duplicated(subset=["player_id", "week"]).any(), (
        "player-week uniqueness violated after dedup - this should be unreachable"
    )
    return df


def _player_recent_form(group):
    """
    Momentum + week-over-week touches trend from a player's most recent
    PLAYED games (bye weeks just aren't rows, so they're skipped naturally).
    Momentum uses up to the last 3 played games, weighted 50/30/20
    (most-recent first); with fewer than 3 games the weights actually used
    are renormalized to sum to 1.0.
    """
    g = group.sort_values("week")
    recent = g.tail(3)
    fps = list(recent["fantasy_points_ppr"])[::-1]
    touches = list(recent["touches"])[::-1]
    n = len(fps)

    weights = list(MOMENTUM_WEIGHTS[:n])
    weight_sum = sum(weights)
    norm_weights = [w / weight_sum for w in weights] if weight_sum else []
    momentum_score = sum(w * f for w, f in zip(norm_weights, fps)) if n else None

    latest_touches = touches[0] if n >= 1 else None
    prior_touches = touches[1] if n >= 2 else None
    touches_wow_change = (latest_touches - prior_touches) if n >= 2 else None

    return pd.Series({
        "momentum_score": momentum_score,
        "momentum_games_used": n,
        "latest_game_touches": latest_touches,
        "prior_game_touches": prior_touches,
        "touches_wow_change": touches_wow_change,
    })


def _classify_opportunity_trend(change):
    if pd.isna(change):
        return "insufficient_data"
    if change >= 1:
        return "gaining"
    if change <= -1:
        return "losing"
    return "stable"


def _season_aggregates(weekly_df):
    """
    Shared season-long aggregation: one row per player with totals,
    efficiency rates, and consistency_score from a set of weekly rows. Used
    for BOTH the current-season snapshot (`build_players_current`, which
    adds recency/momentum on top) and the prior-season baseline
    (`build_prior_season_baseline`, which deliberately does not - a stale
    season's "momentum" and "week-over-week trend" aren't meaningful framed
    as if they were current). Returns identity columns (player_id,
    player_display_name, position, team = the team on their most recent row
    in `weekly_df`, last_opponent, season, latest_game_week) plus every
    season-aggregate metric.
    """
    weekly_df = weekly_df.sort_values(["player_id", "week"])

    latest_idx = weekly_df.groupby("player_id")["week"].idxmax()
    identity = weekly_df.loc[
        latest_idx, ["player_id", "player_display_name", "position", "team", "opponent_team", "week"]
    ].rename(columns={"opponent_team": "last_opponent", "week": "latest_game_week"})

    has_yac = "receiving_yards_after_catch" in weekly_df.columns
    has_target_share = "target_share" in weekly_df.columns
    has_air_yards_share = "air_yards_share" in weekly_df.columns

    agg_spec = {
        "games_played": ("week", "count"),
        "sum_fantasy_points": ("fantasy_points_ppr", "sum"),
        "avg_fantasy_points": ("fantasy_points_ppr", "mean"),
        "std_fantasy_points": ("fantasy_points_ppr", lambda s: s.std(ddof=0)),
        "sum_targets": ("targets", "sum"),
        "sum_carries": ("carries", "sum"),
        "sum_receptions": ("receptions", "sum"),
        "sum_receiving_yards": ("receiving_yards", "sum"),
        "sum_rushing_yards": ("rushing_yards", "sum"),
        "sum_attempts": ("attempts", "sum"),
        "sum_completions": ("completions", "sum"),
        "sum_passing_yards": ("passing_yards", "sum"),
        "sum_passing_tds": ("passing_tds", "sum"),
        "sum_rushing_tds": ("rushing_tds", "sum"),
        "sum_receiving_tds": ("receiving_tds", "sum"),
    }
    if has_yac:
        agg_spec["sum_yac"] = ("receiving_yards_after_catch", "sum")
    if has_target_share:
        agg_spec["mean_target_share"] = ("target_share", "mean")
    if has_air_yards_share:
        agg_spec["mean_air_yards_share"] = ("air_yards_share", "mean")

    agg = weekly_df.groupby("player_id").agg(**agg_spec).reset_index()

    agg["total_touches"] = agg["sum_targets"] + agg["sum_carries"]
    agg["total_targets"] = agg["sum_targets"]
    agg["total_carries"] = agg["sum_carries"]
    agg["total_receptions"] = agg["sum_receptions"]
    agg["total_passing_yards"] = agg["sum_passing_yards"]
    agg["total_rushing_yards"] = agg["sum_rushing_yards"]
    agg["total_receiving_yards"] = agg["sum_receiving_yards"]
    agg["total_passing_tds"] = agg["sum_passing_tds"]
    agg["total_rushing_tds"] = agg["sum_rushing_tds"]
    agg["total_receiving_tds"] = agg["sum_receiving_tds"]

    agg["yards_per_target"] = safe_divide(agg["sum_receiving_yards"], agg["sum_targets"])
    agg["yards_per_carry"] = safe_divide(agg["sum_rushing_yards"], agg["sum_carries"])
    agg["catch_rate"] = safe_divide(agg["sum_receptions"], agg["sum_targets"])
    agg["yac_per_reception"] = safe_divide(agg["sum_yac"], agg["sum_receptions"]) if has_yac else pd.NA
    agg["target_share_pct"] = agg["mean_target_share"] * 100 if has_target_share else pd.NA
    agg["air_yards_share_pct"] = agg["mean_air_yards_share"] * 100 if has_air_yards_share else pd.NA
    agg["yards_per_touch"] = safe_divide(
        agg["sum_receiving_yards"] + agg["sum_rushing_yards"], agg["total_touches"]
    )
    agg["points_per_touch"] = safe_divide(agg["sum_fantasy_points"], agg["total_touches"])
    agg["completion_pct"] = safe_divide(agg["sum_completions"], agg["sum_attempts"]) * 100
    agg["passing_yards_per_attempt"] = safe_divide(agg["sum_passing_yards"], agg["sum_attempts"])

    # Consistency score: avg / population stdev. Needs >=2 games and a
    # non-zero spread, otherwise it's not meaningful - leave it null.
    agg["consistency_score"] = safe_divide(agg["avg_fantasy_points"], agg["std_fantasy_points"])
    agg.loc[agg["games_played"] < 2, "consistency_score"] = pd.NA

    result = identity.merge(agg, on="player_id")
    result["season"] = weekly_df["season"].iloc[0]
    return result


def build_players_current(weekly_df):
    """One row per player: season-to-date aggregates + recency/momentum analytics."""
    if weekly_df.empty:
        return pd.DataFrame(columns=PLAYERS_CURRENT_EMPTY_COLUMNS)

    base = _season_aggregates(weekly_df)

    recent_form = (
        weekly_df.groupby("player_id", group_keys=True)
        .apply(_player_recent_form, include_groups=False)
        .reset_index()
    )

    current = base.merge(recent_form, on="player_id")
    current["opportunity_trend"] = current["touches_wow_change"].apply(_classify_opportunity_trend)

    keep_cols = PLAYERS_CURRENT_EMPTY_COLUMNS
    current = current[[c for c in keep_cols if c in current.columns]]
    return current.sort_values("momentum_score", ascending=False, na_position="last").reset_index(drop=True)


def build_prior_season_baseline(weekly_df):
    """
    One row per player: season-long aggregates from a PRIOR, fully-completed
    season, for use as a Week 1 baseline before any current-season games
    exist. Deliberately excludes momentum, week-over-week touches, and
    opportunity_trend - those are recency concepts that don't apply to a
    season that's a year stale, and displaying them would imply a
    current-season signal that doesn't exist. `team` is renamed
    `historical_team` so it's never confused with a player's current team
    (which comes from the uploaded DK salary CSV, not this table).
    """
    if weekly_df.empty:
        return pd.DataFrame(columns=PRIOR_SEASON_BASELINE_EMPTY_COLUMNS)

    base = _season_aggregates(weekly_df).rename(columns={"team": "historical_team"})
    keep_cols = PRIOR_SEASON_BASELINE_EMPTY_COLUMNS
    base = base[[c for c in keep_cols if c in base.columns]]
    return base.sort_values("avg_fantasy_points", ascending=False, na_position="last").reset_index(drop=True)


def build_defense_matchups(weekly_df):
    """
    Average fantasy points allowed by each defense to each position, from
    completed-week player games only. `matchup_rating_percentile` is ranked
    separately within each position (0-100, higher is always a better
    matchup for the offensive player) so QB/RB/WR/TE - which score on very
    different scales - are never compared on one shared scale.
    """
    if weekly_df.empty:
        return pd.DataFrame(columns=DEFENSE_MATCHUPS_EMPTY_COLUMNS)

    grouped = (
        weekly_df.groupby(["opponent_team", "position"])["fantasy_points_ppr"]
        .agg(fantasy_points_allowed="mean", games="count")
        .reset_index()
        .rename(columns={"opponent_team": "defense_team"})
    )

    position_avg = (
        weekly_df.groupby("position")["fantasy_points_ppr"].mean().rename("position_league_average")
    )
    grouped = grouped.merge(position_avg, on="position", how="left")
    grouped["matchup_delta"] = grouped["fantasy_points_allowed"] - grouped["position_league_average"]

    # Higher raw points-allowed -> higher percentile -> better matchup for the offense.
    grouped["matchup_rating_percentile"] = (
        grouped.groupby("position")["fantasy_points_allowed"].rank(pct=True, ascending=True) * 100
    )
    grouped["matchup_rank"] = grouped.groupby("position")["fantasy_points_allowed"].rank(
        ascending=False, method="min"
    )

    return grouped.sort_values(["position", "matchup_rank"]).reset_index(drop=True)


def build_team_summary(raw_team_df, season, latest_completed_week):
    """
    Team-level offense volume + the team's OWN defensive production
    (sacks_per_game, turnovers_forced_per_game come from that team's
    def_sacks/def_interceptions/def_fumbles_forced - what that team's
    defense did, not what was done to it), completed weeks only.

    This table is informational context only and is never read by the DK
    Lineup Helper's projection formula - matchup quality there comes
    exclusively from `defense_matchups` (fantasy points a defense allows to
    a position), computed in `build_defense_matchups` below.

    Intentionally does NOT include a points-allowed field: nflreadpy's
    team_stats has no scoring column, and fabricating one from other fields
    would be misleading.
    """
    if raw_team_df.empty or latest_completed_week is None:
        return pd.DataFrame(columns=TEAM_SUMMARY_EMPTY_COLUMNS)

    df = raw_team_df[
        (raw_team_df["season"] == season)
        & (raw_team_df["season_type"] == "REG")
        & (raw_team_df["week"] <= latest_completed_week)
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=TEAM_SUMMARY_EMPTY_COLUMNS)

    df = df.sort_values(["team", "week"]).drop_duplicates(subset=["team", "week"], keep="last")

    agg = df.groupby("team").agg(
        games_played=("week", "count"),
        sum_passing_yards=("passing_yards", "sum"),
        sum_rushing_yards=("rushing_yards", "sum"),
        sum_attempts=("attempts", "sum"),
        sum_carries=("carries", "sum"),
        sum_def_sacks=("def_sacks", "sum"),
        sum_def_interceptions=("def_interceptions", "sum"),
        sum_def_fumbles_forced=("def_fumbles_forced", "sum"),
    ).reset_index()

    agg["pass_yards_per_game"] = safe_divide(agg["sum_passing_yards"], agg["games_played"])
    agg["rush_yards_per_game"] = safe_divide(agg["sum_rushing_yards"], agg["games_played"])
    agg["pass_rate_pct"] = safe_divide(agg["sum_attempts"], agg["sum_attempts"] + agg["sum_carries"]) * 100
    agg["sacks_per_game"] = safe_divide(agg["sum_def_sacks"], agg["games_played"])
    agg["turnovers_forced_per_game"] = safe_divide(
        agg["sum_def_interceptions"] + agg["sum_def_fumbles_forced"], agg["games_played"]
    )

    return agg[[c for c in TEAM_SUMMARY_EMPTY_COLUMNS if c in agg.columns]]


def run_role_refresh(season, week, data_dir=None):
    """
    Refresh depth-chart + ESPN injury + role/eligibility outputs. Deliberately
    independent of the player-stat pipeline above - injury reports and depth
    charts change on their own cadence (see .github/workflows for the
    separate Friday/Sunday schedule) and this is fast enough to re-run close
    to lock without re-pulling the whole season's stats.

    A failed/empty depth-chart or injury fetch NEVER overwrites the last
    known good Parquet file for that source (see `write_snapshot_with_fallback`)
    - `player_role_context.parquet`, by contrast, is always safe to write
    fresh, since it's a pure recomputation from whatever depth/injury data is
    currently on disk (freshly fetched or a preserved prior snapshot either
    way), never itself "the last known good source."
    """
    from lib.eligibility import compute_role_context
    from lib.espn_injuries import fetch_espn_injuries
    from lib.manual_overrides import load_overrides

    data_dir = data_dir or DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()

    raw_depth = load_raw_depth_charts(season)
    depth_df = build_depth_chart_snapshot(raw_depth, season, week)
    depth_chart_source_timestamp = (
        depth_df["depth_chart_source_timestamp"].iloc[0] if not depth_df.empty else None
    )

    injuries_df, injury_run_metadata = fetch_espn_injuries()

    overrides_df, override_log = load_overrides(season=season, week=week)
    for line in override_log:
        print(f"[manual override] {line}")

    role_context_df = compute_role_context(depth_df, injuries_df, injury_run_metadata, overrides_df)

    depth_path = os.path.join(data_dir, "depth_charts_current.parquet")
    injuries_path = os.path.join(data_dir, "injuries_current.parquet")
    role_path = os.path.join(data_dir, "player_role_context.parquet")

    depth_written_fresh = write_snapshot_with_fallback(depth_df, depth_path, "depth chart")
    injuries_written_fresh = write_snapshot_with_fallback(injuries_df, injuries_path, "ESPN injury")
    role_context_df.to_parquet(role_path, index=False)

    if depth_written_fresh and not depth_df.empty:
        depth_status = "ok"
    elif not depth_written_fresh:
        depth_status = "stale_kept"
    else:
        depth_status = "unavailable"

    depth_chart_metadata = {
        "season": season,
        "week": week,
        "fetched_at": now_iso,
        "source_timestamp": depth_chart_source_timestamp,
        "record_count": int(len(depth_df)),
        "teams_covered": int(depth_df["canonical_team"].nunique()) if not depth_df.empty else 0,
        "status": depth_status,
    }
    with open(os.path.join(data_dir, "depth_chart_metadata.json"), "w") as f:
        json.dump(depth_chart_metadata, f, indent=2)

    injury_metadata = {**injury_run_metadata, "season": season, "week": week, "written_fresh": injuries_written_fresh}
    with open(os.path.join(data_dir, "injury_metadata.json"), "w") as f:
        json.dump(injury_metadata, f, indent=2)

    print(f"Depth chart snapshot: {json.dumps(depth_chart_metadata, indent=2)}")
    print(
        f"ESPN injuries: {injury_run_metadata.get('teams_succeeded')}/"
        f"{injury_run_metadata.get('teams_attempted')} teams succeeded, "
        f"source_success={injury_run_metadata.get('source_success')}"
    )
    print(f"Role context: {len(role_context_df)} players classified")
    if not role_context_df.empty:
        print(role_context_df["role_classification"].value_counts().to_string())

    return {
        "depth_chart_metadata": depth_chart_metadata,
        "injury_metadata": injury_metadata,
        "role_context_rows": int(len(role_context_df)),
    }


def run_pipeline():
    os.makedirs(DATA_DIR, exist_ok=True)

    active_season = determine_active_season()
    active_player_df = _try_load_player_stats(active_season)
    active_schedule_df = _try_load_schedule(active_season)

    if not active_schedule_df.empty:
        latest_completed_week, next_slate_week = determine_week_status(active_schedule_df, active_season)
    else:
        # No schedule published/available yet for the active season at all -
        # treat it the same as "before Week 1."
        latest_completed_week, next_slate_week = None, 1

    app_mode, source_season = determine_app_mode(active_season, latest_completed_week)
    status = "ok" if latest_completed_week is not None else "no_completed_weeks_yet"

    print(
        f"Active season {active_season}: app_mode={app_mode}, "
        f"latest_completed_week={latest_completed_week}, next_slate_week={next_slate_week}"
    )

    if app_mode == "in_season":
        weekly_df = build_players_weekly(active_player_df, active_season, latest_completed_week)
        current_df = build_players_current(weekly_df)
        defense_matchups_df = build_defense_matchups(weekly_df)
        raw_team_df = load_raw_team_stats(active_season)
        team_summary_df = build_team_summary(raw_team_df, active_season, latest_completed_week)
        baseline_df = pd.DataFrame(columns=PRIOR_SEASON_BASELINE_EMPTY_COLUMNS)
    else:
        print(f"No completed week for {active_season} yet - building {source_season} baseline for Week 1")
        weekly_df = pd.DataFrame(columns=PLAYER_STAT_COLUMNS + ["touches"])
        current_df = pd.DataFrame(columns=PLAYERS_CURRENT_EMPTY_COLUMNS)
        defense_matchups_df = pd.DataFrame(columns=DEFENSE_MATCHUPS_EMPTY_COLUMNS)
        team_summary_df = pd.DataFrame(columns=TEAM_SUMMARY_EMPTY_COLUMNS)
        raw_team_df = pd.DataFrame()

        prior_player_df = _try_load_player_stats(source_season)
        prior_reg = (
            prior_player_df[prior_player_df["season_type"] == "REG"]
            if not prior_player_df.empty else prior_player_df
        )
        if prior_reg.empty:
            raise RuntimeError(
                f"No usable prior-season ({source_season}) data available to build a "
                f"Week 1 baseline for {active_season}"
            )
        prior_max_week = int(prior_reg["week"].max())
        prior_weekly_df = build_players_weekly(prior_player_df, source_season, prior_max_week)
        baseline_df = build_prior_season_baseline(prior_weekly_df)

    weekly_df.to_parquet(os.path.join(DATA_DIR, "players_weekly.parquet"), index=False)
    current_df.to_parquet(os.path.join(DATA_DIR, "players_current.parquet"), index=False)
    defense_matchups_df.to_parquet(os.path.join(DATA_DIR, "defense_matchups.parquet"), index=False)
    team_summary_df.to_parquet(os.path.join(DATA_DIR, "team_summary.parquet"), index=False)
    baseline_df.to_parquet(os.path.join(DATA_DIR, "players_prior_season_baseline.parquet"), index=False)
    if not raw_team_df.empty:
        raw_team_df.to_parquet(os.path.join(DATA_DIR, "team_stats.parquet"), index=False)

    metadata = {
        "season": active_season,
        "active_season": active_season,
        "source_season": source_season,
        "app_mode": app_mode,
        "latest_completed_week": latest_completed_week,
        "next_slate_week": next_slate_week,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "player_rows": int(len(weekly_df)),
        "player_count": int(weekly_df["player_id"].nunique()) if not weekly_df.empty else 0,
        "teams": sorted(weekly_df["team"].dropna().unique().tolist()) if not weekly_df.empty else [],
        "prior_season_baseline_rows": int(len(baseline_df)),
    }
    with open(os.path.join(DATA_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote Parquet files + metadata.json to {DATA_DIR}")
    print(json.dumps(metadata, indent=2))

    print("\nRecord counts:")
    print(f"  players_weekly.parquet:              {len(weekly_df):>6} rows")
    print(f"  players_current.parquet:             {len(current_df):>6} rows")
    print(f"  defense_matchups.parquet:            {len(defense_matchups_df):>6} rows")
    print(f"  team_summary.parquet:                {len(team_summary_df):>6} rows")
    print(f"  team_stats.parquet (raw):            {len(raw_team_df):>6} rows")
    print(f"  players_prior_season_baseline.parquet: {len(baseline_df):>4} rows")

    print("\nRefreshing role/eligibility context (depth chart + ESPN injuries)...")
    role_week = next_slate_week if next_slate_week is not None else (latest_completed_week or 1)
    metadata["role_refresh"] = run_role_refresh(active_season, role_week)

    return metadata


if __name__ == "__main__":
    run_pipeline()
