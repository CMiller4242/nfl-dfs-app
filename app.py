import plotly.express as px
import streamlit as st

from lib.data import (
    POSITION_COLORS,
    POSITIONS,
    data_freshness_caption,
    load_metadata,
    load_players_current,
)

st.set_page_config(page_title="NFL DFS Dashboard", page_icon="🏈", layout="wide")

st.title("🏈 NFL DFS Dashboard")
st.caption(data_freshness_caption())

meta = load_metadata()
current = load_players_current()

if not meta or meta.get("status") != "ok" or current.empty:
    st.warning(
        "No completed-week data found yet. Run `python dfs_data_pipeline.py` locally, "
        "or wait for the weekly GitHub Actions refresh - it only publishes a week once "
        "every game in it has a final score."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Quick summary stats
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Latest Completed Week", meta.get("latest_completed_week", "?"))
col2.metric("Next Slate Week", meta.get("next_slate_week") or "Season complete")
col3.metric("Players Tracked", f"{meta.get('player_count', len(current)):,}")
col4.metric("Teams", len(meta.get("teams", [])))

st.divider()

# ---------------------------------------------------------------------------
# Top momentum players across all positions
# ---------------------------------------------------------------------------
st.subheader("🔥 Top Momentum Players")
st.caption(
    "Momentum score uses each player's most recent PLAYED games (byes are skipped, not "
    "zeroed), weighted 50% most recent / 30% second-most-recent / 20% third-most-recent. "
    "Players with fewer than 3 games have those weights renormalized to sum to 1.0 - see "
    "the Games Used column."
)

filter_col, count_col = st.columns([3, 1])
with filter_col:
    selected_positions = st.multiselect("Positions", POSITIONS, default=POSITIONS)
with count_col:
    top_n = st.number_input("Show top", min_value=5, max_value=50, value=15, step=5)

filtered = current[current["position"].isin(selected_positions)] if selected_positions else current
top_momentum = filtered.sort_values("momentum_score", ascending=False, na_position="last").head(int(top_n))

chart = px.bar(
    top_momentum.sort_values("momentum_score"),
    x="momentum_score",
    y="player_display_name",
    color="position",
    color_discrete_map=POSITION_COLORS,
    orientation="h",
    labels={"momentum_score": "Momentum Score", "player_display_name": ""},
    hover_data={"team": True, "last_opponent": True, "momentum_games_used": True, "games_played": True},
)
chart.update_layout(height=max(400, 24 * len(top_momentum)), showlegend=True)
st.plotly_chart(chart, width="stretch")

with st.expander("View as table"):
    display_cols = [
        "player_display_name", "position", "team", "last_opponent",
        "avg_fantasy_points", "momentum_score", "momentum_games_used", "games_played",
        "touches_wow_change", "opportunity_trend",
    ]
    st.dataframe(
        top_momentum[display_cols].rename(columns={
            "player_display_name": "Player",
            "position": "Pos",
            "team": "Team",
            "last_opponent": "Last Opp",
            "avg_fantasy_points": "Season Avg",
            "momentum_score": "Momentum",
            "momentum_games_used": "Games Used",
            "games_played": "GP",
            "touches_wow_change": "Touches WoW",
            "opportunity_trend": "Trend",
        }),
        width="stretch",
        hide_index=True,
    )

st.divider()
st.caption(
    "Use the pages in the sidebar to explore a single position, check defense "
    "matchups, or upload a DraftKings salary CSV for lineup help."
)
