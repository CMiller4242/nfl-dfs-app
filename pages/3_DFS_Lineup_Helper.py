import io

import pandas as pd
import streamlit as st

from lib.data import POSITIONS, data_freshness_caption, load_defense_matchups, load_players_current
from lib.dk_helper import (
    FUZZY_MATCH_THRESHOLD,
    MATCHUP_ADJUSTMENT_WEIGHT,
    MOMENTUM_ADJUSTMENT_WEIGHT,
    best_value_by_position,
    compute_projections,
    match_dk_players,
    validate_dk_columns,
)

st.set_page_config(page_title="DFS Lineup Helper | NFL DFS", page_icon="💰", layout="wide")

st.title("💰 DFS Lineup Helper")
st.caption(data_freshness_caption())

st.markdown(
    "Upload this week's DraftKings salary CSV (Position, Name, Salary, Game Info, "
    "TeamAbbrev, AvgPointsPerGame) to get matchup-adjusted projections and value plays. "
    "**A DK CSV you upload here is used only for this session and is never committed to the repo.**"
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

confident = result[result["match_method"] != "unmatched"]
review = result[result["match_method"] == "unmatched"]
st.success(
    f"{len(confident)} of {len(result)} players matched with confidence "
    f"({(result['match_method'] == 'exact_name_team_position').sum()} exact name+team+position, "
    f"{(result['match_method'] == 'exact_name_team').sum()} exact name+team, "
    f"{(result['match_method'] == 'fuzzy_team_position').sum()} fuzzy). "
    f"{len(review)} need manual review."
)

st.divider()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
f1, f2, f3, f4, f5 = st.columns([1.1, 1.5, 1.3, 1.1, 1.3])
with f1:
    pos_options = sorted([p for p in result["Position"].unique() if p in POSITIONS])
    pos_filter = st.multiselect("Position", pos_options, default=pos_options)
with f2:
    min_salary, max_salary = int(result["Salary"].min()), int(result["Salary"].max())
    min_salary_filter = st.number_input("Min salary", min_value=0, max_value=max_salary, value=min_salary, step=500)
with f3:
    team_options = sorted(result["TeamAbbrev"].dropna().unique())
    team_filter = st.multiselect("Team", team_options, default=[])
with f4:
    valid_values = result["projected_value"].dropna()
    min_value_filter = st.number_input(
        "Min projected value", min_value=0.0, value=0.0, step=0.1, format="%.1f",
        help="Filters on projected points per $1000 salary."
    )
with f5:
    matchup_quality = st.multiselect(
        "Matchup quality", ["Favorable", "Neutral", "Tough"], default=["Favorable", "Neutral", "Tough"]
    )


def _matchup_bucket(delta):
    if pd.isna(delta):
        return "Neutral"
    if delta >= 0.5:
        return "Favorable"
    if delta <= -0.5:
        return "Tough"
    return "Neutral"


result["matchup_quality"] = result["matchup_delta"].apply(_matchup_bucket)

filtered = result[result["Position"].isin(pos_filter)] if pos_filter else result
filtered = filtered[filtered["Salary"] >= min_salary_filter]
if team_filter:
    filtered = filtered[filtered["TeamAbbrev"].isin(team_filter)]
if min_value_filter > 0:
    filtered = filtered[filtered["projected_value"].fillna(-1) >= min_value_filter]
filtered = filtered[filtered["matchup_quality"].isin(matchup_quality)] if matchup_quality else filtered

st.divider()

# ---------------------------------------------------------------------------
# Top value plays per position
# ---------------------------------------------------------------------------
st.subheader("💎 Top Value Plays by Position")
st.caption(
    "Filtered to each position first, then ranked by projected value. Excludes players with "
    "no/zero salary or an unresolved match, so a labeled fallback can never show up as a value play."
)
best_value = best_value_by_position(filtered)
value_cols = st.columns(len(POSITIONS))
for col, pos in zip(value_cols, POSITIONS):
    pos_df = best_value.get(pos, filtered.iloc[0:0])
    with col:
        st.markdown(f"**{pos}**")
        if pos_df.empty:
            st.caption("No qualifying players")
        for _, row in pos_df.iterrows():
            st.markdown(
                f"- **{row['Name']}** ({row['TeamAbbrev']})  \n"
                f"  ${row['Salary']:,.0f} · {row['projected_value']:.2f} pts/$1k"
            )

st.divider()

# ---------------------------------------------------------------------------
# Full player pool (confident matches only, review table is separate below)
# ---------------------------------------------------------------------------
st.subheader("Player Pool")

pool = filtered.rename(
    columns={
        "TeamAbbrev": "Team",
        "opponent": "Opponent",
        "player_avg": "Season Avg",
        "momentum_score": "Momentum",
        "matchup_delta": "Matchup Delta",
        "projected_points": "Projected Points",
        "projected_value": "Projected Value",
        "match_method": "Match Method",
        "match_score": "Match Score",
        "projection_status": "Status",
    }
)[
    ["Name", "Position", "Team", "Opponent", "Salary", "Season Avg", "Momentum", "Matchup Delta",
     "Projected Points", "Projected Value", "matchup_quality", "Match Method", "Status"]
].rename(columns={"matchup_quality": "Matchup Quality"}).sort_values("Projected Value", ascending=False, na_position="last")

st.dataframe(
    pool,
    width="stretch",
    hide_index=True,
    column_config={
        "Salary": st.column_config.NumberColumn("Salary", format="$%d"),
        "Season Avg": st.column_config.NumberColumn("Season Avg", format="%.1f"),
        "Momentum": st.column_config.NumberColumn("Momentum", format="%.1f"),
        "Matchup Delta": st.column_config.NumberColumn("Matchup Delta", format="%.1f"),
        "Projected Points": st.column_config.NumberColumn("Projected Points", format="%.1f"),
        "Projected Value": st.column_config.ProgressColumn(
            "Value (pts/$1k)", min_value=0,
            max_value=max(float(pool["Projected Value"].max() or 1), 1), format="%.2f"
        ),
    },
)

st.divider()

# ---------------------------------------------------------------------------
# Unmatched / low-confidence review table
# ---------------------------------------------------------------------------
st.subheader("⚠️ Needs Review: Unmatched or Low-Confidence Players")
st.caption(
    f"Fuzzy matches below a score of {FUZZY_MATCH_THRESHOLD} (out of 100) are never auto-accepted. "
    "These rows either found no name+team candidate at all, or the best fuzzy candidate within the "
    "same team and position scored too low to trust. Their projections (if any) use DK's own "
    "AvgPointsPerGame as a clearly-labeled fallback."
)

review_display = filtered[filtered["match_method"] == "unmatched"][
    ["Name", "Position", "TeamAbbrev", "Salary", "Game Info", "AvgPointsPerGame", "match_score", "projection_status"]
].rename(columns={
    "TeamAbbrev": "Team",
    "match_score": "Best Fuzzy Score",
    "projection_status": "Status",
})

if review_display.empty:
    st.success("No unmatched or low-confidence players in the current filter.")
else:
    st.dataframe(review_display, width="stretch", hide_index=True)
    st.download_button(
        "Download review list as CSV",
        review_display.to_csv(index=False).encode("utf-8"),
        file_name="dk_unmatched_review.csv",
        mime="text/csv",
    )

st.divider()

with st.expander("How projections are calculated"):
    st.markdown(
        f"""
`projected_points = player_avg + (momentum_score - player_avg) * {MOMENTUM_ADJUSTMENT_WEIGHT}
+ matchup_delta * {MATCHUP_ADJUSTMENT_WEIGHT}`

`projected_value = projected_points / (Salary / 1000)`

- **player_avg**: season-to-date average fantasy points (PPR) from completed games.
- **momentum_score**: weighted average of the player's most recent played games
  (50% / 30% / 20%, most recent first; renormalized if fewer than 3 games are available).
- **matchup_delta**: the upcoming opponent's average fantasy points allowed to this
  position, minus the position's league average - looked up from DK's own Position
  and the opponent parsed from `Game Info`, independent of player matching.

**Player matching** tries, in order: (1) normalized name + team + position exact match,
(2) normalized name + team exact match, (3) a fuzzy name match restricted to the same
team and position, accepted only above a score of {FUZZY_MATCH_THRESHOLD}/100. There is no
unrestricted, name-only fuzzy fallback. Anything that doesn't clear one of those bars
lands in the review table above and its projection (if shown) falls back to DK's own
AvgPointsPerGame - never silently presented as a confident number.
        """
    )
