import plotly.express as px
import streamlit as st

from lib.data import POSITION_COLORS, POSITIONS, data_freshness_caption, load_players_current, load_players_weekly

st.set_page_config(page_title="Position Explorer | NFL DFS", page_icon="📊", layout="wide")

st.title("📊 Position Explorer")
st.caption(data_freshness_caption())

current = load_players_current()
weekly = load_players_weekly()

if current.empty:
    st.warning("No completed-week data found yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()

LOW_SAMPLE_GAMES = 3  # players with fewer played games than this are flagged, not hidden by default

# Base columns shown for every position, plus position-specific efficiency columns.
BASE_COLUMNS = {
    "player_display_name": "Player",
    "team": "Team",
    "last_opponent": "Last Opp",
    "games_played": "GP",
    "avg_fantasy_points": "Season Avg",
    "consistency_score": "Consistency",
    "momentum_score": "Momentum",
    "momentum_games_used": "Games Used",
    "opportunity_trend": "Trend",
}

POSITION_COLUMNS = {
    "QB": {
        "completion_pct": "Comp %",
        "passing_yards_per_attempt": "Yds/Att",
        "total_passing_yards": "Pass Yds",
        "total_passing_tds": "Pass TDs",
        "total_rushing_yards": "Rush Yds",
        "yards_per_carry": "Rush YPC",
        "total_rushing_tds": "Rush TDs",
    },
    "RB": {
        "total_carries": "Carries",
        "yards_per_carry": "YPC",
        "total_targets": "Targets",
        "catch_rate": "Catch Rate",
        "points_per_touch": "Pts/Touch",
        "yards_per_touch": "Yds/Touch",
    },
    "WR": {
        "total_targets": "Targets",
        "target_share_pct": "Target Share %",
        "catch_rate": "Catch Rate",
        "yards_per_target": "Yds/Target",
        "yac_per_reception": "YAC/Rec",
        "air_yards_share_pct": "Air Yards Share %",
        "points_per_touch": "Pts/Touch",
    },
    "TE": {
        "total_targets": "Targets",
        "target_share_pct": "Target Share %",
        "catch_rate": "Catch Rate",
        "yards_per_target": "Yds/Target",
        "yac_per_reception": "YAC/Rec",
        "air_yards_share_pct": "Air Yards Share %",
        "points_per_touch": "Pts/Touch",
    },
}


@st.cache_data(show_spinner=False)
def league_avg_by_week_and_position(weekly_df):
    """Precompute once so switching the position radio doesn't re-run a groupby."""
    return weekly_df.groupby(["week", "position"])["fantasy_points_ppr"].mean().reset_index()


league_avg_all = league_avg_by_week_and_position(weekly)

position = st.radio("Position", POSITIONS, horizontal=True)

pos_current = current[current["position"] == position].copy()
pos_weekly = weekly[weekly["position"] == position]

st.divider()

# ---------------------------------------------------------------------------
# Efficiency table
# ---------------------------------------------------------------------------
st.subheader(f"{position} Efficiency Table")

filter_col1, filter_col2 = st.columns([2, 1])
with filter_col1:
    name_filter = st.text_input("Filter by player name", "")
with filter_col2:
    hide_low_sample = st.checkbox(f"Hide < {LOW_SAMPLE_GAMES} games played", value=False)

table = pos_current
if name_filter:
    table = table[table["player_display_name"].str.contains(name_filter, case=False, na=False)]
if hide_low_sample:
    table = table[table["games_played"] >= LOW_SAMPLE_GAMES]
else:
    st.caption(f"Players with fewer than {LOW_SAMPLE_GAMES} games played are shown but should be read with caution (small sample).")

columns_for_position = {**BASE_COLUMNS, **POSITION_COLUMNS.get(position, {})}
available_cols = [c for c in columns_for_position if c in table.columns]
st.dataframe(
    table[available_cols].rename(columns=columns_for_position).sort_values("Momentum", ascending=False, na_position="last"),
    width="stretch",
    hide_index=True,
)

st.divider()

# ---------------------------------------------------------------------------
# Volume vs. efficiency scatter
# ---------------------------------------------------------------------------
st.subheader(f"{position} Volume vs. Efficiency")
st.caption("Touches = targets + carries (season total). Bubble size = season fantasy point average.")

scatter_df = pos_current[pos_current["total_touches"] > 0].copy()
# Bubble size can't be negative, but fantasy points can be (fumbles/INTs) - clip for sizing only.
scatter_df["_bubble_size"] = scatter_df["avg_fantasy_points"].clip(lower=0.1)
scatter = px.scatter(
    scatter_df,
    x="total_touches",
    y="points_per_touch",
    size="_bubble_size",
    color="team",
    hover_name="player_display_name",
    hover_data={
        "last_opponent": True,
        "games_played": True,
        "momentum_score": ":.1f",
        "avg_fantasy_points": ":.1f",
        "_bubble_size": False,
    },
    labels={"total_touches": "Touches (season)", "points_per_touch": "Points per Touch"},
    size_max=30,
)
scatter.update_layout(height=550, showlegend=False)
st.plotly_chart(scatter, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# Weekly trend chart
# ---------------------------------------------------------------------------
st.subheader(f"{position} Weekly Trend")

league_avg = league_avg_all[league_avg_all["position"] == position].rename(columns={"fantasy_points_ppr": "avg_points"})

top_players = (
    pos_current.sort_values("momentum_score", ascending=False, na_position="last")["player_display_name"].head(5).tolist()
)
selected_players = st.multiselect(
    "Overlay individual players", sorted(pos_current["player_display_name"].unique()), default=top_players
)

trend_fig = px.line(
    league_avg, x="week", y="avg_points", labels={"week": "Week", "avg_points": "Fantasy Points (PPR)"},
)
trend_fig.update_traces(name=f"{position} League Avg", line=dict(dash="dash", color="gray"), showlegend=True)

if selected_players:
    player_trend = pos_weekly[pos_weekly["player_display_name"].isin(selected_players)]
    for name, group in player_trend.groupby("player_display_name"):
        trend_fig.add_scatter(
            x=group["week"], y=group["fantasy_points_ppr"], mode="lines+markers", name=name
        )

trend_fig.update_layout(height=500, legend_title_text="")
st.plotly_chart(trend_fig, width="stretch")
