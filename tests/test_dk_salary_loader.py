import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from lib.dk_salary_loader import (
    REQUIRED_SALARY_COLUMNS,
    SalaryCsvValidationError,
    load_slate_metadata,
    validate_salary_csv_bytes,
    write_slate_metadata,
)

VALID_CSV = (
    "Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
    "QB,Patrick Mahomes,7800,KC@BUF 09/07/2026 01:00PM ET,KC,22.1\n"
    "RB,Christian McCaffrey,9000,SF@LA 09/07/2026 04:05PM ET,SF,20.3\n"
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOAD_SCRIPT = os.path.join(REPO_ROOT, "load_dk_salaries.py")


# ---------------------------------------------------------------------------
# validate_salary_csv_bytes
# ---------------------------------------------------------------------------
def test_validate_accepts_valid_csv():
    df = validate_salary_csv_bytes(VALID_CSV.encode("utf-8"))
    assert len(df) == 2
    assert list(df.columns) == REQUIRED_SALARY_COLUMNS


def test_validate_missing_columns_lists_every_missing_name():
    csv_bytes = b"Position,Name,Salary\nQB,Some Guy,5000\n"
    with pytest.raises(SalaryCsvValidationError) as exc_info:
        validate_salary_csv_bytes(csv_bytes)
    message = str(exc_info.value)
    assert "Game Info" in message
    assert "TeamAbbrev" in message
    assert "AvgPointsPerGame" in message


def test_validate_header_only_csv_raises_no_player_rows():
    csv_bytes = b"Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
    with pytest.raises(SalaryCsvValidationError, match="no player rows"):
        validate_salary_csv_bytes(csv_bytes)


def test_validate_unparseable_bytes_raises_helpful_error():
    with pytest.raises(SalaryCsvValidationError, match="Could not parse"):
        validate_salary_csv_bytes(b"\x00\x01\x02\xff\xfe not a csv at all")


# ---------------------------------------------------------------------------
# slate metadata read/write
# ---------------------------------------------------------------------------
def test_load_slate_metadata_missing_file_returns_empty_dict(tmp_path):
    assert load_slate_metadata(path=str(tmp_path / "nope.json")) == {}


def test_write_and_load_slate_metadata_round_trip(tmp_path):
    path = str(tmp_path / "dk_slate_metadata.json")
    written = write_slate_metadata(
        season=2026, week=1, source="manual_copy", row_count=142, filename="DKSalaries.csv", path=path,
    )
    assert written["season"] == 2026
    assert written["week"] == 1
    assert "updated_at_utc" in written

    loaded = load_slate_metadata(path=path)
    assert loaded == written


# ---------------------------------------------------------------------------
# load_dk_salaries.py CLI - run as a real subprocess against a scratch cwd,
# never touching this repo's real data/ directory.
# ---------------------------------------------------------------------------
def _run_cli(cwd, *args):
    return subprocess.run(
        [sys.executable, LOAD_SCRIPT, *args],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def test_cli_loads_valid_csv_and_writes_metadata(tmp_path):
    source_csv = tmp_path / "DKSalaries.csv"
    source_csv.write_text(VALID_CSV)

    result = _run_cli(str(tmp_path), str(source_csv), "--season", "2026", "--week", "1")
    assert result.returncode == 0, result.stderr

    current_csv = tmp_path / "data" / "dk_salaries" / "current.csv"
    assert current_csv.exists()
    assert current_csv.read_text() == VALID_CSV

    metadata_path = tmp_path / "data" / "dk_slate_metadata.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["season"] == 2026
    assert metadata["week"] == 1
    assert metadata["source"] == "manual_copy"
    assert metadata["row_count"] == 2
    assert metadata["filename"] == "DKSalaries.csv"


def test_cli_archives_previous_current_csv_on_reload(tmp_path):
    source_csv = tmp_path / "DKSalaries_wk1.csv"
    source_csv.write_text(VALID_CSV)
    _run_cli(str(tmp_path), str(source_csv), "--season", "2026", "--week", "1")

    updated_csv = (
        "Position,Name,Salary,Game Info,TeamAbbrev,AvgPointsPerGame\n"
        "WR,Puka Nacua,8300,SF@LA 09/14/2026 04:05PM ET,LA,18.9\n"
    )
    source_csv2 = tmp_path / "DKSalaries_wk2.csv"
    source_csv2.write_text(updated_csv)
    result = _run_cli(str(tmp_path), str(source_csv2), "--season", "2026", "--week", "2")
    assert result.returncode == 0, result.stderr

    archive_dir = tmp_path / "data" / "dk_salaries" / "archive"
    archived_files = list(archive_dir.glob("*.csv"))
    assert len(archived_files) == 1
    assert archived_files[0].name.startswith("2026-wk1-")
    assert archived_files[0].read_text() == VALID_CSV  # the OLD (week 1) content

    current_csv = tmp_path / "data" / "dk_salaries" / "current.csv"
    assert current_csv.read_text() == updated_csv  # the NEW (week 2) content is now active


def test_cli_rejects_invalid_csv_and_writes_nothing(tmp_path):
    source_csv = tmp_path / "Bad.csv"
    source_csv.write_text("Position,Name,Salary\nQB,Some Guy,5000\n")

    result = _run_cli(str(tmp_path), str(source_csv), "--season", "2026", "--week", "1")
    assert result.returncode != 0
    assert "missing required column" in result.stderr

    assert not (tmp_path / "data" / "dk_salaries" / "current.csv").exists()
    assert not (tmp_path / "data" / "dk_slate_metadata.json").exists()


def test_cli_rejects_missing_source_file(tmp_path):
    result = _run_cli(str(tmp_path), str(tmp_path / "nope.csv"), "--season", "2026", "--week", "1")
    assert result.returncode != 0
    assert "could not read" in result.stderr.lower()
