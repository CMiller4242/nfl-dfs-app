"""
Tests for the role-refresh fallback fix: player_role_context.parquet must be
built from whatever injury data was ACTUALLY preserved on disk after a
failed ESPN fetch, never from the empty in-memory fetch result - see
dfs_data_pipeline._resolve_role_injury_snapshot and run_role_refresh.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import dfs_data_pipeline
import lib.espn_injuries
from dfs_data_pipeline import _resolve_role_injury_snapshot, run_role_refresh
from lib.eligibility import compute_role_context
from lib.role_config import INJURY_FRESHNESS_HOURS

NOW = datetime.now(timezone.utc)

INJURY_COLUMNS = [
    "espn_athlete_id", "player_display_name", "source_position", "espn_team_id",
    "espn_team_abbrev", "source_status", "injury_designation", "availability_classification",
    "is_confirmed_unavailable", "injury_type", "injury_details", "source_retrieved_at",
]


def _injury_row(espn_id, team, classification, designation, retrieved_at):
    return {
        "espn_athlete_id": espn_id, "player_display_name": f"Player {espn_id}", "source_position": "QB",
        "espn_team_id": "1", "espn_team_abbrev": team, "source_status": designation,
        "injury_designation": designation, "availability_classification": classification,
        "is_confirmed_unavailable": classification == "confirmed_unavailable",
        "injury_type": None, "injury_details": None, "source_retrieved_at": retrieved_at,
    }


def _empty_fetch_result(now=NOW, error="ProxyError: could not reach ESPN"):
    return pd.DataFrame(columns=INJURY_COLUMNS), {
        "retrieved_at": now.isoformat(), "source_success": False, "teams_attempted": 0,
        "teams_succeeded": 0, "teams_failed": [], "failed_team_abbrevs": [], "player_rows": 0,
        "skipped_player_rows": 0, "error": error,
    }


def _successful_fetch_result(rows, now=NOW):
    df = pd.DataFrame(rows, columns=INJURY_COLUMNS)
    return df, {
        "retrieved_at": now.isoformat(), "source_success": True, "teams_attempted": 32,
        "teams_succeeded": 32, "teams_failed": [], "failed_team_abbrevs": [], "player_rows": len(df),
        "skipped_player_rows": 0, "error": None,
    }


# ---------------------------------------------------------------------------
# A. Successful fetch
# ---------------------------------------------------------------------------
def test_successful_fetch_uses_fresh_data_as_is():
    fresh_df, fresh_meta = _successful_fetch_result([
        _injury_row("1", "KC", "available", "None listed", NOW.isoformat()),
    ])
    role_df, role_meta, audit = _resolve_role_injury_snapshot(
        fresh_df, fresh_meta, "/nonexistent/path.parquet", injuries_written_fresh=True, now=NOW
    )
    assert role_df is fresh_df
    assert role_meta is fresh_meta
    assert role_meta["retrieved_at"] == NOW.isoformat()
    assert audit == {
        "used_fallback_snapshot": False,
        "fallback_snapshot_retrieved_at": None,
        "fallback_snapshot_age_hours": None,
        "fallback_snapshot_is_stale": None,
        "role_context_source": "fresh_fetch",
    }


# ---------------------------------------------------------------------------
# B. Failed fetch + valid current (fresh-enough) fallback
# ---------------------------------------------------------------------------
def test_failed_fetch_with_valid_fallback_reloads_preserved_snapshot(tmp_path):
    preserved_retrieved_at = (NOW - timedelta(hours=3)).isoformat()
    preserved_df = pd.DataFrame([
        _injury_row("1", "KC", "confirmed_unavailable", "Out", preserved_retrieved_at),
        _injury_row("2", "KC", "available", "None listed", preserved_retrieved_at),
    ], columns=INJURY_COLUMNS)
    path = tmp_path / "injuries_current.parquet"
    preserved_df.to_parquet(path, index=False)

    empty_df, failed_meta = _empty_fetch_result()
    role_df, role_meta, audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=False, now=NOW
    )

    # The empty in-memory fetch result must NOT be what's used.
    assert len(role_df) == 2
    assert set(role_df["espn_athlete_id"]) == {"1", "2"}
    assert role_meta["retrieved_at"] == preserved_retrieved_at  # not "now"
    assert role_meta["source_success"] is False  # latest-attempt fields otherwise untouched

    assert audit["used_fallback_snapshot"] is True
    assert audit["fallback_snapshot_retrieved_at"] == preserved_retrieved_at
    assert audit["fallback_snapshot_age_hours"] == pytest.approx(3.0, abs=0.01)
    assert audit["fallback_snapshot_is_stale"] is False
    assert audit["role_context_source"] == "fallback_snapshot"

    # On disk must remain exactly the preserved snapshot - never overwritten.
    on_disk = pd.read_parquet(path)
    assert len(on_disk) == 2


def test_failed_fetch_with_valid_fallback_keeps_a_blocked_starter_elevating_its_backup(tmp_path):
    # The concrete regression scenario: a starter Out in the preserved
    # snapshot must still elevate its backup, not fall to role_unresolved,
    # after a failed refresh.
    preserved_retrieved_at = (NOW - timedelta(hours=2)).isoformat()
    preserved_df = pd.DataFrame([
        _injury_row("1", "KC", "confirmed_unavailable", "Out", preserved_retrieved_at),
        _injury_row("2", "KC", "available", "None listed", preserved_retrieved_at),
    ], columns=INJURY_COLUMNS)
    path = tmp_path / "injuries_current.parquet"
    preserved_df.to_parquet(path, index=False)

    depth_df = pd.DataFrame([
        {"player_id": "p1", "espn_id": "1", "player_display_name": "Starter QB", "canonical_team": "KC",
         "position_group": "QB", "source_position": "QB", "depth_rank": 1,
         "depth_chart_source_timestamp": (NOW - timedelta(hours=2)).isoformat()},
        {"player_id": "p2", "espn_id": "2", "player_display_name": "Backup QB", "canonical_team": "KC",
         "position_group": "QB", "source_position": "QB", "depth_rank": 2,
         "depth_chart_source_timestamp": (NOW - timedelta(hours=2)).isoformat()},
    ])

    empty_df, failed_meta = _empty_fetch_result()
    role_df, role_meta, _audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=False, now=NOW
    )
    role_context = compute_role_context(depth_df, role_df, role_meta, now=NOW)

    starter = role_context[role_context["player_id"] == "p1"].iloc[0]
    backup = role_context[role_context["player_id"] == "p2"].iloc[0]
    assert starter["role_classification"] == "inactive"
    assert backup["role_classification"] == "injury_elevated_backup"
    assert backup["role_eligible_for_top_values"] == True  # noqa: E712 - never role_unresolved


# ---------------------------------------------------------------------------
# C. Failed fetch + no prior snapshot at all
# ---------------------------------------------------------------------------
def test_failed_fetch_with_no_prior_snapshot_has_no_fallback(tmp_path):
    path = tmp_path / "injuries_current.parquet"  # never created
    empty_df, failed_meta = _empty_fetch_result()

    role_df, role_meta, audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=True, now=NOW
    )
    assert role_df.empty
    assert audit["used_fallback_snapshot"] is False
    assert audit["role_context_source"] == "unavailable"
    assert audit["fallback_snapshot_retrieved_at"] is None


def test_failed_fetch_with_no_prior_snapshot_fails_closed_to_role_unresolved(tmp_path):
    path = tmp_path / "injuries_current.parquet"
    depth_df = pd.DataFrame([
        {"player_id": "p1", "espn_id": "1", "player_display_name": "Some Guy", "canonical_team": "KC",
         "position_group": "QB", "source_position": "QB", "depth_rank": 1,
         "depth_chart_source_timestamp": (NOW - timedelta(hours=1)).isoformat()},
    ])
    empty_df, failed_meta = _empty_fetch_result()
    role_df, role_meta, _audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=True, now=NOW
    )
    role_context = compute_role_context(depth_df, role_df, role_meta, now=NOW)
    assert role_context.iloc[0]["role_classification"] == "role_unresolved"
    assert role_context.iloc[0]["role_eligible_for_top_values"] == False  # noqa: E712


# ---------------------------------------------------------------------------
# D. Failed fetch + stale fallback
# ---------------------------------------------------------------------------
def test_failed_fetch_with_stale_fallback_is_labeled_stale(tmp_path):
    stale_retrieved_at = (NOW - timedelta(hours=INJURY_FRESHNESS_HOURS + 10)).isoformat()
    preserved_df = pd.DataFrame([
        _injury_row("1", "KC", "confirmed_unavailable", "Out", stale_retrieved_at),
    ], columns=INJURY_COLUMNS)
    path = tmp_path / "injuries_current.parquet"
    preserved_df.to_parquet(path, index=False)

    empty_df, failed_meta = _empty_fetch_result()
    role_df, role_meta, audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=False, now=NOW
    )

    # The fallback is still physically preserved and used...
    assert len(role_df) == 1
    assert audit["used_fallback_snapshot"] is True
    # ...but clearly labeled stale, never silently treated as fresh.
    assert audit["fallback_snapshot_is_stale"] is True
    assert audit["fallback_snapshot_age_hours"] > INJURY_FRESHNESS_HOURS


def test_stale_fallback_fails_closed_never_eligible_solely_because_fallback_exists(tmp_path):
    stale_retrieved_at = (NOW - timedelta(hours=INJURY_FRESHNESS_HOURS + 10)).isoformat()
    preserved_df = pd.DataFrame([
        _injury_row("1", "KC", "confirmed_unavailable", "Out", stale_retrieved_at),
        _injury_row("2", "KC", "available", "None listed", stale_retrieved_at),
    ], columns=INJURY_COLUMNS)
    path = tmp_path / "injuries_current.parquet"
    preserved_df.to_parquet(path, index=False)

    depth_df = pd.DataFrame([
        {"player_id": "p1", "espn_id": "1", "player_display_name": "Starter QB", "canonical_team": "KC",
         "position_group": "QB", "source_position": "QB", "depth_rank": 1,
         "depth_chart_source_timestamp": (NOW - timedelta(hours=1)).isoformat()},
        {"player_id": "p2", "espn_id": "2", "player_display_name": "Backup QB", "canonical_team": "KC",
         "position_group": "QB", "source_position": "QB", "depth_rank": 2,
         "depth_chart_source_timestamp": (NOW - timedelta(hours=1)).isoformat()},
    ])
    empty_df, failed_meta = _empty_fetch_result()
    role_df, role_meta, audit = _resolve_role_injury_snapshot(
        empty_df, failed_meta, str(path), injuries_written_fresh=False, now=NOW
    )
    assert audit["fallback_snapshot_is_stale"] is True

    role_context = compute_role_context(depth_df, role_df, role_meta, now=NOW)
    # Stale injury data -> role_data_freshness != "fresh" -> unresolved for
    # BOTH players, even the backup who would otherwise look elevated - a
    # stale fallback must never grant eligibility on its own.
    for _, row in role_context.iterrows():
        assert row["role_classification"] == "role_unresolved"
        assert row["role_eligible_for_top_values"] == False  # noqa: E712
        assert row["role_data_freshness"] != "fresh"


# ---------------------------------------------------------------------------
# E. Full run_role_refresh integration: one failed refresh cannot turn a
# valid, previously-populated Player Pool into an all-role_unresolved pool.
# ---------------------------------------------------------------------------
def _raw_depth_row(gsis_id, espn_id, name, team, pos_abb, pos_rank, dt):
    return {
        "dt": dt, "team": team, "player_name": name, "espn_id": espn_id, "gsis_id": gsis_id,
        "pos_grp_id": 1, "pos_grp": "Offense", "pos_id": 1, "pos_name": pos_abb,
        "pos_abb": pos_abb, "pos_slot": pos_rank, "pos_rank": pos_rank,
    }


def test_run_role_refresh_end_to_end_survives_a_failed_fetch_with_good_fallback(tmp_path, monkeypatch):
    dt = (NOW - timedelta(hours=1)).isoformat()
    raw_depth = pd.DataFrame([
        _raw_depth_row("p1", "1", "Starter QB", "KC", "QB", 1, dt),
        _raw_depth_row("p2", "2", "Backup QB", "KC", "QB", 2, dt),
    ])
    monkeypatch.setattr(dfs_data_pipeline, "load_raw_depth_charts", lambda season: raw_depth)

    good_injuries, good_meta = _successful_fetch_result([
        _injury_row("1", "KC", "confirmed_unavailable", "Out", NOW.isoformat()),
        _injury_row("2", "KC", "available", "None listed", NOW.isoformat()),
    ], now=NOW)
    monkeypatch.setattr(lib.espn_injuries, "fetch_espn_injuries", lambda: (good_injuries, good_meta))

    data_dir = str(tmp_path)
    result_1 = run_role_refresh(2026, 1, data_dir=data_dir)
    role_context_1 = pd.read_parquet(f"{data_dir}/player_role_context.parquet")
    backup_before = role_context_1[role_context_1["player_id"] == "p2"].iloc[0]
    assert backup_before["role_classification"] == "injury_elevated_backup"
    assert result_1["injury_metadata"]["role_context_source"] == "fresh_fetch"

    # Second refresh: ESPN fetch fails entirely this time.
    failed_injuries, failed_meta = _empty_fetch_result(now=NOW + timedelta(minutes=30))
    monkeypatch.setattr(lib.espn_injuries, "fetch_espn_injuries", lambda: (failed_injuries, failed_meta))

    result_2 = run_role_refresh(2026, 1, data_dir=data_dir)
    role_context_2 = pd.read_parquet(f"{data_dir}/player_role_context.parquet")

    # The Player Pool must NOT collapse to all-role_unresolved just because
    # this one refresh's ESPN fetch failed.
    assert not (role_context_2["role_classification"] == "role_unresolved").all()
    backup_after = role_context_2[role_context_2["player_id"] == "p2"].iloc[0]
    assert backup_after["role_classification"] == "injury_elevated_backup"

    injury_meta_2 = result_2["injury_metadata"]
    assert injury_meta_2["source_success"] is False
    assert injury_meta_2["used_fallback_snapshot"] is True
    assert injury_meta_2["role_context_source"] == "fallback_snapshot"

    # The preserved injuries_current.parquet on disk must be untouched by
    # the failed fetch (still the good 2-row snapshot, not overwritten).
    on_disk_injuries = pd.read_parquet(f"{data_dir}/injuries_current.parquet")
    assert len(on_disk_injuries) == 2
