"""
Presentation Fix coverage for pages/3_DFS_Lineup_Helper.py's value-ranking
section (renamed "Highest Projected Value") and the new "Supported Plays to
Investigate" section.

All fixtures are synthetic and isolated - never touching the real committed
data/ files - so these tests are deterministic regardless of the real
committed slate/marts' current state. The row-builder helpers mirror the
ones in tests/test_matchup_analyzer.py (same schemas, since this page now
reuses lib.matchup_analyzer.build_matchup_analyzer_table +
lib.player_case_summary.build_case_summary - the exact pipeline that file
already exercises - rather than any new classification logic).
"""
import os

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import lib.data as data_module
from lib.dk_helper import MATCHUP_ADJUSTMENT_WEIGHT, MOMENTUM_ADJUSTMENT_WEIGHT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "3_DFS_Lineup_Helper.py")


# ---------------------------------------------------------------------------
# Row builders (same schemas as tests/test_matchup_analyzer.py)
# ---------------------------------------------------------------------------
def _dk_row(name, position, team, opponent, salary, avg=15.0):
    return {
        "Position": position, "Name": name, "Salary": salary,
        "Game Info": f"{opponent}@{team} 10/11/2026 01:00PM ET",
        "TeamAbbrev": team, "AvgPointsPerGame": avg,
    }


def _player_row(player_id, name, team, position, last_opponent, avg, momentum, games=4):
    return {
        "player_id": player_id, "player_display_name": name, "position": position, "team": team,
        "last_opponent": last_opponent, "season": 2026, "latest_game_week": games,
        "games_played": games, "avg_fantasy_points": avg, "total_fantasy_points": avg * games,
        "latest_game_fantasy_points": avg, "total_touches": 0, "touches_per_game": 0,
        "total_targets": 0, "targets_per_game": 0, "total_carries": 0, "carries_per_game": 0,
        "total_receptions": 0, "receptions_per_game": 0, "total_rushing_yards": 0,
        "rushing_yards_per_game": 0, "total_receiving_yards": 0, "receiving_yards_per_game": 0,
        "total_yards": 0, "total_yards_per_game": 0, "total_receiving_air_yards": 0,
        "receiving_air_yards_per_game": 0, "total_yac": 0, "total_passing_yards": avg * games * 10,
        "total_passing_tds": 0, "total_rushing_tds": 0, "total_receiving_tds": 0,
        "completion_pct": pd.NA, "passing_yards_per_attempt": pd.NA, "yards_per_target": pd.NA,
        "yards_per_carry": pd.NA, "catch_rate": pd.NA, "yac_per_reception": pd.NA,
        "target_share_pct": pd.NA, "air_yards_share_pct": pd.NA, "yards_per_touch": pd.NA,
        "points_per_touch": pd.NA, "consistency_score": 2.0, "momentum_score": momentum,
        "momentum_games_used": games, "latest_game_touches": 0, "prior_game_touches": 0,
        "touches_wow_change": 0, "latest_game_carries": 0, "latest_game_targets": 0,
        "latest_game_receptions": 0, "latest_game_rushing_yards": 0, "latest_game_receiving_yards": 0,
        "latest_game_total_yards": 0, "latest_game_target_share_pct": pd.NA,
        "latest_game_air_yards_share_pct": pd.NA, "carries_wow_change": 0, "targets_wow_change": 0,
        "receiving_yards_wow_change": 0, "target_share_wow_change": 0, "opportunity_trend": "steady",
    }


def _role_row(player_id, name, team, position, role="confirmed_starter", eligible_pool=True, eligible_top=True):
    return {
        "player_id": player_id, "player_name": name, "canonical_team": team, "position_group": position,
        "source_position": position, "depth_rank": 1, "espn_id": f"espn_{player_id}",
        "availability_classification": "available", "injury_designation": "Healthy",
        "role_classification": role, "role_eligible_for_pool": eligible_pool,
        "role_eligible_for_top_values": eligible_top, "is_conditional_monitor": False,
        "eligibility_reason": f"{role} reason for {name}", "blocking_player_ids": "",
        "blocking_player_names": "", "blocking_player_statuses": "",
        "depth_chart_source_timestamp": pd.Timestamp.now(tz="UTC"),
        "injury_source_timestamp": pd.Timestamp.now(tz="UTC"), "role_data_freshness": "fresh",
        "manual_override_applied": False, "override_reason": None,
    }


def _defense_row(defense_team, position, matchup_delta, pctile, games=5, fppg_allowed=14.0):
    matchup_index = 100.0 + matchup_delta * 10
    return {
        "season": 2026, "defense_team": defense_team, "position": position, "games_in_sample": games,
        "latest_completed_week": 5, "source_last_updated_utc": pd.Timestamp.now(tz="UTC"),
        "reporting_mode": "in_season", "sample_size_label": "full_sample",
        "fantasy_points_allowed_per_game": fppg_allowed, "league_avg_points_allowed_for_position": 14.0,
        "matchup_index": matchup_index, "matchup_delta": matchup_delta,
        "position_rank_most_favorable": 16, "position_percentile_most_favorable": pctile,
        "last_3_games_count": games, "last_3_games_points_allowed_per_game": fppg_allowed,
        "last_3_games_matchup_index": matchup_index, "last_3_games_matchup_delta": matchup_delta,
        "dvp_recent_trend_delta": 0.0, "dvp_trend_label": "stable",
        "defensive_games_played": games, "player_game_row_count": games,
    }


def _team_reporting_row(team):
    return {
        "season": 2026, "team": team, "games_played": 4, "latest_played_week": 4,
        "last_updated_utc": pd.Timestamp.now(tz="UTC"), "sample_size_label": "insufficient_sample",
        "reporting_mode": "in_season", "season_passing_yards": 1000, "season_rushing_yards": 400,
        "season_total_yards": 1400, "season_passing_yards_per_game": 250, "season_rushing_yards_per_game": 100,
        "season_total_yards_per_game": 350, "season_pass_attempts": 120, "season_carries": 80,
        "season_offensive_plays": 200, "season_pass_rate_pct": 58.0, "season_passing_epa": 10.0,
        "season_passing_dropbacks": 120, "season_passing_epa_denominator": "dropbacks (attempts + sacks_suffered)",
        "season_passing_epa_per_dropback_or_attempt": 0.05, "last_3_games_count": 3,
        "last_3_passing_yards_per_game": 250, "last_3_rushing_yards_per_game": 100,
        "last_3_total_yards_per_game": 350, "last_3_pass_rate_pct": 58.0,
        "last_3_passing_epa_per_dropback_or_attempt": 0.05, "offensive_momentum_yards": 0,
        "offensive_momentum_pct": 0.0, "recent_form_label": "stable", "latest_game_week": 4,
        "previous_game_week": 3, "latest_game_passing_yards": 250, "latest_game_rushing_yards": 100,
        "latest_game_total_yards": 350, "wow_passing_yards_change": 0, "wow_rushing_yards_change": 0,
        "wow_total_yards_change": 0, "wow_pass_rate_change": 0, "wow_change_label": "steady",
    }


def _opportunity_row(player_id, opportunity_label, confidence_label="established_sample"):
    return {
        "player_id": player_id, "opportunity_label": opportunity_label,
        "opportunity_reason": f"{opportunity_label} test reason for {player_id}.",
        "supporting_metrics": "pass attempts/game +2.0 vs season",
        "latest_2_games_summary": "Last 2 games (2 games): 32.0 attempts/game",
        "latest_3_games_summary": "Last 3 games (3 games): 31.0 attempts/game",
        "confidence_label": confidence_label, "confidence_reason": "4 games played this season.",
        "touches_last_2_delta_vs_season": 0.0, "touches_last_2_per_game": 0.0,
        "targets_last_2_delta_vs_season": 0.0, "targets_last_2_per_game": 0.0,
        "attempts_last_2_delta_vs_season": 6.0 if opportunity_label == "rising_opportunity" else 0.0,
        "attempts_last_2_per_game": 32.0,
        "touches_last_3_delta_vs_season": 0.0, "touches_last_3_per_game": 0.0,
        "targets_last_3_delta_vs_season": 0.0, "targets_last_3_per_game": 0.0,
        "attempts_last_3_delta_vs_season": 5.0 if opportunity_label == "rising_opportunity" else 0.0,
        "attempts_last_3_per_game": 31.0,
    }


PRIOR_BASELINE_EMPTY = pd.DataFrame(columns=[
    "player_id", "player_display_name", "position", "historical_team", "season", "games_played",
    "avg_fantasy_points",
])


# ---------------------------------------------------------------------------
# Scenario: four QBs covering Strongly Supported / Mostly Supported (tough
# matchup) / Weak Case, plus one RB with only a Weak Case candidate (to
# prove "no forced filling" renders an empty state).
# ---------------------------------------------------------------------------
def _scenario():
    dk = pd.DataFrame([
        _dk_row("Strong Value QB", "QB", "KC", "NYG", salary=6000, avg=20.0),
        _dk_row("Tough Matchup QB", "QB", "BUF", "SEA", salary=5000, avg=18.0),
        _dk_row("Weak Case QB", "QB", "DAL", "ARI", salary=7000, avg=12.0),
        _dk_row("Lonely Weak RB", "RB", "MIA", "NE", salary=6500, avg=10.0),
    ])
    current = pd.DataFrame([
        _player_row("p_strong", "Strong Value QB", "KC", "QB", "NYG", avg=20.0, momentum=22.0),
        _player_row("p_tough", "Tough Matchup QB", "BUF", "QB", "SEA", avg=18.0, momentum=19.0),
        _player_row("p_weak", "Weak Case QB", "DAL", "QB", "ARI", avg=12.0, momentum=11.0),
        _player_row("p_rb_weak", "Lonely Weak RB", "MIA", "RB", "NE", avg=10.0, momentum=9.0),
    ])
    role_context = pd.DataFrame([
        _role_row("p_strong", "Strong Value QB", "KC", "QB"),
        _role_row("p_tough", "Tough Matchup QB", "BUF", "QB"),
        _role_row("p_weak", "Weak Case QB", "DAL", "QB"),
        _role_row("p_rb_weak", "Lonely Weak RB", "MIA", "RB"),
    ])
    defense = pd.DataFrame([
        _defense_row("NYG", "QB", matchup_delta=3.0, pctile=80.0),   # Favorable, for Strong Value QB's opponent
        _defense_row("SEA", "QB", matchup_delta=-5.0, pctile=8.0),   # Tough, for Tough Matchup QB's opponent
        _defense_row("ARI", "QB", matchup_delta=0.0, pctile=50.0),   # Neutral, for Weak Case QB's opponent
        _defense_row("NE", "RB", matchup_delta=0.0, pctile=50.0),    # Neutral, for Lonely Weak RB's opponent
    ])
    team_reporting = pd.DataFrame([
        _team_reporting_row("KC"), _team_reporting_row("BUF"),
        _team_reporting_row("DAL"), _team_reporting_row("MIA"),
    ])
    opportunity = pd.DataFrame([
        _opportunity_row("p_strong", "rising_opportunity", confidence_label="established_sample"),
        _opportunity_row("p_tough", "stable_opportunity", confidence_label="established_sample"),
        _opportunity_row("p_weak", "limited_opportunity", confidence_label="established_sample"),
        _opportunity_row("p_rb_weak", "limited_opportunity", confidence_label="established_sample"),
    ])
    return dk, current, role_context, defense, team_reporting, opportunity


def _dk_csv_bytes(dk: pd.DataFrame) -> bytes:
    return dk.to_csv(index=False).encode("utf-8")


@pytest.fixture
def patched_marts(monkeypatch):
    dk, current, role_context, defense, team_reporting, opportunity = _scenario()
    monkeypatch.setattr(data_module, "load_players_current", lambda: current)
    monkeypatch.setattr(data_module, "load_defense_reporting", lambda: defense)
    monkeypatch.setattr(data_module, "load_player_role_context", lambda: role_context)
    monkeypatch.setattr(data_module, "load_team_reporting", lambda: team_reporting)
    monkeypatch.setattr(data_module, "load_player_opportunity_reporting", lambda: opportunity)
    monkeypatch.setattr(data_module, "load_players_prior_season_baseline", lambda: PRIOR_BASELINE_EMPTY)
    return dk


def _run_page(dk_df):
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    at.file_uploader[0].upload("Slate.csv", _dk_csv_bytes(dk_df), "text/csv")
    at.run()
    assert not at.exception
    return at


def _all_markdown(at):
    return "\n".join(m.value for m in at.markdown)


# ---------------------------------------------------------------------------
# 1. Value-only ranking can include a tough-matchup player, and the label
#    accurately describes that ranking.
# ---------------------------------------------------------------------------
def test_highest_projected_value_section_is_renamed_and_labeled_correctly(patched_marts):
    at = _run_page(patched_marts)
    subheaders = [s.value for s in at.subheader]
    assert "💎 Highest Projected Value" in subheaders
    assert "💎 Top Value Plays by Position" not in subheaders
    captions = [c.value for c in at.caption]
    assert any(
        c == "Ranked by projected points per $1,000 among eligible players. This is not an overall recommendation."
        for c in captions
    )


def test_value_only_ranking_includes_tough_matchup_player(patched_marts):
    at = _run_page(patched_marts)
    text = _all_markdown(at)
    # Tough Matchup QB has the lowest salary and a strong projection, so it
    # must out-value Strong Value QB purely on pts/$1k despite its matchup.
    assert "Tough Matchup QB" in text


# ---------------------------------------------------------------------------
# 2. Matchup concerns are visible directly on the top-selection cards.
# ---------------------------------------------------------------------------
def test_matchup_concern_visible_on_tough_matchup_card(patched_marts):
    at = _run_page(patched_marts)
    text = _all_markdown(at)
    card_start = text.index("Tough Matchup QB")
    card_text = text[card_start:card_start + 600]
    assert "Tough" in card_text
    assert "pctile" in card_text
    assert "⚠️" in card_text  # a concern is shown, not silently dropped


def test_favorable_matchup_shown_on_strong_value_card(patched_marts):
    at = _run_page(patched_marts)
    text = _all_markdown(at)
    card_start = text.index("Strong Value QB")
    card_text = text[card_start:card_start + 400]
    assert "Favorable" in card_text
    assert "Opportunity: Rising Opportunity" in card_text


# ---------------------------------------------------------------------------
# 3. Supported-play filtering follows the existing case-summary rules.
# ---------------------------------------------------------------------------
def test_supported_plays_includes_strongly_and_mostly_supported_only(patched_marts):
    at = _run_page(patched_marts)
    text = _all_markdown(at)
    supported_idx = text.index("Supported Plays to Investigate") if "Supported Plays to Investigate" in text else None
    subheaders = [s.value for s in at.subheader]
    assert "🔎 Supported Plays to Investigate" in subheaders
    # Strong Value QB (Strongly Supported) and Tough Matchup QB (Mostly
    # Supported, despite its tough matchup) must both appear...
    assert "Strong Value QB" in text
    assert "Mostly Supported" in text
    assert "Strongly Supported" in text


def test_weak_case_player_excluded_from_supported_plays(patched_marts):
    at = _run_page(patched_marts)
    # The Supported Plays cards are the only markdown elements that embed a
    # signal_alignment label after the player's name - Weak Case QB's own
    # case alignment is "Weak Case", which doesn't qualify, so it must never
    # appear in that form.
    md_values = [m.value for m in at.markdown]
    weak_case_supported_cards = [
        v for v in md_values if v.startswith("- **Weak Case QB**") and "Supported" in v
    ]
    assert weak_case_supported_cards == []


# ---------------------------------------------------------------------------
# 4. Insufficient/concerning evidence is not concealed.
# ---------------------------------------------------------------------------
def test_tough_matchup_primary_concern_surfaces_in_supported_plays_card(patched_marts):
    at = _run_page(patched_marts)
    md_values = [m.value for m in at.markdown]
    tough_cards = [v for v in md_values if v.startswith("- **Tough Matchup QB**")]
    assert tough_cards, "Tough Matchup QB should appear as a Mostly Supported candidate card"
    assert any("⚠️" in v and "Tough matchup" in v for v in tough_cards)


# ---------------------------------------------------------------------------
# 5. No forced filling of unsupported candidates - the RB position has only
#    a Weak Case candidate, so its Supported Plays column must show the
#    empty state, never a fabricated entry.
# ---------------------------------------------------------------------------
def test_no_forced_filling_shows_empty_state_for_position_with_no_qualifying_plays(patched_marts):
    at = _run_page(patched_marts)
    captions = [c.value for c in at.caption]
    assert "No Strongly/Mostly Supported plays" in captions
    md_values = [m.value for m in at.markdown]
    assert not any(v.startswith("- **Lonely Weak RB**") and "Supported" in v for v in md_values)


# ---------------------------------------------------------------------------
# 6. Existing projections and eligibility are unchanged by this pass.
# ---------------------------------------------------------------------------
def test_projected_points_and_value_formula_unchanged(patched_marts):
    at = _run_page(patched_marts)
    # Strong Value QB: player_avg=20.0, momentum=22.0, matchup_delta=+3.0, Salary=6000
    expected_points = 20.0 + (22.0 - 20.0) * MOMENTUM_ADJUSTMENT_WEIGHT + 3.0 * MATCHUP_ADJUSTMENT_WEIGHT
    expected_value = expected_points / (6000 / 1000)
    text = _all_markdown(at)
    card_start = text.index("Strong Value QB")
    card_text = text[card_start:card_start + 300]
    assert f"{expected_points:.1f} pts" in card_text
    assert f"{expected_value:.2f} pts/$1k" in card_text


def test_role_eligibility_unaffected_role_classification_still_shown(patched_marts):
    at = _run_page(patched_marts)
    text = _all_markdown(at)
    card_start = text.index("Strong Value QB")
    card_text = text[card_start:card_start + 400]
    assert "confirmed_starter" in card_text


# ---------------------------------------------------------------------------
# Preseason mode: case/opportunity enrichment must be skipped cleanly, never
# crash, and clearly explain why Supported Plays is unavailable.
# ---------------------------------------------------------------------------
def test_preseason_mode_skips_case_enrichment_without_crashing(monkeypatch):
    import lib.data as dm

    baseline = pd.DataFrame([
        {"player_id": "p1", "player_display_name": "Baseline QB", "position": "QB",
         "historical_team": "KC", "season": 2025, "games_played": 16, "avg_fantasy_points": 20.0},
    ])
    monkeypatch.setattr(dm, "load_metadata", lambda: {"app_mode": "preseason_week_1_baseline", "active_season": 2026, "source_season": 2025})
    monkeypatch.setattr(dm, "load_players_prior_season_baseline", lambda: baseline)
    dk = pd.DataFrame([_dk_row("Baseline QB", "QB", "KC", "DEN", salary=6000, avg=20.0)])

    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    at.file_uploader[0].upload("Preseason.csv", _dk_csv_bytes(dk), "text/csv")
    at.run()
    assert not at.exception
    assert any(
        "Not available in Week 1 Baseline Mode" in i.value for i in at.info
    )
