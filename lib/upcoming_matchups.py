"""
Upcoming Matchup Discovery (Part 4) - non-UI join/filter/shortlist helpers.

Joins the offensive production mart to the comparable defensive
points-allowed mart via the VERIFIED upcoming-schedule mart
(`dfs_data_pipeline.build_upcoming_schedule` / `lib.data.load_upcoming_schedule`)
- never via the currently-loaded DK salary slate's own opponent parsing,
which is a DIFFERENT, separately-versioned thing (today's slate could be
stale/old while the schedule mart is current, or vice versa - see
`lib.cache_fingerprint`). If the schedule mart is empty/unavailable, every
function here degrades to an empty, clearly-labeled result rather than
fabricating an opponent.

This is a RESEARCH FILTER, not a predictive model: both percentiles are
always kept visible and are never averaged/blended into one score. The
default 75th/75th thresholds are an exploratory starting point, not a
validated rule - see `DEFAULT_MIN_OFFENSE_PERCENTILE`/
`DEFAULT_MIN_DEFENSE_PERCENTILE`.
"""

import pandas as pd

DEFAULT_MIN_OFFENSE_PERCENTILE = 75.0
DEFAULT_MIN_DEFENSE_PERCENTILE = 75.0
DEFAULT_MIN_SAMPLE_GAMES = 1

DISCOVERY_COLUMNS = [
    "team", "opponent_team", "position", "week",
    "offense_points_per_team_game", "offense_percentile", "offense_games_recorded",
    "points_allowed_per_defensive_game", "defense_percentile", "defense_games_recorded",
    "sample_warning", "matchup_uid",
]


def schedule_is_available(upcoming_schedule_df: pd.DataFrame) -> bool:
    return upcoming_schedule_df is not None and not upcoming_schedule_df.empty


def matchup_uid(row) -> str:
    """Stable identifier for one offense/opponent/position/week discovery
    row - used for the session shortlist, never a bare team name (two
    different weeks or positions for the same team pairing must never
    collide)."""
    return "|".join(str(row.get(c, "")) for c in ("team", "opponent_team", "position", "week"))


def build_discovery_table(
    offense_df: pd.DataFrame,
    defense_df: pd.DataFrame,
    upcoming_schedule_df: pd.DataFrame,
    window: str = "Season",
) -> pd.DataFrame:
    """
    One row per (offensive team, position) with its verified upcoming
    opponent (from `upcoming_schedule_df`) and that opponent's comparable
    points-allowed figures for the SAME window and position - never mixed
    windows, never mixed positions. Returns empty (never fabricated) if the
    schedule mart is unavailable.
    """
    if not schedule_is_available(upcoming_schedule_df) or offense_df.empty or defense_df.empty:
        return pd.DataFrame(columns=DISCOVERY_COLUMNS)

    off_cols = {
        "Season": ("season_offense_points_per_team_game", "season_offense_percentile", "season_offense_games_recorded"),
        "Recent": ("recent_offense_points_per_team_game", "recent_offense_percentile", "recent_offense_games_recorded"),
    }.get(window)
    def_cols = {
        "Season": ("season_points_allowed_per_defensive_game", "season_points_allowed_percentile", "season_points_allowed_games_recorded"),
        "Recent": ("recent_points_allowed_per_defensive_game", "recent_points_allowed_percentile", "recent_points_allowed_games_recorded"),
    }.get(window)
    if off_cols is None or def_cols is None:
        return pd.DataFrame(columns=DISCOVERY_COLUMNS)

    offense = offense_df[["team", "position", *off_cols]].rename(columns={
        off_cols[0]: "offense_points_per_team_game",
        off_cols[1]: "offense_percentile",
        off_cols[2]: "offense_games_recorded",
    })
    defense = defense_df[["defense_team", "position", *def_cols]].rename(columns={
        "defense_team": "opponent_team",
        def_cols[0]: "points_allowed_per_defensive_game",
        def_cols[1]: "defense_percentile",
        def_cols[2]: "defense_games_recorded",
    })

    # One upcoming opponent per (team, week) - never more than one row per
    # team-week, matching the schedule mart's own grain.
    schedule = upcoming_schedule_df[["team", "opponent_team", "week"]].drop_duplicates()

    merged = schedule.merge(offense, on="team", how="inner")
    merged = merged.merge(defense, on=["opponent_team", "position"], how="left")

    def _sample_warning(row):
        warnings = []
        off_games = row.get("offense_games_recorded")
        def_games = row.get("defense_games_recorded")
        if pd.isna(off_games) or off_games == 0:
            warnings.append("no recorded offensive games")
        if pd.isna(def_games) or def_games == 0:
            warnings.append("no recorded defensive games")
        return "; ".join(warnings) if warnings else None

    merged["sample_warning"] = merged.apply(_sample_warning, axis=1)
    merged["matchup_uid"] = merged.apply(matchup_uid, axis=1)

    return merged[DISCOVERY_COLUMNS].sort_values(["position", "team"]).reset_index(drop=True)


def apply_discovery_filters(
    df: pd.DataFrame,
    *,
    positions=None,
    min_offense_percentile=None,
    min_defense_percentile=None,
    min_sample_games=None,
) -> pd.DataFrame:
    """
    Independent percentile/sample filters - never combined into one score.
    A null percentile (no recorded games) never passes a minimum-percentile
    filter (it is excluded, not treated as 0 or as automatically passing).
    """
    if df.empty:
        return df
    out = df
    if positions:
        out = out[out["position"].isin(positions)]
    if min_offense_percentile is not None:
        out = out[out["offense_percentile"].notna() & (out["offense_percentile"] >= min_offense_percentile)]
    if min_defense_percentile is not None:
        out = out[out["defense_percentile"].notna() & (out["defense_percentile"] >= min_defense_percentile)]
    if min_sample_games is not None:
        out = out[
            out["offense_games_recorded"].fillna(0).ge(min_sample_games)
            & out["defense_games_recorded"].fillna(0).ge(min_sample_games)
        ]
    return out


def resolve_shortlist_uids(df_with_uid: pd.DataFrame, selected_uids) -> tuple:
    """Split a previously-selected shortlist into (still_valid, stale)
    against the CURRENT discovery table's uids - same stale-selection-
    safety pattern as lib.player_comparison.resolve_selected_uids, so a
    season/week/slate change never leaves a shortlist entry pointing at a
    matchup that no longer exists."""
    current = set(df_with_uid["matchup_uid"]) if not df_with_uid.empty else set()
    still_valid = [u for u in selected_uids if u in current]
    stale = [u for u in selected_uids if u not in current]
    return still_valid, stale
