"""
Canonical player identity, canonical team mapping, and the safe
identity-crosswalk/matching primitives shared by the whole app.

This is the ONE place team-code aliasing and name normalization live -
`lib/dk_helper.py` imports `normalize_team`/`normalize_name` from here rather
than defining its own copies, so DK matching, role/eligibility matching, and
any future consumer all agree on what "the same player" and "the same team"
mean.

## Identifiers available from each source (documented, not assumed)

- **DK salary CSV**: no stable cross-source player ID at all. Just `Name`
  (display string), `TeamAbbrev` (DK's own team code, which sometimes differs
  from nflreadpy's - see `TEAM_ALIASES`), and `Position`. DK exports
  sometimes include an `ID`/`Name + ID` column, but that ID is DK-internal
  (stable only across DK's own exports) and has no known mapping to
  nflreadpy or ESPN IDs, so it is not usable as a crosswalk key here.
- **nflreadpy/nflverse player data**: `player_id` (a stable "gsis" ID, format
  like `00-0033873`) is the backbone ID used everywhere else in this app
  (`players_current.parquet`, `players_weekly.parquet`, etc).
- **NFL depth-chart data**: `nflreadpy.load_depth_charts()` publishes a
  REAL, live, roughly-daily-updated depth chart for all 32 teams, and -
  critically - each row carries BOTH `gsis_id` (nflreadpy's own ID space)
  AND `espn_id` (ESPN's athlete ID space) together. This is a verified,
  source-provided crosswalk between nflreadpy and ESPN identity, not a name
  guess. (The previous `nfl_depth_chart.py` in this repo is a hardcoded,
  static Python list frozen at some past roster snapshot - not a live
  source - and is no longer used by the pipeline; see README.)
- **ESPN roster/injury responses**: each athlete entry carries ESPN's own
  numeric athlete `id`, `displayName`, a `position.abbreviation`, and the
  team context from the roster endpoint's team `id`/`abbreviation`. ESPN's
  ID space is the same one captured as `espn_id` in nflreadpy's depth
  charts, which is what makes the crosswalk above real rather than assumed.

Because DK's salary CSV carries no stable ID, joining a DK row to the
identity crosswalk (or to role/injury context) still has to go through
normalized name + team + position - see `match_dk_row_to_role_context`
below, which is deliberately stricter (zero fuzzy tolerance) than the stats
matcher in `lib/dk_helper.py`, because a wrong match here doesn't just cost
a bad stat lookup - it can put a real injury or promotion label onto the
wrong player.
"""

import pandas as pd

from lib.role_config import FANTASY_POSITIONS

# DraftKings sometimes uses different team codes than nflreadpy. This is the
# single shared mapping: DK's code -> the nflreadpy standard code. Used for
# every team-code comparison in the app (DK matching, role/eligibility
# matching, projections).
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

# nflreadpy depth-chart `pos_abb` values that map onto our four fantasy
# position groups. Only exact matches are mapped; every non-offensive-skill
# position (OL/DL/LB/DB/ST/etc) is intentionally excluded, never guessed.
FANTASY_POSITION_GROUPS = {pos: pos for pos in FANTASY_POSITIONS}

IDENTITY_CROSSWALK_COLUMNS = [
    "player_id", "espn_id", "player_display_name", "canonical_team",
    "position_group", "source_position", "depth_rank", "depth_chart_source_timestamp",
]


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


def build_identity_crosswalk(raw_depth_chart_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the canonical player identity crosswalk from nflreadpy's raw
    `load_depth_charts()` output, restricted to the single latest snapshot
    (`dt`) and the four fantasy position groups.

    One row per (gsis player_id, position_group) - a player only has one
    depth-chart entry per position group per snapshot. Uses the source's own
    `gsis_id`/`espn_id` pairing directly; does NOT attempt any name-based
    reconciliation between nflreadpy and ESPN identity, because none is
    needed here - the source already ties them together.

    Rows with a missing `gsis_id` (no reliable canonical identity) are
    dropped rather than kept with a null key, since every downstream join
    in this app keys on `player_id`.
    """
    if raw_depth_chart_df.empty:
        return pd.DataFrame(columns=IDENTITY_CROSSWALK_COLUMNS)

    df = raw_depth_chart_df.copy()
    latest_dt = df["dt"].max()
    df = df[df["dt"] == latest_dt]
    df = df[df["pos_abb"].isin(FANTASY_POSITION_GROUPS.keys())]
    df = df[df["gsis_id"].notna()]
    if df.empty:
        return pd.DataFrame(columns=IDENTITY_CROSSWALK_COLUMNS)

    df["canonical_team"] = df["team"].apply(normalize_team)
    df["position_group"] = df["pos_abb"].map(FANTASY_POSITION_GROUPS)

    out = df.rename(columns={
        "gsis_id": "player_id",
        "player_name": "player_display_name",
        "pos_abb": "source_position",
        "pos_rank": "depth_rank",
        "dt": "depth_chart_source_timestamp",
    })

    # A player can appear more than once in a raw snapshot in rare cases
    # (e.g. a mid-week depth chart correction re-listed); keep one row per
    # (player_id, position_group), the most recently-listed.
    out = out.sort_values(["player_id", "position_group"]).drop_duplicates(
        subset=["player_id", "position_group"], keep="last"
    )

    return out[IDENTITY_CROSSWALK_COLUMNS].reset_index(drop=True)


def match_dk_row_to_role_context(name, team_abbrev, position, role_context_df: pd.DataFrame):
    """
    Resolve a single DK salary row (which has no stable cross-source ID) to
    exactly one row of `player_role_context`, using ONLY an exact match on
    (normalized name, canonical team, position_group). There is no fuzzy
    fallback here at all - this join feeds injury/role context, and a wrong
    match is worse than an honest "unresolved."

    Returns the matching `player_role_context` row as a dict, or None if
    there is no exact match (zero or multiple candidates both count as "no
    match" - an ambiguous role-context row is never guessed at either).
    """
    if role_context_df.empty:
        return None

    norm_name = normalize_name(name)
    norm_team = normalize_team(team_abbrev)

    candidates = role_context_df[
        (role_context_df["_norm_name"] == norm_name)
        & (role_context_df["canonical_team"] == norm_team)
        & (role_context_df["position_group"] == position)
    ]
    if len(candidates) != 1:
        return None
    return candidates.iloc[0].to_dict()
