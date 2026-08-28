import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lib.data import POSITIONS, data_freshness_caption, load_defense_position_weekly, load_defense_reporting, load_metadata
from lib.defense_trends import (
    DVP_TREND_DISPLAY,
    MATRIX_VIEWS,
    METRIC_SCOPES,
    SORT_METRIC_OPTIONS,
    build_position_detail_table,
    filter_defense_reporting,
    league_position_weekly_average,
    pivot_matrix,
    sort_table,
    weekly_series_with_bye_gaps,
)

st.set_page_config(page_title="Defense vs Position | NFL DFS", page_icon="🛡️", layout="wide")

REPORTING_MODE_LABELS = {
    "in_season": "In Season",
    "preseason_baseline": "Preseason Baseline",
    "no_current_season_data": "No Data",
}

st.title("🛡️ Defense vs Position")
st.caption(data_freshness_caption())
st.caption(
    "A modern, testable replacement for the old Power BI \"Defense vs Position\" research "
    "workflow - trend SIGNALS for research, not a DFS projection or composite score. Every "
    "metric is computed independently within each position; QB, RB, WR, and TE are never "
    "compared, ranked, or colored on a shared scale."
)

defense_reporting = load_defense_reporting()
defense_weekly = load_defense_position_weekly()
meta = load_metadata()
reporting_mode = meta.get("defense_reporting_mode", "no_current_season_data")
reporting_season = meta.get("defense_reporting_season")

if defense_reporting.empty:
    st.warning("No defense reporting data found yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()

# ---------------------------------------------------------------------------
# A. Header / data status
# ---------------------------------------------------------------------------
status_cols = st.columns(4)
status_cols[0].metric("Reporting season", reporting_season if reporting_season is not None else "—")
status_cols[1].metric("Data through week", int(defense_reporting["latest_completed_week"].max()))
status_cols[2].metric("Building next slate", meta.get("next_slate_week") or "Season complete")
status_cols[3].metric("Reporting mode", REPORTING_MODE_LABELS.get(reporting_mode, reporting_mode))

last_updated_raw = defense_reporting["source_last_updated_utc"].iloc[0]
try:
    last_updated_display = pd.to_datetime(last_updated_raw, utc=True).strftime("%b %d, %Y %I:%M %p UTC")
except (ValueError, TypeError):
    last_updated_display = str(last_updated_raw)
st.caption(f"Defense reporting data last generated {last_updated_display}.")

if reporting_mode == "preseason_baseline":
    st.warning(
        f"**Week 1 baseline: prior-season defensive matchup data.** {meta.get('active_season', '?')} "
        f"has no completed games yet, so this DvP report comes from the prior season's full "
        f"({reporting_season}) regular season. Personnel and scheme changes are not yet "
        "reflected, and recent-DvP trend fields are unavailable (shown as \"N/A (Preseason)\") "
        "- this is not 2026 defensive form.",
        icon="⚠️",
    )
elif reporting_mode == "no_current_season_data":
    st.warning("No current-season or prior-season data is available to build this report.", icon="⚠️")

if (defense_reporting["sample_size_label"] == "insufficient_sample").any() and reporting_mode == "in_season":
    st.caption(
        "Some defense/position combinations have fewer than 3 games in sample - their recent "
        "trend reads \"Insufficient Sample\" rather than a real direction. See the toggle below."
    )

st.divider()

# ---------------------------------------------------------------------------
# B. Controls
# ---------------------------------------------------------------------------
st.subheader("Controls")

scope_col, sort_col, dir_col = st.columns([1.6, 1.8, 1])
with scope_col:
    metric_scope = st.radio("Metric scope", METRIC_SCOPES, horizontal=True)
with sort_col:
    sort_options = SORT_METRIC_OPTIONS[metric_scope]
    sort_label = st.selectbox("Sort by (Position Detail table)", [label for _, label in sort_options])
    sort_col_name = {label: col for col, label in sort_options}[sort_label]
with dir_col:
    ascending = st.checkbox("Ascending", value=False)

position = st.radio("Position", POSITIONS, horizontal=True, key="dvp_position")

all_teams = sorted(defense_reporting["defense_team"].unique())
team_col, min_games_col, toggle_col = st.columns([2, 1, 1.6])
with team_col:
    team_filter = st.multiselect("Teams (blank = all)", all_teams, default=[])
with min_games_col:
    max_games = int(defense_reporting["games_in_sample"].max())
    min_games = st.number_input("Min games in sample", min_value=0, max_value=max_games, value=0, step=1)
with toggle_col:
    show_insufficient = st.checkbox("Show insufficient-sample rows", value=True)

filtered_all_positions = filter_defense_reporting(
    defense_reporting, teams=team_filter, min_games=min_games, include_insufficient_sample=show_insufficient
)
filtered_position = filter_defense_reporting(
    defense_reporting, positions=[position], teams=team_filter, min_games=min_games,
    include_insufficient_sample=show_insufficient,
)

st.divider()

# ---------------------------------------------------------------------------
# C. Main DvP matrix
# ---------------------------------------------------------------------------
st.subheader("DvP Matrix")
matrix_view = st.selectbox("Matrix cell values", list(MATRIX_VIEWS.keys()), index=0)
st.caption(
    "Color is always the position-independent percentile (0-100, higher = more favorable "
    "for the offense) regardless of the view selected above - QB/RB/WR/TE are never colored "
    "on a shared scale. Cell text shows the selected view's raw value."
)

percentile_matrix = pivot_matrix(filtered_all_positions, "position_percentile_most_favorable")
value_matrix = pivot_matrix(filtered_all_positions, MATRIX_VIEWS[matrix_view])
games_matrix = pivot_matrix(filtered_all_positions, "games_in_sample")

if percentile_matrix.empty:
    st.info("No defenses match the current filters.")
else:
    text_fmt = "%{text:.0f}" if matrix_view == "Percentile" else ("%{text:.2f}" if matrix_view == "Raw Points Allowed" else "%{text:.1f}")
    heat = go.Figure(
        data=go.Heatmap(
            z=percentile_matrix.values,
            x=percentile_matrix.columns,
            y=percentile_matrix.index,
            zmin=0,
            zmax=100,
            text=value_matrix.reindex_like(percentile_matrix).values,
            texttemplate=text_fmt,
            customdata=games_matrix.reindex_like(percentile_matrix).values,
            hovertemplate=(
                "Defense: %{y}<br>Position: %{x}<br>" + matrix_view + ": %{text}<br>"
                "Percentile: %{z:.0f}<br>Games in sample: %{customdata}<extra></extra>"
            ),
            colorscale="RdYlGn",
            showscale=False,
            xgap=3,
            ygap=2,
        )
    )
    heat.update_layout(height=850, xaxis_title="Position", yaxis_title="Defense", margin=dict(t=10))
    st.plotly_chart(heat, width="stretch")

with st.expander("What does each matrix view mean?"):
    st.markdown(
        """
- **Raw Points Allowed** - average fantasy points (PPR) that defense has allowed to this
  position, from completed games only (`AVERAGE(fantasy_points_ppr)` grouped by opponent +
  position - the original Power BI DAX's own formula).
- **Matchup Index** - `fantasy_points_allowed_per_game / league_avg_points_allowed_for_position * 100`.
  100 = exactly league average for that position; above 100 = more favorable for the offense,
  below 100 = tougher.
- **Percentile** - this defense's rank within the position, expressed 0-100 where higher is
  always more favorable for the offense. This is what drives the heatmap's color in every view.

Every value here is computed **independently within each position** - a defense's QB numbers
never influence, get compared to, or share a color scale with its RB/WR/TE numbers.
        """
    )

st.divider()

# ---------------------------------------------------------------------------
# D. Position detail
# ---------------------------------------------------------------------------
st.subheader(f"{position} Detail")

sorted_position = sort_table(filtered_position, sort_col_name, ascending=ascending)

if sorted_position.empty:
    st.info("No defenses match the current filters for this position.")
else:
    league_avg = sorted_position["league_avg_points_allowed_for_position"].iloc[0]
    st.caption(f"League average for {position}: {league_avg:.2f} fantasy points per game (PPR), from completed games only.")

    detail_table = build_position_detail_table(sorted_position)
    st.dataframe(detail_table, width="stretch", hide_index=True)
    st.download_button(
        f"Download {position} detail as CSV",
        detail_table.to_csv(index=False).encode("utf-8"),
        file_name=f"defense_vs_{position.lower()}.csv",
        mime="text/csv",
    )

    bar_source = sorted_position.dropna(subset=["fantasy_points_allowed_per_game"])
    bar = px.bar(
        bar_source,
        x="fantasy_points_allowed_per_game",
        y="defense_team",
        orientation="h",
        color="position_percentile_most_favorable",
        color_continuous_scale="RdYlGn",
        range_color=(0, 100),
        labels={"fantasy_points_allowed_per_game": "Avg Fantasy Points Allowed (PPR)", "defense_team": "Defense"},
        hover_data={"games_in_sample": True, "matchup_index": ":.1f", "dvp_trend_label": True},
    )
    bar.add_vline(x=league_avg, line_dash="dash", line_color="gray", annotation_text="League avg")
    bar.update_layout(height=max(500, 24 * len(bar_source)), coloraxis_showscale=False, yaxis=dict(categoryorder="total ascending"))
    st.plotly_chart(bar, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# E. Recent-trend chart
# ---------------------------------------------------------------------------
st.subheader("Recent-Trend Chart")
trend_defense = st.selectbox(f"Defense (for {position} weekly trend)", all_teams, key="trend_defense")

max_week = int(defense_reporting["latest_completed_week"].max())
series = weekly_series_with_bye_gaps(defense_weekly, trend_defense, position, max_week)
league_series = league_position_weekly_average(defense_weekly, position, max_week)

if series.empty or series["fantasy_points_allowed"].isna().all():
    st.caption("No weekly data available for this defense/position to chart.")
else:
    trend_fig = px.line(
        series, x="week", y="fantasy_points_allowed", markers=True,
        labels={"week": "Week", "fantasy_points_allowed": "Fantasy Points Allowed (PPR)"},
    )
    trend_fig.update_traces(name=trend_defense, showlegend=True, connectgaps=False)
    trend_fig.add_scatter(
        x=league_series["week"], y=league_series["fantasy_points_allowed"],
        mode="lines", name=f"{position} League Avg", line=dict(dash="dash", color="gray"), connectgaps=False,
    )
    trend_fig.update_layout(height=450, legend_title_text="")
    st.plotly_chart(trend_fig, width="stretch")
    st.caption("Bye weeks show as a genuine gap in the line, never connected across as if the defense played.")

st.divider()

with st.expander("How Defense vs Position is calculated"):
    st.markdown(
        """
**Season DvP** (completed regular-season games only): `fantasy_points_allowed_per_game` is the
average fantasy points (PPR) a defense has allowed to a position, across every completed
player-game - exactly the original Power BI DAX's `AVERAGE(fantasy_points_ppr)` semantics,
including that a week where a defense faced two players at the same position counts both
games (`games_in_sample` is a raw game count for the same reason).

`matchup_index = fantasy_points_allowed_per_game / league_avg_points_allowed_for_position * 100`
and `matchup_delta = fantasy_points_allowed_per_game - league_avg_points_allowed_for_position`.
`position_rank_most_favorable` (dense rank, 1 = most favorable) and
`position_percentile_most_favorable` (0-100, higher = more favorable) are both computed
**within each position separately** - never a shared/global scale.

**Recent DvP** uses each defense's last 3 PLAYED weeks against a position - bye weeks are
skipped, not treated as zero. `dvp_recent_trend_delta` (last-3 vs season) needs at least 2
games to be a real number; `dvp_trend_label` needs the full 3-game window before it will name a
direction (`becoming_more_favorable` / `stable` / `becoming_tougher`), otherwise it reads
"Insufficient Sample" even if the raw number already exists at 2 games - a low-sample trend is
never labeled with the same confidence as a mature one.

**Week 1 Baseline Mode**: before the active season has any completed games, season DvP here
comes from the prior season's full regular season; recent-DvP fields are unavailable and shown
as "N/A (Preseason)", never presented as current-season form.

These are trend **signals** for research - not a DFS projection or composite score. The
Lineup Helper page's projection formula uses `matchup_delta` from this same report (see its own
explainer for the exact blend weight).
        """
    )
