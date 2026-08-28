import pandas as pd
import plotly.express as px
import streamlit as st

from lib.data import data_freshness_caption, load_metadata, load_team_reporting, load_team_stats
from lib.team_trends import (
    METRIC_SCOPES,
    SORT_METRIC_OPTIONS,
    build_display_table,
    compute_kpis,
    filter_team_reporting,
    sort_table,
    weekly_total_yards_by_team,
)

st.set_page_config(page_title="Team Trends | NFL DFS", page_icon="📈", layout="wide")

MAX_TREND_TEAMS = 8  # keep the weekly trend chart readable, not a spaghetti chart

REPORTING_MODE_LABELS = {
    "in_season": "In Season",
    "preseason_baseline": "Preseason Baseline",
    "no_current_season_data": "No Data",
}

st.title("📈 Team Trends")
st.caption(data_freshness_caption())
st.caption(
    "A modern, testable replacement for the old Power BI \"Team Offense Rankings\" report - "
    "trend SIGNALS for research, not a DFS projection or composite score (see the Lineup Helper "
    "page for player-level projections)."
)

team_reporting = load_team_reporting()
meta = load_metadata()
reporting_mode = meta.get("team_reporting_mode", "no_current_season_data")
reporting_season = meta.get("team_reporting_season")

if team_reporting.empty:
    st.warning(
        "No team reporting data found yet. Run `python dfs_data_pipeline.py` first, or wait "
        "for the next scheduled refresh."
    )
    st.stop()

# ---------------------------------------------------------------------------
# A. Header / data status
# ---------------------------------------------------------------------------
status_cols = st.columns(4)
status_cols[0].metric("Data through week", meta.get("latest_completed_week") or "—")
status_cols[1].metric("Building next slate", meta.get("next_slate_week") or "Season complete")
status_cols[2].metric("Reporting mode", REPORTING_MODE_LABELS.get(reporting_mode, reporting_mode))
status_cols[3].metric("Reporting season", reporting_season if reporting_season is not None else "—")

last_updated_raw = team_reporting["last_updated_utc"].iloc[0]
try:
    last_updated_display = pd.to_datetime(last_updated_raw, utc=True).strftime("%b %d, %Y %I:%M %p UTC")
except (ValueError, TypeError):
    last_updated_display = str(last_updated_raw)
st.caption(f"Team reporting data last generated {last_updated_display}.")

if reporting_mode == "preseason_baseline":
    st.warning(
        f"**Preseason Baseline Mode** — {meta.get('active_season', '?')} has no completed games "
        f"yet, so these season averages come from the prior season's full ({reporting_season}) "
        "regular season. Current-season trends, offensive momentum, and week-over-week changes "
        "are **not available** and show as \"N/A (Preseason)\" below - a prior season's final "
        "weeks are never presented as this season's momentum.",
        icon="⚠️",
    )
elif reporting_mode == "no_current_season_data":
    st.warning("No current-season or prior-season team stats are available to build this report.", icon="⚠️")

if (team_reporting["sample_size_label"] == "insufficient_sample").any() and reporting_mode == "in_season":
    st.caption(
        "Some teams have fewer than 3 games played this season - their recent-form/momentum "
        "read as \"Insufficient Sample\" rather than a real trend. See the toggle below."
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
    sort_label = st.selectbox("Sort by", [label for _, label in sort_options])
    sort_col_name = {label: col for col, label in sort_options}[sort_label]
with dir_col:
    ascending = st.checkbox("Ascending", value=False)

all_teams = sorted(team_reporting["team"].unique())
team_col, min_games_col, toggle_col = st.columns([2, 1, 1.6])
with team_col:
    team_filter = st.multiselect("Teams (blank = all)", all_teams, default=[])
with min_games_col:
    max_games = int(team_reporting["games_played"].max())
    min_games = st.number_input("Min games played", min_value=0, max_value=max_games, value=0, step=1)
with toggle_col:
    show_insufficient = st.checkbox("Show insufficient-sample teams", value=True)

filtered = filter_team_reporting(
    team_reporting, teams=team_filter, min_games=min_games, include_insufficient_sample=show_insufficient
)
sorted_df = sort_table(filtered, sort_col_name, ascending=ascending)

st.divider()

# ---------------------------------------------------------------------------
# C. KPI summary row (respects the filters above)
# ---------------------------------------------------------------------------
st.subheader("League Snapshot")
kpis = compute_kpis(filtered)

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "League Avg Total Yards/Game",
    f"{kpis['league_avg_total_yards_per_game']:.1f}" if pd.notna(kpis["league_avg_total_yards_per_game"]) else "—",
)
k2.metric(
    "League Avg Pass Rate",
    f"{kpis['league_avg_pass_rate_pct']:.1f}%" if pd.notna(kpis["league_avg_pass_rate_pct"]) else "—",
)
k3.metric(
    "Most Improved (Momentum)",
    kpis["most_improved_team"] or "—",
    f"{kpis['most_improved_momentum_yards']:+.1f} yds/gm" if kpis["most_improved_momentum_yards"] is not None else None,
)
k4.metric(
    "Highest Last-3 Total Yards/Game",
    kpis["highest_last_3_team"] or "—",
    f"{kpis['highest_last_3_yards_per_game']:.1f}" if kpis["highest_last_3_yards_per_game"] is not None else None,
)

st.divider()

# ---------------------------------------------------------------------------
# D. Main sortable team table
# ---------------------------------------------------------------------------
st.subheader("Team Table")
if sorted_df.empty:
    st.info("No teams match the current filters.")
else:
    display_table = build_display_table(sorted_df)
    st.dataframe(display_table, width="stretch", hide_index=True)
    st.download_button(
        "Download as CSV",
        display_table.to_csv(index=False).encode("utf-8"),
        file_name="team_trends.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# E1. Pass-vs-rush team profile
# ---------------------------------------------------------------------------
st.subheader("Pass vs. Rush Team Profile")
st.caption(
    "Bubble size = total yards/game. Color = offensive momentum (last-3 vs season total "
    "yards/game) - blue is heating up, red is cooling off; exact values are always in the "
    "hover text too, never color-only."
)

if filtered.empty:
    st.caption("No teams match the current filters.")
else:
    profile_df = filtered.dropna(subset=["season_passing_yards_per_game", "season_rushing_yards_per_game"]).copy()
    profile_df["_bubble_size"] = profile_df["season_total_yards_per_game"].clip(lower=0.1)
    max_abs_momentum = profile_df["offensive_momentum_yards"].abs().max()
    color_range = float(max_abs_momentum) if pd.notna(max_abs_momentum) and max_abs_momentum else 1.0

    profile_fig = px.scatter(
        profile_df,
        x="season_passing_yards_per_game",
        y="season_rushing_yards_per_game",
        size="_bubble_size",
        color="offensive_momentum_yards",
        color_continuous_scale="RdBu",
        range_color=[-color_range, color_range],
        hover_name="team",
        hover_data={
            "games_played": True,
            "season_total_yards_per_game": ":.1f",
            "season_pass_rate_pct": ":.1f",
            "season_passing_epa_per_dropback_or_attempt": ":.3f",
            "offensive_momentum_yards": ":.1f",
            "recent_form_label": True,
            "_bubble_size": False,
        },
        labels={
            "season_passing_yards_per_game": "Pass Yards/Game",
            "season_rushing_yards_per_game": "Rush Yards/Game",
            "offensive_momentum_yards": "Momentum",
        },
        size_max=32,
    )
    profile_fig.update_layout(height=550, coloraxis_colorbar_title="Momentum")
    st.plotly_chart(profile_fig, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# E2. Weekly team total-yards trend
# ---------------------------------------------------------------------------
st.subheader("Weekly Team Total Yards Trend")

default_trend_teams = sorted_df["team"].head(5).tolist() if not sorted_df.empty else []
trend_teams = st.multiselect(
    f"Teams to chart (max {MAX_TREND_TEAMS})", all_teams,
    default=default_trend_teams[:MAX_TREND_TEAMS], max_selections=MAX_TREND_TEAMS,
)
show_league_avg = st.checkbox("Show league-average reference line", value=True)

trend_max_week = int(team_reporting["latest_played_week"].max())
weekly_yards = weekly_total_yards_by_team(load_team_stats(), reporting_season, trend_max_week)

if not trend_teams:
    st.caption("Select at least one team above to see its weekly trend.")
elif weekly_yards.empty:
    st.caption("No weekly team stats available for this season to chart.")
else:
    chart_df = weekly_yards[weekly_yards["team"].isin(trend_teams)]
    trend_fig = px.line(
        chart_df, x="week", y="total_yards", color="team", markers=True,
        labels={"week": "Week", "total_yards": "Total Yards", "team": "Team"},
    )
    if show_league_avg:
        league_avg_by_week = weekly_yards.groupby("week")["total_yards"].mean().reset_index()
        trend_fig.add_scatter(
            x=league_avg_by_week["week"], y=league_avg_by_week["total_yards"],
            mode="lines", name="League Avg", line=dict(dash="dash", color="gray"),
        )
    trend_fig.update_layout(height=450, legend_title_text="")
    st.plotly_chart(trend_fig, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# E3. Optional compact ranked bar chart
# ---------------------------------------------------------------------------
st.subheader("Ranked Bar Chart")
bar_metric_label = st.selectbox(
    "Metric", ["Offensive Momentum (yards/game)", "Last-3 Total Yards/Game"], key="bar_metric"
)
bar_col = "offensive_momentum_yards" if bar_metric_label.startswith("Offensive") else "last_3_total_yards_per_game"

bar_df = filtered.dropna(subset=[bar_col]).sort_values(bar_col, ascending=True)
if bar_df.empty:
    st.caption("No teams have a computable value for this metric in the current filter.")
else:
    bar_fig = px.bar(
        bar_df, x=bar_col, y="team", orientation="h", color=bar_col,
        color_continuous_scale="RdBu", labels={bar_col: bar_metric_label, "team": ""},
    )
    bar_fig.update_layout(height=max(400, 24 * len(bar_df)), coloraxis_showscale=False)
    st.plotly_chart(bar_fig, width="stretch")

st.divider()

with st.expander("How Team Trends is calculated"):
    st.markdown(
        """
**Season aggregates** (completed regular-season games only): yards/game are season totals
divided by games played; Pass Rate = attempts / (attempts + carries); Passing EPA Rate =
SUM(passing_epa) / SUM(dropbacks), where dropbacks = attempts + sacks_suffered - an honest
per-play rate, not an average of weekly EPA totals (see the README for why that distinction
matters).

**Recent form** uses each team's last 3 PLAYED games - bye weeks are skipped, not treated as
zero yards. Offensive Momentum = last-3 total yards/game minus season total yards/game. With
fewer than 3 games played, recent-form fields read "Insufficient Sample" rather than a real trend.

**Week-over-week (WoW) change** compares the two most recent PLAYED games, never a strict
calendar week - a WoW change is null with fewer than 2 games played.

**Preseason Baseline Mode**: before the active season has any completed games, season averages
here come from the prior season's full regular season; recency/momentum/WoW fields are
unavailable and shown as "N/A (Preseason)", never presented as current-season momentum.

These are trend **signals** for research - not a DFS projection, matchup rating, or composite
score. See the Lineup Helper page for player-level projections and the Defense Matchups page
for matchup quality.
        """
    )
