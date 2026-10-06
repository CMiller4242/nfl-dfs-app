"""
Player-distribution drill-down for one team+position (Parts 5, 6, 7).

Built directly from `players_weekly.parquet` (the same raw, per-player-per-
completed-week mart every other part of this app already uses) - never a
new fetch, never a reimplementation of the Opportunity Model or Player Case
Summary classification logic. Current role/eligibility/opportunity context
is merged in from the existing `player_role_context.parquet` /
`player_opportunity_reporting.parquet` marts, unchanged; slate-specific
salary/projection/case-summary fields are merged in from an ALREADY-BUILT
Matchup Analyzer table the caller passes in (built once via
`lib.matchup_analyzer.build_matchup_analyzer_table` +
`lib.player_case_summary.build_case_summary`) - this module never calls
those builders itself.

Historical team attribution: every aggregate here groups by the `team`
value each weekly row already carries (the team a player was actually ON
that week) - never by a player's CURRENT roster team. A traded player's
production before and after the trade is correctly split between their two
teams, never double-counted or misattributed to whichever team they're on
today.
"""

import pandas as pd

OFFENSE_DEFENSE_POSITION_RECENT_FORM_GAMES = 3

PLAYER_CONTRIBUTION_COLUMNS = [
    "player_id", "player_display_name", "historical_team", "position",
    "total_fantasy_points", "games_appeared", "latest_week_appeared",
    "points_per_team_game", "points_per_appearance",
    "share_of_team_position_points",
    "total_carries", "share_of_team_position_carries",
    "total_targets", "share_of_team_position_targets", "team_target_share",
    "total_receptions",
]

CURRENT_CONTEXT_COLUMNS = [
    "current_team", "role_classification", "role_display", "role_data_freshness",
    "role_eligible_for_pool", "role_eligible_for_top_values", "eligibility_reason",
    "opportunity_label", "opportunity_reason", "confidence_label",
]

SLATE_CONTEXT_COLUMNS = [
    "Salary", "projected_points", "projected_value", "signal_alignment", "case_summary",
    "positives", "concerns", "missing_evidence", "sample_warnings", "classification_reason",
]


def team_position_recorded_weeks(weekly_df: pd.DataFrame, team: str, position: str) -> list:
    """Every distinct week this team has at least one recorded player row
    at this position - the SAME 'recorded' completed-team-game definition
    `dfs_data_pipeline.build_team_game_position_totals` uses, re-derived
    here directly (not duplicated aggregation logic) since the grain is
    identical: a week is 'recorded' for a team+position exactly when a
    player row for it exists in `players_weekly.parquet`."""
    if weekly_df.empty:
        return []
    subset = weekly_df[(weekly_df["team"] == team) & (weekly_df["position"] == position)]
    return sorted(subset["week"].unique().tolist())


def recent_window_weeks(weekly_df: pd.DataFrame, team: str, position: str,
                         n: int = OFFENSE_DEFENSE_POSITION_RECENT_FORM_GAMES) -> list:
    """The last `n` recorded weeks for this team+position - the exact same
    window `build_offensive_position_reporting`'s recent figures average
    over, so season/recent comparisons here never silently use a different
    window than the matrix/matchup-discovery views."""
    weeks = team_position_recorded_weeks(weekly_df, team, position)
    return weeks[-n:]


def _safe_share(numerator, denominator):
    """A percentage share, or an explicit missing value (never a fabricated
    percentage) when the denominator is zero, negative, or null - a share
    of a non-positive total is undefined, not 0% or 100%."""
    if pd.isna(numerator) or pd.isna(denominator) or denominator <= 0:
        return pd.NA
    return float(numerator) / float(denominator) * 100.0


def build_player_window_contributions(weekly_df: pd.DataFrame, team: str, position: str, weeks) -> pd.DataFrame:
    """
    Every player who recorded a stat line for `team` at `position` during
    `weeks` (season = every recorded week; recent =
    `recent_window_weeks`'s last-N) - ALL contributors, not just the
    current starter or top projection; an excluded/restricted player who
    materially contributed historically is never hidden here (their
    CURRENT restriction is attached separately - see
    `attach_current_context` - and still shown).

    `points_per_team_game` divides by the number of DISTINCT team-games in
    `weeks` (so it reconciles with
    `build_offensive_position_reporting.season_offense_points_per_team_game`/
    `recent_offense_points_per_team_game` when `weeks` matches that
    window exactly) - `points_per_appearance` instead divides by this
    PLAYER's own games_appeared, a different, explicitly separate
    denominator (a part-season addition or a player who missed games has
    fewer appearances than the team has games).

    `team_target_share` uses the FULL TEAM's targets across every position
    in `weeks` as its denominator (not just this position's targets) -
    see `share_of_team_position_targets` for the position-scoped version.
    """
    weeks = list(weeks)
    if weekly_df.empty or not weeks:
        return pd.DataFrame(columns=PLAYER_CONTRIBUTION_COLUMNS)

    subset = weekly_df[
        (weekly_df["team"] == team) & (weekly_df["position"] == position) & (weekly_df["week"].isin(weeks))
    ]
    if subset.empty:
        return pd.DataFrame(columns=PLAYER_CONTRIBUTION_COLUMNS)

    distinct_team_games = subset["week"].nunique()
    team_position_total_points = subset["fantasy_points_ppr"].sum()
    team_position_total_carries = subset["carries"].sum()
    team_position_total_targets = subset["targets"].sum()

    full_team_subset = weekly_df[(weekly_df["team"] == team) & (weekly_df["week"].isin(weeks))]
    team_total_targets_all_positions = full_team_subset["targets"].sum()

    grp = (
        subset.groupby("player_id")
        .agg(
            player_display_name=("player_display_name", "last"),
            total_fantasy_points=("fantasy_points_ppr", "sum"),
            total_carries=("carries", "sum"),
            total_targets=("targets", "sum"),
            total_receptions=("receptions", "sum"),
            games_appeared=("week", "nunique"),
            latest_week_appeared=("week", "max"),
        )
        .reset_index()
    )
    grp["historical_team"] = team
    grp["position"] = position
    grp["points_per_team_game"] = (
        grp["total_fantasy_points"] / distinct_team_games if distinct_team_games else pd.NA
    )
    grp["points_per_appearance"] = grp["total_fantasy_points"] / grp["games_appeared"]
    grp["share_of_team_position_points"] = grp["total_fantasy_points"].apply(
        lambda v: _safe_share(v, team_position_total_points)
    )
    grp["share_of_team_position_carries"] = grp["total_carries"].apply(
        lambda v: _safe_share(v, team_position_total_carries)
    )
    grp["share_of_team_position_targets"] = grp["total_targets"].apply(
        lambda v: _safe_share(v, team_position_total_targets)
    )
    grp["team_target_share"] = grp["total_targets"].apply(
        lambda v: _safe_share(v, team_total_targets_all_positions)
    )

    return grp[PLAYER_CONTRIBUTION_COLUMNS].sort_values("total_fantasy_points", ascending=False).reset_index(drop=True)


def attach_current_context(contributions_df: pd.DataFrame, role_context_df: pd.DataFrame,
                            opportunity_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge in CURRENT (point-in-time, not historical-window) role
    classification/freshness/eligibility (`player_role_context.parquet`)
    and opportunity label/reason/confidence (`player_opportunity_reporting.parquet`)
    by `player_id` - both unchanged, existing marts, never recomputed here.
    A player with no current role-context row (e.g. no longer in the
    league, or the snapshot hasn't resolved them) gets null context, never
    a guessed classification - their historical production row is still
    shown.
    """
    out = contributions_df.copy()
    if out.empty:
        for c in CURRENT_CONTEXT_COLUMNS:
            out[c] = pd.Series(dtype="object")
        return out

    role_source_cols = [
        "player_id", "canonical_team", "role_classification", "role_data_freshness",
        "role_eligible_for_pool", "role_eligible_for_top_values", "eligibility_reason",
    ]
    if role_context_df is not None and not role_context_df.empty:
        available = [c for c in role_source_cols if c in role_context_df.columns]
        role = role_context_df[available].drop_duplicates(subset=["player_id"])
        out = out.merge(role, on="player_id", how="left")
        out = out.rename(columns={"canonical_team": "current_team"})
    else:
        out["current_team"] = pd.NA
        out["role_classification"] = pd.NA
        out["role_data_freshness"] = pd.NA
        out["role_eligible_for_pool"] = pd.NA
        out["role_eligible_for_top_values"] = pd.NA
        out["eligibility_reason"] = pd.NA

    ROLE_LABEL_DISPLAY = {
        "confirmed_starter": "Confirmed Starter", "standard_eligible_rotation": "Eligible Rotation",
        "injury_elevated_backup": "Injury-Elevated", "contingent_backup": "Monitor Injury Status",
        "bench_no_clear_path": "No Clear Opportunity Path", "role_unresolved": "Role Needs Review",
        "inactive": "Inactive",
    }
    out["role_display"] = out["role_classification"].map(ROLE_LABEL_DISPLAY).fillna(out["role_classification"])

    opp_source_cols = ["player_id", "opportunity_label", "opportunity_reason", "confidence_label"]
    if opportunity_df is not None and not opportunity_df.empty:
        available = [c for c in opp_source_cols if c in opportunity_df.columns]
        opp = opportunity_df[available].drop_duplicates(subset=["player_id"])
        out = out.merge(opp, on="player_id", how="left")
    else:
        out["opportunity_label"] = pd.NA
        out["opportunity_reason"] = pd.NA
        out["confidence_label"] = pd.NA

    return out


def attach_slate_case_summary(contributions_df: pd.DataFrame, slate_table: pd.DataFrame) -> pd.DataFrame:
    """
    Left-merge salary/projection/case-summary fields from an ALREADY-BUILT
    (by the caller) Matchup Analyzer + Player Case Summary table, by the
    shared `player_id`/`stat_player_id` identity - both ultimately come
    from the same `players_weekly.parquet` source, so this is a direct
    join, never a fuzzy name match. Adds `slate_data_available` so the page
    can explain when salary/value/case information is absent (no current
    DK slate loaded, or this player isn't on it) WITHOUT hiding the
    historical distribution row itself - see Part 7.
    """
    out = contributions_df.copy()
    prefixed = [f"slate_{c}" for c in SLATE_CONTEXT_COLUMNS]
    if out.empty:
        for c in prefixed:
            out[c] = pd.Series(dtype="object")
        out["slate_data_available"] = pd.Series(dtype="bool")
        return out

    if slate_table is None or slate_table.empty or "stat_player_id" not in slate_table.columns:
        for c in prefixed:
            out[c] = pd.NA
        out["slate_data_available"] = False
        return out

    available = [c for c in SLATE_CONTEXT_COLUMNS if c in slate_table.columns]
    slate = slate_table[["stat_player_id"] + available].drop_duplicates(subset=["stat_player_id"])
    slate = slate.rename(columns={c: f"slate_{c}" for c in available})
    out = out.merge(slate, left_on="player_id", right_on="stat_player_id", how="left")
    out["slate_data_available"] = out["stat_player_id"].notna()
    out = out.drop(columns=["stat_player_id"])
    for c in prefixed:
        if c not in out.columns:
            out[c] = pd.NA
    return out


GAME_BY_GAME_COLUMNS = [
    "week", "opponent_team", "player_id", "player_display_name",
    "fantasy_points_ppr", "carries", "targets", "receptions",
]


def build_game_by_game_table(weekly_df: pd.DataFrame, team: str, position: str, weeks) -> pd.DataFrame:
    """
    One row per (player, week) for `team` at `position` across `weeks` -
    the compact distribution view behind Part 6. Never collapses multiple
    contributors in the same week into one row, so concentration vs. split
    production is directly visible. No causal inference is drawn here
    (e.g. an injury near a workload change) - this is raw per-game data
    only.
    """
    weeks = list(weeks)
    if weekly_df.empty or not weeks:
        return pd.DataFrame(columns=GAME_BY_GAME_COLUMNS)
    subset = weekly_df[
        (weekly_df["team"] == team) & (weekly_df["position"] == position) & (weekly_df["week"].isin(weeks))
    ]
    if subset.empty:
        return pd.DataFrame(columns=GAME_BY_GAME_COLUMNS)
    out = subset[GAME_BY_GAME_COLUMNS].sort_values(
        ["week", "fantasy_points_ppr"], ascending=[True, False]
    ).reset_index(drop=True)
    return out
