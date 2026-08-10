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

Season, the latest fully-completed week, and the next DFS slate week are
all auto-detected from the schedule (using final scores, not just whether
nflreadpy has *any* rows for a week) so nobody has to hand-edit a week
number, and a week with any game still in progress is never treated as
complete.
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


def detect_season(max_lookback=3, now=None):
    """
    Find the most recent season with usable player-stats + schedule data.
    Returns (season, raw_player_stats_df, schedule_df).
    """
    now = now or datetime.now()
    # NFL seasons span Sep-Feb; before March, "this year" still means last
    # year's season (e.g. Jan 2026 games belong to the 2025 season).
    year = now.year if now.month >= 3 else now.year - 1

    for candidate in range(year, year - max_lookback - 1, -1):
        try:
            player_df = nfl.load_player_stats([candidate]).to_pandas()
            schedule_df = nfl.load_schedules([candidate]).to_pandas()
        except Exception as exc:
            print(f"Season {candidate}: unavailable ({exc})")
            continue

        if player_df.empty or "week" not in player_df.columns:
            continue
        if player_df[player_df["season_type"] == "REG"].empty:
            continue

        print(f"Using season {candidate}")
        return candidate, player_df, schedule_df

    raise RuntimeError("No NFL season data available from nflreadpy")


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


def build_players_current(weekly_df):
    """One row per player: season-to-date aggregates + derived analytics."""
    if weekly_df.empty:
        return pd.DataFrame(columns=PLAYERS_CURRENT_EMPTY_COLUMNS)

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

    recent_form = (
        weekly_df.groupby("player_id", group_keys=True)
        .apply(_player_recent_form, include_groups=False)
        .reset_index()
    )

    current = identity.merge(agg, on="player_id").merge(recent_form, on="player_id")
    current["season"] = weekly_df["season"].iloc[0]
    current["opportunity_trend"] = current["touches_wow_change"].apply(_classify_opportunity_trend)

    keep_cols = PLAYERS_CURRENT_EMPTY_COLUMNS
    current = current[[c for c in keep_cols if c in current.columns]]
    return current.sort_values("momentum_score", ascending=False, na_position="last").reset_index(drop=True)


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


def run_pipeline():
    os.makedirs(DATA_DIR, exist_ok=True)

    season, raw_player_df, schedule_df = detect_season()
    latest_completed_week, next_slate_week = determine_week_status(schedule_df, season)

    status = "ok" if latest_completed_week is not None else "no_completed_weeks_yet"
    print(f"Season {season}: latest_completed_week={latest_completed_week}, next_slate_week={next_slate_week}")

    weekly_df = build_players_weekly(raw_player_df, season, latest_completed_week)
    current_df = build_players_current(weekly_df)
    defense_matchups_df = build_defense_matchups(weekly_df)

    raw_team_df = load_raw_team_stats(season)
    team_summary_df = build_team_summary(raw_team_df, season, latest_completed_week)

    weekly_df.to_parquet(os.path.join(DATA_DIR, "players_weekly.parquet"), index=False)
    current_df.to_parquet(os.path.join(DATA_DIR, "players_current.parquet"), index=False)
    defense_matchups_df.to_parquet(os.path.join(DATA_DIR, "defense_matchups.parquet"), index=False)
    team_summary_df.to_parquet(os.path.join(DATA_DIR, "team_summary.parquet"), index=False)
    if not raw_team_df.empty:
        raw_team_df.to_parquet(os.path.join(DATA_DIR, "team_stats.parquet"), index=False)

    metadata = {
        "season": season,
        "latest_completed_week": latest_completed_week,
        "next_slate_week": next_slate_week,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "player_rows": int(len(weekly_df)),
        "player_count": int(weekly_df["player_id"].nunique()) if not weekly_df.empty else 0,
        "teams": sorted(weekly_df["team"].dropna().unique().tolist()) if not weekly_df.empty else [],
    }
    with open(os.path.join(DATA_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote Parquet files + metadata.json to {DATA_DIR}")
    print(json.dumps(metadata, indent=2))

    print("\nRecord counts:")
    print(f"  players_weekly.parquet:     {len(weekly_df):>6} rows")
    print(f"  players_current.parquet:    {len(current_df):>6} rows")
    print(f"  defense_matchups.parquet:   {len(defense_matchups_df):>6} rows")
    print(f"  team_summary.parquet:       {len(team_summary_df):>6} rows")
    print(f"  team_stats.parquet (raw):   {len(raw_team_df):>6} rows")

    return metadata


if __name__ == "__main__":
    run_pipeline()
