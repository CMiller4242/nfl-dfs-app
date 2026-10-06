import pandas as pd
import streamlit as st

from lib.cache_fingerprint import reporting_inputs_fingerprint
from lib.data import (
    POSITIONS,
    load_defense_reporting,
    load_dk_slate_metadata,
    load_metadata,
    load_player_opportunity_reporting,
    load_player_role_context,
    load_players_current,
    load_players_prior_season_baseline,
    load_players_weekly,
    load_team_defense_position_reporting,
    load_team_offense_position_reporting,
    load_team_reporting,
    load_upcoming_schedule,
)
from lib.dk_salary_loader import CURRENT_CSV_PATH, validate_salary_csv_bytes
from lib.matchup_analyzer import build_matchup_analyzer_table
from lib.player_case_summary import build_case_summary, format_case_detail
from lib.schema_guard import missing_columns, refresh_message
from lib.team_position_drilldown import (
    attach_current_context,
    attach_slate_case_summary,
    build_game_by_game_table,
    build_player_window_contributions,
    recent_window_weeks,
    team_position_recorded_weeks,
)
from lib.team_position_matrix import (
    LEGEND_TEXT,
    build_matrix,
    build_matrix_display_table,
    build_sample_count_display_table,
    filter_offense_reporting,
    percentile_to_rgb,
    required_matrix_columns,
)
from lib.upcoming_matchups import (
    DEFAULT_MIN_DEFENSE_PERCENTILE,
    DEFAULT_MIN_OFFENSE_PERCENTILE,
    DEFAULT_MIN_SAMPLE_GAMES,
    apply_discovery_filters,
    build_discovery_table,
    resolve_shortlist_uids,
    schedule_is_available,
)

st.set_page_config(page_title="Offensive Production Matrix | NFL DFS", page_icon="🧮", layout="wide")

meta = load_metadata()
slate_meta = load_dk_slate_metadata()
_fingerprint = reporting_inputs_fingerprint()  # forces this page's cache_data calls below to key off it

st.title("🧮 Offensive Production Matrix")
st.caption(
    "Research workflow: which offenses produce strongly at a position, which opponents allow "
    "substantial production at that position, and how that production is distributed among "
    "individual players - connected to this app's existing role, opportunity, and case-summary "
    "evidence. This is discovery and investigation, not a prediction or an endorsement: a "
    "favorable team-position matchup never automatically implies any one player is a good play."
)

# ---------------------------------------------------------------------------
# Slate / statistics context - the stats season/week this reporting is built
# from is shown separately from the currently loaded DK salary slate, which
# is a DIFFERENT, independently versioned thing. Upcoming opponents below
# come ONLY from the verified schedule mart, never from the slate.
# ---------------------------------------------------------------------------
status_cols = st.columns(4)
status_cols[0].metric("Active Season", meta.get("active_season", meta.get("season", "—")))
status_cols[1].metric("Stats Through Week", meta.get("latest_completed_week", "—"))
status_cols[2].metric("Scoring Basis", meta.get("fantasy_scoring_basis", "PPR"))
status_cols[3].metric("DK Slate Season/Week", f"{slate_meta.get('season', '—')} / {slate_meta.get('week', '—')}")

unresolved = meta.get("unresolved_position_production") or {}
if unresolved.get("unresolved_position_rows"):
    st.warning(
        f"**Unresolved-position production detected.** {unresolved['unresolved_position_rows']} player-week "
        f"row(s) totaling {unresolved.get('unresolved_position_points', 0):.1f} fantasy points came from "
        f"position(s) outside QB/RB/WR/TE ({', '.join(unresolved.get('unresolved_positions', []))}) and are "
        "NOT included anywhere in this matrix, the legacy Defense vs Position report, or the Opportunity "
        "Model - they are flagged here rather than silently dropped.",
        icon="⚠️",
    )

st.divider()

# ---------------------------------------------------------------------------
# PART 3 + PART 4 - Offensive Matrix and Upcoming Matchup Discovery.
# Both gated on the SAME offensive/defensive reporting marts, so a missing
# or legacy-schema mart disables only this section (Part 8) while the
# player-distribution drill-down below (which only needs players_weekly +
# role/opportunity context) keeps working independently.
# ---------------------------------------------------------------------------
offense_df = load_team_offense_position_reporting()
defense_df = load_team_defense_position_reporting()

_required_offense_cols = required_matrix_columns()
_offense_missing = missing_columns(offense_df, _required_offense_cols)
_defense_missing = missing_columns(defense_df, ["defense_team", "position"])
_offense_ready = not offense_df.empty and not _offense_missing

if not _offense_ready:
    st.warning(
        refresh_message(_offense_missing) if (_offense_missing and not offense_df.empty) else
        "No offensive team-position reporting is available yet. Run `python dfs_data_pipeline.py` to "
        "generate `team_offense_position_reporting.parquet` (and the comparable defensive mart) before "
        "using the matrix or matchup discovery below. The player-distribution drill-down further down "
        "this page does not depend on this mart and remains usable.",
        icon="⚠️",
    )
else:
    st.caption(
        f"Fantasy scoring basis: **{offense_df['fantasy_scoring_basis'].iloc[0]}** "
        "(PPR scoring - `fantasy_points_ppr` - not complete DraftKings contest scoring). "
        f"Recent window: last {int(offense_df['recent_games_window'].iloc[0])} completed team-games "
        "with recorded data at that position."
    )

    st.subheader("Offensive Production Matrix")

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        window = st.radio("Window", ["Season", "Recent"], horizontal=True, key="opm_window")
    with mc2:
        value_kind = st.radio(
            "Cell values", ["points", "percentile"], horizontal=True, key="opm_value_kind",
            format_func=lambda v: "Points/team-game" if v == "points" else "Percentile",
        )
    with mc3:
        team_filter = st.multiselect("Teams (blank = all)", sorted(offense_df["team"].dropna().unique()), key="opm_teams")
    with mc4:
        position_filter = st.multiselect("Positions (blank = all)", POSITIONS, key="opm_positions")

    filtered_offense = filter_offense_reporting(offense_df, teams=team_filter, positions=position_filter)
    matrix = build_matrix(filtered_offense, window, value_kind)

    if matrix.empty:
        st.info("No teams match the current filters.")
    else:
        display_df = build_matrix_display_table(filtered_offense, window, value_kind)
        percentile_matrix = build_matrix(filtered_offense, window, "percentile").reindex_like(display_df)

        def _apply_percentile_colors(_):
            return pd.DataFrame(
                [[f"background-color: {percentile_to_rgb(percentile_matrix.loc[r, c])}" for c in display_df.columns]
                 for r in display_df.index],
                index=display_df.index, columns=display_df.columns,
            )

        styled = display_df.style.apply(_apply_percentile_colors, axis=None)
        st.dataframe(styled, width="stretch")
        st.caption(LEGEND_TEXT)
        with st.expander("Sample counts (completed team-games recorded, same window)"):
            st.dataframe(build_sample_count_display_table(filtered_offense, window), width="stretch")

    st.divider()
    st.subheader("Upcoming Matchup Discovery")

    upcoming_schedule_df = load_upcoming_schedule()

    if not schedule_is_available(upcoming_schedule_df):
        st.info(
            "**Upcoming matchup discovery is disabled.** No verified upcoming schedule is available "
            "(`upcoming_schedule.parquet` is missing or empty). Run `python dfs_data_pipeline.py` against "
            "a live data source to regenerate it. The matrix above remains fully usable in the meantime - "
            "opponents are never guessed from the currently loaded DK salary slate.",
            icon="ℹ️",
        )
    elif _defense_missing:
        st.warning(refresh_message(_defense_missing), icon="⚠️")
    else:
        discovery_full = build_discovery_table(offense_df, defense_df, upcoming_schedule_df, window=window)
        if discovery_full.empty:
            st.info("No upcoming matchups could be matched between offensive and defensive reporting.")
        else:
            st.caption(
                "Both percentiles are shown independently and are never averaged into one score. The "
                "default 75th/75th-percentile view below is an exploratory RESEARCH FILTER, not a "
                "validated prediction rule - adjust the thresholds freely."
            )
            d1, d2, d3, d4 = st.columns(4)
            with d1:
                disc_positions = st.multiselect("Positions", POSITIONS, key="opm_disc_positions")
            with d2:
                min_off_pct = st.number_input(
                    "Min offensive percentile", min_value=0.0, max_value=100.0,
                    value=DEFAULT_MIN_OFFENSE_PERCENTILE, step=5.0, key="opm_min_off_pct",
                )
            with d3:
                min_def_pct = st.number_input(
                    "Min opponent points-allowed percentile", min_value=0.0, max_value=100.0,
                    value=DEFAULT_MIN_DEFENSE_PERCENTILE, step=5.0, key="opm_min_def_pct",
                )
            with d4:
                min_games = st.number_input(
                    "Min completed-game sample (both sides)", min_value=0, value=DEFAULT_MIN_SAMPLE_GAMES,
                    step=1, key="opm_min_games",
                )

            discovery = apply_discovery_filters(
                discovery_full, positions=disc_positions or None,
                min_offense_percentile=min_off_pct or None, min_defense_percentile=min_def_pct or None,
                min_sample_games=min_games or None,
            )

            st.session_state.setdefault("opm_shortlist_uids", [])
            valid_uids, stale_uids = resolve_shortlist_uids(discovery_full, st.session_state["opm_shortlist_uids"])
            if stale_uids:
                st.info(
                    f"{len(stale_uids)} shortlisted matchup(s) were removed - they no longer exist in the "
                    "current schedule/reporting data (season, week, or reporting may have changed)."
                )
            st.session_state["opm_shortlist_uids"] = valid_uids

            if discovery.empty:
                st.info("No matchups meet the current filters.")
            else:
                display = discovery.copy()
                display["Sample Warning"] = display["sample_warning"].fillna("—")
                display["Shortlisted"] = display["matchup_uid"].isin(st.session_state["opm_shortlist_uids"])
                cols = ["team", "opponent_team", "position", "week", "offense_points_per_team_game",
                        "offense_percentile", "offense_games_recorded", "points_allowed_per_defensive_game",
                        "defense_percentile", "defense_games_recorded", "Sample Warning", "Shortlisted"]
                rename = {
                    "team": "Offense", "opponent_team": "Opponent", "position": "Position", "week": "Week",
                    "offense_points_per_team_game": "Offense Pts/Team-Game",
                    "offense_percentile": "Offense Percentile", "offense_games_recorded": "Offense Games",
                    "points_allowed_per_defensive_game": "Opp Pts Allowed/Def-Game",
                    "defense_percentile": "Opp Points-Allowed Percentile", "defense_games_recorded": "Defense Games",
                }
                edited = st.data_editor(
                    display[cols].rename(columns=rename),
                    column_config={"Shortlisted": st.column_config.CheckboxColumn("Shortlist")},
                    disabled=[v for k, v in rename.items()] + ["Sample Warning"],
                    hide_index=True, width="stretch", key="opm_discovery_editor",
                )
                new_shortlist = discovery.loc[edited["Shortlisted"].values, "matchup_uid"].tolist()
                st.session_state["opm_shortlist_uids"] = new_shortlist
                if new_shortlist:
                    st.caption(f"{len(new_shortlist)} matchup(s) in your session research shortlist.")

st.divider()

# ---------------------------------------------------------------------------
# PARTS 5-7 - Player-distribution drill-down. Independent of the matrix/
# discovery marts above - only needs players_weekly + role/opportunity
# context (+ an optional loaded DK slate for Part 7's case-summary connect).
# ---------------------------------------------------------------------------
st.subheader("Player-Distribution Drill-Down")

weekly_df = load_players_weekly()
if weekly_df.empty:
    st.info("No current-season player-week data available yet to drill into.")
else:
    dd1, dd2, dd3 = st.columns(3)
    with dd1:
        dd_team = st.selectbox("Team", sorted(weekly_df["team"].dropna().unique()), key="opm_dd_team")
    with dd2:
        dd_position = st.selectbox("Position", POSITIONS, key="opm_dd_position")
    with dd3:
        dd_window = st.radio("Window", ["Season", "Recent"], horizontal=True, key="opm_dd_window")

    recorded_weeks = team_position_recorded_weeks(weekly_df, dd_team, dd_position)
    weeks = recorded_weeks if dd_window == "Season" else recent_window_weeks(weekly_df, dd_team, dd_position)

    if not weeks:
        st.caption(f"No recorded {dd_position} production for {dd_team} in this window.")
    else:
        st.caption(
            f"{len(weeks)} distinct completed team-game(s) with recorded {dd_position} data for {dd_team} "
            f"in the {dd_window.lower()} window (weeks {', '.join(str(w) for w in weeks)})."
        )
        contributions = build_player_window_contributions(weekly_df, dd_team, dd_position, weeks)
        contributions = attach_current_context(
            contributions, load_player_role_context(), load_player_opportunity_reporting()
        )

        # Part 7: connect to existing slate-specific case-summary evidence,
        # ONLY when a DK salary slate is actually loaded - never fabricated,
        # never independently reclassified (reuses build_matchup_analyzer_table
        # + build_case_summary verbatim).
        slate_table = None
        try:
            import os

            if os.path.exists(CURRENT_CSV_PATH):
                with open(CURRENT_CSV_PATH, "rb") as f:
                    slate_bytes = f.read()
                dk_df = validate_salary_csv_bytes(slate_bytes)
                base_table = build_matchup_analyzer_table(
                    dk_df, load_players_current(), load_players_prior_season_baseline(),
                    load_defense_reporting(), load_team_reporting(), load_player_role_context(),
                    load_player_opportunity_reporting(),
                )
                slate_table = build_case_summary(base_table)
        except Exception:
            slate_table = None

        contributions = attach_slate_case_summary(contributions, slate_table)
        if slate_table is None:
            st.caption(
                "No current DK salary slate could be loaded - salary, projected value, and case-summary "
                "columns below are unavailable for every player. The historical production distribution "
                "above is unaffected."
            )

        st.dataframe(
            contributions[[
                "player_display_name", "historical_team", "current_team", "role_display", "role_data_freshness",
                "total_fantasy_points", "games_appeared", "points_per_team_game", "points_per_appearance",
                "share_of_team_position_points", "total_carries", "share_of_team_position_carries",
                "total_targets", "share_of_team_position_targets", "team_target_share", "total_receptions",
                "latest_week_appeared", "opportunity_label", "opportunity_reason", "confidence_label",
                "slate_Salary", "slate_projected_value", "slate_signal_alignment",
            ]].rename(columns={
                "player_display_name": "Player", "historical_team": "Team (this window)",
                "current_team": "Current Team", "role_display": "Current Role",
                "role_data_freshness": "Role Freshness", "total_fantasy_points": "Total Points (window)",
                "games_appeared": "Games Appeared", "points_per_team_game": "Points/Team-Game",
                "points_per_appearance": "Points/Appearance",
                "share_of_team_position_points": "Share of Position Points %",
                "total_carries": "Carries", "share_of_team_position_carries": "Share of Position Carries %",
                "total_targets": "Targets", "share_of_team_position_targets": "Share of Position Targets %",
                "team_target_share": "Full-Team Target Share %", "total_receptions": "Receptions",
                "latest_week_appeared": "Latest Week", "opportunity_label": "Opportunity",
                "opportunity_reason": "Opportunity Reason", "confidence_label": "Opportunity Confidence",
                "slate_Salary": "Salary (if on loaded slate)", "slate_projected_value": "Projected Value",
                "slate_signal_alignment": "Signal Alignment",
            }),
            width="stretch", hide_index=True,
        )
        st.caption(
            "Denominators: \"Share of Position *\" divides by this team's TOTAL at the position in this "
            "window (every contributor summed); \"Full-Team Target Share %\" divides by the team's total "
            "targets across ALL positions in this window - a different, wider denominator. A blank share "
            "means the position's total was zero or unavailable, not that the player's share was "
            "literally 0%. Snap counts and route data are not shown - no such fields exist in this "
            "project's data sources."
        )

        st.markdown("**Game-by-game**")
        gbg = build_game_by_game_table(weekly_df, dd_team, dd_position, weeks)
        st.dataframe(
            gbg.drop(columns=["player_id"]).rename(columns={
                "week": "Week", "opponent_team": "Opponent", "player_display_name": "Player",
                "fantasy_points_ppr": "Fantasy Points (PPR)", "carries": "Carries", "targets": "Targets",
                "receptions": "Receptions",
            }),
            width="stretch", hide_index=True,
        )

        st.markdown("**Full player case detail**")
        detail_options = sorted(contributions["player_display_name"].dropna().unique())
        if not detail_options:
            st.caption("No players to select in this window.")
        elif slate_table is None:
            st.caption("No DK salary slate loaded - case detail is unavailable, but the distribution above is historical and unaffected.")
        else:
            detail_name = st.selectbox("Select a player", detail_options, key="opm_detail_player")
            detail_row_candidates = slate_table[slate_table["Name"] == detail_name]
            if detail_row_candidates.empty:
                st.caption(f"{detail_name} is not on the currently loaded DK salary slate - no case detail available.")
            else:
                detail_row = detail_row_candidates.iloc[0]
                detail = format_case_detail(detail_row)
                st.markdown(f"**Signal Alignment:** {detail_row.get('signal_alignment', '—')}")
                st.caption(detail["classification_reason"])
                st.markdown("**Positive evidence:**")
                st.write(detail["why_liked"])
                st.markdown("**Concerns:**")
                st.write(detail["what_could_break"])
                st.markdown("**Missing evidence:**")
                st.write(detail["missing_evidence"])
                st.markdown("**Sample-size warnings:**")
                st.write(detail["sample_warnings"])
                st.markdown("**Eligibility restrictions:**")
                st.write(detail["role_context"])

st.divider()

with st.expander("How this page is calculated"):
    st.markdown(
        """
- **Team-game totals, not per-player averages**: every cell in the matrix sums ALL players at a
  position on a team for one completed game first, then averages those TEAM-GAME totals across the
  window - a team with two productive WRs in one game is not double-counted as two separate games.
- **Percentiles are computed strictly within position** - QB/RB/WR/TE never share a scale, and a
  team with no recorded completed games at a position gets no percentile (never a fabricated 0th).
  Ties share the same rank-based percentile (pandas' average-rank `pct=True` behavior).
- **Points-allowed (team-game basis)** is a NEW, separately-named metric from the legacy Defense vs
  Position report's `fantasy_points_allowed_per_game` (an average per OPPOSING PLAYER APPEARANCE,
  unchanged - see that page). Never read these two numbers interchangeably.
- **Verified zero vs. unavailable**: a completed team-game with a real recorded zero at a position
  counts as zero; a team-game with NO recorded player data at that position is excluded from the
  average entirely, never silently treated as a zero.
- **Upcoming opponents** come only from a verified schedule mart, never from the currently loaded DK
  salary slate - if that mart is missing, matchup discovery disables itself with an explanation
  rather than guessing.
- **This page does not predict, score, or endorse** any player or matchup. A favorable team-position
  percentile is evidence to investigate further via the drill-down above, not a recommendation, and
  a higher matchup percentile is never a probability of success.
        """
    )
