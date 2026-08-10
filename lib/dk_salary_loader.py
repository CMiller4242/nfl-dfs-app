"""
Persistent backend DraftKings salary CSV: shared validation + metadata I/O
used by BOTH the Streamlit page (pages/3_DFS_Lineup_Helper.py, session-only
reads) and the local `load_dk_salaries.py` helper script (which writes the
committed data/dk_salaries/current.csv + data/dk_slate_metadata.json).
Sharing this module means the app and the script apply the exact same
validation - never a case where the script says a CSV is fine but the app
then rejects it, or vice versa.

Layout this module supports:

    data/
      dk_salaries/
        current.csv          the committed, default-active salary slate
        archive/              past current.csv snapshots, one per reload
      dk_slate_metadata.json  season, week, source, updated_at_utc, row_count, filename

Nothing here ever writes an app-session upload back to disk - see
`pages/3_DFS_Lineup_Helper.py`'s uploader handling and the README's
"Do not modify an uploaded file on Streamlit Community Cloud" note. Only
`load_dk_salaries.py`, a script YOU run locally, ever writes to
data/dk_salaries/.
"""

import io
import json
import os
from datetime import datetime, timezone

import pandas as pd

REQUIRED_SALARY_COLUMNS = ["Position", "Name", "Salary", "Game Info", "TeamAbbrev", "AvgPointsPerGame"]

DK_SALARIES_DIR = os.path.join("data", "dk_salaries")
CURRENT_CSV_PATH = os.path.join(DK_SALARIES_DIR, "current.csv")
ARCHIVE_DIR = os.path.join(DK_SALARIES_DIR, "archive")
SLATE_METADATA_PATH = os.path.join("data", "dk_slate_metadata.json")


class SalaryCsvValidationError(ValueError):
    """Raised when a DK salary CSV fails validation. Message is UI-ready."""


def validate_salary_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    """
    Parse and validate raw DK salary CSV bytes.

    Checks, in order: the bytes parse as CSV at all, every column in
    `REQUIRED_SALARY_COLUMNS` is present, and there is at least one player
    row (a header-only or fully-blank export is not a usable slate).

    Raises `SalaryCsvValidationError` with a specific, user-facing message
    on any failure; returns the parsed DataFrame unmodified on success.
    """
    try:
        df = pd.read_csv(io.BytesIO(file_bytes))
    except Exception as exc:
        raise SalaryCsvValidationError(f"Could not parse this file as CSV: {exc}") from exc

    missing = [c for c in REQUIRED_SALARY_COLUMNS if c not in df.columns]
    if missing:
        raise SalaryCsvValidationError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Required columns: {', '.join(REQUIRED_SALARY_COLUMNS)}."
        )

    if df.dropna(how="all").empty:
        raise SalaryCsvValidationError("CSV has the required columns but no player rows.")

    return df


def load_slate_metadata(path: str = SLATE_METADATA_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def write_slate_metadata(*, season, week, source, row_count, filename, path: str = SLATE_METADATA_PATH) -> dict:
    """Write data/dk_slate_metadata.json, overwriting any prior contents."""
    metadata = {
        "season": season,
        "week": week,
        "source": source,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count,
        "filename": filename,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(metadata, f, indent=2)
    return metadata
