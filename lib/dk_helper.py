"""
Helpers for the DFS Lineup Helper page: parsing a DraftKings salary CSV,
matching DK player names/teams against nflreadpy stats, and blending
projections from season average + momentum + matchup quality.
"""

import re

import pandas as pd
from rapidfuzz import fuzz, process

# DraftKings sometimes uses different team codes than nflreadpy. Map DK's
# code -> the nflreadpy standard code so joins line up.
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

STAT_COLUMNS_TO_CARRY = [
    "player_id", "player_display_name", "team", "opponent_team", "position", "week",
    "fantasy_points_ppr", "fp_season_avg", "momentum_score",
    "fantasy_points_ppr_3wk_avg", "touches", "touches_wow_change",
    "points_per_touch", "yards_per_target", "yards_per_carry", "catch_rate",
]

# Projection blend weights: season average, momentum, matchup-adjusted average.
PROJECTION_WEIGHTS = {"season_avg": 0.45, "momentum": 0.35, "matchup": 0.20}
MATCHUP_POINTS_PER_RATING = 1.5  # points added/subtracted per rating point away from neutral (3)


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
    the player's own team). Returns "" if it can't be determined.
    """
    if not isinstance(game_info, str):
        return ""
    match = re.match(r"\s*([A-Za-z]{2,4})@([A-Za-z]{2,4})", game_info.strip())
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
    Join DK salary rows against the current player-stats snapshot on
    normalized name + team, falling back to fuzzy name matching within the
    same team, then fuzzy matching by name alone (covers trades/relocations).
    Adds a `stat_*`-prefixed column per stat plus `match_quality`.
    """
    stats = stats_df.copy()
    stats["_norm_name"] = stats["player_display_name"].apply(normalize_name)
    stats["_norm_team"] = stats["team"].apply(normalize_team)

    exact_lookup = {}
    by_team = {}
    for idx, row in stats.iterrows():
        key = (row["_norm_name"], row["_norm_team"])
        exact_lookup.setdefault(key, idx)
        by_team.setdefault(row["_norm_team"], []).append((row["_norm_name"], idx))

    all_names = list(zip(stats["_norm_name"], stats.index))

    records = []
    for _, dk_row in dk_df.iterrows():
        dk_name_norm = normalize_name(dk_row["Name"])
        dk_team_norm = normalize_team(dk_row["TeamAbbrev"])

        match_idx, quality = exact_lookup.get((dk_name_norm, dk_team_norm)), "exact"
        if match_idx is None:
            quality = "none"
            candidates = by_team.get(dk_team_norm, [])
            if candidates:
                names = [c[0] for c in candidates]
                result = process.extractOne(dk_name_norm, names, scorer=fuzz.token_sort_ratio)
                if result and result[1] >= 85:
                    match_idx = next(idx for n, idx in candidates if n == result[0])
                    quality = "fuzzy"
            if match_idx is None and all_names:
                names_all = [n for n, _ in all_names]
                result = process.extractOne(dk_name_norm, names_all, scorer=fuzz.token_sort_ratio)
                if result and result[1] >= 92:
                    match_idx = next(idx for n, idx in all_names if n == result[0])
                    quality = "name_only"

        record = dk_row.to_dict()
        record["match_quality"] = quality
        if match_idx is not None:
            stat_row = stats.loc[match_idx]
            for col in STAT_COLUMNS_TO_CARRY:
                if col in stat_row.index:
                    record[f"stat_{col}"] = stat_row[col]
        records.append(record)

    return pd.DataFrame(records)


def compute_projections(df: pd.DataFrame, defense_matchups: pd.DataFrame) -> pd.DataFrame:
    """
    Adds `opponent`, `matchup_rating`, `projected_points`, and `projected_value`
    (projected points per $1000 salary) to a matched DK dataframe.
    """
    df = df.copy()
    df["opponent"] = df.apply(
        lambda r: parse_opponent(r.get("Game Info"), r.get("TeamAbbrev")), axis=1
    )

    rating_lookup = defense_matchups.set_index(["defense_team", "position"])["matchup_rating"]

    def _rating(row):
        try:
            return float(rating_lookup.loc[(row["opponent"], row["Position"])])
        except KeyError:
            return 3.0  # neutral when we don't have a matchup read

    df["matchup_rating"] = df.apply(_rating, axis=1)

    dk_avg = pd.to_numeric(df.get("AvgPointsPerGame"), errors="coerce")
    season_avg = df.get("stat_fp_season_avg")
    momentum = df.get("stat_momentum_score")
    season_avg = (season_avg if season_avg is not None else pd.Series(index=df.index, dtype=float))
    momentum = (momentum if momentum is not None else pd.Series(index=df.index, dtype=float))

    # Fall back to DK's own season average when a player has no nflreadpy match.
    season_avg = season_avg.fillna(dk_avg).fillna(0)
    momentum = momentum.fillna(dk_avg).fillna(0)

    matchup_adjusted = season_avg + (df["matchup_rating"] - 3) * MATCHUP_POINTS_PER_RATING

    df["projected_points"] = (
        PROJECTION_WEIGHTS["season_avg"] * season_avg
        + PROJECTION_WEIGHTS["momentum"] * momentum
        + PROJECTION_WEIGHTS["matchup"] * matchup_adjusted
    ).round(2)

    salary = pd.to_numeric(df["Salary"], errors="coerce")
    df["projected_value"] = (df["projected_points"] / (salary / 1000)).round(2)

    return df
