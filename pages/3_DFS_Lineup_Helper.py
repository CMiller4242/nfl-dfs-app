import io
import os

import pandas as pd
import streamlit as st

from lib.data import (
    POSITIONS,
    data_freshness_caption,
    load_defense_reporting,
    load_depth_chart_metadata,
    load_dk_slate_metadata,
    load_injury_metadata,
    load_metadata,
    load_player_role_context,
    load_players_current,
    load_players_prior_season_baseline,
)
from lib.dk_helper import (
    FUZZY_MATCH_THRESHOLD,
    MATCHUP_ADJUSTMENT_WEIGHT,
    MOMENTUM_ADJUSTMENT_WEIGHT,
    best_value_by_position,
    compute_prior_season_projections,
    compute_projections,
    match_dk_players,
    match_dk_players_prior_season,
    needs_review,
    validate_dk_columns,
)
from lib.dk_salary_loader import CURRENT_CSV_PATH, SalaryCsvValidationError, validate_salary_csv_bytes
from lib.eligibility import attach_role_context_to_dk_rows
from lib.role_config import DEPTH_CHART_FRESHNESS_HOURS, INJURY_FRESHNESS_HOURS

st.set_page_config(page_title="DFS Lineup Helper | NFL DFS", page_icon="💰", layout="wide")

st.title("💰 DFS Lineup Helper")
st.caption(data_freshness_caption())

meta = load_metadata()
app_mode = meta.get("app_mode", "in_season")
active_season = meta.get("active_season", meta.get("season"))
source_season = meta.get("source_season", active_season)
is_preseason = app_mode == "preseason_week_1_baseline"

if is_preseason:
    st.warning(
        "**Week 1 Baseline Mode** — projections use prior-season production. "
        "Current-season momentum and defense-vs-position data are unavailable.",
        icon="⚠️",
    )
    st.caption(
        f"This is a temporary mode, only active because no completed regular-season week exists "
        f"yet for {active_season}. It is not a replacement for the current-season pipeline - once "
        f"Week 1 finishes and the data refreshes, this page automatically switches back to normal "
        f"in-season projections using {active_season} data."
    )

# ---------------------------------------------------------------------------
# Depth chart / injury freshness banner
# ---------------------------------------------------------------------------
depth_meta = load_depth_chart_metadata()
injury_meta = load_injury_metadata()


def _age_hours(timestamp_str):
    if not timestamp_str:
        return None
    try:
        ts = pd.to_datetime(timestamp_str, utc=True)
    except (ValueError, TypeError):
        return None
    return (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 3600.0


def _freshness_label(age_hours, limit_hours):
    if age_hours is None:
        return "unavailable"
    return "fresh" if age_hours <= limit_hours else f"stale ({age_hours:.0f}h old)"


depth_age = _age_hours(depth_meta.get("source_timestamp"))
depth_freshness = _freshness_label(depth_age, DEPTH_CHART_FRESHNESS_HOURS)
injury_source_ok = bool(injury_meta.get("source_success"))

# role_context_source distinguishes what player_role_context.parquet was
# ACTUALLY built from (see dfs_data_pipeline._resolve_role_injury_snapshot)
# from source_success, which only ever describes the latest fetch attempt -
# a failed fetch with a good preserved snapshot must not show as
# "unavailable" here, since role classifications are still fully usable.
role_context_source = injury_meta.get("role_context_source")
fallback_age = injury_meta.get("fallback_snapshot_age_hours")
fallback_stale = injury_meta.get("fallback_snapshot_is_stale")

if role_context_source == "fresh_fetch":
    injury_age = _age_hours(injury_meta.get("retrieved_at"))
    injury_freshness = _freshness_label(injury_age, INJURY_FRESHNESS_HOURS)
elif role_context_source == "fallback_snapshot":
    age_str = f"{fallback_age:.0f}h old" if fallback_age is not None else "age unknown"
    injury_freshness = f"stale fallback ({age_str})" if fallback_stale else f"fresh (fallback, {age_str})"
elif role_context_source == "unavailable":
    injury_freshness = "unavailable (last fetch failed, no fallback)"
else:
    # A metadata file written before this fallback fix existed - fall back
    # to the old (less precise) source_success-only reading rather than
    # crashing on a missing field.
    injury_age = _age_hours(injury_meta.get("retrieved_at"))
    injury_freshness = _freshness_label(injury_age, INJURY_FRESHNESS_HOURS) if injury_source_ok else "unavailable (last fetch failed)"

freshness_cols = st.columns(2)
with freshness_cols[0]:
    st.metric("Depth chart data", depth_freshness, help=f"Source timestamp: {depth_meta.get('source_timestamp', 'unknown')}")
with freshness_cols[1]:
    st.metric(
        "ESPN injury data", injury_freshness,
        help=(
            f"Last fetch attempt: {injury_meta.get('retrieved_at', 'unknown')} (source_success={injury_source_ok}). "
            + (f"Role context is using a preserved snapshot from {injury_meta.get('fallback_snapshot_retrieved_at')}."
               if role_context_source == "fallback_snapshot" else "")
        ),
    )

depth_ok = depth_freshness == "fresh"
injury_ok = role_context_source == "fresh_fetch" or (role_context_source == "fallback_snapshot" and not fallback_stale)

if not depth_ok or not injury_ok:
    st.warning(
        "Role/eligibility data (depth chart and/or ESPN injuries) is stale or unavailable. This is "
        "never treated as real-time - a scheduled refresh is a best-effort snapshot, not a live feed. "
        "Players whose role depends on unresolvable data are automatically routed to Needs Review "
        "rather than guessed at. Check the GitHub Actions tab if this persists."
    )
elif role_context_source == "fallback_snapshot":
    st.info(
        "The latest ESPN injury fetch failed, but role/eligibility is using a still-fresh preserved "
        "snapshot from the last successful fetch - the Player Pool below reflects real injury data, "
        "not a guess."
    )

st.divider()

# ---------------------------------------------------------------------------
# DK salary CSV source: the committed backend slate (data/dk_salaries/
# current.csv, loaded via `python load_dk_salaries.py` - see README) is the
# default active slate on startup. The uploader is always available as a
# session-only override - never written back to disk, so it's safe to use
# on a read-only deployment (e.g. Streamlit Community Cloud) too.
# ---------------------------------------------------------------------------
st.markdown(
    "Reads this week's DraftKings salary CSV (Position, Name, Salary, Game Info, "
    "TeamAbbrev, AvgPointsPerGame) to get "
    + ("prior-season-baseline projections and value plays. " if is_preseason else "matchup-adjusted projections and value plays. ")
    + "**The committed backend file is the default active slate; a session upload overrides it "
    "for your own session only and is never written back to disk or committed.**"
)

slate_meta = load_dk_slate_metadata()

auto_loaded_bytes = None
if os.path.exists(CURRENT_CSV_PATH):
    with open(CURRENT_CSV_PATH, "rb") as f:
        auto_loaded_bytes = f.read()

uploaded = st.file_uploader(
    "DraftKings salary CSV (session-only override)", type="csv",
    help=(
        f"`{CURRENT_CSV_PATH}` is the committed backend slate and auto-loads by default when present. "
        "Uploading here overrides it for this browser session only."
    ),
)

if uploaded is not None:
    file_bytes = uploaded.getvalue()
    active_source = "Session upload override"
    active_filename = uploaded.name
    slate_season, slate_week, slate_updated_at = None, None, None
elif auto_loaded_bytes is not None:
    file_bytes = auto_loaded_bytes
    active_source = "Committed backend salary file"
    active_filename = slate_meta.get("filename") or os.path.basename(CURRENT_CSV_PATH)
    slate_season = slate_meta.get("season")
    slate_week = slate_meta.get("week")
    slate_updated_at = slate_meta.get("updated_at_utc")
else:
    st.info(
        f"No salary data available yet. Load this week's DraftKings export as the committed backend "
        f"slate with `python load_dk_salaries.py path/to/DKSalaries.csv --season <season> --week <week>` "
        f"(see README), or upload a CSV above for this session only."
    )
    st.stop()

try:
    validate_salary_csv_bytes(file_bytes)
except SalaryCsvValidationError as exc:
    st.error(f"'{active_filename}': {exc}")
    st.stop()

if slate_updated_at:
    try:
        slate_updated_display = pd.to_datetime(slate_updated_at, utc=True).strftime("%b %d, %Y %I:%M %p UTC")
    except (ValueError, TypeError):
        slate_updated_display = str(slate_updated_at)
else:
    slate_updated_display = "not tracked for session uploads" if active_source == "Session upload override" else "unknown"

source_cols = st.columns(4)
source_cols[0].metric("Salary data source", active_source)
source_cols[1].metric("Slate season", slate_season if slate_season is not None else "—")
source_cols[2].metric("Slate week", slate_week if slate_week is not None else "—")
source_cols[3].metric("File last updated", slate_updated_display)
st.caption(f"Active file: `{active_filename}`")


@st.cache_data(show_spinner="Matching players and computing projections...")
def process_dk_csv(file_bytes: bytes, mode: str) -> pd.DataFrame:
    dk_df = pd.read_csv(io.BytesIO(file_bytes))
    missing = validate_dk_columns(dk_df)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")

    if mode == "preseason_week_1_baseline":
        baseline = load_players_prior_season_baseline()
        if baseline.empty:
            raise ValueError(
                "No prior-season baseline data available. Run `python dfs_data_pipeline.py` first."
            )
        matched = match_dk_players_prior_season(dk_df, baseline)
        return compute_prior_season_projections(matched)

    current = load_players_current()
    defense = load_defense_reporting()
    matched = match_dk_players(dk_df, current)
    return compute_projections(matched, defense)


try:
    result = process_dk_csv(file_bytes, app_mode)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

result = result.copy()
result["app_mode"] = app_mode
result["active_season"] = active_season
result["source_season"] = source_season
result["data_basis"] = (
    f"{source_season} prior season" if is_preseason else f"{active_season} season to date"
)

# ---------------------------------------------------------------------------
# Attach role/eligibility context (depth chart + ESPN injuries + overrides,
# precomputed by the pipeline into player_role_context.parquet). Zero-fuzzy
# identity match - see lib.player_identity.match_dk_row_to_role_context.
# Every row defaults to role_unresolved/not-eligible until a confident match
# is found.
# ---------------------------------------------------------------------------
role_context_df = load_player_role_context()
result = attach_role_context_to_dk_rows(result, role_context_df)


def _matchup_bucket(delta):
    if pd.isna(delta):
        return "Unknown"
    if delta >= 0.5:
        return "Favorable"
    if delta <= -0.5:
        return "Tough"
    return "Neutral"


result["matchup_quality"] = result["matchup_delta"].apply(_matchup_bucket)


def _describe_review_reason(row):
    method = row.get("match_method")
    status = row.get("projection_status")
    if row.get("role_classification") == "role_unresolved" and status == "ok":
        return f"Stats matched, but role/injury identity is unresolved: {row.get('eligibility_reason')}"
    if method == "ambiguous_prior_season_match":
        return f"Ambiguous: multiple prior-season players share this name + position ({row.get('best_candidate_name')})"
    if method == "unmatched":
        candidate = row.get("best_candidate_name")
        if pd.notna(candidate):
            score = row.get("match_score")
            score_str = f", best fuzzy score {score:.0f}/100" if pd.notna(score) else ""
            return f"No confident match - closest candidate '{candidate}'{score_str}"
        return "No confident match (no name/team/position candidate found - e.g. a rookie or no prior-season history)"
    if status == "no_salary":
        return "Missing or zero salary"
    if status == "no_player_average":
        return "Matched, but no usable average available"
    return status or "Unknown"


# ---------------------------------------------------------------------------
# Match + role summary
# ---------------------------------------------------------------------------
if is_preseason:
    exact_n = (result["match_method"] == "exact_name_team_position").sum()
    identity_n = (result["match_method"] == "prior_team_identity_match").sum()
    matched_mask = result["match_method"].isin(["exact_name_team_position", "prior_team_identity_match"])
    match_summary = (
        f"{matched_mask.sum()} of {len(result)} players matched to {source_season} history "
        f"({exact_n} exact name+team+position, {identity_n} matched by name+position after a team change)."
    )
else:
    matched_mask = result["match_method"] != "unmatched"
    match_summary = (
        f"{matched_mask.sum()} of {len(result)} players matched with confidence "
        f"({(result['match_method'] == 'exact_name_team_position').sum()} exact name+team+position, "
        f"{(result['match_method'] == 'exact_name_team').sum()} exact name+team, "
        f"{(result['match_method'] == 'fuzzy_team_position').sum()} fuzzy)."
    )

stats_review_needed = needs_review(result)
role_unresolved_but_stats_ok = result[(result["projection_status"] == "ok") & (result["role_classification"] == "role_unresolved")]
total_needs_review = len(stats_review_needed) + len(role_unresolved_but_stats_ok)

st.success(
    f"{match_summary} {total_needs_review} row(s) need review (unresolved/ambiguous stats or role/injury "
    "identity, missing average, or missing salary) and are excluded from value rankings below."
)

st.divider()

# ---------------------------------------------------------------------------
# Filters + toggle
# ---------------------------------------------------------------------------
f1, f2, f3, f4, f5 = st.columns([1.1, 1.5, 1.3, 1.1, 1.3])
with f1:
    pos_options = sorted([p for p in result["Position"].unique() if p in POSITIONS])
    pos_filter = st.multiselect("Position", pos_options, default=pos_options)
with f2:
    min_salary, max_salary = int(result["Salary"].min()), int(result["Salary"].max())
    min_salary_filter = st.number_input("Min salary", min_value=0, max_value=max_salary, value=min_salary, step=500)
with f3:
    team_options = sorted(result["TeamAbbrev"].dropna().unique())
    team_filter = st.multiselect("Team", team_options, default=[])
with f4:
    min_value_filter = st.number_input(
        "Min projected value", min_value=0.0, value=0.0, step=0.1, format="%.1f",
        help="Filters on projected points per $1000 salary. Only applies to rows with a real projection."
    )
with f5:
    matchup_quality = st.multiselect(
        "Matchup quality", ["Favorable", "Neutral", "Tough", "Unknown"],
        default=["Favorable", "Neutral", "Tough", "Unknown"],
        help="Always \"Unknown\" in Week 1 Baseline Mode - there's no current-season matchup data yet."
        if is_preseason else None,
    )

include_conditional = st.checkbox(
    "Include conditional injury replacements",
    value=False,
    help="OFF by default. When ON, contingent_backup players (eligible only if a Questionable/Doubtful "
    "player ahead of them sits out) appear in the Player Pool table with a clear conditional label. "
    "They are NEVER included in Top Value Plays, on or off.",
)

# Position/team/salary scope the whole page. Value and matchup-quality
# filters only make sense for rows with a real projection.
base_filtered = result[result["Position"].isin(pos_filter)] if pos_filter else result
base_filtered = base_filtered[base_filtered["Salary"] >= min_salary_filter]
if team_filter:
    base_filtered = base_filtered[base_filtered["TeamAbbrev"].isin(team_filter)]

ok_rows = base_filtered[base_filtered["projection_status"] == "ok"]
if min_value_filter > 0:
    ok_rows = ok_rows[ok_rows["projected_value"] >= min_value_filter]
if matchup_quality:
    ok_rows = ok_rows[ok_rows["matchup_quality"].isin(matchup_quality)]

# --- Bucket by role classification (only meaningful once stats matched "ok") ---
inactive_rows = ok_rows[ok_rows["role_classification"] == "inactive"]
excluded_rows = ok_rows[ok_rows["role_classification"] == "bench_no_clear_path"]
monitor_rows = ok_rows[ok_rows["role_classification"] == "contingent_backup"]
top_values_rows = ok_rows[ok_rows["role_eligible_for_top_values"] == True]  # noqa: E712

pool_base_rows = ok_rows[ok_rows["role_eligible_for_pool"] == True]  # noqa: E712
pool_rows = pool_base_rows if include_conditional else pool_base_rows[pool_base_rows["role_classification"] != "contingent_backup"]

stats_review_rows_filtered = needs_review(base_filtered)
role_unresolved_rows_filtered = base_filtered[
    (base_filtered["projection_status"] == "ok") & (base_filtered["role_classification"] == "role_unresolved")
]
review_rows = pd.concat([stats_review_rows_filtered, role_unresolved_rows_filtered])

st.divider()

# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------
st.subheader("Summary")
s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Eligible players", len(top_values_rows))
s2.metric("Monitored / conditional", len(monitor_rows))
s3.metric("Excluded by role", len(excluded_rows))
s4.metric("Inactive", len(inactive_rows))
s5.metric("Unresolved role data", len(review_rows[review_rows.get("role_classification", "") == "role_unresolved"]) if "role_classification" in review_rows.columns else 0)

st.divider()

# ---------------------------------------------------------------------------
# Top value plays per position
# ---------------------------------------------------------------------------
st.subheader("💎 Top Value Plays by Position")
st.caption(
    "Filtered to each position first, then ranked by projected value. Requires a confident stats "
    "match, a real projection, a usable salary, AND role_eligible_for_top_values - bench, inactive, "
    "unresolved, and contingent (conditional) players never appear here, regardless of the toggle above."
)
best_value = best_value_by_position(top_values_rows)
value_cols = st.columns(len(POSITIONS))
for col, pos in zip(value_cols, POSITIONS):
    pos_df = best_value.get(pos, top_values_rows.iloc[0:0])
    with col:
        st.markdown(f"**{pos}**")
        if pos_df.empty:
            st.caption("No qualifying players")
        for _, row in pos_df.iterrows():
            st.markdown(
                f"- **{row['Name']}** ({row['TeamAbbrev']})  \n"
                f"  ${row['Salary']:,.0f} · {row['projected_value']:.2f} pts/$1k  \n"
                f"  _{row.get('role_classification', 'n/a')}_"
            )

st.divider()

# ---------------------------------------------------------------------------
# Shared role/eligibility display columns, appended to whichever mode-
# specific projection columns are already defined below.
# ---------------------------------------------------------------------------
ROLE_DISPLAY_RENAME = {
    "role_classification": "Role / Availability",
    "depth_rank": "Depth Rank",
    "eligibility_reason": "Opportunity Context",
    "blocking_player_names": "Blocking Players",
    "injury_designation": "Injury Status",
    "role_data_freshness": "Role Data Freshness",
}
ROLE_DISPLAY_COLS = list(ROLE_DISPLAY_RENAME.keys())


def _with_role_display(df, base_cols, base_rename):
    """Append the standard role/eligibility columns (renamed for display) to a mode-specific column set."""
    rename = {**base_rename, **ROLE_DISPLAY_RENAME}
    cols = base_cols + [c for c in ROLE_DISPLAY_COLS if c in df.columns]
    out = df.rename(columns=rename)[[rename.get(c, c) for c in cols]]
    if "Depth Rank" in out.columns:
        out["Depth Rank"] = out["Depth Rank"].apply(lambda v: f"{v:g}" if pd.notna(v) else "—")
    return out


# ---------------------------------------------------------------------------
# Player pool - confident stats match + role-eligible for the pool
# ---------------------------------------------------------------------------
st.subheader("Player Pool")
st.caption(
    "Valid, role-eligible plays only. "
    + ("Includes contingent (conditional) players, clearly labeled, since the toggle above is ON."
       if include_conditional else
       "Toggle \"Include conditional injury replacements\" above to also show contingent players here.")
)

if is_preseason:
    pool_source = pool_rows.copy()
    pool_source["Prior Season Games"] = pool_source.get("stat_games_played")
    pool_source["Prior Season Consistency"] = pool_source.get("stat_consistency_score")
    base_cols = ["Name", "Position", "current_team", "historical_team", "opponent", "Salary",
                 "player_avg", "Prior Season Games", "Prior Season Consistency",
                 "projected_points", "projected_value", "data_basis", "match_method"]
    base_rename = {
        "current_team": "Current Team", "historical_team": "Historical Team", "opponent": "Opponent",
        "player_avg": "Prior Season FPPG", "projected_points": "Baseline Projected Points",
        "projected_value": "Projected Value", "match_method": "Match Method", "data_basis": "Projection Basis",
    }
    pool = _with_role_display(pool_source, base_cols, base_rename).sort_values(
        "Projected Value", ascending=False, na_position="last"
    )
    value_col_label = "Baseline Projected Points"
else:
    base_cols = ["Name", "Position", "TeamAbbrev", "opponent", "Salary", "player_avg", "momentum_score",
                 "matchup_delta", "fantasy_points_allowed_per_game", "matchup_index",
                 "position_percentile_most_favorable", "projected_points", "projected_value",
                 "matchup_quality", "data_basis", "match_method", "match_score"]
    base_rename = {
        "TeamAbbrev": "Team", "opponent": "Opponent", "player_avg": "Season Avg", "momentum_score": "Momentum",
        "matchup_delta": "Matchup Delta", "fantasy_points_allowed_per_game": "Opp Pts Allowed to Pos",
        "matchup_index": "Matchup Index", "position_percentile_most_favorable": "Matchup Percentile",
        "projected_points": "Projected Points", "projected_value": "Projected Value",
        "match_method": "Match Method", "match_score": "Match Score", "matchup_quality": "Matchup Quality",
        "data_basis": "Projection Basis",
    }
    pool = _with_role_display(pool_rows, base_cols, base_rename).sort_values(
        "Projected Value", ascending=False, na_position="last"
    )
    value_col_label = "Projected Points"

if pool.empty:
    st.info("No players with a confident, role-eligible, computable projection in the current filter.")
else:
    st.dataframe(
        pool,
        width="stretch",
        hide_index=True,
        column_config={
            "Salary": st.column_config.NumberColumn("Salary", format="$%d"),
            value_col_label: st.column_config.NumberColumn(value_col_label, format="%.1f"),
            "Projected Value": st.column_config.ProgressColumn(
                "Value (pts/$1k)", min_value=0,
                max_value=max(float(pool["Projected Value"].max() or 1), 1), format="%.2f"
            ),
        },
    )
    st.download_button(
        "Download Player Pool as CSV",
        pool.to_csv(index=False).encode("utf-8"),
        file_name="dk_player_pool.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Plays to Monitor - contingent_backup (Questionable/Doubtful blocker ahead)
# ---------------------------------------------------------------------------
st.subheader("👀 Plays to Monitor")
st.caption(
    "Contingent scenarios: eligibility here depends on a Questionable/Doubtful player ahead of them "
    "on the depth chart actually sitting out. Never in Top Value Plays. Shown here regardless of the "
    "toggle above (the toggle only controls whether they ALSO appear in Player Pool)."
)
if monitor_rows.empty:
    st.caption("No conditional/contingent scenarios in the current filter.")
else:
    if is_preseason:
        monitor_cols = ["Name", "Position", "current_team", "Salary"]
        monitor_rename = {"current_team": "Team"}
    else:
        monitor_cols = ["Name", "Position", "TeamAbbrev", "Salary"]
        monitor_rename = {"TeamAbbrev": "Team"}
    monitor_display = _with_role_display(monitor_rows, monitor_cols, monitor_rename)
    st.dataframe(monitor_display, width="stretch", hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Excluded by Role Context - bench_no_clear_path
# ---------------------------------------------------------------------------
st.subheader("🚫 Excluded by Role Context")
st.caption(
    "Bench players ranked below the standard eligible depth tier with no injury path opened up ahead "
    "of them - a healthy or unconfirmed player still blocks the way."
)
if excluded_rows.empty:
    st.caption("No bench/no-clear-path players in the current filter.")
else:
    if is_preseason:
        excl_cols = ["Name", "Position", "current_team", "Salary"]
        excl_rename = {"current_team": "Team"}
    else:
        excl_cols = ["Name", "Position", "TeamAbbrev", "Salary"]
        excl_rename = {"TeamAbbrev": "Team"}
    st.dataframe(_with_role_display(excluded_rows, excl_cols, excl_rename), width="stretch", hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Needs Review: unmatched, ambiguous, missing average/salary, or role-unresolved
# ---------------------------------------------------------------------------
st.subheader("⚠️ Needs Review")
st.caption(
    "No projection or role determination is guessed at for these rows. Lands here if the player "
    "couldn't be confidently matched to stats, has no usable average or salary, or their role/injury "
    "identity couldn't be resolved (stale/missing/failed depth-chart or ESPN data)."
)

review_display = review_rows.copy()
review_display["Matched / Best Candidate"] = review_display["matched_player_name"].fillna(
    review_display["best_candidate_name"]
)
review_display["Reason"] = review_display.apply(_describe_review_reason, axis=1)

review_id_cols = (
    ["Name", "Position", "current_team", "historical_team", "opponent", "Salary"] if is_preseason
    else ["Name", "Position", "TeamAbbrev", "opponent", "Salary"]
)
review_id_rename = {"current_team": "Current Team", "historical_team": "Historical Team", "opponent": "Opponent"} if is_preseason else {"TeamAbbrev": "Team", "opponent": "Opponent"}
review_display = review_display.rename(columns={"Name": "DK Name", **review_id_rename})
review_id_cols_out = ["DK Name" if c == "Name" else review_id_rename.get(c, c) for c in review_id_cols]

review_cols_final = review_id_cols_out + [
    "match_method", "match_score", "Matched / Best Candidate", "projection_status",
    "role_classification", "Reason",
]
review_display = review_display[[c for c in review_cols_final if c in review_display.columns]]

if review_display.empty:
    st.success("No players need review in the current filter.")
else:
    st.dataframe(review_display, width="stretch", hide_index=True)
    st.download_button(
        "Download Needs Review as CSV",
        review_display.to_csv(index=False).encode("utf-8"),
        file_name="dk_needs_review.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Inactive - collapsed by default
# ---------------------------------------------------------------------------
with st.expander(f"⛔ Inactive ({len(inactive_rows)})", expanded=False):
    st.caption("Confirmed unavailable (Out, IR, Suspended, or officially inactive). Excluded from the pool and top values entirely.")
    if inactive_rows.empty:
        st.caption("No inactive players in the current filter.")
    else:
        if is_preseason:
            inactive_cols = ["Name", "Position", "current_team", "Salary"]
            inactive_rename = {"current_team": "Team"}
        else:
            inactive_cols = ["Name", "Position", "TeamAbbrev", "Salary"]
            inactive_rename = {"TeamAbbrev": "Team"}
        st.dataframe(_with_role_display(inactive_rows, inactive_cols, inactive_rename), width="stretch", hide_index=True)

st.divider()

with st.expander("How projections and role/eligibility are calculated"):
    if is_preseason:
        st.markdown(
            f"""
**Week 1 Baseline Mode** - no current-season games have finished yet for {active_season}, so
projections use `{source_season}` (prior season) regular-season production instead:

`Baseline Projected Points = prior_season_avg_fantasy_points` (no momentum or matchup
adjustment - neither exists yet this season)

`Projected Value = Baseline Projected Points / (Salary / 1000)`

**Player matching** tries, in order: (1) normalized name + current DK team + position,
exact match; (2) if that fails (e.g. the player changed teams), normalized name + position
only, matched against {source_season} history - accepted **only** when it uniquely
identifies exactly one player. There is no fuzzy name matching in this mode.
            """
        )
    else:
        st.markdown(
            f"""
`projected_points = player_avg + (momentum_score - player_avg) * {MOMENTUM_ADJUSTMENT_WEIGHT}
+ matchup_delta * {MATCHUP_ADJUSTMENT_WEIGHT}`

`projected_value = projected_points / (Salary / 1000)`

**Player matching** tries, in order: (1) normalized name + team + position exact match,
(2) normalized name + team exact match, (3) a fuzzy name match restricted to the same
team and position, accepted only above a score of {FUZZY_MATCH_THRESHOLD}/100.
            """
        )
    st.markdown(
        """
**Role/eligibility** is computed entirely separately from projections, from the current
depth chart (`nflreadpy`) and ESPN injury reports - never from salary or projected value.
A player is only ever evaluated against higher-ranked players on their OWN team and
position group; an injury elsewhere can never affect their eligibility. Role classifications:

- `confirmed_starter` / `standard_eligible_rotation`: within the configured standard
  eligible depth tier (QB1; RB1-2; WR1-3; TE1-2) - eligible regardless of anyone's injury
  status, never mislabeled as an injury replacement.
- `injury_elevated_backup`: below the standard tier, but every player ahead of them on the
  depth chart is confirmed unavailable (Out/IR/Suspended) - eligible, with the specific
  blocker(s) named in "Opportunity Context."
- `contingent_backup`: below the standard tier, and at least one player ahead is merely
  Questionable/Doubtful (not confirmed out) - a monitor-only scenario, never in Top Value
  Plays, and only in Player Pool if you toggle "Include conditional injury replacements" on.
- `bench_no_clear_path`: below the standard tier with a healthy or unconfirmed player still
  ahead of them - excluded.
- `inactive`: the player is themselves confirmed unavailable - excluded entirely.
- `role_unresolved`: depth-chart or injury data is missing/stale/failed for this player -
  fails closed to "needs review," never silently treated as available.

Manual corrections (e.g. a late beat-writer report) can be added to
`data/manual/eligibility_overrides.csv` - each override requires a `player_id`, a `reason`,
and an `expires_at`, and can only affect that exact player's own team + position, never
cross-team. An applied override always shows "Manual override: <reason>" in Opportunity
Context.
        """
    )
