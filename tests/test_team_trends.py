import pandas as pd
import pytest

from lib.team_trends import (
    build_display_table,
    compute_kpis,
    filter_team_reporting,
    sort_table,
    weekly_total_yards_by_team,
)


def _team_row(team, games_played=5, season_total_ypg=350.0, season_pass_rate=55.0,
              momentum=10.0, last3_ypg=360.0, sample="full_sample", recent_form="stable",
              wow_change="steady"):
    return {
        "team": team, "games_played": games_played,
        "season_total_yards_per_game": season_total_ypg, "season_passing_yards_per_game": season_total_ypg * 0.6,
        "season_rushing_yards_per_game": season_total_ypg * 0.4, "season_pass_rate_pct": season_pass_rate,
        "season_passing_epa_per_dropback_or_attempt": 0.05,
        "last_3_total_yards_per_game": last3_ypg, "last_3_passing_yards_per_game": last3_ypg * 0.6,
        "last_3_rushing_yards_per_game": last3_ypg * 0.4, "last_3_pass_rate_pct": season_pass_rate,
        "last_3_passing_epa_per_dropback_or_attempt": 0.05,
        "offensive_momentum_yards": momentum,
        "offensive_momentum_pct": (momentum / season_total_ypg) if momentum is not None else None,
        "wow_passing_yards_change": 5.0, "wow_rushing_yards_change": -3.0, "wow_total_yards_change": 2.0,
        "wow_pass_rate_change": 1.0,
        "sample_size_label": sample, "recent_form_label": recent_form, "wow_change_label": wow_change,
    }


def test_filter_by_teams_min_games_and_sample():
    df = pd.DataFrame([
        _team_row("KC", games_played=5, sample="full_sample"),
        _team_row("BUF", games_played=1, sample="insufficient_sample"),
        _team_row("SF", games_played=3, sample="limited_sample"),
    ])
    out = filter_team_reporting(df, teams=["KC", "BUF"])
    assert set(out["team"]) == {"KC", "BUF"}

    out2 = filter_team_reporting(df, min_games=3)
    assert set(out2["team"]) == {"KC", "SF"}

    out3 = filter_team_reporting(df, include_insufficient_sample=False)
    assert "BUF" not in set(out3["team"])
    assert set(out3["team"]) == {"KC", "SF"}


def test_filter_empty_input_returns_empty():
    assert filter_team_reporting(pd.DataFrame()).empty


def test_sort_table_respects_direction_and_missing_column():
    df = pd.DataFrame([_team_row("KC", season_total_ypg=300), _team_row("BUF", season_total_ypg=400)])
    desc = sort_table(df, "season_total_yards_per_game", ascending=False)
    assert desc["team"].tolist() == ["BUF", "KC"]
    asc = sort_table(df, "season_total_yards_per_game", ascending=True)
    assert asc["team"].tolist() == ["KC", "BUF"]
    # Unknown column -> returns df unchanged rather than raising.
    same = sort_table(df, "not_a_real_column")
    assert same["team"].tolist() == df["team"].tolist()


def test_compute_kpis_picks_the_right_standout_teams():
    df = pd.DataFrame([
        _team_row("KC", season_total_ypg=340, season_pass_rate=52, momentum=5, last3_ypg=345),
        _team_row("BUF", season_total_ypg=360, season_pass_rate=58, momentum=25, last3_ypg=385),
    ])
    kpis = compute_kpis(df)
    assert kpis["league_avg_total_yards_per_game"] == pytest.approx((340 + 360) / 2)
    assert kpis["league_avg_pass_rate_pct"] == pytest.approx((52 + 58) / 2)
    assert kpis["most_improved_team"] == "BUF"
    assert kpis["most_improved_momentum_yards"] == pytest.approx(25)
    assert kpis["highest_last_3_team"] == "BUF"
    assert kpis["highest_last_3_yards_per_game"] == pytest.approx(385)


def test_compute_kpis_empty_input_returns_all_none():
    kpis = compute_kpis(pd.DataFrame())
    assert all(v is None for v in kpis.values())


def test_compute_kpis_handles_all_null_momentum_without_crashing():
    df = pd.DataFrame([_team_row("KC", momentum=None), _team_row("BUF", momentum=None)])
    kpis = compute_kpis(df)
    assert kpis["most_improved_team"] is None
    assert kpis["league_avg_total_yards_per_game"] is not None


def test_build_display_table_formats_nulls_as_em_dash_and_signs_changes():
    df = pd.DataFrame([_team_row("KC", momentum=None)])
    df.loc[0, "offensive_momentum_yards"] = None
    df.loc[0, "wow_passing_yards_change"] = 12.0
    df.loc[0, "wow_rushing_yards_change"] = -8.0

    display = build_display_table(df)
    assert display.loc[0, "Offensive Momentum"] == "—"
    assert display.loc[0, "WoW Pass Yards"] == "+12"
    assert display.loc[0, "WoW Rush Yards"] == "-8"
    assert display.loc[0, "Recent Form"] == "➖ Stable"


def test_build_display_table_empty_input():
    out = build_display_table(pd.DataFrame())
    assert out.empty


# ---------------------------------------------------------------------------
# weekly_total_yards_by_team
# ---------------------------------------------------------------------------
def _stat_row(team, week, passing_yards, rushing_yards, season=2025, season_type="REG"):
    return {"season": season, "week": week, "team": team, "season_type": season_type,
            "passing_yards": passing_yards, "rushing_yards": rushing_yards}


def test_weekly_total_yards_skips_bye_and_future_weeks():
    raw = pd.DataFrame([
        _stat_row("KC", 1, 200, 80),
        _stat_row("KC", 2, 300, 100),
        # week 3 bye - absent
        _stat_row("KC", 4, 250, 90),
        _stat_row("KC", 5, 999, 999),  # beyond max_week - excluded
    ])
    out = weekly_total_yards_by_team(raw, season=2025, max_week=4)
    assert sorted(out["week"].tolist()) == [1, 2, 4]
    assert out[out["week"] == 1]["total_yards"].iloc[0] == pytest.approx(280)
    assert out[out["week"] == 4]["total_yards"].iloc[0] == pytest.approx(340)


def test_weekly_total_yards_empty_or_none_max_week():
    assert weekly_total_yards_by_team(pd.DataFrame(), 2025, 4).empty
    raw = pd.DataFrame([_stat_row("KC", 1, 200, 80)])
    assert weekly_total_yards_by_team(raw, 2025, None).empty
