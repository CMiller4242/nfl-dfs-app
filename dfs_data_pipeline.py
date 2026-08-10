"""
NFL DFS data pipeline.

Pulls current-season player and team stats from nflreadpy, computes the
derived metrics the Streamlit app needs (rolling averages, efficiency,
opportunity trend, momentum score, defense-vs-position matchups), and
writes everything to /data as Parquet + a small metadata.json.

Designed to run unattended from GitHub Actions: the season and current
week are auto-detected from the data, nothing needs to be edited by hand.
"""

import json
import os
from datetime import datetime, timezone

import nflreadpy as nfl
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

ROLLING_WINDOW = 3
MOMENTUM_WEIGHTS = (0.5, 0.3, 0.2)  # last week, two weeks ago, three weeks ago
POSITIONS = ["QB", "RB", "WR", "TE"]

PLAYER_STAT_COLUMNS = [
    "player_id", "player_name", "player_display_name", "position", "position_group",
    "season", "week", "season_type", "team", "opponent_team",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    "fantasy_points", "fantasy_points_ppr",
]

TEAM_STAT_COLUMNS = [
    "season", "week", "team", "season_type", "opponent_team",
    "passing_yards", "passing_tds", "rushing_yards", "rushing_tds",
    "receiving_yards", "receiving_tds",
]


def detect_season(max_lookback=3):
    """
    Find the most recent season that has usable regular-season player
    stats, so nobody has to hand-edit a week/season number each week.
    Returns (season, raw_player_stats_df).
    """
    now = datetime.now()
    # NFL seasons span Sep-Feb; before March, "this year" still means last
    # year's season (e.g. Jan 2026 games belong to the 2025 season).
    year = now.year if now.month >= 3 else now.year - 1

    for candidate in range(year, year - max_lookback - 1, -1):
        try:
            df = nfl.load_player_stats([candidate]).to_pandas()
        except Exception as exc:
            print(f"Season {candidate}: unavailable ({exc})")
            continue

        if df.empty or "week" not in df.columns:
            continue

        reg = df[df["season_type"] == "REG"]
        if reg.empty:
            continue

        print(f"Using season {candidate}: {len(reg)} regular-season rows found")
        return candidate, df

    raise RuntimeError("No NFL season data available from nflreadpy")


def load_team_stats(season):
    try:
        df = nfl.load_team_stats([season]).to_pandas()
    except Exception as exc:
        print(f"Warning: could not load team stats: {exc}")
        return pd.DataFrame()

    df = df[(df["season"] == season) & (df["season_type"] == "REG")].copy()
    cols = [c for c in TEAM_STAT_COLUMNS if c in df.columns]
    return df[cols].reset_index(drop=True)


def prepare_player_stats(raw_df, season):
    """Filter to this season's regular-season skill-position rows and trim columns."""
    df = raw_df[(raw_df["season"] == season) & (raw_df["season_type"] == "REG")].copy()
    df = df[df["position"].isin(POSITIONS)].copy()
    cols = [c for c in PLAYER_STAT_COLUMNS if c in df.columns]
    df = df[cols].copy()
    for col in ["fantasy_points", "fantasy_points_ppr", "targets", "carries", "attempts",
                "receptions", "receiving_yards", "rushing_yards"]:
        if col in df.columns:
            df[col] = df[col].fillna(0)
    return df.sort_values(["player_id", "week"]).reset_index(drop=True)


def compute_derived_metrics(df):
    """Add rolling averages, efficiency, opportunity trend, and momentum score."""
    df = df.sort_values(["player_id", "week"]).copy()
    grp = df.groupby("player_id", group_keys=False)

    # Rolling 3-week averages
    for col in ["fantasy_points_ppr", "targets", "carries", "receiving_yards", "rushing_yards"]:
        df[f"{col}_3wk_avg"] = grp[col].transform(
            lambda s: s.rolling(ROLLING_WINDOW, min_periods=1).mean()
        )

    # Season-to-date average (as of each row's week) - used as the "season average" input
    # to the projection blend on the Lineup Helper page.
    df["fp_season_avg"] = grp["fantasy_points_ppr"].transform(lambda s: s.expanding().mean())

    # Efficiency
    df["yards_per_target"] = (df["receiving_yards"] / df["targets"]).where(df["targets"] > 0)
    df["yards_per_carry"] = (df["rushing_yards"] / df["carries"]).where(df["carries"] > 0)
    df["catch_rate"] = (df["receptions"] / df["targets"]).where(df["targets"] > 0)

    df["touches"] = df[["carries", "targets", "attempts"]].fillna(0).sum(axis=1)
    df["points_per_touch"] = (df["fantasy_points_ppr"] / df["touches"]).where(df["touches"] > 0)

    # Opportunity trend: week-over-week touches change
    df["touches_prev_week"] = grp["touches"].shift(1)
    df["touches_wow_change"] = df["touches"] - df["touches_prev_week"]

    # Momentum score: from the perspective of each row's week being "last week",
    # 50% last week + 30% two weeks ago + 20% three weeks ago. Missing history
    # (e.g. a player's first couple of games) counts as 0, which naturally
    # tempers momentum for players without a track record yet.
    w1, w2, w3 = MOMENTUM_WEIGHTS
    fp_last = df["fantasy_points_ppr"].fillna(0)
    fp_2wk_ago = grp["fantasy_points_ppr"].shift(1).fillna(0)
    fp_3wk_ago = grp["fantasy_points_ppr"].shift(2).fillna(0)
    df["momentum_score"] = fp_last * w1 + fp_2wk_ago * w2 + fp_3wk_ago * w3

    return df


def compute_current_snapshot(df):
    """One row per player: their most recently played week."""
    idx = df.groupby("player_id")["week"].idxmax()
    return df.loc[idx].sort_values("momentum_score", ascending=False).reset_index(drop=True)


def compute_defense_matchups(df):
    """Average fantasy points allowed by each defense to each position, with a
    1-5 matchup rating computed separately per position (positions score on
    very different scales, so ratings must not be compared across positions)."""
    matchups = (
        df.groupby(["opponent_team", "position"])["fantasy_points_ppr"]
        .agg(avg_points_allowed="mean", games="count")
        .reset_index()
        .rename(columns={"opponent_team": "defense_team"})
    )

    matchups["matchup_rank"] = matchups.groupby("position")["avg_points_allowed"].rank(
        ascending=False, method="min"
    )

    # Rescale rank to a 1-5 rating within each position (5 = best matchup for offense).
    max_rank_by_position = matchups.groupby("position")["matchup_rank"].transform("max")
    matchups["matchup_rating"] = (
        ((matchups["matchup_rank"] - 1) / (max_rank_by_position - 1)) * 4 + 1
    ).where(max_rank_by_position > 1, 3.0)

    return matchups.sort_values(["position", "matchup_rank"]).reset_index(drop=True)


def run_pipeline():
    os.makedirs(DATA_DIR, exist_ok=True)

    season, raw_player_df = detect_season()
    player_df = prepare_player_stats(raw_player_df, season)

    if player_df.empty:
        raise RuntimeError(f"No skill-position player rows found for season {season}")

    current_week = int(player_df["week"].max())
    print(f"Season {season}, current week {current_week}")

    player_df = compute_derived_metrics(player_df)
    current_snapshot = compute_current_snapshot(player_df)
    defense_matchups = compute_defense_matchups(player_df)
    team_df = load_team_stats(season)

    player_df.to_parquet(os.path.join(DATA_DIR, "players_weekly.parquet"), index=False)
    current_snapshot.to_parquet(os.path.join(DATA_DIR, "players_current.parquet"), index=False)
    defense_matchups.to_parquet(os.path.join(DATA_DIR, "defense_matchups.parquet"), index=False)
    if not team_df.empty:
        team_df.to_parquet(os.path.join(DATA_DIR, "team_stats.parquet"), index=False)

    metadata = {
        "season": season,
        "current_week": current_week,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "player_rows": int(len(player_df)),
        "player_count": int(player_df["player_id"].nunique()),
        "teams": sorted(player_df["team"].dropna().unique().tolist()),
    }
    with open(os.path.join(DATA_DIR, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"Wrote Parquet files + metadata.json to {DATA_DIR}")
    print(json.dumps(metadata, indent=2))
    return metadata


if __name__ == "__main__":
    run_pipeline()
