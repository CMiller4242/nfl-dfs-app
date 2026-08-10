import io

import pandas as pd
import streamlit as st

from lib.data import POSITIONS, data_freshness_caption, load_defense_matchups, load_players_current
from lib.dk_helper import compute_projections, match_dk_players, validate_dk_columns

st.set_page_config(page_title="DFS Lineup Helper | NFL DFS", page_icon="💰", layout="wide")

st.title("💰 DFS Lineup Helper")
st.caption(data_freshness_caption())

st.markdown(
    "Upload this week's DraftKings salary CSV (Position, Name, Salary, Game Info, "
    "TeamAbbrev, AvgPointsPerGame) to get matchup-adjusted projections and value plays."
)

uploaded = st.file_uploader("DraftKings salary CSV", type="csv")

if not uploaded:
    st.info("Waiting for a DraftKings salary CSV upload.")
    st.stop()


@st.cache_data(show_spinner="Matching players and computing projections...")
def process_dk_csv(file_bytes: bytes) -> pd.DataFrame:
    dk_df = pd.read_csv(io.BytesIO(file_bytes))
    missing = validate_dk_columns(dk_df)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")

    current = load_players_current()
    defense = load_defense_matchups()
    matched = match_dk_players(dk_df, current)
    return compute_projections(matched, defense)


try:
    result = process_dk_csv(uploaded.getvalue())
except ValueError as exc:
    st.error(str(exc))
    st.stop()

matched_count = (result["match_quality"] != "none").sum()
fuzzy_count = (result["match_quality"].isin(["fuzzy", "name_only"])).sum()
unmatched_count = (result["match_quality"] == "none").sum()
st.success(
    f"Matched {matched_count} of {len(result)} players ({fuzzy_count} via fuzzy match, "
    f"{unmatched_count} unmatched — projections for unmatched players fall back to DK's own AvgPointsPerGame)."
)

st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1.2, 2, 1.5, 1])
with filter_col1:
    pos_options = sorted([p for p in result["Position"].unique() if p in POSITIONS])
    pos_filter = st.multiselect("Position", pos_options, default=pos_options)
with filter_col2:
    min_sal, max_sal = int(result["Salary"].min()), int(result["Salary"].max())
    salary_range = st.slider("Salary range", min_sal, max_sal, (min_sal, max_sal), step=100)
with filter_col3:
    name_search = st.text_input("Search player name", "")
with filter_col4:
    show_unmatched = st.checkbox("Include unmatched", value=True)

filtered = result[result["Position"].isin(pos_filter)] if pos_filter else result
filtered = filtered[(filtered["Salary"] >= salary_range[0]) & (filtered["Salary"] <= salary_range[1])]
if name_search:
    filtered = filtered[filtered["Name"].str.contains(name_search, case=False, na=False)]
if not show_unmatched:
    filtered = filtered[filtered["match_quality"] != "none"]

st.divider()

# ---------------------------------------------------------------------------
# Top value plays per position
# ---------------------------------------------------------------------------
st.subheader("💎 Top Value Plays by Position")
value_cols = st.columns(len(POSITIONS))
for col, pos in zip(value_cols, POSITIONS):
    pos_df = filtered[filtered["Position"] == pos].sort_values("projected_value", ascending=False).head(3)
    with col:
        st.markdown(f"**{pos}**")
        if pos_df.empty:
            st.caption("No players in filter")
        for _, row in pos_df.iterrows():
            st.markdown(
                f"- **{row['Name']}** ({row['TeamAbbrev']})  \n"
                f"  ${row['Salary']:,.0f} · {row['projected_value']:.2f} pts/$1k"
            )

st.divider()

# ---------------------------------------------------------------------------
# Full player pool
# ---------------------------------------------------------------------------
st.subheader("Player Pool")

display_df = filtered.rename(
    columns={
        "Name": "Name",
        "Position": "Position",
        "TeamAbbrev": "Team",
        "opponent": "Opponent",
        "Salary": "Salary",
        "projected_points": "Projected Points",
        "projected_value": "Projected Value",
        "matchup_rating": "Defense Matchup Rating",
        "match_quality": "Match",
    }
)[
    ["Name", "Position", "Team", "Opponent", "Salary", "Projected Points",
     "Projected Value", "Defense Matchup Rating", "Match"]
].sort_values("Projected Value", ascending=False)

st.dataframe(
    display_df,
    width='stretch',
    hide_index=True,
    column_config={
        "Salary": st.column_config.NumberColumn("Salary", format="$%d"),
        "Projected Points": st.column_config.NumberColumn("Projected Points", format="%.1f"),
        "Projected Value": st.column_config.ProgressColumn(
            "Value (pts/$1k)", min_value=0, max_value=max(float(display_df["Projected Value"].max() or 1), 1), format="%.2f"
        ),
        "Defense Matchup Rating": st.column_config.NumberColumn("Defense Matchup Rating", format="%.1f / 5"),
    },
)

with st.expander("How projections are calculated"):
    st.markdown(
        "`Projected Points = 45% season average + 35% momentum score + 20% "
        "matchup-adjusted average`, where the matchup adjustment shifts a player's "
        "season average by ±1.5 points per rating point away from a neutral (3/5) matchup. "
        "`Projected Value = Projected Points / (Salary / 1000)`.\n\n"
        "Opponent is parsed from DK's `Game Info` field (e.g. `LAC@KC ...`) as whichever "
        "team isn't the player's own. Players are matched to stats by name + team, falling "
        "back to fuzzy name matching when DK's naming doesn't line up exactly with nflreadpy."
    )
