import pandas as pd
import pytest

from lib.player_comparison import (
    MAX_COMPARISON_PLAYERS,
    REQUIRED_COMPARISON_COLUMNS,
    attach_row_uid,
    build_comparison_table,
    candidate_players,
    comparison_label,
    player_section_label,
    resolve_selected_uids,
    slate_row_uid,
)


def _row(**overrides):
    base = {
        "Name": "Test Player", "TeamAbbrev": "KC", "Position": "WR", "Salary": 7000,
        "projected_points": 15.0, "projected_value": 2.5,
        "role_classification": "confirmed_starter", "role_display": "Confirmed Starter",
        "role_data_freshness": "fresh", "role_eligible_for_pool": True, "role_eligible_for_top_values": True,
        "is_conditional_monitor": False, "eligibility_reason": "WR1 - within the standard eligible tier.",
        "projection_status": "ok", "match_method": "exact_name_team_position", "match_score": 100.0,
        "best_candidate_name": pd.NA, "opponent": "BUF",
        "opportunity_label": "rising_opportunity", "opportunity_label_display": "Rising Opportunity",
        "opportunity_reason": "Rising opportunity test reason.", "confidence_label": "established_sample",
        "opportunity_confidence_display": "Established Sample",
        "position_percentile_most_favorable": 80.0, "fantasy_points_allowed_per_game": 14.0,
        "defensive_games_played": 6, "team_recent_form_label": "heating_up",
        "team_offensive_momentum_yards": 15.0, "signal_alignment": "Strongly Supported",
        "role_player_id": "role_1", "stat_player_id": "stat_1",
    }
    base.update(overrides)
    return pd.Series(base)


def _df(*rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Stable identifiers - never a bare name
# ---------------------------------------------------------------------------
def test_slate_row_uid_distinguishes_duplicate_names_by_team_and_position():
    a = _row(Name="Josh Allen", TeamAbbrev="BUF", Position="QB", Salary=7700)
    b = _row(Name="Josh Allen", TeamAbbrev="JAX", Position="LB", Salary=4000)
    assert slate_row_uid(a) != slate_row_uid(b)


def test_slate_row_uid_is_deterministic_for_the_same_row():
    a = _row()
    assert slate_row_uid(a) == slate_row_uid(a.copy())


def test_attach_row_uid_safe_on_empty_frame():
    out = attach_row_uid(pd.DataFrame())
    assert "slate_row_uid" in out.columns
    assert out.empty


def test_comparison_label_always_shows_team_and_position():
    label = comparison_label(_row(Name="Josh Allen", TeamAbbrev="BUF", Position="QB", Salary=7700))
    assert "Josh Allen" in label
    assert "BUF" in label
    assert "QB" in label


def test_comparison_label_distinguishes_duplicate_names():
    a = comparison_label(_row(Name="Josh Allen", TeamAbbrev="BUF", Position="QB", Salary=7700))
    b = comparison_label(_row(Name="Josh Allen", TeamAbbrev="JAX", Position="LB", Salary=4000))
    assert a != b


def test_comparison_label_handles_missing_name_team_salary_without_crashing():
    label = comparison_label(_row(Name=pd.NA, TeamAbbrev=pd.NA, Salary=pd.NA))
    assert "Unknown Player" in label
    assert "salary unavailable" in label


# ---------------------------------------------------------------------------
# Candidate filtering - same-position default, never excludes by eligibility
# ---------------------------------------------------------------------------
def test_candidate_players_filters_by_position():
    df = _df(_row(Name="A", Position="WR"), _row(Name="B", Position="RB"))
    out = candidate_players(df, "WR")
    assert set(out["Name"]) == {"A"}


def test_candidate_players_all_lifts_the_position_restriction():
    df = _df(_row(Name="A", Position="WR"), _row(Name="B", Position="RB"))
    out = candidate_players(df, "All")
    assert set(out["Name"]) == {"A", "B"}


def test_candidate_players_includes_restricted_and_inactive_players():
    df = _df(
        _row(Name="Bench Guy", Position="WR", role_classification="bench_no_clear_path",
             role_eligible_for_pool=False, role_eligible_for_top_values=False),
        _row(Name="Hurt Guy", Position="WR", role_classification="inactive",
             role_eligible_for_pool=False, role_eligible_for_top_values=False),
    )
    out = candidate_players(df, "WR")
    assert {"Bench Guy", "Hurt Guy"}.issubset(set(out["Name"]))


# ---------------------------------------------------------------------------
# Stale-selection resolution (slate change safety)
# ---------------------------------------------------------------------------
def test_resolve_selected_uids_splits_valid_and_stale():
    df = attach_row_uid(_df(_row(Name="A"), _row(Name="B")))
    a_uid = df[df["Name"] == "A"]["slate_row_uid"].iloc[0]
    valid, stale = resolve_selected_uids(df, [a_uid, "nonexistent-uid"])
    assert valid == [a_uid]
    assert stale == ["nonexistent-uid"]


def test_resolve_selected_uids_preserves_input_order():
    df = attach_row_uid(_df(_row(Name="A"), _row(Name="B"), _row(Name="C")))
    uids = list(df["slate_row_uid"])
    reordered = [uids[2], uids[0], uids[1]]
    valid, stale = resolve_selected_uids(df, reordered)
    assert valid == reordered
    assert stale == []


def test_resolve_selected_uids_on_empty_current_slate_marks_everything_stale():
    empty = attach_row_uid(pd.DataFrame())
    valid, stale = resolve_selected_uids(empty, ["some-uid"])
    assert valid == []
    assert stale == ["some-uid"]


# ---------------------------------------------------------------------------
# Eligibility section - reused from lib.matchup_analyzer, never reimplemented
# ---------------------------------------------------------------------------
def test_player_section_label_inactive():
    assert player_section_label(_row(role_classification="inactive")) == "Inactive"


def test_player_section_label_excluded_by_role_context():
    row = _row(role_classification="bench_no_clear_path", role_eligible_for_pool=False, role_eligible_for_top_values=False)
    assert player_section_label(row) == "Excluded by Role Context"


def test_player_section_label_plays_to_monitor():
    row = _row(role_classification="contingent_backup", role_eligible_for_top_values=False, is_conditional_monitor=True)
    assert player_section_label(row) == "Plays to Monitor"


def test_player_section_label_featured_top_value():
    row = _row(role_eligible_for_top_values=True, is_conditional_monitor=False)
    assert player_section_label(row) == "Featured / Top Value"


def test_player_section_label_needs_review_when_unmatched():
    row = _row(match_method="unmatched", projection_status="review_required")
    assert player_section_label(row) == "Needs Review"


# ---------------------------------------------------------------------------
# Comparison table - formatting, missing values, no new score/winner
# ---------------------------------------------------------------------------
def test_build_comparison_table_empty_selection_returns_empty_frame():
    assert build_comparison_table(pd.DataFrame()).empty


def test_build_comparison_table_missing_values_render_as_unavailable_not_zero():
    row = _row(
        projected_points=pd.NA, projected_value=pd.NA, defensive_games_played=pd.NA,
        fantasy_points_allowed_per_game=pd.NA, team_offensive_momentum_yards=pd.NA,
    )
    out = build_comparison_table(pd.DataFrame([row]))
    col = out.columns[0]
    assert out.loc["Projected Fantasy Points", col] == "Unavailable"
    assert out.loc["Projected Value (pts/$1k)", col] == "Unavailable"
    assert out.loc["Distinct Defensive Games in Sample", col] == "Unavailable"
    assert out.loc["Avg PPR / Opposing Player Appearance", col] == "Unavailable"
    assert out.loc["Team Offensive Momentum (yds)", col] == "Unavailable"
    # Never a fabricated zero anywhere in the missing cells.
    assert "0" not in out.loc["Projected Fantasy Points", col]


def test_build_comparison_table_real_values_formatted_with_units():
    row = _row(Salary=7500, projected_points=18.3, projected_value=2.44)
    out = build_comparison_table(pd.DataFrame([row]))
    col = out.columns[0]
    assert out.loc["Salary", col] == "$7,500"
    assert out.loc["Projected Fantasy Points", col] == "18.3"
    assert out.loc["Projected Value (pts/$1k)", col] == "2.44"


def test_build_comparison_table_has_one_column_per_selected_player():
    rows = pd.DataFrame([_row(Name="A"), _row(Name="B"), _row(Name="C"), _row(Name="D")])
    out = build_comparison_table(rows)
    assert len(out.columns) == 4


def test_build_comparison_table_includes_eligibility_section_and_case_classification():
    row = _row(role_classification="bench_no_clear_path", role_eligible_for_pool=False, role_eligible_for_top_values=False,
               signal_alignment="Weak Case")
    out = build_comparison_table(pd.DataFrame([row]))
    col = out.columns[0]
    assert out.loc["Eligibility Section", col] == "Excluded by Role Context"
    assert out.loc["Signal Alignment (Case Summary)", col] == "Weak Case"


def test_build_comparison_table_never_introduces_a_ranking_or_winner_row():
    rows = pd.DataFrame([_row(Name="A", projected_value=5.0), _row(Name="B", projected_value=1.0)])
    out = build_comparison_table(rows)
    forbidden = {"rank", "winner", "score", "best", "recommended"}
    index_text = " ".join(str(i).lower() for i in out.index)
    assert not any(word in index_text for word in forbidden)


def test_comparison_metrics_count_matches_max_players_limit_is_independent():
    # Sanity: MAX_COMPARISON_PLAYERS is a UI selection cap, unrelated to how
    # many metric rows exist - just confirms the constant is a small, sane int.
    assert MAX_COMPARISON_PLAYERS == 4


def test_required_comparison_columns_is_a_nonempty_list_of_real_field_names():
    assert len(REQUIRED_COMPARISON_COLUMNS) > 5
    assert "defensive_games_played" in REQUIRED_COMPARISON_COLUMNS
    assert "signal_alignment" in REQUIRED_COMPARISON_COLUMNS
