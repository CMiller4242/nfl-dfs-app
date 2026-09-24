import io
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from lib.data import (
    POSITIONS,
    load_defense_reporting,
    load_depth_chart_metadata,
    load_dk_slate_metadata,
    load_injury_metadata,
    load_metadata,
    load_player_role_context,
    load_players_current,
    load_players_prior_season_baseline,
    load_team_reporting,
)
from lib.dk_salary_loader import CURRENT_CSV_PATH, SalaryCsvValidationError, validate_salary_csv_bytes
from lib.matchup_analyzer import (
    EARLY_SAMPLE_GAMES,
    SAMPLE_SIZE_LIMITED_MIN_GAMES,
    apply_research_filters,
    build_csv_export,
    build_display_table,
    build_matchup_analyzer_table,
    build_needs_review_export,
    excluded_by_role_context,
    featured_top_value,
    inactive_players,
    needs_review_rows,
    plays_to_monitor,
    salary_vs_value_chart_data,
    summary_counts,
    team_environment_chart_data,
    valid_player_pool,
    volume_vs_matchup_chart_data,
)
from lib.role_config import DEPTH_CHART_FRESHNESS_HOURS, INJURY_FRESHNESS_HOURS

st.set_page_config(page_title="Matchup Analyzer | NFL DFS", page_icon="🔎", layout="wide")

meta = load_metadata()
slate_meta = load_dk_slate_metadata()
active_season = meta.get("active_season", meta.get("season"))
slate_week = slate_meta.get("week")

st.title("🔎 Matchup Analyzer Expanded")
st.caption(
    f"Week {slate_week if slate_week is not None else '?'} DraftKings slate research — player workload, "
    "role context, team form, and position-specific matchup evidence."
)

# ---------------------------------------------------------------------------
# Data-status banner (item 2)
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

role_context_source = injury_meta.get("role_context_source")
fallback_age = injury_meta.get("fallback_snapshot_age_hours")
fallback_stale = injury_meta.get("fallback_snapshot_is_stale")
if role_context_source == "fresh_fetch":
    injury_freshness = _freshness_label(_age_hours(injury_meta.get("retrieved_at")), INJURY_FRESHNESS_HOURS)
elif role_context_source == "fallback_snapshot":
    age_str = f"{fallback_age:.0f}h old" if fallback_age is not None else "age unknown"
    injury_freshness = f"stale fallback ({age_str})" if fallback_stale else f"fresh (fallback, {age_str})"
elif role_context_source == "unavailable":
    injury_freshness = "unavailable (last fetch failed, no fallback)"
else:
    injury_freshness = _freshness_label(_age_hours(injury_meta.get("retrieved_at")), INJURY_FRESHNESS_HOURS)

status_cols = st.columns(4)
status_cols[0].metric("Active Season", active_season or "—")
status_cols[1].metric("Data Through Week", meta.get("latest_completed_week", "—"))
status_cols[2].metric("Building Week", meta.get("next_slate_week") or "Season complete")
status_cols[3].metric("Reporting Mode", meta.get("team_reporting_mode", "—"))

status_cols2 = st.columns(4)
status_cols2[0].metric("DK Slate Season/Week", f"{slate_meta.get('season', '—')} / {slate_meta.get('week', '—')}")
status_cols2[1].metric("Salary Source", slate_meta.get("source", "—"))
status_cols2[2].metric("Depth Chart Freshness", depth_freshness)
status_cols2[3].metric("Injury Data Freshness", injury_freshness)

st.caption(
    f"Salary file last updated: {slate_meta.get('updated_at_utc', 'unknown')} · "
    f"Player-stat source last refreshed: {meta.get('last_updated', 'unknown')}."
)

games_played_for_warning = load_players_current()
current_season_games = int(games_played_for_warning["games_played"].max()) if not games_played_for_warning.empty else 0
if 0 < current_season_games < SAMPLE_SIZE_LIMITED_MIN_GAMES:
    st.warning(
        f"**Early-season sample**: current-season rates reflect {current_season_games} completed game"
        f"{'s' if current_season_games != 1 else ''}. Use prior-season baseline and role/workload evidence "
        "alongside current form - momentum/trend numbers at this sample size are not established form."
    )

st.divider()

# ---------------------------------------------------------------------------
# DK salary source (same committed-file-first convention as the Lineup
# Helper page - see pages/3_DFS_Lineup_Helper.py)
# ---------------------------------------------------------------------------
auto_loaded_bytes = None
if os.path.exists(CURRENT_CSV_PATH):
    with open(CURRENT_CSV_PATH, "rb") as f:
        auto_loaded_bytes = f.read()

uploaded = st.file_uploader(
    "DraftKings salary CSV (session-only override)", type="csv",
    help=f"`{CURRENT_CSV_PATH}` is the committed backend slate and auto-loads by default when present.",
)

if uploaded is not None:
    file_bytes = uploaded.getvalue()
elif auto_loaded_bytes is not None:
    file_bytes = auto_loaded_bytes
else:
    st.info(
        "No salary data available yet. Load this week's DraftKings export as the committed backend slate "
        "with `python load_dk_salaries.py path/to/DKSalaries.csv --season <season> --week <week>`, or "
        "upload a CSV above for this session only."
    )
    st.stop()

try:
    dk_df = validate_salary_csv_bytes(file_bytes)
except SalaryCsvValidationError as exc:
    st.error(str(exc))
    st.stop()

st.divider()


@st.cache_data(show_spinner="Building matchup research table...")
def _build_table(file_bytes: bytes) -> pd.DataFrame:
    dk = pd.read_csv(io.BytesIO(file_bytes))
    return build_matchup_analyzer_table(
        dk,
        load_players_current(),
        load_players_prior_season_baseline(),
        load_defense_reporting(),
        load_team_reporting(),
        load_player_role_context(),
    )


table = _build_table(file_bytes)

# ---------------------------------------------------------------------------
# Controls (item 7)
# ---------------------------------------------------------------------------
MAX_SALARY_BOUND = int(pd.to_numeric(table["Salary"], errors="coerce").max() or 10000)

FILTER_DEFAULTS = {
    "ma_positions": [], "ma_teams": [], "ma_opponents": [], "ma_min_salary": 0,
    "ma_max_salary": MAX_SALARY_BOUND,
    "ma_min_value": 0.0, "ma_min_points": 0.0, "ma_category": "All",
    "ma_featured_only": False, "ma_include_conditional": False, "ma_show_early_sample": True,
    "ma_search": "",
    "ma_min_targets": 0.0, "ma_min_touches": 0.0, "ma_min_target_share": 0.0,
    "ma_min_air_yards_share": 0.0, "ma_min_matchup_index": 0.0,
    "ma_matchup_pctile_min": 0, "ma_matchup_pctile_max": 100,
    "ma_min_momentum": 0.0, "ma_min_games": 0,
}
for _key, _default in FILTER_DEFAULTS.items():
    st.session_state.setdefault(_key, _default)


def _reset_filters():
    for key, default in FILTER_DEFAULTS.items():
        st.session_state[key] = default


st.subheader("Controls")
c1, c2, c3, c4 = st.columns(4)
with c1:
    position_filter = st.multiselect("Position", POSITIONS, key="ma_positions")
with c2:
    team_options = sorted(table["TeamAbbrev"].dropna().unique())
    team_filter = st.multiselect("Team", team_options, key="ma_teams")
with c3:
    opponent_options = sorted([o for o in table["opponent"].dropna().unique() if o])
    opponent_filter = st.multiselect("Opponent", opponent_options, key="ma_opponents")
with c4:
    category = st.selectbox(
        "Role Category", ["All", "Valid Player Pool", "Featured / Top Value", "Plays to Monitor",
                           "Excluded by Role Context", "Needs Review", "Inactive"],
        key="ma_category",
    )

c5, c6, c7, c8 = st.columns(4)
with c5:
    min_salary = st.number_input("Min salary", min_value=0, max_value=MAX_SALARY_BOUND, step=500, key="ma_min_salary")
with c6:
    max_salary = st.number_input("Max salary", min_value=0, max_value=MAX_SALARY_BOUND, step=500, key="ma_max_salary")
with c7:
    min_value = st.number_input("Min projected value", min_value=0.0, step=0.1, format="%.1f", key="ma_min_value")
with c8:
    min_points = st.number_input("Min projected points", min_value=0.0, step=0.5, format="%.1f", key="ma_min_points")

c9, c10, c11, c12 = st.columns(4)
with c9:
    featured_only = st.checkbox("Show only featured/top-value eligible", key="ma_featured_only")
with c10:
    include_conditional = st.checkbox(
        "Include conditional injury replacements", key="ma_include_conditional",
        help="OFF by default. Contingent/conditional players still always appear in Plays to Monitor.",
    )
with c11:
    show_early_sample = st.checkbox(
        "Show early-sample players (small current-season sample)", key="ma_show_early_sample",
    )
with c12:
    search = st.text_input("Player search", key="ma_search")

with st.expander("Research filters (position-aware — a null metric never excludes a QB/RB from a WR-only filter)"):
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        min_targets = st.number_input("Min targets/game", min_value=0.0, step=0.5, key="ma_min_targets")
    with r2:
        min_touches = st.number_input("Min touches/game", min_value=0.0, step=0.5, key="ma_min_touches")
    with r3:
        min_target_share = st.number_input("Min target share %", min_value=0.0, step=1.0, key="ma_min_target_share")
    with r4:
        min_air_yards_share = st.number_input("Min air-yards share %", min_value=0.0, step=1.0, key="ma_min_air_yards_share")

    r5, r6, r7, r8 = st.columns(4)
    with r5:
        min_matchup_index = st.number_input("Min matchup index", min_value=0.0, step=5.0, key="ma_min_matchup_index")
    with r6:
        pctile_min = st.number_input("Matchup percentile min", min_value=0, max_value=100, step=5, key="ma_matchup_pctile_min")
        pctile_max = st.number_input("Matchup percentile max", min_value=0, max_value=100, step=5, key="ma_matchup_pctile_max")
    with r7:
        min_momentum = st.number_input("Min offensive momentum (team, yards)", step=5.0, key="ma_min_momentum")
    with r8:
        min_games = st.number_input("Min games played", min_value=0, step=1, key="ma_min_games")

st.button("Reset filters", on_click=_reset_filters)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
filtered = apply_research_filters(
    table,
    positions=position_filter or None,
    teams=team_filter or None,
    opponents=opponent_filter or None,
    min_salary=min_salary or None,
    max_salary=max_salary or None,
    min_value=min_value or None,
    min_points=min_points or None,
    name_search=search or None,
    min_targets_per_game=min_targets or None,
    min_touches_per_game=min_touches or None,
    min_target_share_pct=min_target_share or None,
    min_air_yards_share_pct=min_air_yards_share or None,
    min_matchup_index=min_matchup_index or None,
    matchup_percentile_range=(pctile_min, pctile_max) if (pctile_min, pctile_max) != (0, 100) else None,
    min_offensive_momentum=min_momentum or None,
    min_games_played=min_games or None,
)

if not show_early_sample:
    filtered = filtered[~filtered["is_early_sample"]]

CATEGORY_FUNCS = {
    "Valid Player Pool": lambda d: valid_player_pool(d, include_conditional=include_conditional),
    "Featured / Top Value": featured_top_value,
    "Plays to Monitor": plays_to_monitor,
    "Excluded by Role Context": excluded_by_role_context,
    "Needs Review": needs_review_rows,
    "Inactive": inactive_players,
}
if category != "All":
    filtered = CATEGORY_FUNCS[category](filtered)
if featured_only:
    filtered = featured_top_value(filtered)

st.divider()

# ---------------------------------------------------------------------------
# Summary / ranking area (item 9)
# ---------------------------------------------------------------------------
st.subheader("Summary")
counts = summary_counts(filtered, include_conditional=include_conditional)
s1, s2, s3, s4, s5, s6 = st.columns(6)
s1.metric("Player Pool", counts["player_pool_count"])
s2.metric("Featured / Top Value", counts["featured_count"])
s3.metric("Plays to Monitor", counts["monitor_count"])
s4.metric("Excluded by Role", counts["excluded_count"])
s5.metric("Needs Review", counts["needs_review_count"])
s6.metric("Inactive", counts["inactive_count"])

s7, s8 = st.columns(2)
s7.metric("Avg Salary (Player Pool)", f"${counts['avg_pool_salary']:,.0f}" if counts["avg_pool_salary"] else "—")
s8.metric("Median Value (Player Pool)", f"{counts['median_pool_value']:.2f} pts/$1k" if counts["median_pool_value"] else "—")

st.divider()

# ---------------------------------------------------------------------------
# Main Player Pool evidence table (item 8)
# ---------------------------------------------------------------------------
st.subheader("Player Pool Evidence Table")
st.caption(
    "Every row here is either the currently selected Role Category, or (with \"All\" selected) every "
    "matched row in the current filter - use the Role column and the category views below to separate "
    "valid/featured plays from monitor/excluded/review/inactive ones."
)

display_position = position_filter[0] if len(position_filter) == 1 else None
display_table = build_display_table(filtered, display_position)

PCT_COLUMNS = ["Target Share %", "Air Yards Share %", "Catch Rate", "Comp %", "Matchup Percentile"]
RATE_COLUMNS = [
    "Current Season FPPG", "Prior Season FPPG", "Delta vs Prior Season", "Projected Points",
    "Carries/Game", "Targets/Game", "Receptions/Game", "Touches/Game", "Total Yards/Game",
    "Yards/Touch", "Yards/Target", "YAC/Rec", "Rush Attempts/Game", "Yards/Attempt",
    "DvP FPPG Allowed", "Matchup Index", "Matchup Delta", "Team Total Yards/Game",
    "Team Pass Rate", "Team Offensive Momentum", "Team Passing EPA Rate",
]
WOW_COLUMNS = ["WoW Touches", "WoW Targets", "WoW Target Share"]

column_config = {"Salary": st.column_config.NumberColumn("Salary", format="$%d")}
for col in display_table.columns:
    if col in PCT_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%.1f%%")
    elif col in WOW_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%+.1f")
    elif col in RATE_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%.2f")

if display_table.empty:
    st.info("No players match the current filters.")
else:
    st.dataframe(
        display_table.sort_values("Projected Value", ascending=False, na_position="last") if "Projected Value" in display_table.columns else display_table,
        width="stretch", hide_index=True, column_config=column_config,
    )
    st.download_button(
        "Download Player Pool research table as CSV",
        build_csv_export(filtered).to_csv(index=False).encode("utf-8"),
        file_name="matchup_analyzer_player_pool.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Compact research views (item 10)
# ---------------------------------------------------------------------------
st.subheader("💎 Top Value by Position")
featured = featured_top_value(filtered)
value_tabs = st.tabs(POSITIONS)
for tab, pos in zip(value_tabs, POSITIONS):
    with tab:
        pos_df = featured[featured["Position"] == pos].sort_values("projected_value", ascending=False, na_position="last").head(10)
        if pos_df.empty:
            st.caption("No qualifying players.")
        else:
            st.dataframe(
                pos_df[["Name", "TeamAbbrev", "opponent", "Salary", "projected_points", "projected_value",
                        "role_display", "matchup_index"]].rename(columns={
                    "Name": "Player", "TeamAbbrev": "Team", "opponent": "Opponent", "Salary": "Salary",
                    "projected_points": "Projected Points", "projected_value": "Projected Value",
                    "role_display": "Role", "matchup_index": "Matchup Index",
                }),
                width="stretch", hide_index=True,
            )

st.divider()

st.subheader("👀 Plays to Monitor")
st.caption("Contingent scenarios - eligibility depends on a Questionable/Doubtful player ahead of them actually sitting out. Never Featured/Top Value.")
monitor = plays_to_monitor(filtered)
if monitor.empty:
    st.caption("No conditional/contingent scenarios in the current filter.")
else:
    st.dataframe(
        monitor[["Name", "TeamAbbrev", "Position", "Salary", "role_display", "blocking_player_names",
                 "injury_designation", "eligibility_reason", "projected_points", "projected_value"]].rename(columns={
            "Name": "Player", "TeamAbbrev": "Team", "role_display": "Role",
            "blocking_player_names": "Affected Blocker", "injury_designation": "Blocker Status",
            "eligibility_reason": "Opportunity Context", "projected_points": "Projected Points (conditional)",
            "projected_value": "Projected Value (conditional)",
        }),
        width="stretch", hide_index=True,
    )

st.divider()

with st.expander(f"🚫 Excluded by Role Context ({len(excluded_by_role_context(filtered))})", expanded=False):
    excluded = excluded_by_role_context(filtered)
    st.caption("Bench players ranked below the standard eligible depth tier with no injury path opened up ahead of them.")
    if excluded.empty:
        st.caption("No bench/no-clear-path players in the current filter.")
    else:
        st.dataframe(
            excluded[["Name", "role_display", "depth_rank", "TeamAbbrev", "Salary", "blocking_player_names", "eligibility_reason"]].rename(columns={
                "Name": "Player", "role_display": "Role", "depth_rank": "Depth Rank", "TeamAbbrev": "Team",
                "blocking_player_names": "Blockers", "eligibility_reason": "Reason",
            }),
            width="stretch", hide_index=True,
        )

with st.expander(f"⚠️ Needs Review ({len(needs_review_rows(filtered))})", expanded=False):
    review = needs_review_rows(filtered)
    st.caption("No projection or role determination is guessed at for these rows - see Reason for exactly why.")
    if review.empty:
        st.success("No players need review in the current filter.")
    else:
        review_export = build_needs_review_export(review)
        st.dataframe(
            review_export.rename(columns={
                "Name": "Player", "TeamAbbrev": "Team", "opponent": "Opponent", "match_method": "Match Method",
                "match_score": "Match Score", "best_candidate_name": "Best Candidate",
                "projection_status": "Projection Status", "role_classification": "Role",
                "needs_review_reason": "Reason",
            }),
            width="stretch", hide_index=True,
        )
        st.download_button(
            "Download Needs Review as CSV", review_export.to_csv(index=False).encode("utf-8"),
            file_name="matchup_analyzer_needs_review.csv", mime="text/csv",
        )

with st.expander(f"⛔ Inactive ({len(inactive_players(filtered))})", expanded=False):
    inactive = inactive_players(filtered)
    st.caption("Confirmed unavailable - excluded from the pool and top values entirely.")
    if inactive.empty:
        st.caption("No inactive players in the current filter.")
    else:
        st.dataframe(
            inactive[["Name", "TeamAbbrev", "Position", "Salary", "injury_designation", "eligibility_reason"]].rename(columns={
                "Name": "Player", "TeamAbbrev": "Team", "injury_designation": "Injury Status",
                "eligibility_reason": "Reason",
            }),
            width="stretch", hide_index=True,
        )

st.divider()

# ---------------------------------------------------------------------------
# Charts (item 11)
# ---------------------------------------------------------------------------
st.subheader("Research Charts")

chart_col1, chart_col2 = st.columns(2)
with chart_col1:
    st.markdown("**Salary vs. Projected Value**")
    sv_data = salary_vs_value_chart_data(filtered)
    if sv_data.empty:
        st.caption("No eligible rows to chart.")
    else:
        sv_data = sv_data.copy()
        sv_data["_bubble"] = pd.to_numeric(sv_data["projected_points"], errors="coerce").clip(lower=0.1)
        fig = px.scatter(
            sv_data, x="Salary", y="projected_value", color="Position", symbol="role_display",
            size="_bubble", size_max=25, hover_name="Name",
            hover_data={"TeamAbbrev": True, "opponent": True, "Salary": True, "projected_points": ":.1f",
                        "projected_value": ":.2f", "role_display": True, "matchup_index": ":.1f",
                        "stat_total_touches": True, "_bubble": False},
            labels={"Salary": "Salary ($)", "projected_value": "Projected Value (pts/$1k)"},
        )
        fig.update_layout(height=450)
        st.plotly_chart(fig, width="stretch")

with chart_col2:
    st.markdown("**Volume vs. Matchup Opportunity**")
    vol_position = st.selectbox("Position for volume chart", POSITIONS, key="ma_vol_chart_pos")
    vol_data = volume_vs_matchup_chart_data(filtered, vol_position)
    if vol_data.empty:
        st.caption("No eligible rows to chart for this position.")
    else:
        volume_col = {"RB": "touches_per_game", "WR": "targets_per_game", "TE": "targets_per_game", "QB": "carries_per_game"}[vol_position]
        fig2 = px.scatter(
            vol_data, x=volume_col, y="matchup_index", color="role_display", hover_name="Name",
            hover_data={"TeamAbbrev": True, "opponent": True, "stat_games_played": True, "sample_label": True},
            labels={volume_col: volume_col.replace("_", " ").title(), "matchup_index": "Matchup Index"},
        )
        fig2.add_hline(y=100, line_dash="dash", line_color="gray")
        fig2.update_layout(height=450)
        st.plotly_chart(fig2, width="stretch")

st.markdown("**Team Offensive Momentum vs. Opponent Positional Matchup Index**")
st.caption(
    "Each point is one (team, opponent, position) combination in the current slate. X = the player's own "
    "team's recent offensive momentum (last-3 games total yards/game minus season total yards/game). "
    "Y = how favorable the opponent's defense has been against that position (100 = league average)."
)
team_env_data = team_environment_chart_data(filtered)
if team_env_data.empty:
    st.caption("No rows with both team momentum and matchup index available to chart.")
else:
    fig3 = px.scatter(
        team_env_data, x="team_offensive_momentum_yards", y="matchup_index", color="Position",
        hover_data={"TeamAbbrev": True, "opponent": True},
        labels={"team_offensive_momentum_yards": "Team Offensive Momentum (yards)", "matchup_index": "Matchup Index"},
    )
    fig3.add_hline(y=100, line_dash="dash", line_color="gray")
    fig3.add_vline(x=0, line_dash="dash", line_color="gray")
    fig3.update_layout(height=450)
    st.plotly_chart(fig3, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# How to read this page (item 15)
# ---------------------------------------------------------------------------
with st.expander("How to read this page"):
    st.markdown(
        f"""
- **Projected Value** = projected points per $1,000 of salary.
- **Matchup Index** = the opponent's fantasy points allowed to this position vs. the league
  average for that position, scaled so 100 = league average. Above 100 is a MORE favorable
  matchup for the offensive player; below 100 is tougher.
- **Early-season sample**: with fewer than {SAMPLE_SIZE_LIMITED_MIN_GAMES} current-season games
  played, rate/trend numbers (momentum, WoW deltas, matchup trend) are not established form -
  use the "Sample Label" column and the banner at the top of the page, and lean on prior-season
  baseline and role/workload evidence alongside them. Players with {EARLY_SAMPLE_GAMES} or fewer
  games are flagged as an early sample specifically.
- **Role eligibility** filters out bench/inactive/unresolved players from the Featured/Top-Value
  view automatically - it's computed entirely separately from salary or projection (see the Lineup
  Helper page's "How projections and role/eligibility are calculated" for the full classification
  rules, reused unchanged here).
- This page is decision support, not a lineup generator - it surfaces evidence (role, volume,
  matchup, team environment) so you can apply your own judgment. It never computes a composite
  score, ownership projection, or ceiling/boom-bust model.
        """
    )
