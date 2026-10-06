"""
Non-UI builder for the Matchup Analyzer Expanded page
(pages/5_Matchup_Analyzer.py). This module joins the current DK salary slate
to marts that are ALREADY computed - it never recomputes a player aggregate,
DvP number, or team trend itself. It reuses the existing identity-matching,
projection, and role/eligibility engines exactly as-is:

  - `lib.dk_helper.match_dk_players` / `compute_projections` (unchanged
    transparent projection formula, unchanged strict/fuzzy-restricted
    matching ladder)
  - `lib.dk_helper.match_dk_players_prior_season` (unchanged prior-season
    identity match, used here only for its stat carry - not its projection)
  - `lib.eligibility.attach_role_context_to_dk_rows` (unchanged role/
    injury/depth-chart engine)

On top of that shared foundation, this module adds the additional
current-season workload/yardage/per-game columns, prior-season FPPG,
team-environment context, and the extra DvP fields (last-3 trend, sample
size, rank) that the Lineup Helper page doesn't need but this research page
does - via plain left-merges keyed on the same identities already resolved
above (`stat_player_id` for player stats, canonical team for team reporting,
`(opponent, Position)` for defense reporting). A merge that can't resolve
leaves the new columns null, never a fabricated value, and never borrows
another player's/team's data.
"""

import pandas as pd

from lib.dk_helper import (
    compute_projections,
    match_dk_players,
    match_dk_players_prior_season,
    needs_review as stats_needs_review,
)
from lib.defense_trends import DVP_TREND_DISPLAY
from lib.eligibility import attach_role_context_to_dk_rows
from lib.opportunity_config import (
    CONFIDENCE_LABEL_DISPLAY,
    OPPORTUNITY_LABEL_DISPLAY,
    POOL_PROMOTION_DISPLAY_LABEL,
    WORKLOAD_WATCHLIST_DISPLAY_LABEL,
)
from lib.opportunity_model import compute_role_safety_gate
from lib.player_identity import normalize_team
from lib.team_trends import SAMPLE_SIZE_DISPLAY

# ---------------------------------------------------------------------------
# Human-readable role labels (never a raw role_classification value in the UI)
# ---------------------------------------------------------------------------
ROLE_LABEL_DISPLAY = {
    "confirmed_starter": "Confirmed Starter",
    "standard_eligible_rotation": "Eligible Rotation",
    "injury_elevated_backup": "Injury-Elevated",
    "contingent_backup": "Monitor Injury Status",
    "bench_no_clear_path": "No Clear Opportunity Path",
    "role_unresolved": "Role Needs Review",
    "inactive": "Inactive",
}

# Games-played tiers for the current season sample-size label - the exact
# same vocabulary/thresholds already used by Team Trends / Defense vs
# Position (dfs_data_pipeline.SAMPLE_SIZE_LIMITED_MIN_GAMES /
# SAMPLE_SIZE_FULL_MIN_GAMES), reused here rather than inventing a new one.
SAMPLE_SIZE_LIMITED_MIN_GAMES = 3
SAMPLE_SIZE_FULL_MIN_GAMES = 6

# A player at or below this many current-season games played gets an
# explicit "early sample" flag - momentum/trend fields at this sample size
# are never established form (see the page's data-status banner).
EARLY_SAMPLE_GAMES = 2

# players_current.parquet columns NOT already carried by
# lib.dk_helper.STAT_COLUMNS_TO_CARRY - merged in separately by player_id so
# this research page can show full workload/yardage/per-game/WoW context
# without touching dk_helper's own (deliberately minimal) carry list.
EXTRA_CURRENT_COLUMNS = [
    "player_id",
    "total_carries", "carries_per_game",
    "total_targets", "targets_per_game",
    "total_receptions", "receptions_per_game",
    "touches_per_game",
    "total_rushing_yards", "rushing_yards_per_game",
    "total_receiving_yards", "receiving_yards_per_game",
    "total_yards", "total_yards_per_game",
    "total_receiving_air_yards", "receiving_air_yards_per_game",
    "target_share_pct", "air_yards_share_pct", "yac_per_reception", "yards_per_touch",
    "carries_wow_change", "targets_wow_change",
    "receiving_yards_wow_change", "target_share_wow_change",
    "latest_game_fantasy_points", "latest_game_week",
    "total_passing_yards", "total_passing_tds",
    "completion_pct", "passing_yards_per_attempt",
]

# Extra defense_reporting columns not already carried by
# lib.dk_helper.compute_projections's DEFENSE_CONTEXT_DISPLAY_FIELDS.
DEFENSE_EXTRA_COLUMNS = [
    "defense_team", "position",
    "games_in_sample", "player_game_row_count", "defensive_games_played", "sample_size_label",
    "league_avg_points_allowed_for_position",
    "last_3_games_count", "recent_defensive_games_used", "last_3_games_points_allowed_per_game",
    "last_3_games_matchup_index", "last_3_games_matchup_delta",
    "dvp_recent_trend_delta", "dvp_trend_label",
]

# team_reporting columns surfaced on each row as the player's own team's
# current offensive environment.
TEAM_REPORTING_COLUMNS = [
    "team", "games_played", "season_total_yards_per_game",
    "season_passing_yards_per_game", "season_rushing_yards_per_game",
    "season_pass_rate_pct", "season_passing_epa_per_dropback_or_attempt",
    "last_3_total_yards_per_game", "offensive_momentum_yards",
    "offensive_momentum_pct", "recent_form_label", "sample_size_label",
    "reporting_mode",
]


def _merge_extra_current_stats(df: pd.DataFrame, players_current_df: pd.DataFrame) -> pd.DataFrame:
    """Left-merge the extra players_current columns by the already-resolved
    `stat_player_id` - never by name, so an unmatched row simply gets nulls."""
    value_cols = [c for c in EXTRA_CURRENT_COLUMNS if c != "player_id"]
    out = df.copy()
    has_data = (
        players_current_df is not None and not players_current_df.empty
        and "player_id" in players_current_df.columns
    )
    if not has_data:
        for c in value_cols:
            out[c] = pd.NA
        return out

    cols = ["player_id"] + [c for c in value_cols if c in players_current_df.columns]
    extra = players_current_df[cols].drop_duplicates(subset=["player_id"])
    out = out.merge(extra, how="left", left_on="stat_player_id", right_on="player_id", suffixes=("", "_cur"))
    if "player_id" in out.columns and "stat_player_id" in out.columns:
        out = out.drop(columns=["player_id"])
    for c in value_cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out


def _prior_season_columns(dk_df: pd.DataFrame, prior_baseline_df: pd.DataFrame) -> pd.DataFrame:
    """
    Independent prior-season identity match (lib.dk_helper.match_dk_players_prior_season),
    reduced to just the FPPG/games/match-method columns this page needs.
    Returns a frame with the SAME length/row-order as `dk_df` so it can be
    assigned back positionally - never a fabricated 0 when a player has no
    prior-season history (e.g. a rookie) or the baseline table is empty
    (as it deliberately is during in-season mode - see
    dfs_data_pipeline.run_pipeline - prior-season baseline is a Week-1-only
    concept, not something this page recomputes or repopulates).
    """
    n = len(dk_df)
    if prior_baseline_df is None or prior_baseline_df.empty:
        return pd.DataFrame({
            "prior_season_fppg": pd.Series([pd.NA] * n, dtype="object"),
            "prior_season_games": pd.Series([pd.NA] * n, dtype="object"),
            "prior_season_match_method": ["no_prior_season_data"] * n,
        })

    matched = match_dk_players_prior_season(dk_df.reset_index(drop=True), prior_baseline_df)
    out = pd.DataFrame(index=range(n))
    out["prior_season_fppg"] = matched["stat_avg_fantasy_points"].values if "stat_avg_fantasy_points" in matched.columns else pd.NA
    out["prior_season_games"] = matched["stat_games_played"].values if "stat_games_played" in matched.columns else pd.NA
    out["prior_season_match_method"] = matched["match_method"].values
    return out


def _merge_team_reporting(df: pd.DataFrame, team_reporting_df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["canonical_team"] = out["TeamAbbrev"].apply(normalize_team)
    value_cols = [c for c in TEAM_REPORTING_COLUMNS if c != "team"]
    has_data = (
        team_reporting_df is not None and not team_reporting_df.empty and "team" in team_reporting_df.columns
    )
    if not has_data:
        for c in value_cols:
            out[f"team_{c}"] = pd.NA
        return out

    cols = ["team"] + [c for c in value_cols if c in team_reporting_df.columns]
    rename = {c: f"team_{c}" for c in cols if c != "team"}
    tr = team_reporting_df[cols].drop_duplicates(subset=["team"]).rename(columns=rename)
    out = out.merge(tr, how="left", left_on="canonical_team", right_on="team", suffixes=("", "_team"))
    if "team" in out.columns:
        out = out.drop(columns=["team"])
    for c in value_cols:
        if f"team_{c}" not in out.columns:
            out[f"team_{c}"] = pd.NA
    return out


def _merge_defense_extra(df: pd.DataFrame, defense_reporting_df: pd.DataFrame) -> pd.DataFrame:
    """Extra DvP fields (last-3 trend, sample size, league average) keyed on
    (opponent, Position) - the same key convention already used by
    lib.dk_helper.compute_projections's own defense-context lookup."""
    out = df.copy()
    value_cols = [c for c in DEFENSE_EXTRA_COLUMNS if c not in ("defense_team", "position")]
    has_data = (
        defense_reporting_df is not None and not defense_reporting_df.empty
        and "defense_team" in defense_reporting_df.columns and "position" in defense_reporting_df.columns
    )
    if not has_data:
        for c in value_cols:
            out[c] = pd.NA
        return out

    cols = ["defense_team", "position"] + [c for c in value_cols if c in defense_reporting_df.columns]
    indexed = defense_reporting_df[cols].drop_duplicates(subset=["defense_team", "position"])
    out = out.merge(indexed, how="left", left_on=["opponent", "Position"], right_on=["defense_team", "position"])
    out = out.drop(columns=["defense_team", "position"], errors="ignore")
    for c in value_cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out


def _sample_label(games_played) -> str:
    if pd.isna(games_played):
        return SAMPLE_SIZE_DISPLAY["insufficient_sample"]
    if games_played < SAMPLE_SIZE_LIMITED_MIN_GAMES:
        return SAMPLE_SIZE_DISPLAY["insufficient_sample"]
    if games_played < SAMPLE_SIZE_FULL_MIN_GAMES:
        return SAMPLE_SIZE_DISPLAY["limited_sample"]
    return SAMPLE_SIZE_DISPLAY["full_sample"]


def _has_opponent(df: pd.DataFrame) -> pd.Series:
    return df["opponent"].notna() & (df["opponent"] != "")


OPPORTUNITY_MERGE_COLUMNS = [
    "player_id", "opportunity_label", "opportunity_reason", "supporting_metrics",
    "latest_2_games_summary", "latest_3_games_summary", "confidence_label", "confidence_reason",
    "touches_last_2_delta_vs_season", "touches_last_2_per_game",
    "targets_last_2_delta_vs_season", "targets_last_2_per_game",
    "attempts_last_2_delta_vs_season", "attempts_last_2_per_game",
    "touches_last_3_delta_vs_season", "touches_last_3_per_game",
    "targets_last_3_delta_vs_season", "targets_last_3_per_game",
    "attempts_last_3_delta_vs_season", "attempts_last_3_per_game",
]

_RECENT_VS_SEASON_METRIC = {"RB": "touches", "WR": "targets", "TE": "targets", "QB": "attempts"}
_RECENT_VS_SEASON_UNIT = {"touches": "touches/g", "targets": "targets/g", "attempts": "att/g"}


def _recent_vs_season_display(row, window: str) -> str:
    metric = _RECENT_VS_SEASON_METRIC.get(row.get("Position"))
    if not metric:
        return "—"
    delta = row.get(f"{metric}_{window}_delta_vs_season")
    per_game = row.get(f"{metric}_{window}_per_game")
    if pd.isna(delta) or pd.isna(per_game):
        return "—"
    return f"{per_game:.1f} {_RECENT_VS_SEASON_UNIT[metric]} ({delta:+.1f})"


def _merge_opportunity(df: pd.DataFrame, opportunity_df: pd.DataFrame) -> pd.DataFrame:
    """Left-merge the Opportunity Model's classification/window fields by
    the already-resolved `stat_player_id` - same convention as
    `_merge_extra_current_stats`. Null/'—' when unresolved or the mart is
    empty, never fabricated."""
    out = df.copy()
    value_cols = [c for c in OPPORTUNITY_MERGE_COLUMNS if c != "player_id"]
    has_data = opportunity_df is not None and not opportunity_df.empty and "player_id" in opportunity_df.columns
    if has_data:
        cols = ["player_id"] + [c for c in value_cols if c in opportunity_df.columns]
        extra = opportunity_df[cols].drop_duplicates(subset=["player_id"])
        out = out.merge(extra, how="left", left_on="stat_player_id", right_on="player_id", suffixes=("", "_opp"))
        if "player_id" in out.columns and "stat_player_id" in out.columns:
            out = out.drop(columns=["player_id"])
    for c in value_cols:
        if c not in out.columns:
            out[c] = pd.NA
    return out


def build_matchup_analyzer_table(
    dk_df: pd.DataFrame,
    players_current_df: pd.DataFrame,
    prior_baseline_df: pd.DataFrame,
    defense_reporting_df: pd.DataFrame,
    team_reporting_df: pd.DataFrame,
    role_context_df: pd.DataFrame,
    opportunity_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Build the one research DataFrame the Matchup Analyzer page renders -
    one row per DK salary row, joined to identity/stats/role/team/defense/
    opportunity context. Deterministic: same inputs always produce the same
    output, and a player's row only ever carries ITS OWN resolved
    identity's data (see each `_merge_*` helper's docstring for the exact
    join key).
    """
    dk_df = dk_df.reset_index(drop=True)

    matched = match_dk_players(dk_df, players_current_df)
    df = compute_projections(matched, defense_reporting_df)
    df = attach_role_context_to_dk_rows(df, role_context_df)
    df = _merge_extra_current_stats(df, players_current_df)

    prior_cols = _prior_season_columns(dk_df, prior_baseline_df)
    for c in prior_cols.columns:
        df[c] = prior_cols[c].values

    df = _merge_team_reporting(df, team_reporting_df)
    df = _merge_defense_extra(df, defense_reporting_df)
    df = _merge_opportunity(df, opportunity_df)

    df["role_display"] = df["role_classification"].map(ROLE_LABEL_DISPLAY).fillna(df["role_classification"])
    if "dvp_trend_label" in df.columns:
        df["dvp_trend_display"] = df["dvp_trend_label"].map(DVP_TREND_DISPLAY).fillna(df["dvp_trend_label"])
    else:
        df["dvp_trend_display"] = pd.NA
    df["sample_label"] = df["stat_games_played"].apply(_sample_label) if "stat_games_played" in df.columns else SAMPLE_SIZE_DISPLAY["insufficient_sample"]
    df["is_early_sample"] = df["stat_games_played"].apply(
        lambda g: bool(pd.notna(g) and g <= EARLY_SAMPLE_GAMES)
    ) if "stat_games_played" in df.columns else False
    df["prior_fppg_delta"] = df["player_avg"] - pd.to_numeric(df["prior_season_fppg"], errors="coerce")
    df["needs_review_reason"] = df.apply(_describe_review_reason, axis=1)

    df["opportunity_label_display"] = df["opportunity_label"].map(OPPORTUNITY_LABEL_DISPLAY).fillna("—")
    df["opportunity_confidence_display"] = df["confidence_label"].map(CONFIDENCE_LABEL_DISPLAY).fillna("—")
    df["recent_2_vs_season_display"] = df.apply(lambda r: _recent_vs_season_display(r, "last_2"), axis=1)
    df["recent_3_vs_season_display"] = df.apply(lambda r: _recent_vs_season_display(r, "last_3"), axis=1)

    # Role-safety gate (item 11) - the ONLY place opportunity data is allowed
    # to touch role eligibility, and only additively (see
    # lib.opportunity_model.compute_role_safety_gate's docstring for the
    # exact, narrow rule: never overrides inactive/role_unresolved/
    # contingent_backup, and never promotes beyond the research Player Pool).
    df["games_played"] = df["stat_games_played"] if "stat_games_played" in df.columns else pd.NA
    df["position"] = df["Position"] if "Position" in df.columns else pd.NA
    gate = df.apply(compute_role_safety_gate, axis=1, result_type="expand")
    df["opportunity_pool_eligible"] = gate["opportunity_pool_eligible"]
    df["opportunity_top_value_eligible"] = gate["opportunity_top_value_eligible"]
    df["opportunity_eligibility_reason"] = gate["opportunity_eligibility_reason"]
    df["workload_watchlist_eligible"] = gate["workload_watchlist_eligible"]
    df["workload_watchlist_reason"] = gate["workload_watchlist_reason"]
    df["role_review_required"] = gate["role_review_required"]
    # True only when the disabled-by-default OPPORTUNITY_POOL_PROMOTION_ENABLED
    # config flag has been explicitly turned on - see
    # lib.opportunity_model.compute_role_safety_gate. By default this is
    # always False, and `workload_watchlist_eligible` (research-only, never
    # Player Pool/Top Value) is what carries a rising bench_no_clear_path
    # player's signal instead.
    df["is_pool_only_rising_promotion"] = (
        (df["role_classification"] == "bench_no_clear_path") & (df["opportunity_pool_eligible"] == True)  # noqa: E712
    )

    return df


def _describe_review_reason(row) -> str:
    status = row.get("projection_status")
    role = row.get("role_classification")
    method = row.get("match_method")

    if method == "unmatched":
        candidate = row.get("best_candidate_name")
        if pd.notna(candidate):
            score = row.get("match_score")
            score_str = f", best fuzzy score {score:.0f}/100" if pd.notna(score) else ""
            return f"No confident stats match - closest candidate '{candidate}'{score_str}"
        return "No confident stats match found"
    if status == "no_salary":
        return "Missing or zero salary"
    if status == "no_player_average":
        return "Matched, but no usable current-season average available"
    if role == "role_unresolved":
        return f"Stats matched, but role/injury identity is unresolved: {row.get('eligibility_reason')}"
    if status == "ok" and row.get("role_eligible_for_pool") and not (pd.notna(row.get("opponent")) and row.get("opponent") != ""):
        return "Matched and role-eligible, but opponent/matchup could not be resolved"
    return status or "Unknown"


# ---------------------------------------------------------------------------
# Category bucketing (item 6) - reuses role_classification/role_eligible_*
# fields from the unmodified role engine; never redefines eligibility itself.
# ---------------------------------------------------------------------------
def valid_player_pool(df: pd.DataFrame, include_conditional: bool = False) -> pd.DataFrame:
    """
    Valid Player Pool = the existing role engine's own role_eligible_for_pool,
    OR (additively) `opportunity_pool_eligible` - see
    lib.opportunity_model.compute_role_safety_gate. By default
    `opportunity_pool_eligible` is IDENTICAL to `role_eligible_for_pool` (the
    opportunity model adds no promotion), so this OR is a no-op in the
    default configuration. It only ever diverges when a human has
    explicitly enabled the disabled-by-default
    `OPPORTUNITY_POOL_PROMOTION_ENABLED` config flag (lib.opportunity_config)
    - workload alone can NEVER add a bench_no_clear_path/inactive/
    role_unresolved/contingent_backup player to this pool otherwise. A
    rising-workload bench_no_clear_path player who does NOT clear that
    (disabled) flag instead appears only in `workload_watchlist_section`
    below - research visibility, never Player Pool eligibility.
    """
    opportunity_promoted = (
        df["opportunity_pool_eligible"] == True if "opportunity_pool_eligible" in df.columns  # noqa: E712
        else pd.Series(False, index=df.index)
    )
    base = df[
        (df["projection_status"] == "ok")
        & ((df["role_eligible_for_pool"] == True) | opportunity_promoted)  # noqa: E712
        & _has_opponent(df)
    ]
    if not include_conditional:
        base = base[base["role_classification"] != "contingent_backup"]
    return base


def rising_opportunity_section(df: pd.DataFrame, include_conditional: bool = False, include_monitor_only: bool = False) -> pd.DataFrame:
    """
    The "Rising Opportunity" compact view (item 13): Valid Player Pool rows
    (which already excludes monitor-only/inactive/unresolved/excluded by
    construction - see `valid_player_pool`) with a rising opportunity label.
    `include_monitor_only` is a SEPARATE, clearly-labeled toggle (never the
    default) that additionally surfaces rising contingent_backup players -
    still tagged as monitor-only, never merged into the regular pool rows.
    """
    pool = valid_player_pool(df, include_conditional=include_conditional)
    rising_pool = pool[pool["opportunity_label"] == "rising_opportunity"]
    if not include_monitor_only:
        return rising_pool

    monitor_rising = df[(df["role_classification"] == "contingent_backup") & (df["opportunity_label"] == "rising_opportunity")]
    return pd.concat([rising_pool, monitor_rising]).drop_duplicates(subset=["Name", "TeamAbbrev", "Position", "Salary"])


def featured_top_value(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["projection_status"] == "ok")
        & (df["role_eligible_for_top_values"] == True)  # noqa: E712
        & (df["is_conditional_monitor"] != True)  # noqa: E712
        & _has_opponent(df)
    ]


def plays_to_monitor(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["role_classification"] == "contingent_backup"]


def excluded_by_role_context(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["role_classification"] == "bench_no_clear_path"]


def workload_watchlist_section(df: pd.DataFrame) -> pd.DataFrame:
    """
    "Workload Watchlist / Role Review" (Issue 2 hardening): bench_no_clear_path
    rows whose recent workload clears the configured rising-workload gates
    (see lib.opportunity_model.compute_role_safety_gate /
    lib.opportunity_config's "Workload Watchlist / Role Review policy").
    RESEARCH VISIBILITY ONLY - these rows are a subset of
    `excluded_by_role_context` and are never unioned into
    `valid_player_pool` or `featured_top_value`. A player never appears
    here described as role-eligible; `role_classification` stays
    `bench_no_clear_path` and `role_eligible_for_pool` stays False.
    """
    if "workload_watchlist_eligible" not in df.columns:
        return df.iloc[0:0]
    return df[df["workload_watchlist_eligible"] == True]  # noqa: E712


def inactive_players(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["role_classification"] == "inactive"]


def needs_review_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Reuses lib.dk_helper.needs_review for the stats-side criteria
    (unmatched/ambiguous, no average, no salary), unioned with rows whose
    stats matched fine but role/injury identity is unresolved, or whose
    opponent/matchup couldn't be resolved for an otherwise pool-eligible row."""
    stats_bad = stats_needs_review(df)
    role_unresolved_but_stats_ok = df[
        (df["projection_status"] == "ok") & (df["role_classification"] == "role_unresolved")
    ]
    missing_opponent = df[
        (df["projection_status"] == "ok")
        & (df["role_eligible_for_pool"] == True)  # noqa: E712
        & ~_has_opponent(df)
    ]
    return pd.concat([stats_bad, role_unresolved_but_stats_ok, missing_opponent]).drop_duplicates(
        subset=["Name", "TeamAbbrev", "Position", "Salary"]
    ).sort_index()


# ---------------------------------------------------------------------------
# Filtering (item 7) - position-aware: a null research metric never counts
# as a failed "minimum X" filter, so QB/RB rows are never dropped just
# because a WR-only metric (e.g. air_yards_share_pct) is null for them.
# ---------------------------------------------------------------------------
def _min_filter(df: pd.DataFrame, col: str, threshold: float) -> pd.DataFrame:
    if not threshold or col not in df.columns:
        return df
    series = pd.to_numeric(df[col], errors="coerce")
    return df[series.isna() | (series >= threshold)]


def apply_research_filters(
    df: pd.DataFrame,
    *,
    positions=None,
    teams=None,
    opponents=None,
    min_salary=None,
    max_salary=None,
    min_value=None,
    min_points=None,
    name_search=None,
    min_targets_per_game=None,
    min_touches_per_game=None,
    min_target_share_pct=None,
    min_air_yards_share_pct=None,
    min_matchup_index=None,
    matchup_percentile_range=None,
    min_offensive_momentum=None,
    min_games_played=None,
    opportunity_labels=None,
    rising_opportunity_only=False,
    min_opportunity_confidence=None,
) -> pd.DataFrame:
    """Apply every "Controls" filter (item 7) to an already-built matchup
    table. Never mutates `df`; every threshold is a no-op unless set."""
    out = df

    if positions:
        out = out[out["Position"].isin(positions)]
    if teams:
        out = out[out["TeamAbbrev"].isin(teams)]
    if opponents:
        out = out[out["opponent"].isin(opponents)]
    if min_salary is not None:
        out = out[pd.to_numeric(out["Salary"], errors="coerce") >= min_salary]
    if max_salary is not None:
        out = out[pd.to_numeric(out["Salary"], errors="coerce") <= max_salary]
    if name_search:
        out = out[out["Name"].str.contains(name_search, case=False, na=False)]

    if min_value:
        series = pd.to_numeric(out["projected_value"], errors="coerce")
        out = out[series.isna() | (series >= min_value)]
    if min_points:
        series = pd.to_numeric(out["projected_points"], errors="coerce")
        out = out[series.isna() | (series >= min_points)]

    out = _min_filter(out, "targets_per_game", min_targets_per_game)
    out = _min_filter(out, "touches_per_game", min_touches_per_game)
    out = _min_filter(out, "target_share_pct", min_target_share_pct)
    out = _min_filter(out, "air_yards_share_pct", min_air_yards_share_pct)
    out = _min_filter(out, "matchup_index", min_matchup_index)
    out = _min_filter(out, "team_offensive_momentum_yards", min_offensive_momentum)

    if matchup_percentile_range:
        lo, hi = matchup_percentile_range
        series = pd.to_numeric(out["position_percentile_most_favorable"], errors="coerce")
        out = out[series.isna() | series.between(lo, hi)]

    if min_games_played:
        series = pd.to_numeric(out["stat_games_played"], errors="coerce")
        out = out[series.isna() | (series >= min_games_played)]

    if rising_opportunity_only:
        out = out[out["opportunity_label"] == "rising_opportunity"]
    elif opportunity_labels:
        out = out[out["opportunity_label"].isin(opportunity_labels)]

    if min_opportunity_confidence:
        tier = {"insufficient_sample": 0, "early_sample": 1, "established_sample": 2}
        min_rank = tier.get(min_opportunity_confidence, 0)
        out = out[out["confidence_label"].map(tier).fillna(-1) >= min_rank]

    return out


# ---------------------------------------------------------------------------
# Position-aware visible column sets (item 8) - human labels only.
# ---------------------------------------------------------------------------
IDENTITY_COLUMNS = {
    "Name": "Player", "Position": "Position", "TeamAbbrev": "Team", "opponent": "Opponent",
    "Salary": "Salary", "Game Info": "Game Info",
}
ROLE_COLUMNS = {
    "role_display": "Role", "depth_rank": "Depth Rank", "eligibility_reason": "Opportunity Context",
    "blocking_player_names": "Blocking Player / Injury Context", "match_score": "Match Confidence",
    "role_data_freshness": "Data Freshness", "manual_override_applied": "Manual Override",
}
PROJECTION_COLUMNS = {
    "projected_points": "Projected Points", "projected_value": "Projected Value",
    "player_avg": "Current Season FPPG", "prior_season_fppg": "Prior Season FPPG",
    "prior_fppg_delta": "Delta vs Prior Season", "stat_games_played": "Current Season Games",
    "sample_label": "Sample Label",
}
OPPORTUNITY_COLUMNS = {
    "opportunity_label_display": "Opportunity",
    "recent_2_vs_season_display": "Recent 2 vs Season",
    "recent_3_vs_season_display": "Recent 3 vs Season",
    "opportunity_confidence_display": "Opportunity Confidence",
}
RB_VOLUME_COLUMNS = {
    "carries_per_game": "Carries/Game", "targets_per_game": "Targets/Game",
    "receptions_per_game": "Receptions/Game", "touches_per_game": "Touches/Game",
    "total_yards_per_game": "Total Yards/Game", "yards_per_touch": "Yards/Touch",
    "stat_touches_wow_change": "WoW Touches", "target_share_pct": "Target Share %",
}
WR_TE_VOLUME_COLUMNS = {
    "targets_per_game": "Targets/Game", "receptions_per_game": "Receptions/Game",
    "receiving_yards_per_game": "Receiving Yards/Game", "receiving_air_yards_per_game": "Air Yards/Game",
    "target_share_pct": "Target Share %", "air_yards_share_pct": "Air Yards Share %",
    "stat_catch_rate": "Catch Rate", "stat_yards_per_target": "Yards/Target", "yac_per_reception": "YAC/Rec",
    "targets_wow_change": "WoW Targets", "target_share_wow_change": "WoW Target Share",
}
QB_VOLUME_COLUMNS = {
    "total_passing_yards": "Passing Yards", "passing_yards_per_attempt": "Yards/Attempt",
    "completion_pct": "Comp %", "total_carries": "Rush Attempts", "carries_per_game": "Rush Attempts/Game",
    "momentum_score": "Recent Trend (FPPG)",
}
MATCHUP_COLUMNS = {
    "fantasy_points_allowed_per_game": "DvP FPPG Allowed", "matchup_index": "Matchup Index",
    "matchup_delta": "Matchup Delta", "position_rank_most_favorable": "Matchup Rank",
    "position_percentile_most_favorable": "Matchup Percentile",
    "dvp_trend_display": "Last-3 DvP Trend", "defensive_games_played": "Defensive Games",
    "player_game_row_count": "Opposing Player Appearances",
}
TEAM_ENV_COLUMNS = {
    "team_season_total_yards_per_game": "Team Total Yards/Game",
    "team_season_pass_rate_pct": "Team Pass Rate",
    "team_offensive_momentum_yards": "Team Offensive Momentum",
    "team_season_passing_epa_per_dropback_or_attempt": "Team Passing EPA Rate",
    "team_reporting_mode": "Team Reporting Mode",
}


def columns_for_position(position: str) -> dict:
    """The {raw_column: display_label} map, in display order, for one position."""
    volume = {"RB": RB_VOLUME_COLUMNS, "WR": WR_TE_VOLUME_COLUMNS, "TE": WR_TE_VOLUME_COLUMNS, "QB": QB_VOLUME_COLUMNS}.get(position, {})
    return {
        **IDENTITY_COLUMNS, **ROLE_COLUMNS, **PROJECTION_COLUMNS, **OPPORTUNITY_COLUMNS,
        **volume, **MATCHUP_COLUMNS, **TEAM_ENV_COLUMNS,
    }


def build_display_table(df: pd.DataFrame, position: str = None) -> pd.DataFrame:
    """The renamed, ordered, display-ready column subset. When `position` is
    given, only that position's volume columns are included; otherwise all
    four positions' volume columns are shown (used for a mixed-position
    table), with a null/not-applicable cell - never a fabricated 0 - for any
    position where that metric doesn't apply."""
    if position:
        columns = columns_for_position(position)
    else:
        columns = {**IDENTITY_COLUMNS, **ROLE_COLUMNS, **PROJECTION_COLUMNS, **OPPORTUNITY_COLUMNS,
                   **RB_VOLUME_COLUMNS, **WR_TE_VOLUME_COLUMNS, **QB_VOLUME_COLUMNS,
                   **MATCHUP_COLUMNS, **TEAM_ENV_COLUMNS}
    available = [c for c in columns if c in df.columns]
    return df[available].rename(columns=columns)


def build_csv_export(df: pd.DataFrame) -> pd.DataFrame:
    """Every user-visible column (raw field names, audit-ready) plus stable
    identity fields needed to understand the joins."""
    columns = {**IDENTITY_COLUMNS, **ROLE_COLUMNS, **PROJECTION_COLUMNS, **OPPORTUNITY_COLUMNS,
               **RB_VOLUME_COLUMNS, **WR_TE_VOLUME_COLUMNS, **QB_VOLUME_COLUMNS,
               **MATCHUP_COLUMNS, **TEAM_ENV_COLUMNS}
    audit_cols = ["stat_player_id", "role_player_id", "match_method", "match_score",
                  "role_classification", "projection_status", "canonical_team", "opponent",
                  "dvp_trend_label", "prior_season_match_method",
                  "opportunity_label", "opportunity_pool_eligible", "opportunity_top_value_eligible",
                  "opportunity_eligibility_reason", "is_pool_only_rising_promotion",
                  "workload_watchlist_eligible", "workload_watchlist_reason", "role_review_required"]
    export_cols = audit_cols + [c for c in columns if c in df.columns and c not in audit_cols]
    export_cols = [c for c in export_cols if c in df.columns]
    return df[export_cols].reset_index(drop=True)


def build_needs_review_export(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["Name", "Position", "TeamAbbrev", "opponent", "Salary", "match_method", "match_score",
            "best_candidate_name", "projection_status", "role_classification", "needs_review_reason"]
    available = [c for c in cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Chart data helpers (item 11)
# ---------------------------------------------------------------------------
def salary_vs_value_chart_data(df: pd.DataFrame) -> pd.DataFrame:
    """Salary-vs-value scatter data: excludes inactive/unresolved rows and
    anything without both a real salary and a real projected value - never
    plots a fabricated point."""
    out = df[
        (df["role_classification"] != "inactive")
        & (df["role_classification"] != "role_unresolved")
        & df["Salary"].notna()
        & df["projected_value"].notna()
    ]
    return out


def volume_vs_matchup_chart_data(df: pd.DataFrame, position: str) -> pd.DataFrame:
    """RB: touches/game vs matchup index. WR/TE: targets/game vs matchup
    index. QB: rush attempts/game (its available volume metric) vs matchup
    index. Rows with either value null are excluded rather than zero-filled."""
    volume_col = {"RB": "touches_per_game", "WR": "targets_per_game", "TE": "targets_per_game", "QB": "carries_per_game"}.get(position)
    if volume_col is None or volume_col not in df.columns:
        return df.iloc[0:0]
    out = df[df["Position"] == position]
    return out[out[volume_col].notna() & out["matchup_index"].notna()]


def team_environment_chart_data(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (team, opponent, position) combination actually present
    in the slate, for the team-momentum-vs-matchup-index view. Deduplicated
    since many players share the same team+opponent+position row."""
    cols = ["TeamAbbrev", "opponent", "Position", "team_offensive_momentum_yards", "matchup_index", "Name"]
    available = [c for c in cols if c in df.columns]
    out = df[available].dropna(subset=["team_offensive_momentum_yards", "matchup_index"])
    return out.drop_duplicates(subset=["TeamAbbrev", "opponent", "Position"])


# ---------------------------------------------------------------------------
# Summary counts (item 9)
# ---------------------------------------------------------------------------
def summary_counts(df: pd.DataFrame, include_conditional: bool = False) -> dict:
    pool = valid_player_pool(df, include_conditional=include_conditional)
    featured = featured_top_value(df)
    monitor = plays_to_monitor(df)
    excluded = excluded_by_role_context(df)
    review = needs_review_rows(df)
    inactive = inactive_players(df)

    avg_salary = pd.to_numeric(pool["Salary"], errors="coerce").mean() if not pool.empty else None
    median_value = pd.to_numeric(pool["projected_value"], errors="coerce").median() if not pool.empty else None

    return {
        "player_pool_count": len(pool),
        "featured_count": len(featured),
        "monitor_count": len(monitor),
        "excluded_count": len(excluded),
        "needs_review_count": len(review),
        "inactive_count": len(inactive),
        "avg_pool_salary": avg_salary,
        "median_pool_value": median_value,
    }
