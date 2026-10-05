import pandas as pd
import pytest

from lib.opportunity_model import (
    build_player_opportunity_reporting,
    compute_role_safety_gate,
    primary_workload_last_2,
)


def _weekly_row(player_id, week, position="RB", team="KC", season=2026, carries=10, targets=2,
                receptions=1, rushing_yards=40, receiving_yards=10, receiving_air_yards=8,
                receiving_yards_after_catch=4, target_share=0.1, air_yards_share=0.08,
                attempts=0, passing_yards=0, passing_tds=0, fantasy_points_ppr=10.0,
                player_display_name="Test Player"):
    touches = carries + targets
    return {
        "player_id": player_id, "player_name": player_display_name, "player_display_name": player_display_name,
        "position": position, "position_group": position, "season": season, "week": week,
        "season_type": "REG", "team": team, "opponent_team": "BUF",
        "completions": 0, "attempts": attempts, "passing_yards": passing_yards, "passing_tds": passing_tds,
        "passing_interceptions": 0, "carries": carries, "rushing_yards": rushing_yards, "rushing_tds": 0,
        "receptions": receptions, "targets": targets, "receiving_yards": receiving_yards, "receiving_tds": 0,
        "receiving_yards_after_catch": receiving_yards_after_catch, "receiving_air_yards": receiving_air_yards,
        "target_share": target_share, "air_yards_share": air_yards_share,
        "fantasy_points": fantasy_points_ppr, "fantasy_points_ppr": fantasy_points_ppr, "touches": touches,
    }


def _weekly_df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Last-2/last-3 windowing across byes, safe/null handling
# ---------------------------------------------------------------------------
def test_last_2_uses_most_recent_played_games_across_a_bye():
    rows = [
        _weekly_row("p1", 1, carries=10),
        _weekly_row("p1", 2, carries=12),
        # week 3 bye - simply absent
        _weekly_row("p1", 4, carries=20),
        _weekly_row("p1", 5, carries=22),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out[out["player_id"] == "p1"].iloc[0]
    assert row["last_2_games_used"] == 2
    assert row["carries_last_2_total"] == 42  # weeks 4 + 5, NOT week 2+4 or calendar-based
    assert row["carries_last_2_per_game"] == pytest.approx(21.0)


def test_games_used_reflects_actual_available_games_not_fabricated_full_window():
    rows = [_weekly_row("p1", 1, carries=10), _weekly_row("p1", 2, carries=14)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["last_3_games_used"] == 2  # only 2 games exist, never padded to 3
    assert row["carries_last_3_total"] == 24


def test_safe_division_never_infinite_with_zero_targets():
    rows = [_weekly_row("p1", 1, targets=0, receptions=0, receiving_yards=0),
            _weekly_row("p1", 2, targets=0, receptions=0, receiving_yards=0)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert pd.isna(row["catch_rate_last_2"])
    assert pd.isna(row["yards_per_target_last_2"])


def test_catch_rate_and_yards_per_target_use_totals_not_average_of_game_rates():
    # Game 1: 1/1 target (100%), Game 2: 0/9 targets (0%) - average-of-rates
    # would be 50%; totals-based is 1/10 = 10%.
    rows = [
        _weekly_row("p1", 1, targets=1, receptions=1, receiving_yards=15),
        _weekly_row("p1", 2, targets=9, receptions=0, receiving_yards=0),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["catch_rate_last_2"] == pytest.approx(1 / 10)
    assert row["yards_per_target_last_2"] == pytest.approx(15 / 10)
    assert row["yards_per_reception_last_2"] == pytest.approx(15 / 1)


# ---------------------------------------------------------------------------
# Season-vs-recent deltas
# ---------------------------------------------------------------------------
def test_season_vs_recent_delta_for_touches_and_targets():
    rows = [
        _weekly_row("p1", 1, carries=10, targets=2),
        _weekly_row("p1", 2, carries=14, targets=3),
        _weekly_row("p1", 3, carries=20, targets=5),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    season_touches_pg = row["touches_season_per_game"]
    last2_touches_pg = row["touches_last_2_per_game"]
    assert row["touches_last_2_delta_vs_season"] == pytest.approx(last2_touches_pg - season_touches_pg)
    assert row["target_share_last_2_delta_vs_season_pct_points"] == pytest.approx(
        row["target_share_last_2_pct"] - row["target_share_season_pct"]
    )


def test_delta_is_null_when_metric_entirely_absent_from_source():
    # A weekly frame missing a column entirely (e.g. an older/minimal
    # fixture) must never fabricate a 0/false delta for it.
    rows = [_weekly_row("p1", 1, carries=10), _weekly_row("p1", 2, carries=12), _weekly_row("p1", 3, carries=14)]
    df = _weekly_df(rows).drop(columns=["receiving_air_yards"])
    out = build_player_opportunity_reporting(df)
    row = out.iloc[0]
    assert pd.isna(row["receiving_air_yards_last_2_delta_vs_season"])


def test_classification_insufficient_sample_never_fabricates_a_trend_with_one_game():
    rows = [_weekly_row("p1", 1, carries=10)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "insufficient_sample"
    assert row["supporting_metrics"] == ""
    assert row["latest_3_games_summary"] == ""


# ---------------------------------------------------------------------------
# RB classification
# ---------------------------------------------------------------------------
def test_rb_rising_opportunity_on_touches_increase():
    # Season average includes the last-2 games too (same convention as the
    # rest of the app's momentum/WoW metrics) - so the jump needs to be
    # large enough to still clear the absolute threshold after dilution.
    rows = [
        _weekly_row("p1", 1, position="RB", carries=4, targets=1),
        _weekly_row("p1", 2, position="RB", carries=16, targets=4),
        _weekly_row("p1", 3, position="RB", carries=20, targets=5),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "rising_opportunity"
    assert "Rising opportunity" in row["opportunity_reason"]


def test_rb_declining_opportunity_on_touches_decrease():
    rows = [
        _weekly_row("p1", 1, position="RB", carries=20, targets=5),
        _weekly_row("p1", 2, position="RB", carries=16, targets=4),
        _weekly_row("p1", 3, position="RB", carries=4, targets=1),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "declining_opportunity"


def test_rb_stable_opportunity_when_flat():
    rows = [
        _weekly_row("p1", 1, position="RB", carries=15, targets=3),
        _weekly_row("p1", 2, position="RB", carries=15, targets=3),
        _weekly_row("p1", 3, position="RB", carries=15, targets=3),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "stable_opportunity"


def test_rb_limited_opportunity_below_touches_floor():
    rows = [
        _weekly_row("p1", 1, position="RB", carries=2, targets=0),
        _weekly_row("p1", 2, position="RB", carries=2, targets=0),
        _weekly_row("p1", 3, position="RB", carries=2, targets=0),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "limited_opportunity"


# ---------------------------------------------------------------------------
# WR/TE classification
# ---------------------------------------------------------------------------
def test_wr_rising_opportunity_on_target_share_increase():
    rows = [
        _weekly_row("p1", 1, position="WR", carries=0, targets=3, receptions=2, receiving_yards=25,
                    target_share=0.08, air_yards_share=0.07),
        _weekly_row("p1", 2, position="WR", carries=0, targets=10, receptions=7, receiving_yards=95,
                    target_share=0.30, air_yards_share=0.28),
        _weekly_row("p1", 3, position="WR", carries=0, targets=12, receptions=8, receiving_yards=110,
                    target_share=0.35, air_yards_share=0.33),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "rising_opportunity"
    assert "targets/game" in row["opportunity_reason"]


def test_wr_declining_opportunity_on_target_share_decrease():
    rows = [
        _weekly_row("p1", 1, position="WR", carries=0, targets=12, receptions=8, receiving_yards=110,
                    target_share=0.35, air_yards_share=0.33),
        _weekly_row("p1", 2, position="WR", carries=0, targets=10, receptions=7, receiving_yards=95,
                    target_share=0.30, air_yards_share=0.28),
        _weekly_row("p1", 3, position="WR", carries=0, targets=1, receptions=0, receiving_yards=0,
                    target_share=0.05, air_yards_share=0.04),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "declining_opportunity"


def test_te_stable_opportunity_when_flat():
    rows = [
        _weekly_row("p1", 1, position="TE", carries=0, targets=5, receptions=4, receiving_yards=40,
                    target_share=0.15, air_yards_share=0.12),
        _weekly_row("p1", 2, position="TE", carries=0, targets=5, receptions=4, receiving_yards=42,
                    target_share=0.15, air_yards_share=0.12),
        _weekly_row("p1", 3, position="TE", carries=0, targets=5, receptions=4, receiving_yards=38,
                    target_share=0.15, air_yards_share=0.12),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "stable_opportunity"


# ---------------------------------------------------------------------------
# QB classification (confirmed source fields only: attempts, carries)
# ---------------------------------------------------------------------------
def test_qb_rising_opportunity_on_attempts_increase():
    rows = [
        _weekly_row("p1", 1, position="QB", carries=2, targets=0, attempts=20, passing_yards=220),
        _weekly_row("p1", 2, position="QB", carries=2, targets=0, attempts=30, passing_yards=240),
        _weekly_row("p1", 3, position="QB", carries=2, targets=0, attempts=40, passing_yards=300),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "rising_opportunity"
    assert "pass attempts/game" in row["opportunity_reason"]


def test_qb_declining_opportunity_on_rush_attempts_decrease():
    rows = [
        _weekly_row("p1", 1, position="QB", carries=12, targets=0, attempts=30, passing_yards=220),
        _weekly_row("p1", 2, position="QB", carries=10, targets=0, attempts=30, passing_yards=220),
        _weekly_row("p1", 3, position="QB", carries=0, targets=0, attempts=30, passing_yards=220),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "declining_opportunity"


def test_qb_classification_never_uses_passing_yards_alone():
    # Big passing-yards outlier (a 400-yard game) but flat attempts/rush -
    # must NOT be read as rising opportunity.
    rows = [
        _weekly_row("p1", 1, position="QB", carries=2, targets=0, attempts=30, passing_yards=220),
        _weekly_row("p1", 2, position="QB", carries=2, targets=0, attempts=30, passing_yards=230),
        _weekly_row("p1", 3, position="QB", carries=2, targets=0, attempts=30, passing_yards=410),
    ]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] != "rising_opportunity"


# ---------------------------------------------------------------------------
# Insufficient / early sample handling
# ---------------------------------------------------------------------------
def test_insufficient_sample_with_one_game():
    rows = [_weekly_row("p1", 1, carries=20)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["opportunity_label"] == "insufficient_sample"
    assert row["confidence_label"] == "insufficient_sample"


def test_early_sample_with_exactly_two_games():
    rows = [_weekly_row("p1", 1, position="RB", carries=10, targets=1), _weekly_row("p1", 2, position="RB", carries=16, targets=3)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["confidence_label"] == "early_sample"
    # Still classified (not insufficient) - uses the 2 games available.
    assert row["opportunity_label"] in ("rising_opportunity", "stable_opportunity", "declining_opportunity", "limited_opportunity")
    assert row["last_3_games_used"] == 2  # last-3 uses available games only, never fabricated


def test_established_sample_at_three_games():
    rows = [_weekly_row("p1", w, carries=15) for w in (1, 2, 3)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    row = out.iloc[0]
    assert row["confidence_label"] == "established_sample"


# ---------------------------------------------------------------------------
# Output schema / no duplicates / empty handling
# ---------------------------------------------------------------------------
def test_one_row_per_player_no_duplicates():
    rows = [_weekly_row("p1", 1), _weekly_row("p1", 2), _weekly_row("p2", 1)]
    out = build_player_opportunity_reporting(_weekly_df(rows))
    assert len(out) == 2
    assert not out["player_id"].duplicated().any()


def test_empty_weekly_input_returns_empty_with_schema():
    out = build_player_opportunity_reporting(pd.DataFrame())
    assert out.empty
    assert "opportunity_label" in out.columns
    assert "touches_last_2_delta_vs_season" in out.columns


def test_prior_season_reference_never_blended_into_current_season_math():
    rows = [_weekly_row("p1", 1, carries=10), _weekly_row("p1", 2, carries=12)]
    prior = pd.DataFrame([{"player_id": "p1", "avg_fantasy_points": 99.0, "games_played": 16}])
    out = build_player_opportunity_reporting(_weekly_df(rows), prior_baseline_df=prior)
    row = out.iloc[0]
    assert row["prior_season_fppg"] == 99.0
    assert row["prior_season_games_played"] == 16
    # Current-season per-game average is unaffected by the prior-season number.
    assert row["carries_season_per_game"] == pytest.approx(11.0)


def test_prior_season_reference_null_when_no_history():
    rows = [_weekly_row("p1", 1), _weekly_row("p1", 2)]
    out = build_player_opportunity_reporting(_weekly_df(rows), prior_baseline_df=pd.DataFrame())
    row = out.iloc[0]
    assert pd.isna(row["prior_season_fppg"])


# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------
def test_thresholds_are_read_from_config_not_hardcoded(monkeypatch):
    import lib.opportunity_model as model

    rows = [
        _weekly_row("p1", 1, position="RB", carries=10, targets=1),
        _weekly_row("p1", 2, position="RB", carries=11, targets=1),
        _weekly_row("p1", 3, position="RB", carries=15, targets=1),
    ]
    # With the real default threshold (3.0), this delta is too small to be rising.
    out_default = build_player_opportunity_reporting(_weekly_df(rows))
    assert out_default.iloc[0]["opportunity_label"] != "rising_opportunity"

    # Lowering the configured threshold (not touching the data at all) flips
    # the classification - proving the rule reads the config, not a hardcoded number.
    monkeypatch.setattr(model, "RB_RISING_TOUCHES_DELTA", 0.5)
    out_patched = build_player_opportunity_reporting(_weekly_df(rows))
    assert out_patched.iloc[0]["opportunity_label"] == "rising_opportunity"


# ---------------------------------------------------------------------------
# Role-safety gate - no override, WR3/TE2/RB3 promotion, contingent stays monitor
# ---------------------------------------------------------------------------
def _gate_row(role_classification, role_eligible_for_pool, role_eligible_for_top_values=False,
              role_data_freshness="fresh", games_played=3, position="WR",
              opportunity_label="rising_opportunity", targets_last_2_per_game=6.0):
    return pd.Series({
        "role_classification": role_classification,
        "role_eligible_for_pool": role_eligible_for_pool,
        "role_eligible_for_top_values": role_eligible_for_top_values,
        "role_data_freshness": role_data_freshness,
        "games_played": games_played,
        "position": position,
        "opportunity_label": opportunity_label,
        "targets_last_2_per_game": targets_last_2_per_game,
        "touches_last_2_per_game": pd.NA,
        "attempts_last_2_per_game": pd.NA,
    })


def test_inactive_never_promoted():
    row = _gate_row("inactive", False)
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False
    assert "inactive" in gate["opportunity_eligibility_reason"].lower()


def test_role_unresolved_never_promoted():
    row = _gate_row("role_unresolved", False)
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False
    assert "unresolved" in gate["opportunity_eligibility_reason"].lower()


def test_contingent_backup_never_promoted_stays_monitor_only():
    row = _gate_row("contingent_backup", True, role_eligible_for_top_values=False)
    gate = compute_role_safety_gate(row)
    # Pass-through of the existing role engine's own eligibility - the
    # opportunity model adds no NEW promotion for contingent_backup.
    assert gate["opportunity_pool_eligible"] == row["role_eligible_for_pool"]
    assert gate["opportunity_top_value_eligible"] == row["role_eligible_for_top_values"]


def test_already_eligible_classifications_pass_through_unchanged():
    for role in ("confirmed_starter", "standard_eligible_rotation", "injury_elevated_backup"):
        row = _gate_row(role, True, role_eligible_for_top_values=True)
        gate = compute_role_safety_gate(row)
        assert gate["opportunity_pool_eligible"] is True
        assert gate["opportunity_top_value_eligible"] is True


def test_bench_no_clear_path_promoted_when_all_gates_pass():
    row = _gate_row(
        "bench_no_clear_path", False, role_data_freshness="fresh", games_played=3,
        position="WR", opportunity_label="rising_opportunity", targets_last_2_per_game=6.0,
    )
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is True
    assert gate["opportunity_top_value_eligible"] is False  # never promoted to Top Value
    assert "Player Pool only" in gate["opportunity_eligibility_reason"]


def test_bench_no_clear_path_not_promoted_when_role_data_stale():
    row = _gate_row("bench_no_clear_path", False, role_data_freshness="stale")
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False


def test_bench_no_clear_path_not_promoted_when_not_rising():
    row = _gate_row("bench_no_clear_path", False, opportunity_label="stable_opportunity")
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False


def test_bench_no_clear_path_not_promoted_below_workload_floor():
    row = _gate_row("bench_no_clear_path", False, position="WR", targets_last_2_per_game=1.0)
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False


def test_bench_no_clear_path_not_promoted_below_min_games():
    row = _gate_row("bench_no_clear_path", False, games_played=1)
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False


def test_primary_workload_last_2_is_position_aware():
    row = _gate_row("standard_eligible_rotation", True, position="RB")
    row["touches_last_2_per_game"] = 12.0
    assert primary_workload_last_2(row) == 12.0
    row2 = _gate_row("standard_eligible_rotation", True, position="QB")
    row2["attempts_last_2_per_game"] = 35.0
    assert primary_workload_last_2(row2) == 35.0


def test_gate_never_crashes_on_null_role_fields():
    row = pd.Series({
        "role_classification": "bench_no_clear_path", "role_eligible_for_pool": False,
        "role_eligible_for_top_values": False, "role_data_freshness": pd.NA,
        "games_played": pd.NA, "position": "RB", "opportunity_label": pd.NA,
        "touches_last_2_per_game": pd.NA,
    })
    gate = compute_role_safety_gate(row)
    assert gate["opportunity_pool_eligible"] is False
