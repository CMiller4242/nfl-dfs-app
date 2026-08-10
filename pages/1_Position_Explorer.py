import plotly.express as px
import streamlit as st

from lib.data import POSITION_COLORS, POSITIONS, data_freshness_caption, load_players_current, load_players_weekly

st.set_page_config(page_title="Position Explorer | NFL DFS", page_icon="📊", layout="wide")

st.title("📊 Position Explorer")
st.caption(data_freshness_caption())

current = load_players_current()
weekly = load_players_weekly()

if current.empty:
    st.warning("No data found in `/data` yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()

position = st.radio("Position", POSITIONS, horizontal=True)

pos_current = current[current["position"] == position].copy()
pos_weekly = weekly[weekly["position"] == position].copy()

st.divider()

# ---------------------------------------------------------------------------
# Efficiency table
# ---------------------------------------------------------------------------
st.subheader(f"{position} Efficiency Table")

name_filter = st.text_input("Filter by player name", "")
table = pos_current
if name_filter:
    table = table[table["player_display_name"].str.contains(name_filter, case=False, na=False)]

efficiency_cols = {
    "player_display_name": "Player",
    "team": "Team",
    "opponent_team": "Last Opp",
    "week": "Last Wk",
    "fantasy_points_ppr": "Last Wk Pts",
    "fantasy_points_ppr_3wk_avg": "3-Wk Avg",
    "fp_season_avg": "Season Avg",
    "touches": "Touches",
    "touches_wow_change": "Touches WoW",
    "points_per_touch": "Pts/Touch",
    "yards_per_target": "Yds/Target",
    "yards_per_carry": "Yds/Carry",
    "catch_rate": "Catch Rate",
    "momentum_score": "Momentum",
}
available_cols = [c for c in efficiency_cols if c in table.columns]
st.dataframe(
    table[available_cols].rename(columns=efficiency_cols).sort_values("Momentum", ascending=False),
    width='stretch',
    hide_index=True,
)

st.divider()

# ---------------------------------------------------------------------------
# Volume vs. efficiency scatter
# ---------------------------------------------------------------------------
st.subheader(f"{position} Volume vs. Efficiency")
st.caption("Touches = carries + targets + pass attempts. Bubble size = 3-week scoring average.")

scatter_df = pos_current[pos_current["touches"] > 0].copy()
# Bubble size can't be negative, but fantasy points can be (fumbles/INTs) - clip for sizing only.
scatter_df["_bubble_size"] = scatter_df["fantasy_points_ppr_3wk_avg"].clip(lower=0.1)
scatter = px.scatter(
    scatter_df,
    x="touches",
    y="points_per_touch",
    size="_bubble_size",
    color="team",
    hover_name="player_display_name",
    hover_data={"opponent_team": True, "momentum_score": ":.1f", "fantasy_points_ppr_3wk_avg": ":.1f", "_bubble_size": False},
    labels={"touches": "Touches", "points_per_touch": "Points per Touch"},
    size_max=30,
)
scatter.update_layout(height=550, showlegend=False)
st.plotly_chart(scatter, width='stretch')

st.divider()

# ---------------------------------------------------------------------------
# Weekly trend chart
# ---------------------------------------------------------------------------
st.subheader(f"{position} Weekly Trend")

league_avg = (
    pos_weekly.groupby("week")["fantasy_points_ppr"].mean().reset_index().rename(
        columns={"fantasy_points_ppr": "avg_points"}
    )
)
league_avg["player_display_name"] = f"{position} League Average"

top_players = (
    pos_current.sort_values("momentum_score", ascending=False)["player_display_name"].head(5).tolist()
)
selected_players = st.multiselect("Overlay individual players", sorted(pos_current["player_display_name"].unique()), default=top_players)

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
st.plotly_chart(trend_fig, width='stretch')
