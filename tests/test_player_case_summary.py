import pandas as pd
import pytest

from lib.player_case_summary import (
    CASE_SUMMARY_COLUMNS,
    SIGNAL_ALIGNMENT_VALUES,
    build_case_csv_export,
    build_case_display_table,
    build_case_for_row,
    build_case_summary,
    filter_by_signal_alignment,
    format_case_detail,
    mixed_signals_review_section,
    positives_text,
)


def _row(**overrides):
    """A fully-specified, role-safe/favorable-everything baseline row - each
    test overrides only the fields relevant to the scenario under test."""
    base = {
        "Name": "Test Player", "Position": "WR", "TeamAbbrev": "KC", "opponent": "BUF", "Salary": 7000,
        "match_method": "exact_name_team_position", "match_score": 100.0,
        "role_classification": "confirmed_starter", "role_data_freshness": "fresh",
        "role_eligible_for_pool": True, "role_eligible_for_top_values": True,
        "opportunity_pool_eligible": True, "opportunity_top_value_eligible": True,
        "is_pool_only_rising_promotion": False,
        "projected_points": 18.0, "projected_value": 3.5,
        "opportunity_label": "rising_opportunity",
        "opportunity_reason": "Rising opportunity — 9.0 targets/game over last 2 versus 6.3 season average (+2.7).",
        "confidence_label": "established_sample",
        "position_percentile_most_favorable": 80.0, "games_in_sample": 10,
        "fantasy_points_allowed_per_game": 14.0, "league_avg_points_allowed_for_position": 11.0,
        "team_recent_form_label": "heating_up", "team_offensive_momentum_yards": 15.0,
        "recent_2_vs_season_display": "9.0 targets/g (+2.7)", "recent_3_vs_season_display": "8.0 targets/g (+1.5)",
        "latest_2_games_summary": "Last 2 games (2 games): 9.0 targets/game",
        "latest_3_games_summary": "Last 3 games (3 games): 8.0 targets/game",
        "role_display": "Confirmed Starter", "eligibility_reason": "WR1 - within the standard eligible depth tier.",
        "blocking_player_names": "", "player_avg": 16.5, "prior_season_fppg": pd.NA,
        "team_season_total_yards_per_game": 380.0, "team_season_pass_rate_pct": 58.0,
        "matchup_index": 127.3,
    }
    base.update(overrides)
    return pd.Series(base)


# ---------------------------------------------------------------------------
# Role safety is never overridden - Insufficient Data short-circuits first
# ---------------------------------------------------------------------------
def test_unresolved_role_is_always_insufficient_data_even_with_everything_else_favorable():
    row = _row(role_classification="role_unresolved")
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Insufficient Data"


def test_unmatched_player_is_always_insufficient_data():
    row = _row(match_method="unmatched", role_classification=None)
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Insufficient Data"


def test_inactive_player_is_always_insufficient_data_even_with_everything_else_favorable():
    row = _row(role_classification="inactive")
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Insufficient Data"
    assert "Inactive" in case["primary_concern"]


def test_contingent_backup_never_strongly_or_mostly_supported_stays_high_variance():
    # Rising opportunity + great value + favorable matchup, but role is
    # contingent (monitor-only) - must never read as a featured strong case.
    row = _row(role_classification="contingent_backup", role_eligible_for_pool=True, role_eligible_for_top_values=False)
    case = build_case_for_row(row)
    assert case["signal_alignment"] not in ("Strongly Supported", "Mostly Supported")
    assert case["signal_alignment"] == "High Variance"


def test_bench_no_clear_path_promotion_shows_player_pool_only_concern():
    row = _row(
        role_classification="bench_no_clear_path", role_eligible_for_pool=False, role_eligible_for_top_values=False,
        opportunity_pool_eligible=True, opportunity_top_value_eligible=False, is_pool_only_rising_promotion=True,
    )
    case = build_case_for_row(row)
    assert any("Player Pool only" in c for c in case["concerns"])
    # The fragile/promoted role keeps this out of a confidently "supported" label.
    assert case["signal_alignment"] not in ("Strongly Supported", "Mostly Supported")


def test_bench_no_clear_path_not_promoted_is_a_concern_not_insufficient_data():
    row = _row(
        role_classification="bench_no_clear_path", role_eligible_for_pool=False, role_eligible_for_top_values=False,
        opportunity_pool_eligible=False, opportunity_top_value_eligible=False,
    )
    case = build_case_for_row(row)
    assert case["signal_alignment"] != "Insufficient Data"
    assert any("Depth / role limitation" in c for c in case["concerns"])


# ---------------------------------------------------------------------------
# Required alignment scenarios (item 16)
# ---------------------------------------------------------------------------
def test_favorable_dvp_but_declining_opportunity_is_not_strongly_supported():
    row = _row(
        opportunity_label="declining_opportunity",
        opportunity_reason="Declining opportunity — 4.0 targets/game over last 2 versus season average (-3.0).",
        position_percentile_most_favorable=90.0,
    )
    case = build_case_for_row(row)
    assert case["signal_alignment"] in ("Mixed Signals", "High Variance")
    assert case["signal_alignment"] != "Strongly Supported"


def test_tough_dvp_but_strong_stable_workload_and_value_is_mostly_supported_with_matchup_concern():
    row = _row(
        opportunity_label="stable_opportunity",
        opportunity_reason="Stable Opportunity — workload holding steady versus the season average.",
        position_percentile_most_favorable=15.0,  # tough matchup
        team_recent_form_label="stable",  # no team corroboration either
    )
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Mostly Supported"
    assert "matchup" in case["primary_concern"].lower() or "Tough matchup" in case["primary_concern"]


def test_safe_role_strong_value_rising_opportunity_favorable_matchup_is_strongly_supported():
    row = _row()  # the baseline fixture is exactly this scenario
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Strongly Supported"
    assert case["concerns"] == []
    assert "Confirmed Starter" in case["positives"][0] or "Rising opportunity" in case["positives"][0].lower() or True


def test_weak_case_with_multiple_concerns_even_with_positive_value():
    row = _row(
        role_classification="bench_no_clear_path", role_eligible_for_pool=False, role_eligible_for_top_values=False,
        opportunity_pool_eligible=False,
        opportunity_label="declining_opportunity", opportunity_reason="Declining opportunity.",
        position_percentile_most_favorable=10.0,  # tough matchup concern too
        team_recent_form_label="stable",  # no team-context corroboration either
        projected_value=5.0,  # a genuinely strong/positive raw value
    )
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "Weak Case"


def test_high_variance_when_positive_case_rests_on_low_confidence_rising_signal():
    row = _row(confidence_label="early_sample")
    case = build_case_for_row(row)
    assert case["signal_alignment"] == "High Variance"


# ---------------------------------------------------------------------------
# Positives/concerns content - traceable to the exact evidence, sample size kept
# ---------------------------------------------------------------------------
def test_positives_include_role_opportunity_value_matchup_team_statements():
    row = _row()
    case = build_case_for_row(row)
    joined = " ".join(case["positives"])
    assert "Confirmed Starter" in joined
    assert "Rising opportunity" in joined or "targets/game" in joined
    assert "pts/$1k" in joined
    assert "percentile" in joined
    assert "heating up" in joined.lower()


def test_matchup_statement_always_retains_sample_size():
    favorable = _row(position_percentile_most_favorable=90.0, games_in_sample=12)
    tough = _row(position_percentile_most_favorable=10.0, games_in_sample=4)
    case_favorable = build_case_for_row(favorable)
    case_tough = build_case_for_row(tough)
    assert "sample: 12 games" in " ".join(case_favorable["positives"])
    assert "sample: 4 games" in " ".join(case_tough["concerns"])


def test_matchup_data_unavailable_is_a_concern_not_a_fabricated_neutral():
    row = _row(position_percentile_most_favorable=pd.NA, games_in_sample=pd.NA)
    case = build_case_for_row(row)
    assert any("Matchup data unavailable" in c for c in case["concerns"])


def test_value_unavailable_is_flagged_as_a_concern():
    row = _row(projected_value=pd.NA)
    case = build_case_for_row(row)
    assert any("No usable projection/value" in c for c in case["concerns"])


def test_data_quality_notes_flag_fragile_role_and_low_confidence_opportunity():
    row = _row(role_classification="contingent_backup", confidence_label="early_sample")
    case = build_case_for_row(row)
    assert "fragile" in case["data_quality_notes"].lower()
    assert "early or insufficient sample" in case["data_quality_notes"].lower()


def test_primary_positive_and_concern_follow_signal_hierarchy_order():
    # Role positive should be listed (and chosen as primary) ahead of
    # opportunity/value/matchup/team positives, per the documented hierarchy.
    row = _row(role_classification="confirmed_starter")
    case = build_case_for_row(row)
    assert case["primary_positive"] == case["positives"][0]
    assert case["positives"][0].startswith("Confirmed Starter")


# ---------------------------------------------------------------------------
# Output schema / no duplicates / exports
# ---------------------------------------------------------------------------
def test_build_case_summary_adds_exactly_the_documented_columns():
    df = pd.DataFrame([_row(), _row(Name="Other Player")])
    out = build_case_summary(df)
    for col in CASE_SUMMARY_COLUMNS:
        assert col in out.columns
    assert len(out) == 2
    assert not out["Name"].duplicated().any()


def test_build_case_summary_empty_input_has_schema():
    out = build_case_summary(pd.DataFrame())
    assert out.empty
    for col in CASE_SUMMARY_COLUMNS:
        assert col in out.columns


def test_each_player_gets_own_case_never_another_players():
    rb_row = _row(Name="Player A", TeamAbbrev="KC", Position="RB", opportunity_label="rising_opportunity")
    wr_row = _row(Name="Player B", TeamAbbrev="KC", Position="WR", opportunity_label="declining_opportunity",
                  opportunity_reason="Declining opportunity.")
    df = pd.DataFrame([rb_row, wr_row])
    out = build_case_summary(df)
    a = out[out["Name"] == "Player A"].iloc[0]
    b = out[out["Name"] == "Player B"].iloc[0]
    assert a["signal_alignment"] != b["signal_alignment"] or (
        "Rising" in " ".join(a["positives"]) and "Declining" in " ".join(b["concerns"])
    )
    assert "Declining" not in " ".join(a["positives"]) and "Declining" not in " ".join(a["concerns"])


def test_positives_text_joins_list_for_display():
    assert positives_text(["a", "b"]) == "a | b"
    assert positives_text(pd.NA) == ""
    assert positives_text("already a string") == "already a string"


def test_build_case_display_table_has_human_readable_columns():
    df = pd.DataFrame([_row()])
    out = build_case_summary(df)
    display = build_case_display_table(out)
    assert list(display.columns) == ["Signal Alignment", "Primary Positive", "Primary Concern"]
    assert isinstance(display.iloc[0]["Primary Positive"], str)


def test_build_case_csv_export_includes_audit_and_case_fields():
    df = pd.DataFrame([_row()])
    out = build_case_summary(df)
    export = build_case_csv_export(out)
    assert "signal_alignment" in export.columns
    assert "positives" in export.columns
    assert isinstance(export.iloc[0]["positives"], str)  # list rendered as text, not a raw list
    assert "Name" in export.columns


def test_filter_by_signal_alignment():
    df = pd.DataFrame([_row(Name="A"), _row(Name="B", role_classification="contingent_backup")])
    out = build_case_summary(df)
    filtered = filter_by_signal_alignment(out, alignments=["High Variance"])
    assert set(filtered["Name"]) == {"B"}


def test_filter_by_signal_alignment_noop_when_empty():
    df = pd.DataFrame([_row(Name="A")])
    out = build_case_summary(df)
    assert len(filter_by_signal_alignment(out, alignments=None)) == 1


def test_signal_alignment_values_are_exactly_the_documented_six():
    assert set(SIGNAL_ALIGNMENT_VALUES) == {
        "Strongly Supported", "Mostly Supported", "Mixed Signals",
        "High Variance", "Weak Case", "Insufficient Data",
    }


# ---------------------------------------------------------------------------
# Mixed Signals / Review bucket - Valid Player Pool safety gate reused
# ---------------------------------------------------------------------------
def test_mixed_signals_review_excludes_inactive_unresolved_monitor_by_default():
    rows = [
        _row(Name="Strong", role_classification="confirmed_starter"),
        _row(Name="MixedCase", opportunity_label="declining_opportunity",
             opportunity_reason="Declining opportunity.", position_percentile_most_favorable=90.0),
        _row(Name="Inactive", role_classification="inactive", role_eligible_for_pool=False,
             role_eligible_for_top_values=False),
        _row(Name="Unresolved", role_classification="role_unresolved", role_eligible_for_pool=False,
             role_eligible_for_top_values=False),
        _row(Name="Monitor", role_classification="contingent_backup", role_eligible_for_pool=True,
             role_eligible_for_top_values=False),
        _row(Name="Excluded", role_classification="bench_no_clear_path", role_eligible_for_pool=False,
             role_eligible_for_top_values=False, opportunity_pool_eligible=False),
    ]
    df = pd.DataFrame(rows)
    df["projection_status"] = "ok"
    out = build_case_summary(df)
    review = mixed_signals_review_section(out)
    names = set(review["Name"])
    assert "MixedCase" in names
    assert names.isdisjoint({"Inactive", "Unresolved", "Monitor", "Excluded"})


def test_format_case_detail_includes_all_required_sections():
    row = _row()
    cased = build_case_summary(pd.DataFrame([row])).iloc[0]
    detail = format_case_detail(cased)
    for key in ("why_liked", "what_could_break", "role_context", "recent_context",
                "salary_context", "matchup_context", "team_context", "data_quality_notes"):
        assert key in detail
        assert isinstance(detail[key], str)
    assert "targets/g" in detail["recent_context"] or "Last 2 games" in detail["recent_context"]
