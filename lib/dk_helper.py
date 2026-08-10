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
# Canonical team-code aliasing and name normalization live in
# lib.player_identity (the single shared identity module) - re-exported here
# so existing imports of these names from lib.dk_helper keep working.
from lib.player_identity import SUFFIXES, TEAM_ALIASES, normalize_name, normalize_team  # noqa: F401

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
    `matched_player_name` (the confirmed match, if any), `best_candidate_name`
    (the closest fuzzy candidate even when it scored below the threshold -
    useful for a human reviewing near-misses), and `stat_*`-prefixed columns
    when matched.
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
        best_candidate_name = None  # best fuzzy candidate even if below threshold, for review

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
                    candidate_idx = next(idx for n, idx in candidates if n == best_name)
                    best_candidate_name = stats.loc[candidate_idx, "player_display_name"]
                    if best_score >= FUZZY_MATCH_THRESHOLD:
                        match_idx = candidate_idx
                        match_method = "fuzzy_team_position"

        record = dk_row.to_dict()
        record["match_method"] = match_method
        record["match_score"] = match_score
        record["matched_player_name"] = None
        record["best_candidate_name"] = best_candidate_name
        if match_idx is not None:
            stat_row = stats.loc[match_idx]
            record["matched_player_name"] = stat_row["player_display_name"]
            for col in STAT_COLUMNS_TO_CARRY:
                if col in stat_row.index:
                    record[f"stat_{col}"] = stat_row[col]
        records.append(record)

    return pd.DataFrame(records)


# Any status other than "ok" means the row must never be ranked/valued -
# it belongs in the "Needs Review" table instead. See compute_projections.
REVIEW_STATUSES = {"review_required", "no_player_average", "no_salary"}


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
    have to cost you matchup context. It's null unless it's independently
    resolvable that way (opponent parses AND that defense/position pair has
    matchup data), never defaulted to a "neutral" number.

    Unmatched/low-confidence rows get NO projection - never a fallback to
    DK's own AvgPointsPerGame. A row that couldn't be confidently matched to
    real stats has no legitimate `player_avg` or `momentum_score` to blend,
    so guessing one (even a labeled one) risks being read as a real number.
    `projection_status` says exactly why a row has no projection:
      - "ok": confident match, valid salary, real player average - the only
        status eligible for value rankings/plays
      - "review_required": no confident player match (unmatched or a fuzzy
        candidate below FUZZY_MATCH_THRESHOLD) - projected_points/value are
        null regardless of salary
      - "no_player_average": matched, but no player average is available
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
            return float("nan")  # not independently resolvable - null, not a neutral guess

    df["matchup_delta"] = df.apply(_delta, axis=1)

    is_matched = df["match_method"] != "unmatched"

    stat_player_avg = df["stat_avg_fantasy_points"] if "stat_avg_fantasy_points" in df.columns else pd.Series(pd.NA, index=df.index)
    stat_momentum = df["stat_momentum_score"] if "stat_momentum_score" in df.columns else pd.Series(pd.NA, index=df.index)

    # No fallback: an unmatched/low-confidence row's player_avg and momentum
    # are null, full stop - never DK's own average standing in for ours.
    player_avg = stat_player_avg.where(is_matched)
    momentum = stat_momentum.where(is_matched)

    salary = pd.to_numeric(df["Salary"], errors="coerce")
    no_salary = salary.isna() | (salary <= 0)
    no_avg = player_avg.isna()

    projected_points = (
        player_avg
        + (momentum - player_avg) * MOMENTUM_ADJUSTMENT_WEIGHT
        + df["matchup_delta"].fillna(0) * MATCHUP_ADJUSTMENT_WEIGHT
    )
    projected_points = projected_points.mask(~is_matched | no_salary | no_avg)

    # Priority, highest last (later .mask calls win): ok < no_salary <
    # no_player_average < review_required. An unmatched row is always
    # "review_required" regardless of whether it also happens to have a
    # salary or an average from some other source.
    status = pd.Series("ok", index=df.index)
    status = status.mask(no_salary, "no_salary")
    status = status.mask(no_avg, "no_player_average")
    status = status.mask(~is_matched, "review_required")

    df["player_avg"] = player_avg.round(2)
    df["momentum_score"] = momentum.round(2)
    df["matchup_delta"] = df["matchup_delta"].round(2)
    df["projected_points"] = projected_points.round(2)
    df["projected_value"] = safe_divide(projected_points, salary / 1000).round(2)
    df["projection_status"] = status

    return df


def needs_review(df: pd.DataFrame) -> pd.DataFrame:
    """Rows that must never be treated as a ranked/valued play: unresolved
    match, missing average, or unusable salary. The single source of truth
    for what belongs in the "Needs Review" table (and, by exclusion, what's
    allowed in value tables/plots and best-value cards)."""
    return df[df["projection_status"] != "ok"]


def best_value_by_position(df: pd.DataFrame, positions=POSITIONS, top_n: int = BEST_VALUE_TOP_N) -> dict:
    """
    Top-N projected-value plays per position. Filters to the position FIRST,
    then ranks within that subset - and excludes anything `needs_review`
    (no salary, no average, or an unresolved/low-confidence match), so a
    "best value" is never a review-required row sorting to the top by
    accident.
    """
    eligible = df[df["projection_status"] == "ok"]
    return {
        pos: eligible[eligible["Position"] == pos].sort_values("projected_value", ascending=False).head(top_n)
        for pos in positions
    }


# ---------------------------------------------------------------------------
# Week 1 / preseason baseline mode
#
# Used only when the active season has no completed regular-season week yet
# (see dfs_data_pipeline.determine_app_mode). Entirely additive - none of the
# in-season functions above are modified or called from here, so in-season
# behavior is unaffected.
# ---------------------------------------------------------------------------

# players_prior_season_baseline columns carried onto a matched DK row,
# prefixed with `stat_`. No momentum/WoW/matchup fields exist on that table
# by design (see build_prior_season_baseline).
PRIOR_SEASON_STAT_COLUMNS_TO_CARRY = [
    "player_id", "player_display_name", "position", "historical_team", "season",
    "games_played", "avg_fantasy_points", "total_touches", "total_targets", "total_carries",
    "yards_per_target", "yards_per_carry", "catch_rate", "points_per_touch",
    "yards_per_touch", "target_share_pct", "air_yards_share_pct", "consistency_score",
]


def match_dk_players_prior_season(dk_df: pd.DataFrame, prior_stats_df: pd.DataFrame) -> pd.DataFrame:
    """
    Match DK salary rows to a PRIOR season's baseline stats. Conservative by
    construction - only two exact-match tiers, no fuzzy matching at all:

      1. normalized name + DK's current team + position, exact - covers
         every player who didn't change teams over the offseason.
      2. normalized name + position only (team ignored, since the player may
         have moved), accepted ONLY when it uniquely identifies exactly one
         player in the prior season's data. Labeled `prior_team_identity_match`.

    If step 2 finds zero candidates (no prior-season history - e.g. a
    rookie) or 2+ candidates (an ambiguous common name at that position),
    the row is left unmatched for manual review rather than guessed -
    `match_method` distinguishes the two cases (`unmatched` vs
    `ambiguous_prior_season_match`) so a reviewer knows why. There is no
    fuzzy-name step here at all, let alone an unrestricted one.

    Adds `match_method`, `match_score` (100.0 for either exact tier, else
    None), `matched_player_name`, `best_candidate_name` (a "; "-joined list
    of the ambiguous candidates' names, when that's the reason for review),
    `historical_team`, `current_team` (DK's own team, canonicalized), and
    `stat_*`-prefixed columns from `PRIOR_SEASON_STAT_COLUMNS_TO_CARRY`.
    """
    stats = prior_stats_df.copy()
    stats["_norm_name"] = stats["player_display_name"].apply(normalize_name)
    stats["_norm_team"] = stats["historical_team"].apply(normalize_team)

    exact_name_team_position = {}
    name_position_groups = {}
    for idx, row in stats.iterrows():
        exact_name_team_position.setdefault((row["_norm_name"], row["_norm_team"], row["position"]), idx)
        name_position_groups.setdefault((row["_norm_name"], row["position"]), []).append(idx)

    records = []
    for _, dk_row in dk_df.iterrows():
        dk_name_norm = normalize_name(dk_row.get("Name"))
        dk_team_norm = normalize_team(dk_row.get("TeamAbbrev"))
        dk_position = dk_row.get("Position")

        match_idx = None
        match_method = "unmatched"
        match_score = None
        best_candidate_name = None

        key_full = (dk_name_norm, dk_team_norm, dk_position)
        if key_full in exact_name_team_position:
            match_idx = exact_name_team_position[key_full]
            match_method = "exact_name_team_position"
            match_score = 100.0
        else:
            candidates = name_position_groups.get((dk_name_norm, dk_position), [])
            if len(candidates) == 1:
                match_idx = candidates[0]
                match_method = "prior_team_identity_match"
                match_score = 100.0
            elif len(candidates) >= 2:
                match_method = "ambiguous_prior_season_match"
                best_candidate_name = "; ".join(
                    sorted(stats.loc[i, "player_display_name"] for i in candidates)
                )
            # len(candidates) == 0 -> no prior-season history at all (e.g. a rookie) - stays "unmatched"

        record = dk_row.to_dict()
        record["match_method"] = match_method
        record["match_score"] = match_score
        record["matched_player_name"] = None
        record["best_candidate_name"] = best_candidate_name
        record["historical_team"] = None
        record["current_team"] = dk_team_norm
        if match_idx is not None:
            stat_row = stats.loc[match_idx]
            record["matched_player_name"] = stat_row["player_display_name"]
            record["historical_team"] = stat_row["historical_team"]
            for col in PRIOR_SEASON_STAT_COLUMNS_TO_CARRY:
                if col in stat_row.index:
                    record[f"stat_{col}"] = stat_row[col]
        records.append(record)

    return pd.DataFrame(records)


def compute_prior_season_projections(matched_df: pd.DataFrame) -> pd.DataFrame:
    """
    Week 1 baseline projection: for a confidently-matched player with prior-
    season history, `projected_points = prior_season_avg_fantasy_points` -
    no momentum or matchup adjustment, because neither exists yet this
    season. `momentum_score` and `matchup_delta` are set to null (not 0.0)
    so it's unambiguous they're unavailable rather than neutral. `opponent`
    is still parsed from the DK row's own `Game Info` for display, even
    though there's no matchup-quality data to look it up against.

    `projection_status` follows the same vocabulary as in-season mode
    (`ok` / `review_required` / `no_player_average` / `no_salary`), reusing
    `needs_review()` and `best_value_by_position()` unchanged.
    """
    df = matched_df.copy()

    df["opponent"] = df.apply(lambda r: parse_opponent(r.get("Game Info"), r.get("TeamAbbrev")), axis=1)

    is_matched = df["match_method"].isin(["exact_name_team_position", "prior_team_identity_match"])

    stat_player_avg = df["stat_avg_fantasy_points"] if "stat_avg_fantasy_points" in df.columns else pd.Series(pd.NA, index=df.index)
    player_avg = stat_player_avg.where(is_matched)

    salary = pd.to_numeric(df["Salary"], errors="coerce")
    no_salary = salary.isna() | (salary <= 0)
    no_avg = player_avg.isna()

    projected_points = player_avg.mask(~is_matched | no_salary | no_avg)

    status = pd.Series("ok", index=df.index)
    status = status.mask(no_salary, "no_salary")
    status = status.mask(no_avg, "no_player_average")
    status = status.mask(~is_matched, "review_required")

    df["player_avg"] = player_avg.round(2)
    df["momentum_score"] = pd.NA  # never available in preseason mode - null, not 0.0
    df["matchup_delta"] = pd.NA   # never available in preseason mode - null, not 0.0
    df["projected_points"] = projected_points.round(2)
    df["projected_value"] = safe_divide(projected_points, salary / 1000).round(2)
    df["projection_status"] = status

    return df
