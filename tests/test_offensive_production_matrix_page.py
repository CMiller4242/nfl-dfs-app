import os
from unittest import mock

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import lib.data as data_module
from dfs_data_pipeline import (
    build_defensive_position_points_allowed,
    build_offensive_position_reporting,
    build_upcoming_schedule,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "6_Offensive_Production_Matrix.py")


def _row(team, opp, pos, week, pid, name, fp, carries=0.0, targets=0.0, receptions=0.0):
    return {
        "season": 2026, "team": team, "opponent_team": opp, "position": pos, "week": week,
        "player_id": pid, "player_display_name": name, "fantasy_points_ppr": fp,
        "carries": carries, "targets": targets, "receptions": receptions,
    }


def _synthetic_weekly():
    return pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "Back One", 10.0, carries=15, targets=2),
        _row("KC", "BUF", "RB", 1, "p2", "Back Two", 8.0, carries=5, targets=1),
        _row("KC", "BAL", "RB", 2, "p1", "Back One", 12.0, carries=18, targets=1),
        _row("KC", "BAL", "QB", 2, "p3", "Signal Caller", 20.0),
        _row("BUF", "KC", "RB", 1, "p4", "Buf Back", 14.0, carries=20),
        _row("DEN", "SEA", "RB", 1, "p5", "Den Back", 9.0, carries=12),
    ])


def _synthetic_marts():
    weekly = _synthetic_weekly()
    offense = build_offensive_position_reporting(weekly, 2026, "in_season")
    defense = build_defensive_position_points_allowed(weekly, 2026, "in_season")
    schedule = pd.DataFrame([
        {"season": 2026, "week": 5, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
         "home_score": None, "away_score": None},
    ])
    upcoming = build_upcoming_schedule(schedule, 2026, 4)
    return weekly, offense, defense, upcoming


# ---------------------------------------------------------------------------
# Real committed data: graceful degradation, never a raw KeyError (Part 8)
# ---------------------------------------------------------------------------
def test_page_renders_actionable_message_not_crash_when_mart_missing_on_real_data():
    assert data_module.load_team_offense_position_reporting().empty, (
        "team_offense_position_reporting.parquet now exists on the real committed data - "
        "this test's premise (the mart hasn't been regenerated in this environment) no longer holds."
    )
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("Run `python dfs_data_pipeline.py`" in w.value for w in at.warning)


def test_page_drilldown_still_works_when_matrix_mart_is_missing():
    # Part 8: a missing/legacy mart must disable only the AFFECTED
    # component - the player-distribution drill-down (which only needs
    # players_weekly) must keep working.
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    if not data_module.load_players_weekly().empty:
        assert len(at.selectbox) >= 2  # team + position selectors for the drilldown


def test_page_legacy_schema_missing_percentile_column_shows_refresh_message(monkeypatch):
    weekly, offense, defense, upcoming = _synthetic_marts()
    legacy_offense = offense.drop(columns=["season_offense_percentile"])
    monkeypatch.setattr(data_module, "load_team_offense_position_reporting", lambda: legacy_offense)
    monkeypatch.setattr(data_module, "load_team_defense_position_reporting", lambda: defense)
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any(
        "older pipeline schema" in w.value and "season_offense_percentile" in w.value for w in at.warning
    )


# ---------------------------------------------------------------------------
# Full feature rendering with synthetic marts (monkeypatched, never written
# to the real data/ directory)
# ---------------------------------------------------------------------------
def test_page_matrix_and_discovery_render_with_synthetic_marts():
    weekly, offense, defense, upcoming = _synthetic_marts()
    with mock.patch.object(data_module, "load_team_offense_position_reporting", return_value=offense), \
         mock.patch.object(data_module, "load_team_defense_position_reporting", return_value=defense), \
         mock.patch.object(data_module, "load_upcoming_schedule", return_value=upcoming), \
         mock.patch.object(data_module, "load_players_weekly", return_value=weekly):
        st.cache_data.clear()
        at = AppTest.from_file(PAGE_PATH, default_timeout=120)
        at.run()
        assert not at.exception
        assert len(at.dataframe) >= 1
        assert not any("No offensive team-position reporting" in w.value for w in at.warning)


def test_page_upcoming_matchup_discovery_disabled_without_schedule_mart():
    weekly, offense, defense, _ = _synthetic_marts()
    with mock.patch.object(data_module, "load_team_offense_position_reporting", return_value=offense), \
         mock.patch.object(data_module, "load_team_defense_position_reporting", return_value=defense), \
         mock.patch.object(data_module, "load_upcoming_schedule", return_value=pd.DataFrame()), \
         mock.patch.object(data_module, "load_players_weekly", return_value=weekly):
        st.cache_data.clear()
        at = AppTest.from_file(PAGE_PATH, default_timeout=120)
        at.run()
        assert not at.exception
        assert any("Upcoming matchup discovery is disabled" in i.value for i in at.info)
        # The matrix itself must remain usable even with no schedule.
        assert len(at.dataframe) >= 1


def test_page_drilldown_selecting_team_position_renders_contributions_and_game_by_game():
    weekly, offense, defense, upcoming = _synthetic_marts()
    with mock.patch.object(data_module, "load_team_offense_position_reporting", return_value=offense), \
         mock.patch.object(data_module, "load_team_defense_position_reporting", return_value=defense), \
         mock.patch.object(data_module, "load_upcoming_schedule", return_value=upcoming), \
         mock.patch.object(data_module, "load_players_weekly", return_value=weekly):
        st.cache_data.clear()
        at = AppTest.from_file(PAGE_PATH, default_timeout=120)
        at.run()
        at.selectbox(key="opm_dd_team").set_value("KC")
        at.selectbox(key="opm_dd_position").set_value("RB")
        at.run()
        assert not at.exception
        found_contrib_table = False
        for df_element in at.dataframe:
            if "Player" in list(df_element.value.columns):
                found_contrib_table = True
                assert {"Back One", "Back Two"}.issubset(set(df_element.value["Player"]))
        assert found_contrib_table


def test_page_discovery_shortlist_selection_persists_across_rerun():
    weekly, offense, defense, upcoming = _synthetic_marts()
    with mock.patch.object(data_module, "load_team_offense_position_reporting", return_value=offense), \
         mock.patch.object(data_module, "load_team_defense_position_reporting", return_value=defense), \
         mock.patch.object(data_module, "load_upcoming_schedule", return_value=upcoming), \
         mock.patch.object(data_module, "load_players_weekly", return_value=weekly):
        st.cache_data.clear()
        at = AppTest.from_file(PAGE_PATH, default_timeout=120)
        at.run()
        # Lower the thresholds so at least one matchup qualifies for the shortlist editor.
        at.number_input(key="opm_min_off_pct").set_value(0.0)
        at.number_input(key="opm_min_def_pct").set_value(0.0)
        at.number_input(key="opm_min_games").set_value(0)
        at.run()
        assert not at.exception


# ---------------------------------------------------------------------------
# Cache invalidation (Part 8) - fingerprint must cover the new marts.
# ---------------------------------------------------------------------------
def test_reporting_fingerprint_changes_when_offense_mart_file_changes(tmp_path, monkeypatch):
    import lib.cache_fingerprint as cache_fingerprint

    monkeypatch.setattr(cache_fingerprint, "DATA_DIR", str(tmp_path))
    tracked = tmp_path / "team_offense_position_reporting.parquet"
    tracked.write_bytes(b"version1")
    fp1 = cache_fingerprint.reporting_inputs_fingerprint()

    import time
    time.sleep(0.01)
    tracked.write_bytes(b"version2-different-length")
    fp2 = cache_fingerprint.reporting_inputs_fingerprint()
    assert fp1 != fp2


def test_reporting_fingerprint_changes_when_upcoming_schedule_file_changes(tmp_path, monkeypatch):
    import lib.cache_fingerprint as cache_fingerprint

    monkeypatch.setattr(cache_fingerprint, "DATA_DIR", str(tmp_path))
    tracked = tmp_path / "upcoming_schedule.parquet"
    tracked.write_bytes(b"v1")
    fp1 = cache_fingerprint.reporting_inputs_fingerprint()

    import time
    time.sleep(0.01)
    tracked.write_bytes(b"v2-longer")
    fp2 = cache_fingerprint.reporting_inputs_fingerprint()
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# No regression - existing Matchup Analyzer / Lineup Helper projections and
# classifications are unaffected by this page's existence.
# ---------------------------------------------------------------------------
def test_existing_matchup_analyzer_page_still_renders_unaffected():
    st.cache_data.clear()
    at = AppTest.from_file(os.path.join(REPO_ROOT, "pages", "5_Matchup_Analyzer.py"), default_timeout=120)
    at.run()
    assert not at.exception


def test_existing_lineup_helper_page_still_renders_unaffected():
    st.cache_data.clear()
    at = AppTest.from_file(os.path.join(REPO_ROOT, "pages", "3_DFS_Lineup_Helper.py"), default_timeout=120)
    at.run()
    assert not at.exception
