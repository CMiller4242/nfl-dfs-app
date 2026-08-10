import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lib.data import POSITIONS, data_freshness_caption, load_defense_matchups

st.set_page_config(page_title="Defense Matchups | NFL DFS", page_icon="🛡️", layout="wide")

st.title("🛡️ Defense vs. Position Matchups")
st.caption(data_freshness_caption())

dm = load_defense_matchups()
if dm.empty:
    st.warning("No completed-week data found yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()

st.markdown(
    "Fantasy points (PPR) allowed by each defense, per position, from completed games only. "
    "**Each position is colored by its own percentile rank (0-100)** — QB, RB, WR, and TE "
    "score on very different raw ranges, so a shared color scale would be misleading. "
    "A higher percentile always means a more favorable matchup for the offensive player, "
    "regardless of position. Cell text shows the raw average fantasy points allowed."
)

pivot_percentile = dm.pivot(index="defense_team", columns="position", values="matchup_rating_percentile").reindex(columns=POSITIONS)
pivot_raw = dm.pivot(index="defense_team", columns="position", values="fantasy_points_allowed").reindex(columns=POSITIONS)

sort_by = st.selectbox("Sort defenses by", ["Alphabetical"] + POSITIONS, index=0)
if sort_by == "Alphabetical":
    pivot_percentile = pivot_percentile.sort_index()
else:
    order = pivot_percentile[sort_by].sort_values(ascending=False).index
    pivot_percentile = pivot_percentile.loc[order]
pivot_raw = pivot_raw.loc[pivot_percentile.index]

fig = go.Figure(
    data=go.Heatmap(
        z=pivot_percentile.values,
        x=pivot_percentile.columns,
        y=pivot_percentile.index,
        zmin=0,
        zmax=100,
        text=pivot_raw.round(1).values,
        texttemplate="%{text}",
        hovertemplate="Defense: %{y}<br>Position: %{x}<br>Avg pts allowed: %{text}<br>Percentile: %{z:.0f}<extra></extra>",
        colorscale="RdYlGn",
        showscale=False,
        xgap=3,
        ygap=2,
    )
)
fig.update_layout(
    height=850,
    xaxis_title="Position",
    yaxis_title="Defense",
    margin=dict(t=10),
)
st.plotly_chart(fig, width="stretch")

st.divider()

st.subheader("Position Detail")
position = st.radio("Position", POSITIONS, horizontal=True, key="matchup_position")
pos_dm = dm[dm["position"] == position].sort_values("fantasy_points_allowed", ascending=False)
league_avg = pos_dm["position_league_average"].iloc[0] if not pos_dm.empty else None

if league_avg is not None:
    st.caption(f"League average for {position}: {league_avg:.2f} fantasy points per game (PPR).")

bar = px.bar(
    pos_dm,
    x="fantasy_points_allowed",
    y="defense_team",
    orientation="h",
    color="matchup_rating_percentile",
    color_continuous_scale="RdYlGn",
    range_color=(0, 100),
    labels={"fantasy_points_allowed": "Avg Fantasy Points Allowed (PPR)", "defense_team": "Defense"},
)
if league_avg is not None:
    bar.add_vline(x=league_avg, line_dash="dash", line_color="gray", annotation_text="League avg")
bar.update_layout(height=800, coloraxis_showscale=False, yaxis=dict(categoryorder="total ascending"))
st.plotly_chart(bar, width="stretch")

with st.expander("View as table"):
    st.dataframe(
        pos_dm[
            ["defense_team", "fantasy_points_allowed", "games", "position_league_average",
             "matchup_delta", "matchup_rating_percentile", "matchup_rank"]
        ].rename(columns={
            "defense_team": "Defense",
            "fantasy_points_allowed": "Avg Pts Allowed",
            "games": "Games",
            "position_league_average": "Position League Avg",
            "matchup_delta": "Matchup Delta",
            "matchup_rating_percentile": "Percentile (0-100)",
            "matchup_rank": "Rank",
        }),
        width="stretch",
        hide_index=True,
    )
