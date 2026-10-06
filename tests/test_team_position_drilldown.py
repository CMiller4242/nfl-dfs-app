import pandas as pd
import pytest

from lib.team_position_drilldown import (
    attach_current_context,
    attach_slate_case_summary,
    build_game_by_game_table,
    build_player_window_contributions,
    recent_window_weeks,
    team_position_recorded_weeks,
)


def _row(team, opp, pos, week, pid, name, fp, carries=0.0, targets=0.0, receptions=0.0):
    return {
        "team": team, "opponent_team": opp, "position": pos, "week": week, "player_id": pid,
        "player_display_name": name, "fantasy_points_ppr": fp, "carries": carries,
        "targets": targets, "receptions": receptions,
    }


def test_team_position_recorded_weeks_only_counts_weeks_with_a_row():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "A", 10.0),
        _row("KC", "DEN", "RB", 3, "p1", "A", 12.0),  # week 2 is a bye
    ])
    assert team_position_recorded_weeks(weekly, "KC", "RB") == [1, 3]


def test_recent_window_weeks_returns_last_n():
    weekly = pd.DataFrame([_row("KC", "X", "RB", w, "p1", "A", 10.0) for w in range(1, 6)])
    assert recent_window_weeks(weekly, "KC", "RB", n=3) == [3, 4, 5]


# ---------------------------------------------------------------------------
# Player totals reconcile with team-position totals; shares use correct
# denominators; zero/nonpositive guards.
# ---------------------------------------------------------------------------
def test_contributions_reconcile_to_team_position_total():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "Back One", 10.0, carries=15, targets=2),
        _row("KC", "BUF", "RB", 1, "p2", "Back Two", 8.0, carries=5, targets=1),
        _row("KC", "BAL", "RB", 2, "p1", "Back One", 12.0, carries=18, targets=1),
    ])
    weeks = team_position_recorded_weeks(weekly, "KC", "RB")
    contrib = build_player_window_contributions(weekly, "KC", "RB", weeks)
    assert contrib["total_fantasy_points"].sum() == pytest.approx(30.0)
    # points_per_team_game for Back One: 22 total / 2 distinct team games
    back_one = contrib[contrib.player_display_name == "Back One"].iloc[0]
    assert back_one["total_fantasy_points"] == pytest.approx(22.0)
    assert back_one["points_per_team_game"] == pytest.approx(11.0)
    assert back_one["points_per_appearance"] == pytest.approx(11.0)  # appeared both games


def test_share_of_team_position_points_denominator_is_position_total():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "Back One", 15.0),
        _row("KC", "BUF", "RB", 1, "p2", "Back Two", 5.0),
    ])
    contrib = build_player_window_contributions(weekly, "KC", "RB", [1])
    back_one = contrib[contrib.player_display_name == "Back One"].iloc[0]
    assert back_one["share_of_team_position_points"] == pytest.approx(75.0)


def test_team_target_share_denominator_is_full_team_not_just_position():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "Back One", 10.0, targets=2),
        _row("KC", "BUF", "WR", 1, "p3", "Wideout", 12.0, targets=6),
    ])
    contrib = build_player_window_contributions(weekly, "KC", "RB", [1])
    back_one = contrib[contrib.player_display_name == "Back One"].iloc[0]
    # full-team targets this week = 2 (RB) + 6 (WR) = 8; Back One's share = 2/8 = 25%
    assert back_one["team_target_share"] == pytest.approx(25.0)
    # but share of POSITION (RB-only) targets must be 100%, a different denominator
    assert back_one["share_of_team_position_targets"] == pytest.approx(100.0)


def test_zero_position_total_guards_share_as_unavailable_not_fabricated():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "TE", 1, "p1", "Blocking TE", 0.0, carries=0, targets=0),
    ])
    contrib = build_player_window_contributions(weekly, "KC", "TE", [1])
    row = contrib.iloc[0]
    assert pd.isna(row["share_of_team_position_points"])  # 0/0 total -> undefined, never 0% or 100%
    assert row["total_fantasy_points"] == 0.0  # raw points still retained


def test_includes_all_contributors_not_just_top_scorer():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "WR", 1, "p1", "WR1", 20.0),
        _row("KC", "BUF", "WR", 1, "p2", "WR2", 1.0),
        _row("KC", "BUF", "WR", 1, "p3", "WR3", 0.5),
    ])
    contrib = build_player_window_contributions(weekly, "KC", "WR", [1])
    assert set(contrib["player_display_name"]) == {"WR1", "WR2", "WR3"}


def test_duplicate_player_names_distinguished_by_player_id():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "WR", 1, "pid_a", "Same Name", 10.0),
        _row("DEN", "SEA", "WR", 1, "pid_b", "Same Name", 5.0),
    ])
    contrib_kc = build_player_window_contributions(weekly, "KC", "WR", [1])
    contrib_den = build_player_window_contributions(weekly, "DEN", "WR", [1])
    assert contrib_kc.iloc[0]["player_id"] == "pid_a"
    assert contrib_den.iloc[0]["player_id"] == "pid_b"
    assert contrib_kc.iloc[0]["total_fantasy_points"] != contrib_den.iloc[0]["total_fantasy_points"]


# ---------------------------------------------------------------------------
# Historical team attribution - never joined via current roster
# ---------------------------------------------------------------------------
def test_historical_team_attribution_for_a_traded_player():
    weekly = pd.DataFrame([
        _row("OLD", "X", "RB", 1, "p1", "Traded Back", 10.0),
        _row("NEW", "Y", "RB", 5, "p1", "Traded Back", 14.0),
    ])
    old_contrib = build_player_window_contributions(weekly, "OLD", "RB", [1])
    new_contrib = build_player_window_contributions(weekly, "NEW", "RB", [5])
    assert old_contrib.iloc[0]["total_fantasy_points"] == pytest.approx(10.0)
    assert old_contrib.iloc[0]["historical_team"] == "OLD"
    assert new_contrib.iloc[0]["total_fantasy_points"] == pytest.approx(14.0)
    assert new_contrib.iloc[0]["historical_team"] == "NEW"
    # Never double-counted if you asked for "OLD" team's totals.
    assert old_contrib.iloc[0]["total_fantasy_points"] != 24.0


def test_empty_weeks_returns_empty_frame_not_a_crash():
    weekly = pd.DataFrame([_row("KC", "BUF", "RB", 1, "p1", "A", 10.0)])
    out = build_player_window_contributions(weekly, "KC", "RB", [])
    assert out.empty


# ---------------------------------------------------------------------------
# Current context + restricted players remain visible
# ---------------------------------------------------------------------------
def test_attach_current_context_restricted_player_retains_restriction():
    weekly = pd.DataFrame([_row("KC", "BUF", "WR", 1, "p1", "Bench Guy", 3.0)])
    contrib = build_player_window_contributions(weekly, "KC", "WR", [1])
    role_context = pd.DataFrame([{
        "player_id": "p1", "canonical_team": "KC", "role_classification": "bench_no_clear_path",
        "role_data_freshness": "fresh", "role_eligible_for_pool": False,
        "role_eligible_for_top_values": False, "eligibility_reason": "No clear path - Starter is Healthy.",
    }])
    out = attach_current_context(contrib, role_context, pd.DataFrame())
    row = out.iloc[0]
    assert row["role_classification"] == "bench_no_clear_path"
    assert row["role_display"] == "No Clear Opportunity Path"
    assert "No clear path" in row["eligibility_reason"]


def test_attach_current_context_missing_role_data_is_null_not_guessed():
    weekly = pd.DataFrame([_row("KC", "BUF", "WR", 1, "p1", "Mystery Guy", 3.0)])
    contrib = build_player_window_contributions(weekly, "KC", "WR", [1])
    out = attach_current_context(contrib, pd.DataFrame(), pd.DataFrame())
    assert pd.isna(out.iloc[0]["role_classification"])


# ---------------------------------------------------------------------------
# Slate connect (Part 7) - reuses an already-built table, never fabricates
# ---------------------------------------------------------------------------
def test_attach_slate_case_summary_no_slate_marks_unavailable_but_keeps_row():
    weekly = pd.DataFrame([_row("KC", "BUF", "WR", 1, "p1", "A", 10.0)])
    contrib = build_player_window_contributions(weekly, "KC", "WR", [1])
    out = attach_slate_case_summary(contrib, None)
    assert out.iloc[0]["slate_data_available"] == False  # noqa: E712
    assert pd.isna(out.iloc[0]["slate_Salary"])
    assert out.iloc[0]["total_fantasy_points"] == 10.0  # historical row still intact


def test_attach_slate_case_summary_joins_by_shared_player_id():
    weekly = pd.DataFrame([_row("KC", "BUF", "WR", 1, "p1", "A", 10.0)])
    contrib = build_player_window_contributions(weekly, "KC", "WR", [1])
    slate_table = pd.DataFrame([{
        "stat_player_id": "p1", "Salary": 7000, "projected_points": 15.0, "projected_value": 2.1,
        "signal_alignment": "Mostly Supported", "case_summary": "test",
    }])
    out = attach_slate_case_summary(contrib, slate_table)
    assert out.iloc[0]["slate_data_available"] == True  # noqa: E712
    assert out.iloc[0]["slate_Salary"] == 7000
    assert out.iloc[0]["slate_signal_alignment"] == "Mostly Supported"


# ---------------------------------------------------------------------------
# Game-by-game (Part 6)
# ---------------------------------------------------------------------------
def test_game_by_game_table_shows_every_contributor_per_week_separately():
    weekly = pd.DataFrame([
        _row("KC", "BUF", "RB", 1, "p1", "A", 10.0),
        _row("KC", "BUF", "RB", 1, "p2", "B", 5.0),
        _row("KC", "BAL", "RB", 2, "p1", "A", 7.0),
    ])
    gbg = build_game_by_game_table(weekly, "KC", "RB", [1, 2])
    assert len(gbg) == 3
    week1 = gbg[gbg.week == 1]
    assert set(week1["player_display_name"]) == {"A", "B"}
