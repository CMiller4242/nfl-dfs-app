import plotly.express as px
import streamlit as st

from lib.data import POSITION_COLORS, POSITIONS, data_freshness_caption, load_players_current, load_players_weekly
from lib.position_explorer import (
    LOW_SAMPLE_GAMES,
    build_csv_export,
    build_display_table,
    early_sample_warning,
    filter_table,
    weekly_receiving_for_player,
    weekly_volume_for_player,
)

st.set_page_config(page_title="Position Explorer | NFL DFS", page_icon="📊", layout="wide")

st.title("📊 Position Explorer")
st.caption(data_freshness_caption())

current = load_players_current()
weekly = load_players_weekly()

if current.empty:
    st.warning("No completed-week data found yet. Run `python dfs_data_pipeline.py` first.")
    st.stop()


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
# Reporting table - workload/opportunity first, efficiency alongside it
# ---------------------------------------------------------------------------
st.subheader(f"{position} Reporting Table")

filter_col1, filter_col2 = st.columns([2, 1])
with filter_col1:
    name_filter = st.text_input("Filter by player name", "")
with filter_col2:
    hide_low_sample = st.checkbox(f"Hide < {LOW_SAMPLE_GAMES} games played", value=False)

table = filter_table(pos_current, name_filter=name_filter, hide_low_sample=hide_low_sample)
if not hide_low_sample:
    st.caption(f"Players with fewer than {LOW_SAMPLE_GAMES} games played are shown but should be read with caution (small sample).")

sample_warning = early_sample_warning(table)
if sample_warning:
    st.warning(sample_warning)

display_table = build_display_table(table, position)
sort_col = "Momentum" if "Momentum" in display_table.columns else display_table.columns[0]

# Percentages/rates get explicit formatting so they stay legible at a glance
# instead of showing raw floats; counts/totals are left as plain numbers.
PCT_COLUMNS = ["Target Share %", "Air Yards Share %", "Comp %", "Catch Rate"]
RATE_COLUMNS = [
    "Season FPPG", "Last Game FPPG", "Momentum", "Yds/Att", "Rush YPC",
    "Carries/Game", "Touches/Game", "Total Yards/Game", "YPC", "Yards/Target",
    "Yards/Touch", "Points/Touch", "Targets/Game", "Receptions/Game",
    "Receiving Yards/Game", "Air Yards/Game", "YAC/Reception",
]
WOW_COLUMNS = ["WoW Carries", "WoW Targets", "WoW Touches", "WoW Receiving Yards", "WoW Target Share"]

column_config = {}
for col in display_table.columns:
    if col in PCT_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%.1f%%")
    elif col in WOW_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%+.1f")
    elif col in RATE_COLUMNS:
        column_config[col] = st.column_config.NumberColumn(col, format="%.2f")

st.dataframe(
    display_table.sort_values(sort_col, ascending=False, na_position="last"),
    width="stretch",
    hide_index=True,
    column_config=column_config,
)

csv_export = build_csv_export(table, position)
st.download_button(
    "Download filtered table as CSV",
    data=csv_export.to_csv(index=False).encode("utf-8"),
    file_name=f"position_explorer_{position.lower()}.csv",
    mime="text/csv",
)

st.divider()

# ---------------------------------------------------------------------------
# Volume vs. efficiency scatter
# ---------------------------------------------------------------------------
if position in ("WR", "TE"):
    st.subheader(f"{position} Volume vs. Efficiency")
    st.caption("Bubble size = Receiving Yards/Game. Color = Season FPPG.")

    scatter_df = pos_current[pos_current["targets_per_game"] > 0].copy()
    scatter_df["_bubble_size"] = scatter_df["receiving_yards_per_game"].clip(lower=0.1)
    scatter = px.scatter(
        scatter_df,
        x="targets_per_game",
        y="yards_per_target",
        size="_bubble_size",
        color="avg_fantasy_points",
        color_continuous_scale="Viridis",
        hover_name="player_display_name",
        hover_data={
            "total_targets": True,
            "total_receptions": True,
            "total_receiving_yards": True,
            "total_receiving_air_yards": True,
            "target_share_pct": ":.1f",
            "air_yards_share_pct": ":.1f",
            "catch_rate": ":.2f",
            "yac_per_reception": ":.1f",
            "avg_fantasy_points": ":.1f",
            "_bubble_size": False,
        },
        labels={"targets_per_game": "Targets/Game", "yards_per_target": "Yards per Target", "avg_fantasy_points": "Season FPPG"},
        size_max=30,
    )
    scatter.update_layout(height=550)
    st.plotly_chart(scatter, width="stretch")

    if "air_yards_share_pct" in pos_current.columns and pos_current["air_yards_share_pct"].notna().any():
        st.caption(f"{position} Target Share vs. Air Yards Share")
        share_df = pos_current[pos_current["target_share_pct"].notna() & pos_current["air_yards_share_pct"].notna()].copy()
        share_fig = px.scatter(
            share_df,
            x="target_share_pct",
            y="air_yards_share_pct",
            color="team",
            hover_name="player_display_name",
            hover_data={"avg_fantasy_points": ":.1f", "total_targets": True},
            labels={"target_share_pct": "Target Share %", "air_yards_share_pct": "Air Yards Share %"},
        )
        share_fig.update_layout(height=450, showlegend=False)
        st.plotly_chart(share_fig, width="stretch")
else:
    st.subheader(f"{position} Volume vs. Efficiency")
    st.caption("Touches = targets + carries (season total). Bubble size = season fantasy point average.")

    scatter_df = pos_current[pos_current["total_touches"] > 0].copy()
    # Bubble size can't be negative, but fantasy points can be (fumbles/INTs) - clip for sizing only.
    scatter_df["_bubble_size"] = scatter_df["avg_fantasy_points"].clip(lower=0.1)
    hover_data = {
        "last_opponent": True,
        "games_played": True,
        "momentum_score": ":.1f",
        "avg_fantasy_points": ":.1f",
        "_bubble_size": False,
    }
    if position == "RB":
        hover_data.update({
            "total_carries": True,
            "total_targets": True,
            "total_rushing_yards": True,
            "total_receiving_yards": True,
            "total_yards": True,
            "touches_per_game": ":.1f",
            "points_per_touch": ":.2f",
        })
    scatter = px.scatter(
        scatter_df,
        x="total_touches",
        y="points_per_touch",
        size="_bubble_size",
        color="team",
        hover_name="player_display_name",
        hover_data=hover_data,
        labels={"total_touches": "Touches (season)", "points_per_touch": "Points per Touch"},
        size_max=30,
    )
    scatter.update_layout(height=550, showlegend=False)
    st.plotly_chart(scatter, width="stretch")

st.divider()

# ---------------------------------------------------------------------------
# Selected-player weekly workload chart
# ---------------------------------------------------------------------------
if position == "RB":
    st.subheader("Weekly Volume - Selected Player")
    player_options = sorted(pos_current["player_display_name"].unique())
    if player_options:
        selected_player = st.selectbox("Player", player_options)
        volume_df = weekly_volume_for_player(pos_weekly, selected_player)
        if not volume_df.empty:
            volume_long = volume_df.melt(id_vars="week", value_vars=["carries", "targets", "touches"], var_name="stat", value_name="value")
            volume_fig = px.bar(
                volume_long, x="week", y="value", color="stat", barmode="group",
                labels={"week": "Week", "value": "Count", "stat": "Stat"},
            )
            volume_fig.update_layout(height=450, legend_title_text="")
            st.plotly_chart(volume_fig, width="stretch")
elif position in ("WR", "TE"):
    st.subheader("Weekly Receiving Workload - Selected Player")
    player_options = sorted(pos_current["player_display_name"].unique())
    if player_options:
        selected_player = st.selectbox("Player", player_options)
        receiving_df = weekly_receiving_for_player(pos_weekly, selected_player)
        if not receiving_df.empty:
            workload_long = receiving_df.melt(id_vars="week", value_vars=["targets", "receptions"], var_name="stat", value_name="value")
            workload_fig = px.bar(
                workload_long, x="week", y="value", color="stat", barmode="group",
                labels={"week": "Week", "value": "Count", "stat": "Stat"},
            )
            workload_fig.add_scatter(
                x=receiving_df["week"], y=receiving_df["receiving_yards"], mode="lines+markers",
                name="Receiving Yards", yaxis="y2",
            )
            workload_fig.update_layout(
                height=450, legend_title_text="",
                yaxis2=dict(title="Receiving Yards", overlaying="y", side="right"),
            )
            st.plotly_chart(workload_fig, width="stretch")

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
