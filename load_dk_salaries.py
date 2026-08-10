"""
Manual local workflow for loading a DraftKings salary CSV into the app's
persistent backend slate (data/dk_salaries/current.csv), so the Streamlit
Lineup Helper page has a committed default salary source on startup -
including on Streamlit Community Cloud, where the running app has no
writable/persistent filesystem of its own to save a session upload to.

This script does NOT run automatically (no scheduled workflow calls it) and
does NOT touch anything an app session itself uploads via the file uploader
- a session upload is read into memory only and is never written to disk by
the app. This is the one and only path that writes to data/dk_salaries/,
and it's something you run yourself, then commit the result.

Usage:
    python load_dk_salaries.py path/to/DKSalaries.csv --season 2026 --week 1

What it does:
  1. Validates the source CSV (required columns, at least one player row) -
     see lib/dk_salary_loader.py, the same validation the app itself uses.
  2. Archives the existing data/dk_salaries/current.csv (if any) into
     data/dk_salaries/archive/<old-season>-wk<old-week>-<timestamp>.csv.
  3. Copies the validated CSV to data/dk_salaries/current.csv.
  4. Writes data/dk_slate_metadata.json (season, week, source,
     updated_at_utc, row_count, filename).

Never place personal lineups, exposure/ownership data, contest entries,
bankroll figures, or notes anywhere under data/dk_salaries/ - only public
DraftKings salary exports belong there, and this directory (plus its
archive) is committed to the repo.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone

from lib.dk_salary_loader import (
    ARCHIVE_DIR,
    CURRENT_CSV_PATH,
    DK_SALARIES_DIR,
    SalaryCsvValidationError,
    load_slate_metadata,
    validate_salary_csv_bytes,
    write_slate_metadata,
)


def _archive_existing_current_csv():
    if not os.path.exists(CURRENT_CSV_PATH):
        return None

    old_metadata = load_slate_metadata()
    old_season = old_metadata.get("season", "unknown")
    old_week = old_metadata.get("week", "unknown")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"{old_season}-wk{old_week}-{stamp}.csv"
    archive_path = os.path.join(ARCHIVE_DIR, archive_name)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    shutil.copy2(CURRENT_CSV_PATH, archive_path)
    return archive_path


def main():
    parser = argparse.ArgumentParser(
        description="Validate a DraftKings salary CSV and load it as the app's committed backend slate."
    )
    parser.add_argument("source_csv", help="Path to the salary CSV you downloaded from DraftKings.")
    parser.add_argument("--season", type=int, required=True, help="NFL season this slate belongs to, e.g. 2026.")
    parser.add_argument("--week", type=int, required=True, help="NFL week this slate belongs to, e.g. 1.")
    parser.add_argument(
        "--source", default="manual_copy",
        help="Label recorded in dk_slate_metadata.json for how this file was loaded (default: manual_copy).",
    )
    args = parser.parse_args()

    try:
        with open(args.source_csv, "rb") as f:
            file_bytes = f.read()
    except OSError as exc:
        print(f"Error: could not read {args.source_csv}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        df = validate_salary_csv_bytes(file_bytes)
    except SalaryCsvValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(DK_SALARIES_DIR, exist_ok=True)

    archived_path = _archive_existing_current_csv()
    if archived_path:
        print(f"Archived previous current.csv to {archived_path}")

    with open(CURRENT_CSV_PATH, "wb") as f:
        f.write(file_bytes)
    print(f"Wrote {len(df)} player rows to {CURRENT_CSV_PATH}")

    metadata = write_slate_metadata(
        season=args.season,
        week=args.week,
        source=args.source,
        row_count=len(df),
        filename=os.path.basename(args.source_csv),
    )
    print(f"Wrote slate metadata to data/dk_slate_metadata.json: {metadata}")
    print("\nNext step: review the diff (git status / git diff) and commit data/dk_salaries/ + data/dk_slate_metadata.json yourself.")


if __name__ == "__main__":
    main()
