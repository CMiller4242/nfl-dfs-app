import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lib.data import POSITIONS, data_freshness_caption, load_defense_matchups

st.set_page_config(page_title="Defense Matchups | NFL DFS", page_icon="🛡️", layout="wide")

st.title("🛡️ Defense vs. Position Matchups")
st.caption(data_freshness_caption())

dm = load_defense_matchups()
if dm.empty:
    st.warning("No data found in `/data` yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()

st.markdown(
    "Average fantasy points (PPR) allowed by each defense, per position. "
    "**Each position column is color-scaled independently** — QB, RB, WR, and TE "
    "score on very different ranges, so comparing raw color intensity across "
    "positions would be misleading. Green = tougher matchup for that defense "
    "(allows more points); red = stingier defense against that position."
)

pivot_raw = dm.pivot(index="defense_team", columns="position", values="avg_points_allowed").reindex(columns=POSITIONS)

sort_by = st.selectbox("Sort defenses by", ["Alphabetical"] + POSITIONS, index=0)
if sort_by == "Alphabetical":
    pivot_raw = pivot_raw.sort_index()
else:
    pivot_raw = pivot_raw.sort_values(sort_by, ascending=False)

# Min-max normalize each position column independently so color intensity is
# fair within a position, while the raw (comparable-in-meaning) value still
# shows in the cell text and hover.
pivot_norm = pivot_raw.apply(
    lambda col: (col - col.min()) / (col.max() - col.min()) if col.max() != col.min() else col * 0 + 0.5,
    axis=0,
)

fig = go.Figure(
    data=go.Heatmap(
        z=pivot_norm.values,
        x=pivot_norm.columns,
        y=pivot_norm.index,
        text=pivot_raw.round(1).values,
        texttemplate="%{text}",
        hovertemplate="Defense: %{y}<br>Position: %{x}<br>Avg pts allowed: %{text}<extra></extra>",
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
st.plotly_chart(fig, width='stretch')

st.divider()

st.subheader("Position Detail")
position = st.radio("Position", POSITIONS, horizontal=True, key="matchup_position")
pos_dm = dm[dm["position"] == position].sort_values("avg_points_allowed", ascending=False)

bar = px.bar(
    pos_dm,
    x="avg_points_allowed",
    y="defense_team",
    orientation="h",
    color="avg_points_allowed",
    color_continuous_scale="RdYlGn",
    labels={"avg_points_allowed": "Avg Fantasy Points Allowed (PPR)", "defense_team": "Defense"},
)
bar.update_layout(height=800, coloraxis_showscale=False, yaxis=dict(categoryorder="total ascending"))
st.plotly_chart(bar, width='stretch')

with st.expander("View as table"):
    st.dataframe(
        pos_dm[["defense_team", "avg_points_allowed", "games", "matchup_rank", "matchup_rating"]].rename(
            columns={
                "defense_team": "Defense",
                "avg_points_allowed": "Avg Pts Allowed",
                "games": "Games",
                "matchup_rank": "Rank",
                "matchup_rating": "Rating (1-5)",
            }
        ),
        width='stretch',
        hide_index=True,
    )
