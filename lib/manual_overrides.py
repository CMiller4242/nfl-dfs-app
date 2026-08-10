"""
Loader/validator for the manually-maintained game-day eligibility override
file (`data/manual/eligibility_overrides.csv`). This is the one place a
human can correct the automated role/eligibility engine (e.g. a beat-writer
report that lands after the pipeline last ran) - and it is validated just
as strictly as any external source: a malformed, expired, or out-of-scope
row is dropped and logged, never silently applied.
"""

import os
from datetime import datetime, timezone

import pandas as pd

from lib.player_identity import normalize_team
from lib.role_config import MANUAL_OVERRIDE_PATH, MANUAL_OVERRIDE_REQUIRED_COLUMNS, VALID_OVERRIDE_STATUSES


def load_overrides(path: str = MANUAL_OVERRIDE_PATH, season=None, week=None, now=None):
    """
    Load, validate, and filter the manual override CSV.

    Returns (overrides_df, validation_log):
      - overrides_df: only rows that are well-formed (every required field
        present and non-blank), have a recognized `override_status` (the
        same role_classification vocabulary the engine itself produces),
        are not expired as of `now`, and - when `season`/`week` are given -
        match that season/week. `team` is canonicalized.
      - validation_log: a human-readable line for every row in the source
        file, describing whether it was applied or dropped and why. Always
        non-empty when the file exists and has rows, so an expired or
        malformed override is never silently ignored without a trace -
        callers (the pipeline) should print/log this.

    A missing file is not an error - it just means no overrides are active
    this run.
    """
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts

    empty = pd.DataFrame(columns=MANUAL_OVERRIDE_REQUIRED_COLUMNS)

    if not os.path.exists(path):
        return empty, [f"No manual override file found at {path} - none applied (this is normal)."]

    try:
        raw = pd.read_csv(path, dtype=str)
    except Exception as exc:
        return empty, [f"Could not read manual override file at {path}: {exc}"]

    missing_cols = [c for c in MANUAL_OVERRIDE_REQUIRED_COLUMNS if c not in raw.columns]
    if missing_cols:
        return empty, [
            f"Manual override file at {path} is missing required column(s): "
            f"{', '.join(missing_cols)} - ignoring the entire file."
        ]

    if raw.empty:
        return empty, [f"Manual override file at {path} has no rows."]

    log = []
    keep_rows = []

    for i, row in raw.iterrows():
        row_label = f"row {i + 2} ({row.get('player_name') or row.get('player_id') or '?'})"  # +2: header + 1-index

        required_nonblank = ["player_id", "reason", "expires_at", "team", "position"]
        missing = [c for c in required_nonblank if pd.isna(row.get(c)) or not str(row.get(c)).strip()]
        if missing:
            log.append(f"{row_label}: dropped - missing required field(s): {', '.join(missing)}.")
            continue

        status = str(row.get("override_status", "")).strip()
        if status not in VALID_OVERRIDE_STATUSES:
            log.append(f"{row_label}: dropped - override_status '{status}' is not a recognized role classification.")
            continue

        try:
            expires_at = pd.to_datetime(row["expires_at"], utc=True)
        except (ValueError, TypeError):
            log.append(f"{row_label}: dropped - expires_at '{row.get('expires_at')}' is not a parseable timestamp.")
            continue
        if expires_at < now_ts:
            log.append(f"{row_label}: dropped - expired at {expires_at.isoformat()} (now: {now_ts.isoformat()}).")
            continue

        row_season_raw = row.get("season")
        if season is not None and not pd.isna(row_season_raw) and str(row_season_raw).strip():
            try:
                row_season_matches = int(float(row_season_raw)) == int(season)
            except (ValueError, TypeError):
                row_season_matches = False
            if not row_season_matches:
                log.append(f"{row_label}: dropped - season {row_season_raw} does not match active season {season}.")
                continue

        row_week_raw = row.get("week")
        if week is not None and not pd.isna(row_week_raw) and str(row_week_raw).strip():
            try:
                row_week_matches = int(float(row_week_raw)) == int(week)
            except (ValueError, TypeError):
                row_week_matches = False
            if not row_week_matches:
                log.append(f"{row_label}: dropped - week {row_week_raw} does not match slate week {week}.")
                continue

        keep_rows.append({
            "season": row.get("season"),
            "week": row.get("week"),
            "team": normalize_team(row["team"]),
            "player_id": str(row["player_id"]).strip(),
            "player_name": row.get("player_name"),
            "position": str(row["position"]).strip(),
            "override_status": status,
            "reason": str(row["reason"]).strip(),
            "expires_at": row["expires_at"],
            "updated_at": row.get("updated_at"),
        })
        log.append(f"{row_label}: applied - {status} ({row['reason']}).")

    result = pd.DataFrame(keep_rows, columns=MANUAL_OVERRIDE_REQUIRED_COLUMNS) if keep_rows else empty
    return result, log
