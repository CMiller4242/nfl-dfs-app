"""
ESPN roster/injury ingestion - refactored from the old root-level
`nfl_injury_tracker.py` script into reusable, pipeline-callable logic with
production-quality HTTP behavior.

ESPN's injury data is treated as an external, imperfect source: it is not
assumed to be real-time, permanent, or an official game-day inactive list
(that's a separate, later-arriving designation ESPN's roster endpoint does
not provide). Every requirement below exists specifically so a failure or a
stale response degrades visibly rather than silently pretending everyone is
healthy - see `fetch_espn_injuries`'s "no silent empty/healthy fallback"
behavior and `lib.role_config`'s freshness limits.
"""

import time
from datetime import datetime, timezone

import pandas as pd
import requests

from lib.role_config import (
    AVAILABLE_STATUSES,
    CONDITIONAL_STATUSES,
    CONFIRMED_UNAVAILABLE_STATUSES,
    ESPN_MAX_RETRIES,
    ESPN_REQUEST_TIMEOUT_SECONDS,
    ESPN_RETRY_BACKOFF_SECONDS,
)

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER_URL_TEMPLATE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"

INJURY_COLUMNS = [
    "espn_athlete_id", "player_display_name", "source_position", "espn_team_id",
    "espn_team_abbrev", "source_status", "injury_designation", "availability_classification",
    "is_confirmed_unavailable", "injury_type", "injury_details", "source_retrieved_at",
]


def normalize_availability_classification(raw_status):
    """
    Map a raw ESPN status string to (availability_classification,
    is_confirmed_unavailable):

      - confirmed_unavailable: Out, IR/Injured Reserve, Suspended/Suspension,
        officially inactive, PUP, etc - the ONLY bucket that can elevate a
        backup by default.
      - conditional: Questionable, Doubtful - NEITHER is confirmed
        unavailable in this implementation; both only ever create a
        contingent/monitor scenario, never an automatic elevation.
      - available: Healthy, Active, Probable, or no injury entry at all
        (ESPN simply listing no designation for a player IS "available",
        not "unknown" - "unknown" is reserved for source failure or a
        genuinely unrecognized status string).
      - unknown: an unrecognized status string. Never treated as
        confirmation of anything - see lib/eligibility.py's fail-closed
        handling.
    """
    if raw_status is None or (isinstance(raw_status, str) and not raw_status.strip()):
        return "available", False

    normalized = raw_status.strip().lower()
    if normalized in CONFIRMED_UNAVAILABLE_STATUSES:
        return "confirmed_unavailable", True
    if normalized in CONDITIONAL_STATUSES:
        return "conditional", False
    if normalized in AVAILABLE_STATUSES:
        return "available", False
    return "unknown", False


def _request_json_with_retries(session, url, max_retries=ESPN_MAX_RETRIES,
                                timeout=ESPN_REQUEST_TIMEOUT_SECONDS,
                                backoff_seconds=ESPN_RETRY_BACKOFF_SECONDS):
    """
    GET `url` as JSON with a timeout and exponential backoff retry
    (2s/4s/8s by default) on transient failures (timeouts, connection
    errors, 5xx). A 4xx response is treated as non-transient and NOT
    retried. Returns (json_or_None, error_message_or_None).
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                try:
                    return response.json(), None
                except ValueError as exc:
                    last_error = f"Invalid JSON response: {exc}"
                    return None, last_error
            if 500 <= response.status_code < 600:
                last_error = f"HTTP {response.status_code} (server error)"
            else:
                return None, f"HTTP {response.status_code}"

        if attempt < max_retries - 1:
            time.sleep(backoff_seconds * (2 ** attempt))

    return None, last_error


def fetch_espn_injuries(session=None):
    """
    Fetch current NFL roster/injury data from ESPN: teams list, then each
    team's roster, extracting every player's availability designation.

    Returns (injuries_df, run_metadata).

    injuries_df columns (INJURY_COLUMNS): `espn_athlete_id`,
    `player_display_name`, `source_position`, `espn_team_id`,
    `espn_team_abbrev`, `source_status` (raw ESPN string, preserved
    verbatim), `injury_designation` (human-readable, "None listed" when
    healthy), `availability_classification` (confirmed_unavailable /
    conditional / available / unknown), `is_confirmed_unavailable` (bool),
    `injury_type`, `injury_details`, `source_retrieved_at`.

    run_metadata: `retrieved_at`, `source_success` (True only if the teams
    list AND every single team roster fetch succeeded), `teams_attempted`,
    `teams_succeeded`, `teams_failed` (list of {team_abbrev, error}),
    `failed_team_abbrevs` (flat list, for the eligibility engine to key
    off), `player_rows`, and `error` (top-level failure reason, if the
    teams list itself couldn't be fetched at all).

    Critically: if a team's roster fetch fails, that team's players are
    simply ABSENT from injuries_df - there is no fallback to an
    empty/healthy record for them. `failed_team_abbrevs` is exactly the
    list the eligibility engine uses to mark those teams' role context as
    unresolved rather than silently assuming health.
    """
    session = session or requests.Session()
    retrieved_at = datetime.now(timezone.utc).isoformat()

    teams_json, teams_error = _request_json_with_retries(session, ESPN_TEAMS_URL)
    if teams_json is None:
        return pd.DataFrame(columns=INJURY_COLUMNS), {
            "retrieved_at": retrieved_at,
            "source_success": False,
            "teams_attempted": 0,
            "teams_succeeded": 0,
            "teams_failed": [],
            "failed_team_abbrevs": [],
            "player_rows": 0,
            "error": f"Could not fetch ESPN teams list: {teams_error}",
        }

    try:
        teams = teams_json["sports"][0]["leagues"][0]["teams"]
    except (KeyError, IndexError, TypeError) as exc:
        return pd.DataFrame(columns=INJURY_COLUMNS), {
            "retrieved_at": retrieved_at,
            "source_success": False,
            "teams_attempted": 0,
            "teams_succeeded": 0,
            "teams_failed": [],
            "failed_team_abbrevs": [],
            "player_rows": 0,
            "error": f"Unexpected ESPN teams response schema: {exc}",
        }

    all_rows = []
    teams_failed = []
    teams_succeeded = 0
    skipped_player_rows = 0

    for team_entry in teams:
        try:
            team_id = team_entry["team"]["id"]
            team_abbrev = team_entry["team"]["abbreviation"]
        except (KeyError, TypeError) as exc:
            teams_failed.append({"team_abbrev": None, "error": f"Malformed team entry: {exc}"})
            continue

        roster_json, roster_error = _request_json_with_retries(
            session, ESPN_ROSTER_URL_TEMPLATE.format(team_id=team_id)
        )
        if roster_json is None:
            teams_failed.append({"team_abbrev": team_abbrev, "error": roster_error})
            continue

        try:
            athlete_groups = roster_json["athletes"]
        except (KeyError, TypeError) as exc:
            teams_failed.append({"team_abbrev": team_abbrev, "error": f"Malformed roster response: {exc}"})
            continue

        try:
            team_rows = []
            for group in athlete_groups:
                for player in group.get("items", []):
                    athlete_id = player.get("id")
                    display_name = player.get("displayName")
                    if not athlete_id or not display_name:
                        # Response schema validation: a player row without a
                        # stable ID and a name isn't usable identity data -
                        # skip it rather than emit a half-populated row.
                        skipped_player_rows += 1
                        continue

                    injuries = player.get("injuries", [])
                    raw_status = injury_type = injury_details = None
                    if injuries:
                        latest = injuries[0]
                        raw_status = latest.get("status")
                        injury_type = latest.get("type")
                        injury_details = (latest.get("details") or {}).get("detail")

                    availability_classification, is_confirmed_unavailable = (
                        normalize_availability_classification(raw_status)
                    )

                    team_rows.append({
                        "espn_athlete_id": str(athlete_id),
                        "player_display_name": display_name,
                        "source_position": (player.get("position") or {}).get("abbreviation"),
                        "espn_team_id": str(team_id),
                        "espn_team_abbrev": team_abbrev,
                        "source_status": raw_status,
                        "injury_designation": raw_status if raw_status else "None listed",
                        "availability_classification": availability_classification,
                        "is_confirmed_unavailable": is_confirmed_unavailable,
                        "injury_type": injury_type,
                        "injury_details": injury_details,
                        "source_retrieved_at": retrieved_at,
                    })
        except Exception as exc:  # noqa: BLE001 - any parse failure fails this team, not the whole run
            teams_failed.append({"team_abbrev": team_abbrev, "error": f"Error parsing roster players: {exc}"})
            continue

        all_rows.extend(team_rows)
        teams_succeeded += 1

    injuries_df = pd.DataFrame(all_rows, columns=INJURY_COLUMNS)

    run_metadata = {
        "retrieved_at": retrieved_at,
        "source_success": len(teams_failed) == 0 and teams_succeeded > 0,
        "teams_attempted": len(teams),
        "teams_succeeded": teams_succeeded,
        "teams_failed": teams_failed,
        "failed_team_abbrevs": [t["team_abbrev"] for t in teams_failed if t["team_abbrev"]],
        "skipped_player_rows": skipped_player_rows,
        "player_rows": len(injuries_df),
    }
    return injuries_df, run_metadata
