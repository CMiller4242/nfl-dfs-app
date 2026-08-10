import json
import os
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from lib.dk_salary_loader import CURRENT_CSV_PATH, SLATE_METADATA_PATH

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "3_DFS_Lineup_Helper.py")

VALID_DK_CSV = (
    "Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
    "QB,Patrick Mahomes,7800,KC@BUF 09/07/2026 01:00PM ET,KC,22.1\n"
    "RB,Christian McCaffrey,9000,SF@LA 09/07/2026 04:05PM ET,SF,20.3\n"
)


@pytest.fixture
def isolated_salary_files():
    """
    The page reads data/dk_salaries/current.csv and data/dk_slate_metadata.json
    from fixed paths (lib/dk_salary_loader.py) with no injectable override, so
    this fixture backs up whatever is really committed there, clears both for
    the duration of the test, and always restores the original state
    afterward - regardless of test outcome.
    """
    backups = {}
    for path in (CURRENT_CSV_PATH, SLATE_METADATA_PATH):
        abs_path = os.path.join(REPO_ROOT, path)
        if os.path.exists(abs_path):
            backup_path = abs_path + ".test-backup"
            shutil.move(abs_path, backup_path)
            backups[abs_path] = backup_path
        else:
            backups[abs_path] = None
    os.makedirs(os.path.join(REPO_ROOT, os.path.dirname(CURRENT_CSV_PATH)), exist_ok=True)

    try:
        yield
    finally:
        for abs_path, backup_path in backups.items():
            if os.path.exists(abs_path):
                os.remove(abs_path)
            if backup_path is not None:
                shutil.move(backup_path, abs_path)


def _write_committed_slate(season=2026, week=1, row_count=2, csv_text=VALID_DK_CSV):
    with open(os.path.join(REPO_ROOT, CURRENT_CSV_PATH), "w") as f:
        f.write(csv_text)
    with open(os.path.join(REPO_ROOT, SLATE_METADATA_PATH), "w") as f:
        json.dump(
            {
                "season": season, "week": week, "source": "manual_copy",
                "updated_at_utc": "2026-08-01T12:00:00+00:00",
                "row_count": row_count, "filename": "current.csv",
            },
            f,
        )


def _metrics_by_label(at):
    return {m.label: m.value for m in at.metric}


# ---------------------------------------------------------------------------
def test_committed_current_csv_loads_with_no_uploader_interaction(isolated_salary_files):
    _write_committed_slate(season=2026, week=1)

    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()

    assert not at.exception
    metrics = _metrics_by_label(at)
    assert metrics["Salary data source"] == "Committed backend salary file"
    assert metrics["Slate season"] == "2026"
    assert metrics["Slate week"] == "1"
    assert any("Active file: `current.csv`" in el.value for el in at.caption)


def test_session_upload_overrides_committed_file_only_for_that_session(isolated_salary_files):
    # Distinct content from the uploaded CSV below, so we can prove on disk
    # which one actually "won" after the upload.
    committed_csv = (
        "Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
        "TE,George Kittle,5900,SF@LA 09/07/2026 04:05PM ET,SF,12.8\n"
    )
    _write_committed_slate(season=2025, week=18, row_count=1, csv_text=committed_csv)

    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()
    assert not at.exception
    assert _metrics_by_label(at)["Salary data source"] == "Committed backend salary file"

    at.file_uploader[0].upload("SessionOverride.csv", VALID_DK_CSV.encode("utf-8"), "text/csv")
    at.run()

    assert not at.exception
    metrics = _metrics_by_label(at)
    assert metrics["Salary data source"] == "Session upload override"
    # Session-only: no season/week metadata exists for an upload.
    assert metrics["Slate season"] == "—"
    assert metrics["Slate week"] == "—"

    # The committed backend file on disk must be completely untouched - a
    # session upload is never written back to disk.
    with open(os.path.join(REPO_ROOT, CURRENT_CSV_PATH)) as f:
        committed_contents_after = f.read()
    assert committed_contents_after == committed_csv
    assert "Kittle" in committed_contents_after
    assert "Mahomes" not in committed_contents_after

    metadata_on_disk = json.load(open(os.path.join(REPO_ROOT, SLATE_METADATA_PATH)))
    assert metadata_on_disk["season"] == 2025  # unchanged by the session upload


def test_invalid_csv_missing_required_columns_shows_helpful_error(isolated_salary_files):
    with open(os.path.join(REPO_ROOT, CURRENT_CSV_PATH), "w") as f:
        f.write("Position,Name,Salary\nQB,Some Guy,5000\n")

    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()

    assert not at.exception
    assert len(at.error) == 1
    assert "missing required column" in at.error[0].value
    assert "Game Info" in at.error[0].value


def test_header_only_csv_shows_helpful_no_player_rows_error(isolated_salary_files):
    with open(os.path.join(REPO_ROOT, CURRENT_CSV_PATH), "w") as f:
        f.write("Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n")

    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()

    assert not at.exception
    assert len(at.error) == 1
    assert "no player rows" in at.error[0].value


def test_no_salary_data_at_all_produces_useful_empty_state(isolated_salary_files):
    # Fixture already ensures neither file exists; don't write anything.
    at = AppTest.from_file(PAGE_PATH, default_timeout=60)
    at.run()

    assert not at.exception
    assert any("No salary data available" in el.value for el in at.info)
    assert any("load_dk_salaries.py" in el.value for el in at.info)
