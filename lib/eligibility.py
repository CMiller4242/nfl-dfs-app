"""
Standalone role/eligibility engine. No Streamlit page contains any of this
decision logic - pages call `compute_role_context` (pipeline-time, via
dfs_data_pipeline.py) and `attach_role_context_to_dk_rows` (Streamlit-time)
and only render the result.

Eligibility is always computed within an exact (canonical_team,
position_group) group, from the current depth-chart snapshot - a player is
never evaluated against, or promoted by, an injury/depth record outside
that group. See `compute_role_context`'s per-group loop.
"""

from datetime import datetime, timezone

import pandas as pd

from lib.player_identity import match_dk_row_to_role_context, normalize_name, normalize_team
from lib.role_config import DEFAULT_ELIGIBLE_DEPTH_RANK, DEPTH_CHART_FRESHNESS_HOURS, INJURY_FRESHNESS_HOURS

ROLE_UNRESOLVED = "role_unresolved"
INACTIVE = "inactive"
CONFIRMED_STARTER = "confirmed_starter"
STANDARD_ELIGIBLE_ROTATION = "standard_eligible_rotation"
INJURY_ELEVATED_BACKUP = "injury_elevated_backup"
CONTINGENT_BACKUP = "contingent_backup"
BENCH_NO_CLEAR_PATH = "bench_no_clear_path"

ELIGIBLE_ROLE_CLASSIFICATIONS = {CONFIRMED_STARTER, STANDARD_ELIGIBLE_ROTATION, INJURY_ELEVATED_BACKUP}

ROLE_CONTEXT_COLUMNS = [
    "player_id", "player_name", "canonical_team", "position_group", "source_position",
    "depth_rank", "espn_id", "availability_classification", "injury_designation",
    "role_classification", "role_eligible_for_pool", "role_eligible_for_top_values",
    "is_conditional_monitor", "eligibility_reason",
    "blocking_player_ids", "blocking_player_names", "blocking_player_statuses",
    "depth_chart_source_timestamp", "injury_source_timestamp", "role_data_freshness",
    "manual_override_applied", "override_reason",
]


def _as_aware_utc(ts):
    ts = pd.Timestamp(ts) if not isinstance(ts, pd.Timestamp) else ts
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _hours_since(timestamp_value, now):
    if timestamp_value is None or (isinstance(timestamp_value, float) and pd.isna(timestamp_value)):
        return None
    try:
        ts = pd.to_datetime(timestamp_value, utc=True)
    except (ValueError, TypeError):
        return None
    return (now - ts).total_seconds() / 3600.0


def _format_blocker(row) -> str:
    return f"{row['player_name']} ({row['position_group']}{int(row['depth_rank'])}) is {row['injury_designation']}"


def compute_role_context(
    depth_chart_df: pd.DataFrame,
    injury_df: pd.DataFrame,
    injury_run_metadata: dict = None,
    overrides_df: pd.DataFrame = None,
    now=None,
    depth_chart_freshness_hours: float = DEPTH_CHART_FRESHNESS_HOURS,
    injury_freshness_hours: float = INJURY_FRESHNESS_HOURS,
    eligible_depth_rank: dict = None,
) -> pd.DataFrame:
    """
    Compute role/eligibility context for every player on the current depth
    chart. Pure function of its inputs - no I/O - directly testable.

    Classification per player, in priority order:
      1. Data isn't resolvable (depth chart stale/missing, or this player's
         own injury status is "unknown" - team fetch failed, player not
         found in the injury source, or a team mismatch between the depth
         chart and injury source for this identity) -> `role_unresolved`,
         never eligible.
      2. Player is themselves confirmed_unavailable -> `inactive`, never
         eligible.
      3. Player's depth_rank is within the configured standard-eligible tier
         for their position -> `confirmed_starter` (rank 1) or
         `standard_eligible_rotation` - eligible regardless of anyone else's
         injury status (never mislabeled as an injury replacement).
      4. Otherwise (a "bench" rank by configuration), look at every
         higher-ranked player (lower depth_rank) in the same team +
         position group:
           - any of them is available/unknown -> `bench_no_clear_path`
             (an available or unconfirmed player still blocks the path)
           - none available/unknown, all confirmed_unavailable ->
             `injury_elevated_backup`, eligible
           - none available/unknown, at least one merely conditional
             (Questionable/Doubtful) -> `contingent_backup`, a monitor-only
             scenario, not eligible for the pool/top values by default

    Manual overrides (already loaded/validated - see lib.manual_overrides)
    are applied last, and only when they uniquely identify one player by
    (player_id, canonical_team, position_group) - never across teams or
    positions.
    """
    now = _as_aware_utc(now or datetime.now(timezone.utc))
    eligible_depth_rank = eligible_depth_rank or DEFAULT_ELIGIBLE_DEPTH_RANK
    injury_run_metadata = injury_run_metadata or {}
    failed_teams_canonical = {normalize_team(t) for t in injury_run_metadata.get("failed_team_abbrevs", [])}
    injury_retrieved_at = injury_run_metadata.get("retrieved_at")

    if depth_chart_df is None or depth_chart_df.empty:
        return pd.DataFrame(columns=ROLE_CONTEXT_COLUMNS)

    dc = depth_chart_df.copy()
    dc["espn_id"] = dc["espn_id"].astype(str)

    if injury_df is not None and not injury_df.empty:
        inj = injury_df.copy()
        inj["espn_athlete_id"] = inj["espn_athlete_id"].astype(str)
        inj_lookup = inj.drop_duplicates(subset=["espn_athlete_id"], keep="first").set_index("espn_athlete_id")
    else:
        inj_lookup = pd.DataFrame(columns=["availability_classification", "injury_designation", "espn_team_abbrev"])

    rows = []
    for _, r in dc.iterrows():
        espn_id = r["espn_id"]
        team_injury_failed = r["canonical_team"] in failed_teams_canonical

        if espn_id in inj_lookup.index:
            inj_row = inj_lookup.loc[espn_id]
            inj_team_canonical = normalize_team(inj_row["espn_team_abbrev"])
            if inj_team_canonical != r["canonical_team"]:
                # Adversarial safety: an injury record must match the player
                # AND the team before it can affect eligibility. A mismatch
                # (e.g. one source hasn't caught up to a trade yet) is
                # treated as unresolved, never silently trusted either way.
                availability = "unknown"
                injury_designation = "Unavailable (team mismatch between depth chart and injury source)"
            else:
                availability = inj_row["availability_classification"]
                injury_designation = inj_row["injury_designation"]
        else:
            availability = "unknown"
            injury_designation = (
                "Unavailable (ESPN source failure for this team)" if team_injury_failed
                else "Unavailable (player not found in injury source)"
            )

        depth_age = _hours_since(r["depth_chart_source_timestamp"], now)
        injury_age = _hours_since(injury_retrieved_at, now)
        depth_stale = depth_age is None or depth_age > depth_chart_freshness_hours
        injury_stale = injury_age is None or injury_age > injury_freshness_hours or team_injury_failed

        if depth_stale and injury_stale:
            freshness = "unavailable"
        elif depth_stale or injury_stale:
            freshness = "stale"
        else:
            freshness = "fresh"

        # Precise, per-source reason so "unresolved" never generically blames
        # the wrong side - a failed injury fetch must say so, not "depth
        # chart data is stale" (which would be true of neither source here).
        staleness_reasons = []
        if depth_stale:
            staleness_reasons.append("depth chart data is stale or unavailable")
        if injury_stale:
            if team_injury_failed:
                staleness_reasons.append("injury data is unavailable for this team (ESPN fetch failed)")
            else:
                staleness_reasons.append("injury data is stale")

        rows.append({
            "player_id": r["player_id"],
            "player_name": r["player_display_name"],
            "canonical_team": r["canonical_team"],
            "position_group": r["position_group"],
            "source_position": r["source_position"],
            "depth_rank": r["depth_rank"],
            "espn_id": espn_id,
            "availability_classification": availability,
            "injury_designation": injury_designation,
            "depth_chart_source_timestamp": r["depth_chart_source_timestamp"],
            "injury_source_timestamp": injury_retrieved_at,
            "role_data_freshness": freshness,
            "_staleness_reason": " and ".join(staleness_reasons),
        })

    base = pd.DataFrame(rows)
    results = []

    for (_team, _pos), group in base.groupby(["canonical_team", "position_group"], sort=False):
        group = group.sort_values("depth_rank").reset_index(drop=True)
        tier = eligible_depth_rank.get(group["position_group"].iloc[0], 1)

        for _, player in group.iterrows():
            resolvable = player["role_data_freshness"] == "fresh" and player["availability_classification"] != "unknown"
            blocking_ids = blocking_names = blocking_statuses = ""

            if not resolvable:
                role_classification = ROLE_UNRESOLVED
                eligible_pool = eligible_top = monitor = False
                reason_detail = player["_staleness_reason"] or "injury/availability status could not be confirmed for this player"
                reason = f"Role unresolved for {player['player_name']} - {reason_detail}."
            elif player["availability_classification"] == "confirmed_unavailable":
                role_classification = INACTIVE
                eligible_pool = eligible_top = monitor = False
                reason = f"Inactive - {player['player_name']} is {player['injury_designation']}."
            elif player["depth_rank"] <= tier:
                role_classification = CONFIRMED_STARTER if player["depth_rank"] == 1 else STANDARD_ELIGIBLE_ROTATION
                eligible_pool = eligible_top = True
                monitor = False
                reason = f"{player['position_group']}{int(player['depth_rank'])} - within the standard eligible depth tier (top {tier})."
            else:
                blockers = group[group["depth_rank"] < player["depth_rank"]]
                blocker_classes = blockers["availability_classification"].tolist()
                blocking_ids = ";".join(blockers["player_id"])
                blocking_names = "; ".join(blockers.apply(_format_blocker, axis=1))
                blocking_statuses = "; ".join(blockers["availability_classification"])

                unresolved_blockers = blockers[blockers["availability_classification"].isin(["available", "unknown"])]
                if not unresolved_blockers.empty:
                    role_classification = BENCH_NO_CLEAR_PATH
                    eligible_pool = eligible_top = monitor = False
                    reason = f"No clear path - {_format_blocker(unresolved_blockers.iloc[0])}."
                elif all(c == "confirmed_unavailable" for c in blocker_classes):
                    role_classification = INJURY_ELEVATED_BACKUP
                    eligible_pool = eligible_top = True
                    monitor = False
                    reason = "Elevated role - " + " and ".join(blockers.apply(_format_blocker, axis=1)) + "."
                else:
                    role_classification = CONTINGENT_BACKUP
                    # Pool-eligible (so the "include conditional injury
                    # replacements" toggle can surface them, clearly labeled,
                    # in the main table) but NEVER top-values-eligible - a
                    # contingent scenario must never appear as a "top value
                    # play" regardless of that toggle.
                    eligible_pool = True
                    eligible_top = False
                    monitor = True
                    conditional_blockers = blockers[blockers["availability_classification"] == "conditional"]
                    reason = "Contingent value - " + " and ".join(conditional_blockers.apply(_format_blocker, axis=1)) + "."

            results.append({
                **player.to_dict(),
                "role_classification": role_classification,
                "role_eligible_for_pool": eligible_pool,
                "role_eligible_for_top_values": eligible_top,
                "is_conditional_monitor": monitor,
                "eligibility_reason": reason,
                "blocking_player_ids": blocking_ids,
                "blocking_player_names": blocking_names,
                "blocking_player_statuses": blocking_statuses,
                "manual_override_applied": False,
                "override_reason": None,
            })

    result_df = pd.DataFrame(results, columns=ROLE_CONTEXT_COLUMNS) if results else pd.DataFrame(columns=ROLE_CONTEXT_COLUMNS)
    result_df = _apply_overrides(result_df, overrides_df, now)
    return result_df[ROLE_CONTEXT_COLUMNS].reset_index(drop=True)


def _apply_overrides(result_df: pd.DataFrame, overrides_df, now) -> pd.DataFrame:
    """
    Apply already-validated, unexpired manual overrides (see
    lib.manual_overrides.load_overrides - this function does not itself
    check expiry/schema, only identity scope). An override is applied ONLY
    when it uniquely identifies exactly one (player_id, canonical_team,
    position_group) row - never across teams or positions, never a guess.
    """
    if overrides_df is None or overrides_df.empty or result_df.empty:
        return result_df

    result_df = result_df.copy()
    for _, override in overrides_df.iterrows():
        override_team = normalize_team(override["team"])
        mask = (
            (result_df["player_id"] == override["player_id"])
            & (result_df["canonical_team"] == override_team)
            & (result_df["position_group"] == override["position"])
        )
        if mask.sum() != 1:
            print(
                f"Warning: manual override for player_id={override['player_id']} "
                f"team={override_team} position={override['position']} did not match exactly "
                f"one player_role_context row ({mask.sum()} matches) - override NOT applied."
            )
            continue

        idx = result_df[mask].index[0]
        status = override["override_status"]
        result_df.loc[idx, "role_classification"] = status
        # Same pool-vs-top-values split as the natural classification:
        # contingent_backup is pool-eligible (toggle-visible) but never
        # top-values-eligible; everything in ELIGIBLE_ROLE_CLASSIFICATIONS
        # is eligible for both.
        result_df.loc[idx, "role_eligible_for_pool"] = (
            status in ELIGIBLE_ROLE_CLASSIFICATIONS or status == CONTINGENT_BACKUP
        )
        result_df.loc[idx, "role_eligible_for_top_values"] = status in ELIGIBLE_ROLE_CLASSIFICATIONS
        result_df.loc[idx, "is_conditional_monitor"] = status == CONTINGENT_BACKUP
        result_df.loc[idx, "eligibility_reason"] = f"Manual override: {override['reason']}"
        result_df.loc[idx, "manual_override_applied"] = True
        result_df.loc[idx, "override_reason"] = override["reason"]

    return result_df


ROLE_JOIN_RENAME = {"player_id": "role_player_id"}
ROLE_JOIN_COLUMNS = [c for c in ROLE_CONTEXT_COLUMNS if c != "player_name"]
ROLE_JOIN_OUTPUT_COLUMNS = [ROLE_JOIN_RENAME.get(c, c) for c in ROLE_JOIN_COLUMNS]


def attach_role_context_to_dk_rows(dk_matched_df: pd.DataFrame, role_context_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join role/eligibility context onto (already stats-matched) DK salary
    rows for display and Top-Value-Play filtering, via the strict, zero-
    fuzzy identity match in `lib.player_identity.match_dk_row_to_role_context`.

    Every row starts out defaulted to `role_unresolved` / not eligible -
    the fail-closed default - and is only overwritten when the DK row
    resolves to exactly one `player_role_context` row.
    """
    df = dk_matched_df.copy()
    rc = role_context_df.copy() if role_context_df is not None and not role_context_df.empty else pd.DataFrame(columns=ROLE_CONTEXT_COLUMNS)
    if not rc.empty:
        rc["_norm_name"] = rc["player_name"].apply(normalize_name)

    for out_col in ROLE_JOIN_OUTPUT_COLUMNS:
        df[out_col] = None
    df["role_classification"] = ROLE_UNRESOLVED
    df["role_eligible_for_pool"] = False
    df["role_eligible_for_top_values"] = False
    df["is_conditional_monitor"] = False
    df["eligibility_reason"] = "No confident role-context match (unresolved player identity for role/injury data)."

    for idx, dk_row in df.iterrows():
        match = match_dk_row_to_role_context(dk_row.get("Name"), dk_row.get("TeamAbbrev"), dk_row.get("Position"), rc)
        if match is None:
            continue
        for src_col, out_col in zip(ROLE_JOIN_COLUMNS, ROLE_JOIN_OUTPUT_COLUMNS):
            df.loc[idx, out_col] = match.get(src_col)

    return df
