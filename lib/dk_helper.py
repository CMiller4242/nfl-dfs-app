"""
Helpers for the DFS Lineup Helper page: parsing a DraftKings salary CSV,
matching DK player rows against nflreadpy stats conservatively (never
guessing across positions or unrestricted name-only fuzzy matches), and
computing transparent, auditable projections.
"""

import re

import pandas as pd
from rapidfuzz import fuzz, process

from dfs_data_pipeline import POSITIONS, safe_divide

# DraftKings sometimes uses different team codes than nflreadpy. This is the
# single shared mapping: DK's code -> the nflreadpy standard code.
TEAM_ALIASES = {
    "JAC": "JAX",
    "LAR": "LA",
    "LVR": "LV",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
    "WSH": "WAS",
}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

REQUIRED_DK_COLUMNS = ["Position", "Name", "Salary", "Game Info", "TeamAbbrev"]

# Player-current columns carried onto a matched DK row, prefixed with `stat_`.
STAT_COLUMNS_TO_CARRY = [
    "player_id", "player_display_name", "team", "last_opponent", "position",
    "avg_fantasy_points", "momentum_score", "momentum_games_used", "games_played",
    "total_touches", "touches_wow_change", "opportunity_trend",
    "points_per_touch", "yards_per_target", "yards_per_carry", "catch_rate",
    "consistency_score",
]

# Below this rapidfuzz token_sort_ratio score (0-100), a fuzzy candidate is
# NOT accepted as a match - it's surfaced for manual review instead.
FUZZY_MATCH_THRESHOLD = 88

# Projection blend weights - named constants, tunable in one place.
# projected_points = player_avg
#                     + (momentum_score - player_avg) * MOMENTUM_ADJUSTMENT_WEIGHT
#                     + matchup_delta * MATCHUP_ADJUSTMENT_WEIGHT
MOMENTUM_ADJUSTMENT_WEIGHT = 0.20
MATCHUP_ADJUSTMENT_WEIGHT = 0.30

BEST_VALUE_TOP_N = 3


def normalize_team(abbrev) -> str:
    if not isinstance(abbrev, str):
        return ""
    code = abbrev.strip().upper()
    return TEAM_ALIASES.get(code, code)


def normalize_name(name) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip().replace(".", "").replace("'", "").replace("-", " ")
    tokens = [t for t in name.split() if t not in SUFFIXES]
    return " ".join(tokens)


def validate_dk_columns(df: pd.DataFrame) -> list:
    """Return a list of required columns that are missing, empty if the file looks valid."""
    return [c for c in REQUIRED_DK_COLUMNS if c not in df.columns]


def parse_opponent(game_info, team_abbrev) -> str:
    """
    Parse DK's "Game Info" field, e.g. "LAC@KC 10/19/2025 04:05PM ET", and
    return the opponent for team_abbrev (whichever of the two teams isn't
    the player's own team). Returns "" for missing/malformed/postponed
    values rather than raising, since a real salary export can contain any
    of those.
    """
    if not isinstance(game_info, str):
        return ""
    match = re.match(r"\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})", game_info.strip())
    if not match:
        return ""
    away, home = normalize_team(match.group(1)), normalize_team(match.group(2))
    team = normalize_team(team_abbrev)
    if team == home:
        return away
    if team == away:
        return home
    return ""


def match_dk_players(dk_df: pd.DataFrame, stats_df: pd.DataFrame) -> pd.DataFrame:
    """
    Match DK salary rows to the current-season player snapshot using a
    strict, in-order sequence:

      1. normalized name + canonical team + position, exact
      2. normalized name + canonical team, exact (position ignored)
      3. fuzzy name match, restricted to candidates sharing the DK row's
         canonical team AND position, accepted only if the best score is
         >= FUZZY_MATCH_THRESHOLD

    There is no unrestricted, name-only fuzzy fallback - a low-confidence
    or team/position mismatch is left `unmatched` for manual review rather
    than silently guessed. Adds `match_method`, `match_score`,
    `matched_player_name`, and `stat_*`-prefixed columns when matched.
    """
    stats = stats_df.copy()
    stats["_norm_name"] = stats["player_display_name"].apply(normalize_name)
    stats["_norm_team"] = stats["team"].apply(normalize_team)

    exact_name_team_position = {}
    exact_name_team = {}
    by_team_position = {}
    for idx, row in stats.iterrows():
        exact_name_team_position.setdefault((row["_norm_name"], row["_norm_team"], row["position"]), idx)
        exact_name_team.setdefault((row["_norm_name"], row["_norm_team"]), idx)
        by_team_position.setdefault((row["_norm_team"], row["position"]), []).append((row["_norm_name"], idx))

    records = []
    for _, dk_row in dk_df.iterrows():
        dk_name_norm = normalize_name(dk_row.get("Name"))
        dk_team_norm = normalize_team(dk_row.get("TeamAbbrev"))
        dk_position = dk_row.get("Position")

        match_idx = None
        match_method = "unmatched"
        match_score = None

        key_full = (dk_name_norm, dk_team_norm, dk_position)
        key_name_team = (dk_name_norm, dk_team_norm)

        if key_full in exact_name_team_position:
            match_idx = exact_name_team_position[key_full]
            match_method = "exact_name_team_position"
            match_score = 100.0
        elif key_name_team in exact_name_team:
            match_idx = exact_name_team[key_name_team]
            match_method = "exact_name_team"
            match_score = 100.0
        else:
            candidates = by_team_position.get((dk_team_norm, dk_position), [])
            if candidates:
                names = [c[0] for c in candidates]
                result = process.extractOne(dk_name_norm, names, scorer=fuzz.token_sort_ratio)
                if result:
                    best_name, best_score, _ = result
                    match_score = float(best_score)
                    if best_score >= FUZZY_MATCH_THRESHOLD:
                        match_idx = next(idx for n, idx in candidates if n == best_name)
                        match_method = "fuzzy_team_position"

        record = dk_row.to_dict()
        record["match_method"] = match_method
        record["match_score"] = match_score
        record["matched_player_name"] = None
        if match_idx is not None:
            stat_row = stats.loc[match_idx]
            record["matched_player_name"] = stat_row["player_display_name"]
            for col in STAT_COLUMNS_TO_CARRY:
                if col in stat_row.index:
                    record[f"stat_{col}"] = stat_row[col]
        records.append(record)

    return pd.DataFrame(records)


def compute_projections(matched_df: pd.DataFrame, defense_matchups: pd.DataFrame) -> pd.DataFrame:
    """
    Adds `opponent`, `matchup_delta`, `player_avg`, `momentum_score`,
    `projected_points`, `projected_value`, and `projection_status` to a
    matched DK dataframe.

    projected_points = player_avg
                        + (momentum_score - player_avg) * MOMENTUM_ADJUSTMENT_WEIGHT
                        + matchup_delta * MATCHUP_ADJUSTMENT_WEIGHT
    projected_value  = projected_points / (Salary / 1000)

    `matchup_delta` is looked up from DK's own Position + parsed opponent,
    independent of whether the player matched - a bad player match doesn't
    have to cost you matchup context. `projection_status` clearly labels
    rows that used a fallback or couldn't be projected at all:
      - "ok": confident match, valid salary, real player average
      - "unmatched_fallback": no confident player match; DK's own
        AvgPointsPerGame is used as player_avg (so the momentum term is 0)
      - "no_player_average": no player average available from any source
      - "no_salary": missing/zero/negative salary - no projection is made
    """
    df = matched_df.copy()

    df["opponent"] = df.apply(lambda r: parse_opponent(r.get("Game Info"), r.get("TeamAbbrev")), axis=1)

    delta_lookup = defense_matchups.set_index(["defense_team", "position"])["matchup_delta"] \
        if not defense_matchups.empty else pd.Series(dtype=float)

    def _delta(row):
        try:
            return float(delta_lookup.loc[(row["opponent"], row["Position"])])
        except KeyError:
            return 0.0  # no matchup data available - neutral, not a guess

    df["matchup_delta"] = df.apply(_delta, axis=1)

    is_matched = df["match_method"] != "unmatched"
    dk_avg = pd.to_numeric(df.get("AvgPointsPerGame"), errors="coerce")

    stat_player_avg = df["stat_avg_fantasy_points"] if "stat_avg_fantasy_points" in df.columns else pd.Series(pd.NA, index=df.index)
    stat_momentum = df["stat_momentum_score"] if "stat_momentum_score" in df.columns else pd.Series(pd.NA, index=df.index)

    # Unmatched/low-confidence rows fall back to DK's own average for both
    # inputs (momentum term becomes 0) - clearly labeled via projection_status,
    # never presented as an equally confident projection.
    player_avg = stat_player_avg.where(is_matched, dk_avg)
    momentum = stat_momentum.where(is_matched, dk_avg)

    salary = pd.to_numeric(df["Salary"], errors="coerce")
    no_salary = salary.isna() | (salary <= 0)
    no_avg = player_avg.isna()

    projected_points = (
        player_avg
        + (momentum - player_avg) * MOMENTUM_ADJUSTMENT_WEIGHT
        + df["matchup_delta"] * MATCHUP_ADJUSTMENT_WEIGHT
    )
    projected_points = projected_points.mask(no_salary | no_avg)

    status = pd.Series("ok", index=df.index)
    status = status.mask(~is_matched, "unmatched_fallback")
    status = status.mask(no_avg, "no_player_average")
    status = status.mask(no_salary, "no_salary")  # missing salary takes priority for display

    df["player_avg"] = player_avg.round(2)
    df["momentum_score"] = momentum.round(2)
    df["matchup_delta"] = df["matchup_delta"].round(2)
    df["projected_points"] = projected_points.round(2)
    df["projected_value"] = safe_divide(projected_points, salary / 1000).round(2)
    df["projection_status"] = status

    return df


def best_value_by_position(df: pd.DataFrame, positions=POSITIONS, top_n: int = BEST_VALUE_TOP_N) -> dict:
    """
    Top-N projected-value plays per position. Filters to the position FIRST,
    then ranks within that subset - and excludes rows with no salary or an
    unresolved player match, so a "best value" is never a labeled fallback
    or a $0-salary row sorting to the top by accident.
    """
    eligible = df[(df["projection_status"] == "ok") & df["projected_value"].notna()]
    return {
        pos: eligible[eligible["Position"] == pos].sort_values("projected_value", ascending=False).head(top_n)
        for pos in positions
    }
