"""
Shared, isolated test fixtures for schema-guard / legacy-mart scenarios.

These exist so AppTest-level page tests can exercise "mart predates the
Reporting Integrity pass's schema change" and "mart is on the current
schema" scenarios DETERMINISTICALLY - never by asserting on whatever state
the real, currently-committed `data/` parquet files happen to be in. A real
pipeline run (which needs live network access to nflreadpy, unavailable in
this test suite) may regenerate those real files with new columns at any
time; tests that depend on the real files lacking (or having) a column
become flaky/wrong the moment that happens. Every test that needs a
specific schema shape should request one of these fixtures (or build an
equally isolated, synthetic frame of its own) instead.
"""

import pandas as pd
import pytest

_DEFENSE_REPORTING_BASE_ROW = {
    "season": 2025, "defense_team": "KC", "position": "RB", "games_in_sample": 10,
    "latest_completed_week": 4, "source_last_updated_utc": "2025-01-01T00:00:00Z",
    "reporting_mode": "in_season", "sample_size_label": "full_sample",
    "fantasy_points_allowed_per_game": 12.0, "league_avg_points_allowed_for_position": 11.0,
    "matchup_index": 109.0, "matchup_delta": 1.0,
    "position_rank_most_favorable": 5, "position_percentile_most_favorable": 60.0,
    "last_3_games_count": 3, "last_3_games_points_allowed_per_game": 12.5,
    "last_3_games_matchup_index": 110.0, "last_3_games_matchup_delta": 1.5,
    "dvp_recent_trend_delta": 0.5, "dvp_trend_label": "stable",
}


def _legacy_defense_reporting_frame() -> pd.DataFrame:
    return pd.DataFrame([dict(_DEFENSE_REPORTING_BASE_ROW)])


def _current_defense_reporting_frame() -> pd.DataFrame:
    row = dict(_DEFENSE_REPORTING_BASE_ROW)
    row["defensive_games_played"] = 3
    row["player_game_row_count"] = row["games_in_sample"]
    return pd.DataFrame([row])


@pytest.fixture
def synthetic_legacy_defense_reporting() -> pd.DataFrame:
    """
    A deterministic `defense_reporting` frame on the OLD (pre-Reporting-
    Integrity-pass) schema: the legacy `games_in_sample` raw-row-count
    column is present, but `defensive_games_played`/`player_game_row_count`
    are genuinely absent as columns. Used to prove the schema guard fires
    correctly - and that nothing substitutes `games_in_sample` for the
    missing distinct-game count - regardless of the real mart's actual,
    possibly-already-regenerated state.
    """
    return _legacy_defense_reporting_frame()


@pytest.fixture
def synthetic_current_defense_reporting() -> pd.DataFrame:
    """The same shape, on the CURRENT schema (`defensive_games_played`/
    `player_game_row_count` present alongside the unchanged legacy
    `games_in_sample`) - for success-path coverage alongside the legacy
    scenario above, equally isolated from real data state."""
    return _current_defense_reporting_frame()
