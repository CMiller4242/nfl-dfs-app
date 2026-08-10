import os

import pandas as pd
import pytest

from lib.manual_overrides import load_overrides

NOW = pd.Timestamp("2026-09-07T17:00:00Z")


def _write_csv(path, rows, columns=None):
    columns = columns or [
        "season", "week", "team", "player_id", "player_name", "position",
        "override_status", "reason", "expires_at", "updated_at",
    ]
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _row(**kw):
    base = {
        "season": 2026, "week": 1, "team": "KC", "player_id": "00-1",
        "player_name": "Some Guy", "position": "WR",
        "override_status": "injury_elevated_backup", "reason": "Beat writer report",
        "expires_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-07T00:00:00Z",
    }
    base.update(kw)
    return base


def test_missing_file_returns_empty_and_is_not_an_error(tmp_path):
    df, log = load_overrides(path=str(tmp_path / "nope.csv"), now=NOW)
    assert df.empty
    assert "No manual override file found" in log[0]


def test_missing_required_column_drops_entire_file(tmp_path):
    path = tmp_path / "overrides.csv"
    pd.DataFrame([{"season": 2026}]).to_csv(path, index=False)
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "missing required column" in log[0]


def test_empty_file_with_correct_headers(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "no rows" in log[0]


def test_valid_row_is_kept_and_team_is_canonicalized(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(team="LAR")])  # DK-style alias
    df, log = load_overrides(path=str(path), now=NOW)
    assert len(df) == 1
    assert df.iloc[0]["team"] == "LA"  # canonicalized via normalize_team
    assert "applied" in log[0]


def test_row_missing_required_field_is_dropped(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(reason="")])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "missing required field" in log[0]
    assert "reason" in log[0]


def test_unrecognized_override_status_is_dropped(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(override_status="probably_fine")])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "not a recognized role classification" in log[0]


def test_role_unresolved_is_not_a_valid_override_status(tmp_path):
    # role_unresolved is a fail-closed default, never something a human
    # override should be able to assert.
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(override_status="role_unresolved")])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "not a recognized role classification" in log[0]


def test_unparseable_expiry_is_dropped(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(expires_at="not-a-date")])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "not a parseable timestamp" in log[0]


def test_expired_row_is_dropped(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [_row(expires_at="2020-01-01T00:00:00Z")])
    df, log = load_overrides(path=str(path), now=NOW)
    assert df.empty
    assert "expired" in log[0]


def test_row_scoped_to_a_different_season_is_dropped():
    df, log = load_overrides(
        path=_scoped_csv([_row(season=2025)]), season=2026, week=1, now=NOW,
    )
    assert df.empty
    assert "does not match active season" in log[0]


def test_row_scoped_to_a_different_week_is_dropped():
    df, log = load_overrides(
        path=_scoped_csv([_row(week=2)]), season=2026, week=1, now=NOW,
    )
    assert df.empty
    assert "does not match slate week" in log[0]


def test_row_with_blank_season_week_matches_any_active_season_week():
    df, log = load_overrides(
        path=_scoped_csv([_row(season="", week="")]), season=2026, week=1, now=NOW,
    )
    assert len(df) == 1


def test_row_matching_active_season_and_week_is_kept():
    df, log = load_overrides(
        path=_scoped_csv([_row(season=2026, week=1)]), season=2026, week=1, now=NOW,
    )
    assert len(df) == 1


def test_mixed_valid_and_invalid_rows_only_keeps_the_valid_ones(tmp_path):
    path = tmp_path / "overrides.csv"
    _write_csv(path, [
        _row(player_id="00-1", reason="Good row"),
        _row(player_id="00-2", override_status="nonsense"),
        _row(player_id="00-3", expires_at="2020-01-01T00:00:00Z"),
    ])
    df, log = load_overrides(path=str(path), now=NOW)
    assert len(df) == 1
    assert df.iloc[0]["player_id"] == "00-1"
    assert len(log) == 3  # every row gets a trace line, applied or dropped


def _scoped_csv(rows):
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    _write_csv(path, rows)
    return path
