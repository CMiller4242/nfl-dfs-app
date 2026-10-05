import os

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dfs_data_pipeline import PLAYERS_CURRENT_EMPTY_COLUMNS
from lib.eligibility import ROLE_CONTEXT_COLUMNS
from lib.matchup_analyzer import (
    apply_research_filters,
    build_csv_export,
    build_display_table,
    build_matchup_analyzer_table,
    build_needs_review_export,
    excluded_by_role_context,
    featured_top_value,
    inactive_players,
    needs_review_rows,
    plays_to_monitor,
    salary_vs_value_chart_data,
    summary_counts,
    valid_player_pool,
    volume_vs_matchup_chart_data,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "5_Matchup_Analyzer.py")


# ---------------------------------------------------------------------------
# Fixtures - realistic schemas matching the real marts exactly
# ---------------------------------------------------------------------------
def _dk_row(name, position, team, opponent, salary=8000, week="09/27/2026", avg=15.0):
    return {
        "Position": position, "Name": name, "Salary": salary,
        "Game Info": f"{opponent}@{team} {week} 1:00PM ET" if team else f"{opponent}@BYE {week} 1:00PM ET",
        "TeamAbbrev": team, "AvgPointsPerGame": avg,
    }


def _player_row(player_id, name, team, position, last_opponent="BUF", avg=18.0, games=2, momentum=19.0,
                 total_touches=25, carries=15, targets=6, receptions=4, rushing_yards=70, receiving_yards=40,
                 target_share=22.0, air_yards_share=18.0, air_yards=30):
    total_yards = rushing_yards + receiving_yards
    return {
        "player_id": player_id, "player_display_name": name, "position": position, "team": team,
        "last_opponent": last_opponent, "season": 2026, "latest_game_week": games,
        "games_played": games, "avg_fantasy_points": avg, "total_fantasy_points": avg * games,
        "latest_game_fantasy_points": avg, "total_touches": total_touches,
        "touches_per_game": total_touches / games, "total_targets": targets, "targets_per_game": targets / games,
        "total_carries": carries, "carries_per_game": carries / games,
        "total_receptions": receptions, "receptions_per_game": receptions / games,
        "total_rushing_yards": rushing_yards, "rushing_yards_per_game": rushing_yards / games,
        "total_receiving_yards": receiving_yards, "receiving_yards_per_game": receiving_yards / games,
        "total_yards": total_yards, "total_yards_per_game": total_yards / games,
        "total_receiving_air_yards": air_yards, "receiving_air_yards_per_game": air_yards / games,
        "total_yac": 15, "total_passing_yards": 0, "total_passing_tds": 0, "total_rushing_tds": 0,
        "total_receiving_tds": 0, "completion_pct": pd.NA, "passing_yards_per_attempt": pd.NA,
        "yards_per_target": receiving_yards / targets if targets else pd.NA,
        "yards_per_carry": rushing_yards / carries if carries else pd.NA,
        "catch_rate": receptions / targets if targets else pd.NA, "yac_per_reception": 3.5,
        "target_share_pct": target_share, "air_yards_share_pct": air_yards_share,
        "yards_per_touch": total_yards / total_touches if total_touches else pd.NA,
        "points_per_touch": avg / total_touches if total_touches else pd.NA, "consistency_score": 2.0,
        "momentum_score": momentum, "momentum_games_used": games,
        "latest_game_touches": total_touches, "prior_game_touches": total_touches - 2, "touches_wow_change": 2,
        "latest_game_carries": carries, "latest_game_targets": targets, "latest_game_receptions": receptions,
        "latest_game_rushing_yards": rushing_yards, "latest_game_receiving_yards": receiving_yards,
        "latest_game_total_yards": total_yards, "latest_game_target_share_pct": target_share,
        "latest_game_air_yards_share_pct": air_yards_share, "carries_wow_change": 1.0, "targets_wow_change": 1.0,
        "receiving_yards_wow_change": 5.0, "target_share_wow_change": 2.0, "opportunity_trend": "gaining",
    }


def _role_row(player_id, name, team, position, role="confirmed_starter", depth_rank=1,
              eligible_pool=True, eligible_top=True, monitor=False, blockers="", injury="Healthy"):
    return {
        "player_id": player_id, "player_name": name, "canonical_team": team, "position_group": position,
        "source_position": position, "depth_rank": depth_rank, "espn_id": f"espn_{player_id}",
        "availability_classification": "available", "injury_designation": injury,
        "role_classification": role, "role_eligible_for_pool": eligible_pool,
        "role_eligible_for_top_values": eligible_top, "is_conditional_monitor": monitor,
        "eligibility_reason": f"{role} reason for {name}", "blocking_player_ids": "",
        "blocking_player_names": blockers, "blocking_player_statuses": "",
        "depth_chart_source_timestamp": pd.Timestamp.now(tz="UTC"),
        "injury_source_timestamp": pd.Timestamp.now(tz="UTC"), "role_data_freshness": "fresh",
        "manual_override_applied": False, "override_reason": None,
    }


def _defense_row(defense_team, position, matchup_index=100.0, matchup_delta=0.0, fppg_allowed=12.0,
                  rank=16, pctile=50.0, games=5, sample="full_sample", trend="stable"):
    return {
        "season": 2026, "defense_team": defense_team, "position": position, "games_in_sample": games,
        "latest_completed_week": 2, "source_last_updated_utc": pd.Timestamp.now(tz="UTC"),
        "reporting_mode": "in_season", "sample_size_label": sample,
        "fantasy_points_allowed_per_game": fppg_allowed, "league_avg_points_allowed_for_position": 12.0,
        "matchup_index": matchup_index, "matchup_delta": matchup_delta,
        "position_rank_most_favorable": rank, "position_percentile_most_favorable": pctile,
        "last_3_games_count": games, "last_3_games_points_allowed_per_game": fppg_allowed,
        "last_3_games_matchup_index": matchup_index, "last_3_games_matchup_delta": matchup_delta,
        "dvp_recent_trend_delta": 0.0, "dvp_trend_label": trend,
    }


def _team_reporting_row(team, total_ypg=350.0, pass_rate=58.0, momentum=10.0, epa=0.05, games=2):
    return {
        "season": 2026, "team": team, "games_played": games, "latest_played_week": games,
        "last_updated_utc": pd.Timestamp.now(tz="UTC"), "sample_size_label": "insufficient_sample",
        "reporting_mode": "in_season", "season_passing_yards": 500, "season_rushing_yards": 200,
        "season_total_yards": 700, "season_passing_yards_per_game": 250, "season_rushing_yards_per_game": 100,
        "season_total_yards_per_game": total_ypg, "season_pass_attempts": 60, "season_carries": 40,
        "season_offensive_plays": 100, "season_pass_rate_pct": pass_rate, "season_passing_epa": 5.0,
        "season_passing_dropbacks": 60, "season_passing_epa_denominator": "dropbacks (attempts + sacks_suffered)",
        "season_passing_epa_per_dropback_or_attempt": epa, "last_3_games_count": games,
        "last_3_passing_yards_per_game": 250, "last_3_rushing_yards_per_game": 100,
        "last_3_total_yards_per_game": total_ypg + momentum, "last_3_pass_rate_pct": pass_rate,
        "last_3_passing_epa_per_dropback_or_attempt": epa, "offensive_momentum_yards": momentum,
        "offensive_momentum_pct": momentum / total_ypg, "recent_form_label": "stable",
        "latest_game_week": games, "previous_game_week": games - 1, "latest_game_passing_yards": 250,
        "latest_game_rushing_yards": 100, "latest_game_total_yards": 350, "wow_passing_yards_change": 0,
        "wow_rushing_yards_change": 0, "wow_total_yards_change": 0, "wow_pass_rate_change": 0,
        "wow_change_label": "steady",
    }


def _standard_scenario():
    """One confident RB (KC vs BUF, confirmed_starter), one confident WR
    (DAL vs NYG, injury_elevated_backup), one contingent TE (SF vs LA,
    contingent_backup), one bench WR (excluded), one inactive RB, one
    unmatched DK row (rookie with no stats), one matched-but-role-unresolved
    QB. Covers every category."""
    dk = pd.DataFrame([
        _dk_row("Rashee Rice", "RB", "KC", "BUF", salary=7500, avg=16.0),
        _dk_row("CeeDee Lamb", "WR", "DAL", "NYG", salary=8200, avg=20.0),
        _dk_row("George Kittle", "TE", "SF", "LA", salary=6000, avg=13.0),
        _dk_row("Bench Wideout", "WR", "DAL", "NYG", salary=3000, avg=4.0),
        _dk_row("Hurt Runner", "RB", "SF", "LA", salary=4000, avg=8.0),
        _dk_row("Rookie Nobody", "WR", "KC", "BUF", salary=3500, avg=0.0),
        _dk_row("Unresolved QB", "QB", "KC", "BUF", salary=7000, avg=18.0),
    ])

    current = pd.DataFrame([
        _player_row("p_rb", "Rashee Rice", "KC", "RB", last_opponent="BAL", avg=16.5, carries=18, targets=4),
        _player_row("p_wr", "CeeDee Lamb", "DAL", "WR", last_opponent="PHI", avg=21.0, carries=0, targets=10, receiving_yards=95, air_yards=140, target_share=28.0, air_yards_share=33.0),
        _player_row("p_te", "George Kittle", "SF", "TE", last_opponent="ARI", avg=12.5, carries=0, targets=6, receiving_yards=55, air_yards=40, target_share=18.0, air_yards_share=14.0),
        _player_row("p_bench", "Bench Wideout", "DAL", "WR", last_opponent="PHI", avg=3.5, carries=0, targets=1, receiving_yards=5),
        _player_row("p_hurt", "Hurt Runner", "SF", "RB", last_opponent="ARI", avg=7.5, carries=10, targets=1),
        _player_row("p_qb", "Unresolved QB", "KC", "QB", last_opponent="BAL", avg=18.5, carries=3, targets=0,
                     receptions=0, receiving_yards=0, target_share=pd.NA, air_yards_share=pd.NA, air_yards=pd.NA),
    ])

    role_context = pd.DataFrame([
        _role_row("p_rb", "Rashee Rice", "KC", "RB", role="confirmed_starter", depth_rank=1),
        _role_row("p_wr", "CeeDee Lamb", "DAL", "WR", role="injury_elevated_backup", depth_rank=3,
                   blockers="Injured Starter (WR1) is Out"),
        _role_row("p_te", "George Kittle", "SF", "TE", role="contingent_backup", depth_rank=2,
                   eligible_top=False, monitor=True, blockers="Starter TE (TE1) is Questionable"),
        _role_row("p_bench", "Bench Wideout", "DAL", "WR", role="bench_no_clear_path", depth_rank=4,
                   eligible_pool=False, eligible_top=False, blockers="Healthy Starter (WR1) is Healthy"),
        _role_row("p_hurt", "Hurt Runner", "SF", "RB", role="inactive", depth_rank=2,
                   eligible_pool=False, eligible_top=False, injury="Out"),
        # p_qb intentionally omitted -> role_unresolved (fail-closed default)
    ])

    defense = pd.DataFrame([
        _defense_row("BUF", "RB", matchup_index=120.0, matchup_delta=1.5, rank=5, pctile=85.0, trend="becoming_more_favorable"),
        _defense_row("BUF", "WR", matchup_index=95.0, matchup_delta=-0.3),
        _defense_row("BUF", "QB", matchup_index=100.0, matchup_delta=0.0),
        _defense_row("NYG", "WR", matchup_index=80.0, matchup_delta=-1.2, rank=25, pctile=20.0, trend="becoming_tougher"),
        _defense_row("LA", "TE", matchup_index=110.0, matchup_delta=0.8),
        _defense_row("LA", "RB", matchup_index=105.0, matchup_delta=0.5),
    ])

    team_reporting = pd.DataFrame([
        _team_reporting_row("KC", total_ypg=360.0, momentum=15.0),
        _team_reporting_row("DAL", total_ypg=340.0, momentum=-5.0),
        _team_reporting_row("SF", total_ypg=400.0, momentum=20.0),
    ])

    prior_baseline = pd.DataFrame(columns=[
        "player_id", "player_display_name", "position", "historical_team", "season", "games_played",
        "avg_fantasy_points", "total_touches", "total_targets", "total_carries",
        "yards_per_target", "yards_per_carry", "catch_rate", "points_per_touch",
        "yards_per_touch", "target_share_pct", "air_yards_share_pct", "consistency_score",
    ])

    return dk, current, prior_baseline, defense, team_reporting, role_context


@pytest.fixture
def scenario():
    return _standard_scenario()


@pytest.fixture
def table(scenario):
    dk, current, prior, defense, team, role = scenario
    return build_matchup_analyzer_table(dk, current, prior, defense, team, role)


# ---------------------------------------------------------------------------
# Deterministic join + identity safety
# ---------------------------------------------------------------------------
def test_join_is_deterministic(scenario):
    dk, current, prior, defense, team, role = scenario
    t1 = build_matchup_analyzer_table(dk, current, prior, defense, team, role)
    t2 = build_matchup_analyzer_table(dk, current, prior, defense, team, role)
    pd.testing.assert_frame_equal(t1.reset_index(drop=True), t2.reset_index(drop=True))


def test_output_schema_no_duplicate_rows(table):
    assert len(table) == 7  # one row per DK salary row, no fan-out
    assert not table.duplicated(subset=["Name", "TeamAbbrev", "Position"]).any()


def test_each_player_gets_own_stats_never_another_players(table):
    rb_row = table[table["Name"] == "Rashee Rice"].iloc[0]
    assert rb_row["carries_per_game"] == pytest.approx(9.0)  # 18/2
    wr_row = table[table["Name"] == "CeeDee Lamb"].iloc[0]
    assert wr_row["targets_per_game"] == pytest.approx(5.0)  # 10/2
    assert wr_row["carries_per_game"] == pytest.approx(0.0)


def test_no_cross_team_or_cross_position_leakage(table):
    # Two SF players (Kittle=TE, Hurt Runner=RB) must never swap matchup context.
    kittle = table[table["Name"] == "George Kittle"].iloc[0]
    hurt = table[table["Name"] == "Hurt Runner"].iloc[0]
    assert kittle["opponent"] == "LA"
    assert hurt["opponent"] == "LA"
    assert kittle["matchup_index"] == pytest.approx(110.0)  # LA vs TE
    assert hurt["matchup_index"] == pytest.approx(105.0)   # LA vs RB
    assert kittle["Position"] != hurt["Position"]


def test_unresolved_player_gets_null_not_zero(table):
    rookie = table[table["Name"] == "Rookie Nobody"].iloc[0]
    assert rookie["match_method"] == "unmatched"
    assert pd.isna(rookie["projected_points"])
    assert pd.isna(rookie["projected_value"])
    assert rookie["projection_status"] != "ok"


def test_role_unresolved_when_no_role_context_row(table):
    qb = table[table["Name"] == "Unresolved QB"].iloc[0]
    assert qb["match_method"] != "unmatched"  # stats matched fine
    assert qb["role_classification"] == "role_unresolved"
    assert qb["role_display"] == "Role Needs Review"
    assert qb["role_eligible_for_pool"] == False  # noqa: E712


# ---------------------------------------------------------------------------
# Category bucketing
# ---------------------------------------------------------------------------
def test_valid_player_pool_excludes_contingent_by_default(table):
    pool = valid_player_pool(table, include_conditional=False)
    names = set(pool["Name"])
    assert "Rashee Rice" in names
    assert "CeeDee Lamb" in names
    assert "George Kittle" not in names  # contingent_backup, toggle OFF


def test_valid_player_pool_includes_contingent_when_toggled(table):
    pool = valid_player_pool(table, include_conditional=True)
    assert "George Kittle" in set(pool["Name"])


def test_featured_top_value_never_includes_contingent_regardless_of_toggle(table):
    featured_off = featured_top_value(table)
    assert "George Kittle" not in set(featured_off["Name"])
    # Toggle has no bearing on featured_top_value at all - it only reads role_eligible_for_top_values.
    assert set(featured_off["Name"]) == {"Rashee Rice", "CeeDee Lamb"}


def test_plays_to_monitor_is_exactly_contingent_backup(table):
    monitor = plays_to_monitor(table)
    assert set(monitor["Name"]) == {"George Kittle"}


def test_excluded_by_role_context_is_bench_no_clear_path(table):
    excluded = excluded_by_role_context(table)
    assert set(excluded["Name"]) == {"Bench Wideout"}


def test_inactive_players_bucket(table):
    inactive = inactive_players(table)
    assert set(inactive["Name"]) == {"Hurt Runner"}


def test_needs_review_includes_unmatched_and_role_unresolved(table):
    review = needs_review_rows(table)
    names = set(review["Name"])
    assert "Rookie Nobody" in names       # unmatched stats
    assert "Unresolved QB" in names       # role_unresolved despite matched stats


def test_inactive_excluded_review_never_leak_into_featured(table):
    featured = featured_top_value(table)
    leaked = set(featured["Name"]) & {"Hurt Runner", "Bench Wideout", "Rookie Nobody", "Unresolved QB", "George Kittle"}
    assert leaked == set()


def test_inactive_excluded_review_never_leak_into_valid_pool(table):
    pool = valid_player_pool(table, include_conditional=True)
    leaked = set(pool["Name"]) & {"Hurt Runner", "Bench Wideout", "Rookie Nobody", "Unresolved QB"}
    assert leaked == set()


# ---------------------------------------------------------------------------
# Position-aware research filters
# ---------------------------------------------------------------------------
def test_min_targets_filter_does_not_exclude_rb_with_null_metric(table):
    # Give the RB a null targets_per_game to simulate a metric genuinely unavailable.
    df = table.copy()
    df.loc[df["Name"] == "Rashee Rice", "targets_per_game"] = pd.NA
    out = apply_research_filters(df, min_targets_per_game=5.0)
    assert "Rashee Rice" in set(out["Name"])  # null bypasses the filter, never excluded as if 0


def test_min_targets_filter_excludes_real_low_value(table):
    out = apply_research_filters(table, min_targets_per_game=4.0)
    # Rashee Rice has targets_per_game=2.0 (4/2), a real low number - filtered out.
    assert "Rashee Rice" not in set(out["Name"])
    # CeeDee Lamb has targets_per_game=5.0 (10/2), a real number above the threshold - kept.
    assert "CeeDee Lamb" in set(out["Name"])


def test_min_air_yards_share_never_filters_out_qb_with_null_metric(table):
    out = apply_research_filters(table, min_air_yards_share_pct=90.0)
    # The QB fixture has no receiving stats at all (air_yards_share_pct is null, not 0) -
    # a null research metric must never count as failing a "minimum X" filter.
    assert "Unresolved QB" in set(table["Name"])
    assert "Unresolved QB" in set(out["Name"])
    # CeeDee Lamb has a real, non-null air_yards_share_pct below 90 - legitimately filtered out.
    assert "CeeDee Lamb" not in set(out["Name"])


def test_position_filter(table):
    out = apply_research_filters(table, positions=["WR"])
    assert set(out["Position"].unique()) == {"WR"}


def test_salary_range_filter(table):
    out = apply_research_filters(table, min_salary=5000, max_salary=8000)
    assert (pd.to_numeric(out["Salary"]) >= 5000).all()
    assert (pd.to_numeric(out["Salary"]) <= 8000).all()


def test_name_search_filter(table):
    out = apply_research_filters(table, name_search="lamb")
    assert set(out["Name"]) == {"CeeDee Lamb"}


# ---------------------------------------------------------------------------
# Early-season sample labels
# ---------------------------------------------------------------------------
def test_early_sample_flag_at_two_games(table):
    rb = table[table["Name"] == "Rashee Rice"].iloc[0]
    assert rb["stat_games_played"] == 2
    assert rb["is_early_sample"] == True  # noqa: E712
    assert "Insufficient" in rb["sample_label"] or "insufficient" in rb["sample_label"].lower()


def test_sample_label_full_sample_at_six_games():
    dk = pd.DataFrame([_dk_row("Vet Runner", "RB", "KC", "BUF", salary=7000)])
    current = pd.DataFrame([_player_row("p_vet", "Vet Runner", "KC", "RB", games=6)])
    role = pd.DataFrame([_role_row("p_vet", "Vet Runner", "KC", "RB")])
    empty_defense = pd.DataFrame(columns=["defense_team", "position"])
    empty_team = pd.DataFrame(columns=["team"])
    t = build_matchup_analyzer_table(dk, current, pd.DataFrame(), empty_defense, empty_team, role)
    row = t.iloc[0]
    assert row["is_early_sample"] == False  # noqa: E712
    assert "Full" in row["sample_label"]


# ---------------------------------------------------------------------------
# DvP / team reporting joins + null safety
# ---------------------------------------------------------------------------
def test_dvp_extra_fields_join_correctly(table):
    rb = table[table["Name"] == "Rashee Rice"].iloc[0]
    assert rb["dvp_trend_label"] == "becoming_more_favorable"
    assert rb["games_in_sample"] == 5


def test_team_reporting_join_correctly(table):
    rb = table[table["Name"] == "Rashee Rice"].iloc[0]
    assert rb["team_season_total_yards_per_game"] == pytest.approx(360.0)
    assert rb["team_offensive_momentum_yards"] == pytest.approx(15.0)


def test_defense_join_null_when_unresolved():
    dk = pd.DataFrame([_dk_row("Ghost Player", "RB", "ZZ", "YY", salary=5000)])
    current = pd.DataFrame([_player_row("p_ghost", "Ghost Player", "ZZ", "RB")])
    role = pd.DataFrame([_role_row("p_ghost", "Ghost Player", "ZZ", "RB")])
    empty_defense = pd.DataFrame(columns=["defense_team", "position", "matchup_index", "matchup_delta"])
    empty_team = pd.DataFrame(columns=["team"])
    t = build_matchup_analyzer_table(dk, current, pd.DataFrame(), empty_defense, empty_team, role)
    row = t.iloc[0]
    assert pd.isna(row["matchup_index"])
    assert pd.isna(row["dvp_trend_label"])
    assert pd.isna(row["team_season_total_yards_per_game"])


# ---------------------------------------------------------------------------
# Export fields
# ---------------------------------------------------------------------------
def test_csv_export_contains_audit_and_visible_fields(table):
    export = build_csv_export(table)
    for col in ["stat_player_id", "match_method", "role_classification", "projection_status"]:
        assert col in export.columns
    assert "Name" in export.columns
    assert "Salary" in export.columns


def test_needs_review_export_has_reason_and_match_fields(table):
    review = needs_review_rows(table)
    export = build_needs_review_export(review)
    assert "needs_review_reason" in export.columns
    assert "match_method" in export.columns
    assert len(export) == len(review)


def test_display_table_position_aware_columns():
    dk = pd.DataFrame([_dk_row("A RB", "RB", "KC", "BUF")])
    current = pd.DataFrame([_player_row("a1", "A RB", "KC", "RB")])
    role = pd.DataFrame([_role_row("a1", "A RB", "KC", "RB")])
    t = build_matchup_analyzer_table(dk, current, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), role)
    rb_display = build_display_table(t, "RB")
    assert "Carries/Game" in rb_display.columns
    assert "Air Yards Share %" not in rb_display.columns
    wr_display = build_display_table(t, "WR")
    assert "Air Yards Share %" in wr_display.columns
    assert "Carries/Game" not in wr_display.columns


# ---------------------------------------------------------------------------
# Empty-state / no-salary-data behavior
# ---------------------------------------------------------------------------
def test_empty_reporting_marts_never_crash_the_join():
    """
    A real DK slate (at least one row - the loader itself rejects a
    zero-row CSV, see lib.dk_salary_loader.validate_salary_csv_bytes)
    against completely empty-but-correctly-shaped current/prior/defense/
    team/role marts (e.g. the pipeline hasn't populated them yet) must
    still build a safe, null-filled table - never crash, never a
    fabricated value.
    """
    dk = pd.DataFrame([_dk_row("Nobody Yet", "RB", "KC", "BUF")])
    empty_current = pd.DataFrame(columns=PLAYERS_CURRENT_EMPTY_COLUMNS)
    empty_role = pd.DataFrame(columns=ROLE_CONTEXT_COLUMNS)
    t = build_matchup_analyzer_table(dk, empty_current, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), empty_role)
    assert len(t) == 1
    row = t.iloc[0]
    assert row["match_method"] == "unmatched"
    assert pd.isna(row["projected_points"])
    assert pd.isna(row["matchup_index"])
    assert pd.isna(row["team_season_total_yards_per_game"])
    assert row["role_classification"] == "role_unresolved"

    counts = summary_counts(t)
    assert counts["player_pool_count"] == 0
    assert counts["needs_review_count"] == 1


def test_summary_counts_on_standard_scenario(table):
    counts = summary_counts(table, include_conditional=False)
    assert counts["player_pool_count"] == 2  # Rice, Lamb
    assert counts["featured_count"] == 2
    assert counts["monitor_count"] == 1
    assert counts["excluded_count"] == 1
    assert counts["inactive_count"] == 1
    assert counts["needs_review_count"] == 2  # rookie + unresolved QB
    assert counts["avg_pool_salary"] == pytest.approx((7500 + 8200) / 2)


# ---------------------------------------------------------------------------
# Chart data helpers - nulls excluded, never zero-filled
# ---------------------------------------------------------------------------
def test_salary_vs_value_chart_excludes_inactive_and_unresolved(table):
    chart_data = salary_vs_value_chart_data(table)
    names = set(chart_data["Name"])
    assert "Hurt Runner" not in names          # inactive
    assert "Unresolved QB" not in names        # role_unresolved
    assert "Rashee Rice" in names


def test_volume_vs_matchup_chart_excludes_null_matchup_index():
    dk = pd.DataFrame([_dk_row("No Matchup", "RB", "ZZ", "YY")])
    current = pd.DataFrame([_player_row("nm1", "No Matchup", "ZZ", "RB")])
    role = pd.DataFrame([_role_row("nm1", "No Matchup", "ZZ", "RB")])
    t = build_matchup_analyzer_table(dk, current, pd.DataFrame(), pd.DataFrame(columns=["defense_team", "position"]), pd.DataFrame(columns=["team"]), role)
    chart_data = volume_vs_matchup_chart_data(t, "RB")
    assert chart_data.empty


def test_volume_vs_matchup_chart_data_for_rb(table):
    chart_data = volume_vs_matchup_chart_data(table, "RB")
    assert "Rashee Rice" in set(chart_data["Name"])
    assert chart_data["touches_per_game"].notna().all()
    assert chart_data["matchup_index"].notna().all()


# ---------------------------------------------------------------------------
# AppTest coverage
# ---------------------------------------------------------------------------
def test_page_renders_without_exception_with_committed_salary_data():
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception


def test_page_filter_interactions_do_not_raise():
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception

    at.multiselect(key="ma_positions").set_value(["RB", "WR"])
    at.run()
    assert not at.exception

    at.checkbox(key="ma_include_conditional").set_value(True)
    at.run()
    assert not at.exception

    at.selectbox(key="ma_category").set_value("Needs Review")
    at.run()
    assert not at.exception

    at.number_input(key="ma_min_salary").set_value(4000)
    at.run()
    assert not at.exception


def test_page_reset_filters_button_restores_defaults():
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    at.multiselect(key="ma_positions").set_value(["QB"])
    at.run()
    assert at.session_state["ma_positions"] == ["QB"]

    at.button[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["ma_positions"] == []


def test_page_download_buttons_render():
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.download_button) >= 1


def test_page_no_salary_data_shows_info_and_stops(tmp_path, monkeypatch):
    import lib.dk_salary_loader as loader

    missing_path = str(tmp_path / "does_not_exist.csv")
    monkeypatch.setattr(loader, "CURRENT_CSV_PATH", missing_path)
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("No salary data available" in el.value for el in at.info)
