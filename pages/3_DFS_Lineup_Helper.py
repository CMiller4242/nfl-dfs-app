import io

import pandas as pd
import streamlit as st

from lib.data import (
    POSITIONS,
    data_freshness_caption,
    load_defense_matchups,
    load_metadata,
    load_players_current,
    load_players_prior_season_baseline,
)
from lib.dk_helper import (
    FUZZY_MATCH_THRESHOLD,
    MATCHUP_ADJUSTMENT_WEIGHT,
    MOMENTUM_ADJUSTMENT_WEIGHT,
    best_value_by_position,
    compute_prior_season_projections,
    compute_projections,
    match_dk_players,
    match_dk_players_prior_season,
    needs_review,
    validate_dk_columns,
)

st.set_page_config(page_title="DFS Lineup Helper | NFL DFS", page_icon="💰", layout="wide")

st.title("💰 DFS Lineup Helper")
st.caption(data_freshness_caption())

meta = load_metadata()
app_mode = meta.get("app_mode", "in_season")
active_season = meta.get("active_season", meta.get("season"))
source_season = meta.get("source_season", active_season)
is_preseason = app_mode == "preseason_week_1_baseline"

if is_preseason:
    st.warning(
        "**Week 1 Baseline Mode** — projections use prior-season production. "
        "Current-season momentum and defense-vs-position data are unavailable.",
        icon="⚠️",
    )
    st.caption(
        f"This is a temporary mode, only active because no completed regular-season week exists "
        f"yet for {active_season}. It is not a replacement for the current-season pipeline - once "
        f"Week 1 finishes and the data refreshes, this page automatically switches back to normal "
        f"in-season projections using {active_season} data."
    )

st.markdown(
    "Upload this week's DraftKings salary CSV (Position, Name, Salary, Game Info, "
    "TeamAbbrev, AvgPointsPerGame) to get "
    + ("prior-season-baseline projections and value plays. " if is_preseason else "matchup-adjusted projections and value plays. ")
    + "**A DK CSV you upload here is used only for this session and is never committed to the repo.**"
)

uploaded = st.file_uploader("DraftKings salary CSV", type="csv")

if not uploaded:
    st.info("Waiting for a DraftKings salary CSV upload.")
    st.stop()


@st.cache_data(show_spinner="Matching players and computing projections...")
def process_dk_csv(file_bytes: bytes, mode: str) -> pd.DataFrame:
    dk_df = pd.read_csv(io.BytesIO(file_bytes))
    missing = validate_dk_columns(dk_df)
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")

    if mode == "preseason_week_1_baseline":
        baseline = load_players_prior_season_baseline()
        if baseline.empty:
            raise ValueError(
                "No prior-season baseline data available. Run `python dfs_data_pipeline.py` first."
            )
        matched = match_dk_players_prior_season(dk_df, baseline)
        return compute_prior_season_projections(matched)

    current = load_players_current()
    defense = load_defense_matchups()
    matched = match_dk_players(dk_df, current)
    return compute_projections(matched, defense)


try:
    result = process_dk_csv(uploaded.getvalue(), app_mode)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

result = result.copy()
result["app_mode"] = app_mode
result["active_season"] = active_season
result["source_season"] = source_season
result["data_basis"] = (
    f"{source_season} prior season" if is_preseason else f"{active_season} season to date"
)


def _matchup_bucket(delta):
    if pd.isna(delta):
        return "Unknown"
    if delta >= 0.5:
        return "Favorable"
    if delta <= -0.5:
        return "Tough"
    return "Neutral"


result["matchup_quality"] = result["matchup_delta"].apply(_matchup_bucket)


def _describe_review_reason(row):
    method = row.get("match_method")
    status = row.get("projection_status")
    if method == "ambiguous_prior_season_match":
        return f"Ambiguous: multiple prior-season players share this name + position ({row.get('best_candidate_name')})"
    if method == "unmatched":
        candidate = row.get("best_candidate_name")
        if pd.notna(candidate):
            score = row.get("match_score")
            score_str = f", best fuzzy score {score:.0f}/100" if pd.notna(score) else ""
            return f"No confident match - closest candidate '{candidate}'{score_str}"
        return "No confident match (no name/team/position candidate found - e.g. a rookie or no prior-season history)"
    if status == "no_salary":
        return "Missing or zero salary"
    if status == "no_player_average":
        return "Matched, but no usable average available"
    return status or "Unknown"


# ---------------------------------------------------------------------------
# Match summary
# ---------------------------------------------------------------------------
if is_preseason:
    exact_n = (result["match_method"] == "exact_name_team_position").sum()
    identity_n = (result["match_method"] == "prior_team_identity_match").sum()
    matched_mask = result["match_method"].isin(["exact_name_team_position", "prior_team_identity_match"])
    match_summary = (
        f"{matched_mask.sum()} of {len(result)} players matched to {source_season} history "
        f"({exact_n} exact name+team+position, {identity_n} matched by name+position after a team change)."
    )
else:
    matched_mask = result["match_method"] != "unmatched"
    match_summary = (
        f"{matched_mask.sum()} of {len(result)} players matched with confidence "
        f"({(result['match_method'] == 'exact_name_team_position').sum()} exact name+team+position, "
        f"{(result['match_method'] == 'exact_name_team').sum()} exact name+team, "
        f"{(result['match_method'] == 'fuzzy_team_position').sum()} fuzzy)."
    )

review_needed = needs_review(result)
st.success(
    f"{match_summary} {len(review_needed)} row(s) need review (unresolved/ambiguous match, missing "
    "average, or missing salary) and are excluded from value rankings below."
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
    min_value_filter = st.number_input(
        "Min projected value", min_value=0.0, value=0.0, step=0.1, format="%.1f",
        help="Filters on projected points per $1000 salary. Only applies to rows with a real projection."
    )
with f5:
    matchup_quality = st.multiselect(
        "Matchup quality", ["Favorable", "Neutral", "Tough", "Unknown"],
        default=["Favorable", "Neutral", "Tough", "Unknown"],
        help="Always \"Unknown\" in Week 1 Baseline Mode - there's no current-season matchup data yet."
        if is_preseason else None,
    )

# Position/team/salary scope the whole page (pool + review). Value and
# matchup-quality filters only make sense for rows with a real projection,
# so they're applied after splitting off the review-required rows below.
base_filtered = result[result["Position"].isin(pos_filter)] if pos_filter else result
base_filtered = base_filtered[base_filtered["Salary"] >= min_salary_filter]
if team_filter:
    base_filtered = base_filtered[base_filtered["TeamAbbrev"].isin(team_filter)]

pool_rows = base_filtered[base_filtered["projection_status"] == "ok"]
if min_value_filter > 0:
    pool_rows = pool_rows[pool_rows["projected_value"] >= min_value_filter]
if matchup_quality:
    pool_rows = pool_rows[pool_rows["matchup_quality"].isin(matchup_quality)]

review_rows = needs_review(base_filtered)

st.divider()

# ---------------------------------------------------------------------------
# Top value plays per position
# ---------------------------------------------------------------------------
st.subheader("💎 Top Value Plays by Position")
st.caption(
    "Filtered to each position first, then ranked by projected value. Only rows with a "
    "confident player match, a real average, and a usable salary are eligible - "
    "review-required, unmatched/ambiguous, and $0/missing-salary rows never appear here."
)
best_value = best_value_by_position(pool_rows)
value_cols = st.columns(len(POSITIONS))
for col, pos in zip(value_cols, POSITIONS):
    pos_df = best_value.get(pos, pool_rows.iloc[0:0])
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
# Player pool - only rows with a real, confident projection (projection_status == "ok")
# ---------------------------------------------------------------------------
st.subheader("Player Pool")
st.caption("Only players with a confident match and a computable projection. See \"Needs Review\" below for everything else.")

if is_preseason:
    pool_source = pool_rows.copy()
    pool_source["Prior Season Games"] = pool_source.get("stat_games_played")
    pool_source["Prior Season Consistency"] = pool_source.get("stat_consistency_score")
    pool = pool_source.rename(
        columns={
            "current_team": "Current Team",
            "historical_team": "Historical Team",
            "opponent": "Opponent",
            "player_avg": "Prior Season FPPG",
            "projected_points": "Baseline Projected Points",
            "projected_value": "Projected Value",
            "match_method": "Match Method",
            "data_basis": "Projection Basis",
        }
    )[
        ["Name", "Position", "Current Team", "Historical Team", "Opponent", "Salary",
         "Prior Season FPPG", "Prior Season Games", "Prior Season Consistency",
         "Baseline Projected Points", "Projected Value", "Projection Basis", "Match Method"]
    ].sort_values("Projected Value", ascending=False, na_position="last")
    value_col_label = "Baseline Projected Points"
else:
    pool = pool_rows.rename(
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
            "matchup_quality": "Matchup Quality",
            "data_basis": "Projection Basis",
        }
    )[
        ["Name", "Position", "Team", "Opponent", "Salary", "Season Avg", "Momentum", "Matchup Delta",
         "Projected Points", "Projected Value", "Matchup Quality", "Projection Basis", "Match Method", "Match Score"]
    ].sort_values("Projected Value", ascending=False, na_position="last")
    value_col_label = "Projected Points"

if pool.empty:
    st.info("No players with a confident, computable projection in the current filter.")
else:
    st.dataframe(
        pool,
        width="stretch",
        hide_index=True,
        column_config={
            "Salary": st.column_config.NumberColumn("Salary", format="$%d"),
            value_col_label: st.column_config.NumberColumn(value_col_label, format="%.1f"),
            "Projected Value": st.column_config.ProgressColumn(
                "Value (pts/$1k)", min_value=0,
                max_value=max(float(pool["Projected Value"].max() or 1), 1), format="%.2f"
            ),
        },
    )
    st.download_button(
        "Download Player Pool as CSV",
        pool.to_csv(index=False).encode("utf-8"),
        file_name="dk_player_pool.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Needs Review: everything projection_status != "ok" - unmatched, ambiguous,
# low-confidence, missing average, or missing/zero salary. Never ranked,
# never in value plays.
# ---------------------------------------------------------------------------
st.subheader("⚠️ Needs Review")
if is_preseason:
    st.caption(
        "No projections are computed for these rows. A row lands here if the player has no prior-season "
        "history (e.g. a rookie), their name + position matches multiple prior-season players ambiguously, "
        "or they have a missing/zero salary."
    )
else:
    st.caption(
        f"No fallback projections are ever computed for these rows. A row lands here if the player "
        f"couldn't be confidently matched (fuzzy candidates below {FUZZY_MATCH_THRESHOLD}/100 are never "
        "auto-accepted), has no usable season average, or has a missing/zero salary."
    )

review_display = review_rows.copy()
review_display["Matched / Best Candidate"] = review_display["matched_player_name"].fillna(
    review_display["best_candidate_name"]
)
review_display["Reason"] = review_display.apply(_describe_review_reason, axis=1)

if is_preseason:
    review_display = review_display.rename(columns={
        "Name": "DK Name",
        "current_team": "Current Team",
        "historical_team": "Historical Team",
        "opponent": "Opponent",
    })[
        ["DK Name", "Position", "Current Team", "Historical Team", "Opponent", "Salary",
         "match_method", "match_score", "Matched / Best Candidate", "projection_status", "Reason"]
    ]
else:
    review_display = review_display.rename(columns={
        "Name": "DK Name",
        "TeamAbbrev": "Team",
        "opponent": "Opponent",
    })[
        ["DK Name", "Position", "Team", "Opponent", "Salary", "match_method", "match_score",
         "Matched / Best Candidate", "projection_status", "Reason"]
    ]

if review_display.empty:
    st.success("No players need review in the current filter.")
else:
    st.dataframe(review_display, width="stretch", hide_index=True)
    st.download_button(
        "Download Needs Review as CSV",
        review_display.to_csv(index=False).encode("utf-8"),
        file_name="dk_needs_review.csv",
        mime="text/csv",
    )

st.divider()

with st.expander("How projections are calculated", expanded=is_preseason):
    if is_preseason:
        st.markdown(
            f"""
**Week 1 Baseline Mode** - no current-season games have finished yet for {active_season}, so
projections use `{source_season}` (prior season) regular-season production instead:

`Baseline Projected Points = prior_season_avg_fantasy_points` (no momentum or matchup
adjustment - neither exists yet this season)

`Projected Value = Baseline Projected Points / (Salary / 1000)`

**What this does NOT model:** player role changes, new team/scheme fit, coaching staff
changes, injuries, suspensions, depth chart battles, rookies, and any other offseason
change. A prior-season average is a *starting point*, not a full projection - treat it
with real caution, especially for players who changed teams or situations.

**Player matching** tries, in order: (1) normalized name + current DK team + position,
exact match; (2) if that fails (e.g. the player changed teams), normalized name + position
only, matched against {source_season} history - accepted **only** when it uniquely
identifies exactly one player. Two or more same-name/position players from
{source_season} is left `ambiguous_prior_season_match`; a name with no {source_season}
history at all (rookies) is left `unmatched`. There is no fuzzy name matching in this mode.

"Historical Team" (from {source_season}) and "Current Team" (from this week's DK file) are
both shown so a team change is never hidden by a single ambiguous "Team" column.
            """
        )
    else:
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
  and the opponent parsed from `Game Info`, independent of player matching. It's left
  blank/null unless that lookup actually resolves (a real opponent + matchup history for
  that position) - never defaulted to a "neutral" guess.

**Player matching** tries, in order: (1) normalized name + team + position exact match,
(2) normalized name + team exact match, (3) a fuzzy name match restricted to the same
team and position, accepted only above a score of {FUZZY_MATCH_THRESHOLD}/100. There is no
unrestricted, name-only fuzzy fallback.

**No fallback projections.** A row that doesn't clear one of those match bars gets
`projected_points = null` and `projected_value = null` - it is never assigned DK's own
AvgPointsPerGame or any other stand-in average. Only `projection_status == "ok"` rows
(confident match + real average + usable salary) ever appear in the Player Pool, value
plots, or Top Value Plays cards; everything else lands in "Needs Review" above.
            """
        )
