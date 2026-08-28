import pandas as pd
import pytest

from dfs_data_pipeline import (
    RECENT_FORM_COOLING_OFF_PCT,
    RECENT_FORM_HEATING_UP_PCT,
    TEAM_REPORTING_COLUMNS,
    WOW_DECREASING_YARDS,
    WOW_INCREASING_YARDS,
    build_team_reporting,
)


def _row(team, week, passing_yards=200, rushing_yards=100, attempts=30, carries=25,
         passing_epa=10.0, sacks_suffered=2, season=2025, season_type="REG"):
    return {
        "season": season, "week": week, "team": team, "season_type": season_type, "opponent_team": "OPP",
        "attempts": attempts, "passing_yards": passing_yards, "carries": carries, "rushing_yards": rushing_yards,
        "passing_epa": passing_epa, "sacks_suffered": sacks_suffered,
        "def_sacks": 1, "def_interceptions": 0, "def_fumbles_forced": 0,
    }


def _kc(out):
    return out[out["team"] == "KC"].iloc[0]


# ---------------------------------------------------------------------------
# Season totals / rates
# ---------------------------------------------------------------------------
def test_season_totals_and_rates():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=300, rushing_yards=100, attempts=40, carries=20),
        _row("KC", 2, passing_yards=200, rushing_yards=140, attempts=20, carries=20),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    row = _kc(out)

    assert row["games_played"] == 2
    assert row["season_passing_yards"] == pytest.approx(500)
    assert row["season_rushing_yards"] == pytest.approx(240)
    assert row["season_total_yards"] == pytest.approx(740)
    assert row["season_passing_yards_per_game"] == pytest.approx(250)
    assert row["season_rushing_yards_per_game"] == pytest.approx(120)
    assert row["season_total_yards_per_game"] == pytest.approx(370)
    assert row["season_pass_attempts"] == pytest.approx(60)
    assert row["season_carries"] == pytest.approx(40)
    assert row["season_offensive_plays"] == pytest.approx(100)
    assert row["season_pass_rate_pct"] == pytest.approx(60.0)


def test_zero_attempts_and_carries_gives_null_pass_rate_not_error():
    raw = pd.DataFrame([_row("KC", 1, attempts=0, carries=0)])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)
    assert pd.isna(row["season_pass_rate_pct"])


def test_zero_dropbacks_gives_null_epa_rate_not_error():
    raw = pd.DataFrame([_row("KC", 1, attempts=0, sacks_suffered=0)])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)
    assert row["season_passing_dropbacks"] == pytest.approx(0)
    assert pd.isna(row["season_passing_epa_per_dropback_or_attempt"])


# ---------------------------------------------------------------------------
# Last-3 played games across a bye
# ---------------------------------------------------------------------------
def test_last_3_played_games_skips_bye_week():
    # Weeks 1,2,4 played (week 3 is a bye - simply absent, never zero-filled).
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80),
        _row("KC", 2, passing_yards=300, rushing_yards=120),
        _row("KC", 4, passing_yards=100, rushing_yards=60),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=4, reporting_mode="in_season")
    row = _kc(out)

    assert row["last_3_games_count"] == 3
    assert row["last_3_passing_yards_per_game"] == pytest.approx((200 + 300 + 100) / 3)
    assert row["last_3_rushing_yards_per_game"] == pytest.approx((80 + 120 + 60) / 3)
    assert row["latest_played_week"] == 4


def test_last_3_uses_only_games_available_when_fewer_than_three():
    raw = pd.DataFrame([_row("KC", 1, passing_yards=200, rushing_yards=80)])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)
    assert row["last_3_games_count"] == 1
    assert row["last_3_passing_yards_per_game"] == pytest.approx(200)


# ---------------------------------------------------------------------------
# Week-over-week change across a bye
# ---------------------------------------------------------------------------
def test_wow_change_compares_last_two_played_games_across_a_bye():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80, attempts=30, carries=25),
        _row("KC", 2, passing_yards=300, rushing_yards=100, attempts=35, carries=20),
        # Week 3 is a bye - absent.
        _row("KC", 4, passing_yards=250, rushing_yards=90, attempts=32, carries=22),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=4, reporting_mode="in_season")
    row = _kc(out)

    assert row["latest_game_week"] == 4
    assert row["previous_game_week"] == 2  # not week 3 - the bye is skipped, not treated as the "previous" game
    assert row["wow_passing_yards_change"] == pytest.approx(250 - 300)
    assert row["wow_rushing_yards_change"] == pytest.approx(90 - 100)
    assert row["wow_total_yards_change"] == pytest.approx(340 - 400)


def test_wow_never_treats_a_bye_as_zero_yards():
    # If a bye were (wrongly) treated as a real zero-yardage game, the WoW
    # change would swing to a huge negative number instead of comparing
    # weeks 2 and 4 directly.
    raw = pd.DataFrame([
        _row("KC", 2, passing_yards=300, rushing_yards=100),
        _row("KC", 4, passing_yards=250, rushing_yards=90),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=4, reporting_mode="in_season")
    row = _kc(out)
    assert row["wow_total_yards_change"] == pytest.approx(340 - 400)
    assert row["wow_total_yards_change"] > -300  # sanity: nowhere near a "bye counted as zero" magnitude


# ---------------------------------------------------------------------------
# Fewer than 2 games => null WoW; 1-2 games => insufficient sample labeling
# ---------------------------------------------------------------------------
def test_one_game_has_null_wow_and_insufficient_sample_labels():
    raw = pd.DataFrame([_row("KC", 1)])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)

    assert row["previous_game_week"] is None or pd.isna(row["previous_game_week"])
    assert pd.isna(row["wow_passing_yards_change"])
    assert pd.isna(row["wow_rushing_yards_change"])
    assert pd.isna(row["wow_total_yards_change"])
    assert pd.isna(row["wow_pass_rate_change"])
    assert row["wow_change_label"] == "insufficient_sample"
    assert row["recent_form_label"] == "insufficient_sample"
    assert row["sample_size_label"] == "insufficient_sample"


def test_two_games_gives_a_real_wow_but_still_insufficient_recent_form():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80),
        _row("KC", 2, passing_yards=260, rushing_yards=90),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    row = _kc(out)

    # WoW only needs 2 games, and is defined here.
    assert row["wow_total_yards_change"] == pytest.approx((260 + 90) - (200 + 80))
    assert row["wow_change_label"] in {"increasing", "decreasing", "steady"}
    # But recent-form/momentum still requires TEAM_RECENT_FORM_GAMES (3).
    assert row["recent_form_label"] == "insufficient_sample"
    assert row["offensive_momentum_yards"] is None or pd.isna(row["offensive_momentum_yards"])
    # SAMPLE_SIZE_LIMITED_MIN_GAMES is 3, so 2 games is still "insufficient_sample"
    # (matches recent_form_label's own <3-games floor - a deliberately
    # consistent boundary, not two different thresholds for the same idea).
    assert row["sample_size_label"] == "insufficient_sample"


# ---------------------------------------------------------------------------
# Offensive momentum
# ---------------------------------------------------------------------------
def test_offensive_momentum_calculation():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80),
        _row("KC", 2, passing_yards=200, rushing_yards=80),
        _row("KC", 3, passing_yards=200, rushing_yards=80),
        _row("KC", 4, passing_yards=260, rushing_yards=120),
        _row("KC", 5, passing_yards=300, rushing_yards=140),
        _row("KC", 6, passing_yards=340, rushing_yards=160),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=6, reporting_mode="in_season")
    row = _kc(out)

    season_total_ypg = row["season_total_yards_per_game"]
    last_3_total_ypg = row["last_3_total_yards_per_game"]
    assert row["offensive_momentum_yards"] == pytest.approx(last_3_total_ypg - season_total_ypg)
    assert row["offensive_momentum_pct"] == pytest.approx(
        (last_3_total_ypg - season_total_ypg) / season_total_ypg
    )
    # This team is clearly trending up - last 3 games (weeks 4-6) are all
    # above the season average established across all 6 games.
    assert row["offensive_momentum_yards"] > 0
    assert row["recent_form_label"] == "heating_up"


def test_recent_form_label_thresholds_use_the_documented_constants():
    # Build a team whose momentum pct sits just at/above the heating-up
    # threshold, and one just at/below the cooling-off threshold, to prove
    # the labels are driven by RECENT_FORM_HEATING_UP_PCT/COOLING_OFF_PCT
    # rather than a hardcoded number.
    season_ypg_target = 300.0
    last3_heating = season_ypg_target * (1 + RECENT_FORM_HEATING_UP_PCT + 0.01)
    last3_cooling = season_ypg_target * (1 + RECENT_FORM_COOLING_OFF_PCT - 0.01)

    def team_with_last3_avg(team, last3_value):
        # 6 games: 3 "filler" games averaging to season_ypg_target overall
        # when combined with 3 identical last3_value games.
        filler = 2 * season_ypg_target - last3_value
        rows = [_row(team, w, passing_yards=filler * 0.7, rushing_yards=filler * 0.3) for w in (1, 2, 3)]
        rows += [_row(team, w, passing_yards=last3_value * 0.7, rushing_yards=last3_value * 0.3) for w in (4, 5, 6)]
        return rows

    raw = pd.DataFrame(team_with_last3_avg("HOT", last3_heating) + team_with_last3_avg("COLD", last3_cooling))
    out = build_team_reporting(raw, 2025, latest_completed_week=6, reporting_mode="in_season")

    hot = out[out["team"] == "HOT"].iloc[0]
    cold = out[out["team"] == "COLD"].iloc[0]
    assert hot["recent_form_label"] == "heating_up"
    assert cold["recent_form_label"] == "cooling_off"


def test_wow_change_label_thresholds_use_the_documented_constants():
    raw = pd.DataFrame([
        _row("UP", 1, passing_yards=200, rushing_yards=80),
        _row("UP", 2, passing_yards=200 + WOW_INCREASING_YARDS + 1, rushing_yards=80),
        _row("DOWN", 1, passing_yards=200, rushing_yards=80),
        _row("DOWN", 2, passing_yards=200 + WOW_DECREASING_YARDS - 1, rushing_yards=80),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    assert out[out["team"] == "UP"].iloc[0]["wow_change_label"] == "increasing"
    assert out[out["team"] == "DOWN"].iloc[0]["wow_change_label"] == "decreasing"


# ---------------------------------------------------------------------------
# No incomplete/future-week games used
# ---------------------------------------------------------------------------
def test_excludes_weeks_beyond_latest_completed_week():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80),
        _row("KC", 2, passing_yards=300, rushing_yards=120),
        _row("KC", 3, passing_yards=999, rushing_yards=999),  # in-progress/future week
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    row = _kc(out)
    assert row["games_played"] == 2
    assert row["latest_played_week"] == 2
    assert row["season_passing_yards"] == pytest.approx(500)


def test_excludes_non_regular_season_and_other_seasons():
    raw = pd.DataFrame([
        _row("KC", 1, passing_yards=200, rushing_yards=80, season=2025, season_type="REG"),
        _row("KC", 1, passing_yards=999, rushing_yards=999, season=2025, season_type="POST"),
        _row("KC", 1, passing_yards=999, rushing_yards=999, season=2024, season_type="REG"),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)
    assert row["games_played"] == 1
    assert row["season_passing_yards"] == pytest.approx(200)


# ---------------------------------------------------------------------------
# Preseason baseline mode
# ---------------------------------------------------------------------------
def test_preseason_baseline_keeps_season_aggregates_but_nulls_recency_and_wow():
    raw = pd.DataFrame([
        _row("KC", w, passing_yards=200 + w, rushing_yards=80 + w, season=2025) for w in range(1, 8)
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=7, reporting_mode="preseason_baseline")
    row = _kc(out)

    assert row["reporting_mode"] == "preseason_baseline"
    assert row["games_played"] == 7
    assert pd.notna(row["season_total_yards_per_game"])  # season averages ARE useful in baseline mode

    for col in [
        "last_3_games_count", "last_3_total_yards_per_game", "offensive_momentum_yards",
        "offensive_momentum_pct", "latest_game_week", "previous_game_week",
        "wow_total_yards_change", "wow_pass_rate_change",
    ]:
        assert pd.isna(row[col]), f"{col} should be null in preseason_baseline mode"

    assert row["recent_form_label"] == "not_applicable_preseason"
    assert row["wow_change_label"] == "not_applicable_preseason"


# ---------------------------------------------------------------------------
# Honest EPA rate (denominator = dropbacks = attempts + sacks_suffered)
# ---------------------------------------------------------------------------
def test_epa_rate_is_sum_epa_over_sum_dropbacks_not_an_average_of_weekly_epa():
    raw = pd.DataFrame([
        _row("KC", 1, attempts=30, sacks_suffered=2, passing_epa=15.0),
        _row("KC", 2, attempts=40, sacks_suffered=3, passing_epa=-5.0),
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    row = _kc(out)

    expected_dropbacks = (30 + 2) + (40 + 3)
    expected_epa_rate = (15.0 + -5.0) / expected_dropbacks
    assert row["season_passing_dropbacks"] == pytest.approx(expected_dropbacks)
    assert row["season_passing_epa_per_dropback_or_attempt"] == pytest.approx(expected_epa_rate)
    assert row["season_passing_epa_denominator"] == "dropbacks (attempts + sacks_suffered)"

    # The old (wrong) approach the spec called out: AVERAGE(passing_epa)
    # across weekly rows. Prove the honest rate is NOT that.
    wrong_average_of_weekly_epa = (15.0 + -5.0) / 2
    assert row["season_passing_epa_per_dropback_or_attempt"] != pytest.approx(wrong_average_of_weekly_epa)


def test_epa_fields_are_null_when_source_columns_are_missing():
    raw = pd.DataFrame([
        {"season": 2025, "week": 1, "team": "KC", "season_type": "REG", "opponent_team": "OPP",
         "attempts": 30, "passing_yards": 200, "carries": 25, "rushing_yards": 80,
         "def_sacks": 1, "def_interceptions": 0, "def_fumbles_forced": 0},
    ])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    row = _kc(out)
    assert pd.isna(row["season_passing_epa"])
    assert pd.isna(row["season_passing_epa_per_dropback_or_attempt"])
    assert row["season_passing_epa_denominator"] is None


# ---------------------------------------------------------------------------
# Schema / no duplicates / empty input
# ---------------------------------------------------------------------------
def test_output_schema_matches_team_reporting_columns():
    raw = pd.DataFrame([_row("KC", 1), _row("BUF", 1)])
    out = build_team_reporting(raw, 2025, latest_completed_week=1, reporting_mode="in_season")
    assert list(out.columns) == TEAM_REPORTING_COLUMNS


def test_no_duplicate_team_rows():
    raw = pd.DataFrame([_row("KC", 1), _row("KC", 2), _row("BUF", 1)])
    out = build_team_reporting(raw, 2025, latest_completed_week=2, reporting_mode="in_season")
    assert not out["team"].duplicated().any()
    assert len(out) == 2


def test_empty_input_returns_empty_with_schema():
    out = build_team_reporting(pd.DataFrame(), 2025, latest_completed_week=1, reporting_mode="in_season")
    assert out.empty
    assert list(out.columns) == TEAM_REPORTING_COLUMNS


def test_none_latest_completed_week_returns_empty():
    raw = pd.DataFrame([_row("KC", 1)])
    out = build_team_reporting(raw, 2025, latest_completed_week=None, reporting_mode="in_season")
    assert out.empty
