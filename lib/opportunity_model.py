"""
In-season Opportunity Model: a transparent, configurable workload/role-trend
classifier built entirely from players_weekly.parquet's real, confirmed
columns (carries, targets, receptions, touches, rushing_yards,
receiving_yards, receiving_air_yards, receiving_yards_after_catch,
target_share, air_yards_share, attempts, passing_yards, passing_tds,
fantasy_points_ppr). No snap count, route rate, red-zone work, or any other
field the pipeline doesn't actually have is used or invented.

This module has two independent halves:

  1. `build_player_opportunity_reporting` - pure windowed aggregation +
     classification, with NO knowledge of DK salaries, role/injury context,
     or projections. One row per current-season player. This is what the
     pipeline writes to data/player_opportunity_reporting.parquet.
  2. `compute_role_safety_gate` - a separate, narrow function that takes an
     opportunity row's classification TOGETHER WITH a player's already-
     resolved role-eligibility fields (role_classification,
     role_data_freshness, role_eligible_for_pool/top_values) and decides
     whether workload can earn a research-only Player Pool promotion - see
     lib.opportunity_config's "Player Pool promotion policy" docstring for
     the exact, narrow rule. This function is the ONLY place opportunity
     data is allowed to touch role safety, and even then it can only ever
     ADD a new `opportunity_pool_eligible` field for one specific
     classification (bench_no_clear_path) - it never changes
     role_classification or role_eligible_for_pool themselves, and it never
     promotes inactive, role_unresolved, or contingent_backup players.

Both halves are deliberately separate so role safety data never has to flow
into the pure classification math, and so the classification math can be
fully unit-tested without needing any role/injury fixtures at all.
"""

from datetime import datetime, timezone

import pandas as pd

from dfs_data_pipeline import safe_divide
from lib.opportunity_config import (
    MIN_GAMES_FOR_ANY_CLASSIFICATION,
    MIN_GAMES_FOR_ESTABLISHED_SAMPLE,
    OPPORTUNITY_POOL_PROMOTION_MIN_GAMES,
    OPPORTUNITY_POOL_PROMOTION_WORKLOAD_FLOOR,
    QB_DECLINING_ATTEMPTS_DELTA,
    QB_DECLINING_RUSH_ATTEMPTS_DELTA,
    QB_LIMITED_OPPORTUNITY_ATTEMPTS_FLOOR,
    QB_RISING_ATTEMPTS_DELTA,
    QB_RISING_RUSH_ATTEMPTS_DELTA,
    RB_DECLINING_CARRIES_DELTA,
    RB_DECLINING_TARGETS_DELTA,
    RB_DECLINING_TOUCHES_DELTA,
    RB_LIMITED_OPPORTUNITY_TOUCHES_FLOOR,
    RB_RISING_CARRIES_DELTA,
    RB_RISING_TARGETS_DELTA,
    RB_RISING_TOUCHES_DELTA,
    WR_TE_DECLINING_AIR_YARDS_SHARE_DELTA_PCT_POINTS,
    WR_TE_DECLINING_TARGET_SHARE_DELTA_PCT_POINTS,
    WR_TE_DECLINING_TARGETS_DELTA,
    WR_TE_LIMITED_OPPORTUNITY_TARGETS_FLOOR,
    WR_TE_RISING_AIR_YARDS_SHARE_DELTA_PCT_POINTS,
    WR_TE_RISING_TARGET_SHARE_DELTA_PCT_POINTS,
    WR_TE_RISING_TARGETS_DELTA,
)

# A sentinel "window" large enough that tail(n) always returns a player's
# entire played-games history - i.e. "season to date" computed with the
# exact same generic windowing logic as "last N", rather than a second,
# duplicated code path. No NFL regular season reaches this many games.
SEASON_TO_DATE_WINDOW = 99

WINDOW_SUM_COLUMNS = [
    "carries", "rushing_yards", "targets", "receptions", "receiving_yards",
    "receiving_air_yards", "receiving_yards_after_catch", "touches",
    "fantasy_points", "attempts", "passing_yards", "passing_tds",
]
WINDOW_MEAN_COLUMNS = ["target_share", "air_yards_share"]

# Raw-count metrics get an absolute per-game delta vs season; share metrics
# get a percentage-point delta. "total_yards" is derived (rushing+receiving)
# so it's listed separately from WINDOW_SUM_COLUMNS, which only covers
# directly-aggregated source fields.
DELTA_RAW_METRICS = WINDOW_SUM_COLUMNS + ["total_yards"]
DELTA_PCT_METRICS = WINDOW_MEAN_COLUMNS

IDENTITY_COLUMNS = ["season", "player_id", "player_name", "team", "position",
                    "games_played", "latest_played_week", "source_last_updated_utc"]
CONTEXT_COLUMNS = ["sample_size_label", "opportunity_data_status",
                   "prior_season_fppg", "prior_season_games_played"]
CLASSIFICATION_COLUMNS = ["opportunity_label", "opportunity_reason", "supporting_metrics",
                          "latest_2_games_summary", "latest_3_games_summary",
                          "confidence_label", "confidence_reason"]


def _window_column_names(prefix: str) -> list:
    cols = [f"{prefix}_games_used"]
    for c in WINDOW_SUM_COLUMNS:
        cols += [f"{c}_{prefix}_total", f"{c}_{prefix}_per_game"]
    for c in WINDOW_MEAN_COLUMNS:
        cols.append(f"{c}_{prefix}_pct")
    cols += [f"total_yards_{prefix}_total", f"total_yards_{prefix}_per_game",
             f"catch_rate_{prefix}", f"yards_per_target_{prefix}", f"yards_per_reception_{prefix}"]
    return cols


def _delta_column_names(window: str) -> list:
    cols = [f"{m}_{window}_delta_vs_season" for m in DELTA_RAW_METRICS]
    cols += [f"{m}_{window}_delta_vs_season_pct_points" for m in DELTA_PCT_METRICS]
    return cols


OPPORTUNITY_REPORTING_COLUMNS = (
    IDENTITY_COLUMNS + CONTEXT_COLUMNS
    + _window_column_names("season")
    + _window_column_names("last_2") + _delta_column_names("last_2")
    + _window_column_names("last_3") + _delta_column_names("last_3")
    + CLASSIFICATION_COLUMNS
)


def _window_metrics(weekly_df: pd.DataFrame, window_games: int, prefix: str) -> pd.DataFrame:
    """
    One row per player_id: totals/per-game rates/means from that player's
    most recent `window_games` PLAYED rows (`tail()` after sorting by week -
    a bye is simply an absent row, never zero-filled). A player with fewer
    than `window_games` played games uses whatever games exist -
    `{prefix}_games_used` records exactly how many, so this is never
    misread as a full window. `prefix` becomes the column-name prefix (e.g.
    "last_2", or SEASON_TO_DATE_WINDOW's caller passes "season").
    """
    sum_cols = [c for c in WINDOW_SUM_COLUMNS if c in weekly_df.columns]
    mean_cols = [c for c in WINDOW_MEAN_COLUMNS if c in weekly_df.columns]
    empty_cols = ["player_id"] + _window_column_names(prefix)

    if weekly_df.empty:
        return pd.DataFrame(columns=empty_cols)

    df = weekly_df.sort_values(["player_id", "week"])

    def _agg(group):
        recent = group.tail(window_games)
        n = len(recent)
        out = {f"{prefix}_games_used": n}
        for c in sum_cols:
            total = recent[c].sum()
            out[f"{c}_{prefix}_total"] = total
            out[f"{c}_{prefix}_per_game"] = safe_divide(total, n)
        for c in mean_cols:
            out[f"{c}_{prefix}_pct"] = (recent[c].mean() * 100) if n else None
        return pd.Series(out)

    result = df.groupby("player_id").apply(_agg, include_groups=False).reset_index()

    rush = result.get(f"rushing_yards_{prefix}_total", pd.Series(0, index=result.index))
    rec = result.get(f"receiving_yards_{prefix}_total", pd.Series(0, index=result.index))
    result[f"total_yards_{prefix}_total"] = rush + rec
    result[f"total_yards_{prefix}_per_game"] = safe_divide(
        result[f"total_yards_{prefix}_total"], result[f"{prefix}_games_used"]
    )

    receptions_total = result.get(f"receptions_{prefix}_total")
    targets_total = result.get(f"targets_{prefix}_total")
    receiving_yards_total = result.get(f"receiving_yards_{prefix}_total")
    if receptions_total is not None and targets_total is not None:
        result[f"catch_rate_{prefix}"] = safe_divide(receptions_total, targets_total)
    if receiving_yards_total is not None and targets_total is not None:
        result[f"yards_per_target_{prefix}"] = safe_divide(receiving_yards_total, targets_total)
    if receiving_yards_total is not None and receptions_total is not None:
        result[f"yards_per_reception_{prefix}"] = safe_divide(receiving_yards_total, receptions_total)

    for col in empty_cols:
        if col not in result.columns:
            result[col] = pd.NA
    return result[empty_cols]


def _add_deltas(df: pd.DataFrame, window: str) -> pd.DataFrame:
    for metric in DELTA_RAW_METRICS:
        recent_col = f"{metric}_{window}_per_game"
        season_col = f"{metric}_season_per_game"
        out_col = f"{metric}_{window}_delta_vs_season"
        if recent_col in df.columns and season_col in df.columns:
            df[out_col] = df[recent_col] - df[season_col]
        else:
            df[out_col] = pd.NA
    for metric in DELTA_PCT_METRICS:
        recent_col = f"{metric}_{window}_pct"
        season_col = f"{metric}_season_pct"
        out_col = f"{metric}_{window}_delta_vs_season_pct_points"
        if recent_col in df.columns and season_col in df.columns:
            df[out_col] = df[recent_col] - df[season_col]
        else:
            df[out_col] = pd.NA
    return df


def _sample_size_label(games_played) -> str:
    if pd.isna(games_played) or games_played < MIN_GAMES_FOR_ANY_CLASSIFICATION:
        return "insufficient_sample"
    if games_played < MIN_GAMES_FOR_ESTABLISHED_SAMPLE:
        return "early_sample"
    return "established_sample"


def _fmt1(value) -> str:
    return f"{value:.1f}" if pd.notna(value) else "—"


def _window_summary(row, prefix: str) -> str:
    games_used = row.get(f"{prefix}_games_used")
    if pd.isna(games_used) or games_used == 0:
        return "No played games in this window."
    position = row.get("position")
    window_label = "Last 2 games" if prefix == "last_2" else "Last 3 games"

    if position == "RB":
        parts = [
            (f"carries_{prefix}_per_game", "carries/game"),
            (f"targets_{prefix}_per_game", "targets/game"),
            (f"touches_{prefix}_per_game", "touches/game"),
            (f"total_yards_{prefix}_per_game", "total yards/game"),
        ]
    elif position in ("WR", "TE"):
        parts = [
            (f"targets_{prefix}_per_game", "targets/game"),
            (f"receptions_{prefix}_per_game", "receptions/game"),
            (f"receiving_yards_{prefix}_per_game", "receiving yards/game"),
        ]
    elif position == "QB":
        parts = [
            (f"attempts_{prefix}_per_game", "pass attempts/game"),
            (f"carries_{prefix}_per_game", "rush attempts/game"),
            (f"passing_yards_{prefix}_per_game", "passing yards/game"),
        ]
    else:
        return ""

    rendered = [f"{_fmt1(row.get(col))} {label}" for col, label in parts]
    return f"{window_label} ({int(games_used)} game{'s' if games_used != 1 else ''}): " + ", ".join(rendered)


def _classify_rb(row) -> tuple:
    touches_delta = row.get("touches_last_2_delta_vs_season")
    carries_delta = row.get("carries_last_2_delta_vs_season")
    targets_delta = row.get("targets_last_2_delta_vs_season")
    touches_pg = row.get("touches_last_2_per_game")

    rising = (
        (pd.notna(touches_delta) and touches_delta >= RB_RISING_TOUCHES_DELTA)
        or (pd.notna(carries_delta) and carries_delta >= RB_RISING_CARRIES_DELTA)
        or (pd.notna(targets_delta) and targets_delta >= RB_RISING_TARGETS_DELTA)
    )
    declining = (
        (pd.notna(touches_delta) and touches_delta <= RB_DECLINING_TOUCHES_DELTA)
        or (pd.notna(carries_delta) and carries_delta <= RB_DECLINING_CARRIES_DELTA)
        or (pd.notna(targets_delta) and targets_delta <= RB_DECLINING_TARGETS_DELTA)
    )

    supporting = [s for s in [
        f"touches/game {touches_delta:+.1f} vs season" if pd.notna(touches_delta) else None,
        f"carries/game {carries_delta:+.1f} vs season" if pd.notna(carries_delta) else None,
        f"targets/game {targets_delta:+.1f} vs season" if pd.notna(targets_delta) else None,
    ] if s]

    if rising and declining:
        return "stable_opportunity", "Mixed signals across carries/targets/touches - no single dominant trend.", supporting
    if rising:
        reason = f"Rising opportunity — {_fmt1(touches_pg)} touches/game over last 2"
        if pd.notna(touches_delta):
            reason += f" versus season average ({touches_delta:+.1f})"
        reason += "."
        return "rising_opportunity", reason, supporting
    if declining:
        reason = f"Declining opportunity — {_fmt1(touches_pg)} touches/game over last 2"
        if pd.notna(touches_delta):
            reason += f" versus season average ({touches_delta:+.1f})"
        reason += "."
        return "declining_opportunity", reason, supporting
    if pd.notna(touches_pg) and touches_pg < RB_LIMITED_OPPORTUNITY_TOUCHES_FLOOR:
        return "limited_opportunity", f"Limited opportunity — only {_fmt1(touches_pg)} touches/game over the last 2 games.", supporting
    return "stable_opportunity", "Workload steady versus the season average - no material rise or decline.", supporting


def _classify_wr_te(row) -> tuple:
    targets_delta = row.get("targets_last_2_delta_vs_season")
    target_share_delta = row.get("target_share_last_2_delta_vs_season_pct_points")
    air_yards_share_delta = row.get("air_yards_share_last_2_delta_vs_season_pct_points")
    targets_pg = row.get("targets_last_2_per_game")
    season_targets_pg = row.get("targets_season_per_game")

    rising = (
        (pd.notna(targets_delta) and targets_delta >= WR_TE_RISING_TARGETS_DELTA)
        or (pd.notna(target_share_delta) and target_share_delta >= WR_TE_RISING_TARGET_SHARE_DELTA_PCT_POINTS)
        or (pd.notna(air_yards_share_delta) and air_yards_share_delta >= WR_TE_RISING_AIR_YARDS_SHARE_DELTA_PCT_POINTS)
    )
    declining = (
        (pd.notna(targets_delta) and targets_delta <= WR_TE_DECLINING_TARGETS_DELTA)
        or (pd.notna(target_share_delta) and target_share_delta <= WR_TE_DECLINING_TARGET_SHARE_DELTA_PCT_POINTS)
        or (pd.notna(air_yards_share_delta) and air_yards_share_delta <= WR_TE_DECLINING_AIR_YARDS_SHARE_DELTA_PCT_POINTS)
    )

    supporting = [s for s in [
        f"targets/game {targets_delta:+.1f} vs season" if pd.notna(targets_delta) else None,
        f"target share {target_share_delta:+.1f} pts vs season" if pd.notna(target_share_delta) else None,
        f"air yards share {air_yards_share_delta:+.1f} pts vs season" if pd.notna(air_yards_share_delta) else None,
    ] if s]

    if rising and declining:
        return "stable_opportunity", "Mixed target-volume/share signals - no single dominant trend.", supporting
    if rising:
        reason = f"Rising opportunity — {_fmt1(targets_pg)} targets/game over last 2"
        if pd.notna(season_targets_pg) and pd.notna(targets_delta):
            reason += f" versus {_fmt1(season_targets_pg)} season average ({targets_delta:+.1f})"
        if pd.notna(target_share_delta):
            reason += f", with target share up {target_share_delta:+.1f} percentage points"
        reason += "."
        return "rising_opportunity", reason, supporting
    if declining:
        reason = f"Declining opportunity — {_fmt1(targets_pg)} targets/game over last 2"
        if pd.notna(season_targets_pg) and pd.notna(targets_delta):
            reason += f" versus {_fmt1(season_targets_pg)} season average ({targets_delta:+.1f})"
        reason += "."
        return "declining_opportunity", reason, supporting
    if pd.notna(targets_pg) and targets_pg < WR_TE_LIMITED_OPPORTUNITY_TARGETS_FLOOR:
        return "limited_opportunity", f"Limited opportunity — only {_fmt1(targets_pg)} targets/game over the last 2 games.", supporting
    return "stable_opportunity", "Target volume and share steady versus the season average.", supporting


def _classify_qb(row) -> tuple:
    attempts_delta = row.get("attempts_last_2_delta_vs_season")
    rush_delta = row.get("carries_last_2_delta_vs_season")
    attempts_pg = row.get("attempts_last_2_per_game")

    rising = (
        (pd.notna(attempts_delta) and attempts_delta >= QB_RISING_ATTEMPTS_DELTA)
        or (pd.notna(rush_delta) and rush_delta >= QB_RISING_RUSH_ATTEMPTS_DELTA)
    )
    declining = (
        (pd.notna(attempts_delta) and attempts_delta <= QB_DECLINING_ATTEMPTS_DELTA)
        or (pd.notna(rush_delta) and rush_delta <= QB_DECLINING_RUSH_ATTEMPTS_DELTA)
    )

    supporting = [s for s in [
        f"pass attempts/game {attempts_delta:+.1f} vs season" if pd.notna(attempts_delta) else None,
        f"rush attempts/game {rush_delta:+.1f} vs season" if pd.notna(rush_delta) else None,
    ] if s]

    if rising and declining:
        return "stable_opportunity", "Mixed passing/rushing volume signals - no single dominant trend.", supporting
    if rising:
        reason = f"Rising opportunity — {_fmt1(attempts_pg)} pass attempts/game over last 2, with increased volume."
        return "rising_opportunity", reason, supporting
    if declining:
        reason = "Declining opportunity — passing/rushing volume down versus the season average."
        return "declining_opportunity", reason, supporting
    if pd.notna(attempts_pg) and attempts_pg < QB_LIMITED_OPPORTUNITY_ATTEMPTS_FLOOR:
        return "limited_opportunity", f"Limited opportunity — only {_fmt1(attempts_pg)} pass attempts/game over the last 2 games.", supporting
    return "stable_opportunity", "Passing and rushing volume steady versus the season average.", supporting


def _classify_row(row) -> dict:
    games_played = row.get("games_played")
    position = row.get("position")

    if pd.isna(games_played) or games_played < MIN_GAMES_FOR_ANY_CLASSIFICATION:
        n = int(games_played) if pd.notna(games_played) else 0
        return {
            "opportunity_label": "insufficient_sample",
            "opportunity_reason": "Insufficient sample - fewer than the minimum played games for any workload classification.",
            "supporting_metrics": "",
            "latest_2_games_summary": "",
            "latest_3_games_summary": "",
            "confidence_label": "insufficient_sample",
            "confidence_reason": f"Only {n} game{'s' if n != 1 else ''} played this season.",
        }

    confidence_label = "early_sample" if games_played < MIN_GAMES_FOR_ESTABLISHED_SAMPLE else "established_sample"
    confidence_reason = (
        f"{int(games_played)} game{'s' if games_played != 1 else ''} played this season - "
        + ("read this as an early-season trend, not established form." if confidence_label == "early_sample"
           else "a reasonably established current-season sample.")
    )

    if position == "RB":
        label, reason, supporting = _classify_rb(row)
    elif position in ("WR", "TE"):
        label, reason, supporting = _classify_wr_te(row)
    elif position == "QB":
        label, reason, supporting = _classify_qb(row)
    else:
        label, reason, supporting = "not_applicable", "Position not covered by the opportunity model.", []

    return {
        "opportunity_label": label,
        "opportunity_reason": reason,
        "supporting_metrics": "; ".join(supporting),
        "latest_2_games_summary": _window_summary(row, "last_2"),
        "latest_3_games_summary": _window_summary(row, "last_3"),
        "confidence_label": confidence_label,
        "confidence_reason": confidence_reason,
    }


def build_player_opportunity_reporting(
    weekly_df: pd.DataFrame, prior_baseline_df: pd.DataFrame = None, season: int = None
) -> pd.DataFrame:
    """
    One row per current-season player: season-to-date + last-2/last-3
    PLAYED-game windows, season-vs-recent deltas, and an explainable
    workload classification. Built entirely from players_weekly.parquet -
    byes are simply absent rows (never zero-filled), and a player's actual
    games_used_last_2/games_used_last_3 always reflects how many played
    games really existed, never a fabricated full window.
    """
    if weekly_df.empty:
        return pd.DataFrame(columns=OPPORTUNITY_REPORTING_COLUMNS)

    weekly_df = weekly_df.copy()
    weekly_df["fantasy_points"] = weekly_df["fantasy_points_ppr"]

    identity = (
        weekly_df.sort_values(["player_id", "week"])
        .groupby("player_id")
        .agg(
            player_name=("player_display_name", "last"),
            team=("team", "last"),
            position=("position", "last"),
            latest_played_week=("week", "max"),
        )
        .reset_index()
    )

    season_metrics = _window_metrics(weekly_df, SEASON_TO_DATE_WINDOW, "season")
    last_2 = _window_metrics(weekly_df, 2, "last_2")
    last_3 = _window_metrics(weekly_df, 3, "last_3")

    result = (
        identity.merge(season_metrics, on="player_id", how="left")
        .merge(last_2, on="player_id", how="left")
        .merge(last_3, on="player_id", how="left")
    )
    result["games_played"] = result["season_games_used"]
    result["season"] = season if season is not None else (
        int(weekly_df["season"].iloc[0]) if "season" in weekly_df.columns and not weekly_df.empty else None
    )
    result["source_last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    result["sample_size_label"] = result["games_played"].apply(_sample_size_label)
    result["opportunity_data_status"] = "ok"

    result = _add_deltas(result, "last_2")
    result = _add_deltas(result, "last_3")

    result = _merge_prior_season_reference(result, prior_baseline_df)

    classifications = result.apply(_classify_row, axis=1, result_type="expand")
    result = pd.concat([result, classifications], axis=1)

    for col in OPPORTUNITY_REPORTING_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    return result[OPPORTUNITY_REPORTING_COLUMNS].reset_index(drop=True)


def _merge_prior_season_reference(df: pd.DataFrame, prior_baseline_df: pd.DataFrame) -> pd.DataFrame:
    """
    Prior-season FPPG/games, clearly prefixed and merged by player_id - pure
    reference context, NEVER blended into any current-season workload
    calculation or delta above. Null when the player has no prior-season
    history (e.g. a rookie) or the baseline table is empty (as it
    deliberately is during in-season mode - see dfs_data_pipeline.run_pipeline).
    """
    out = df.copy()
    has_data = (
        prior_baseline_df is not None and not prior_baseline_df.empty
        and "player_id" in prior_baseline_df.columns
    )
    if not has_data:
        out["prior_season_fppg"] = pd.NA
        out["prior_season_games_played"] = pd.NA
        return out

    ref = prior_baseline_df[["player_id", "avg_fantasy_points", "games_played"]].rename(columns={
        "avg_fantasy_points": "prior_season_fppg", "games_played": "prior_season_games_played",
    }).drop_duplicates(subset=["player_id"])
    out = out.merge(ref, on="player_id", how="left")
    return out


# ---------------------------------------------------------------------------
# Role-safety integration (item 11) - see lib.opportunity_config's "Player
# Pool promotion policy" for the exact rule. Deliberately separate from
# `build_player_opportunity_reporting` above: this function needs role
# fields the pure opportunity mart never computes or stores.
# ---------------------------------------------------------------------------
_PRIMARY_WORKLOAD_LAST_2_COLUMN = {
    "RB": "touches_last_2_per_game",
    "WR": "targets_last_2_per_game",
    "TE": "targets_last_2_per_game",
    "QB": "attempts_last_2_per_game",
}


def _safe_bool(value) -> bool:
    """`bool(pd.NA)` (and friends) raise - this treats any null as False,
    never an ambiguous-truth-value crash."""
    return bool(value) if pd.notna(value) else False


def primary_workload_last_2(row) -> float:
    """The position-appropriate primary volume metric, last 2 games/game -
    touches for RB, targets for WR/TE, pass attempts for QB. None for any
    other position."""
    col = _PRIMARY_WORKLOAD_LAST_2_COLUMN.get(row.get("position"))
    return row.get(col) if col else None


def compute_role_safety_gate(row) -> dict:
    """
    Given one player's row (already carrying `opportunity_label`,
    `games_played`, `position`, the last-2 workload fields, AND the
    already-resolved role fields `role_classification`,
    `role_data_freshness`, `role_eligible_for_pool`,
    `role_eligible_for_top_values`), return
    {opportunity_pool_eligible, opportunity_top_value_eligible,
    opportunity_eligibility_reason}.

    This NEVER overrides inactive or role_unresolved, and NEVER promotes
    contingent_backup (a rising-opportunity player who is contingent on a
    Questionable/Doubtful blocker remains monitor-only - see
    lib.opportunity_config). The only classification this can promote
    beyond the existing role engine's own role_eligible_for_pool is
    bench_no_clear_path, and only into the Player Pool (never Top Value),
    and only when every configured gate passes.
    """
    role_classification = row.get("role_classification")
    role_eligible_for_pool = _safe_bool(row.get("role_eligible_for_pool"))
    role_eligible_for_top_values = _safe_bool(row.get("role_eligible_for_top_values"))

    if role_classification in ("inactive", "role_unresolved") or role_classification is None:
        reason = (
            "Role safety: inactive players are never eligible." if role_classification == "inactive"
            else "Role safety: role/injury identity is unresolved."
        )
        return {
            "opportunity_pool_eligible": False,
            "opportunity_top_value_eligible": False,
            "opportunity_eligibility_reason": reason,
        }

    if role_eligible_for_pool or role_classification != "bench_no_clear_path":
        return {
            "opportunity_pool_eligible": role_eligible_for_pool,
            "opportunity_top_value_eligible": role_eligible_for_top_values,
            "opportunity_eligibility_reason": (
                "Existing role eligibility applies; opportunity classification adds no promotion here."
            ),
        }

    # Only remaining case: role_classification == "bench_no_clear_path" and
    # role_eligible_for_pool is False - the one narrow, configured promotion.
    role_data_freshness = row.get("role_data_freshness")
    role_fresh = pd.notna(role_data_freshness) and role_data_freshness == "fresh"
    games_played = row.get("games_played")
    position = row.get("position")
    opportunity_label = row.get("opportunity_label")
    workload = primary_workload_last_2(row)
    floor = OPPORTUNITY_POOL_PROMOTION_WORKLOAD_FLOOR.get(position)

    promote = (
        role_fresh
        and pd.notna(games_played) and games_played >= OPPORTUNITY_POOL_PROMOTION_MIN_GAMES
        and pd.notna(opportunity_label) and opportunity_label == "rising_opportunity"
        and floor is not None and pd.notna(workload) and workload >= floor
    )
    if promote:
        return {
            "opportunity_pool_eligible": True,
            "opportunity_top_value_eligible": False,
            "opportunity_eligibility_reason": (
                f"Player Pool only - rising workload ({workload:.1f} primary volume/game, last 2 games) "
                "overcomes the bench/no-clear-path exclusion for research purposes; not yet Top-Value eligible."
            ),
        }
    return {
        "opportunity_pool_eligible": False,
        "opportunity_top_value_eligible": False,
        "opportunity_eligibility_reason": "Bench, no clear path, and the rising-workload promotion gates are not met.",
    }
