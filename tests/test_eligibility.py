import pandas as pd

from lib.eligibility import (
    BENCH_NO_CLEAR_PATH,
    CONFIRMED_STARTER,
    CONTINGENT_BACKUP,
    ELIGIBLE_ROLE_CLASSIFICATIONS,
    INACTIVE,
    INJURY_ELEVATED_BACKUP,
    ROLE_UNRESOLVED,
    STANDARD_ELIGIBLE_ROTATION,
    attach_role_context_to_dk_rows,
    compute_role_context,
)

NOW = pd.Timestamp("2026-09-07T17:00:00Z")
FRESH_DC_TS = NOW - pd.Timedelta(hours=1)
FRESH_INJ_TS = (NOW - pd.Timedelta(hours=1)).isoformat()
STALE_DC_TS = NOW - pd.Timedelta(hours=200)  # > 168h depth-chart freshness limit
STALE_INJ_TS = (NOW - pd.Timedelta(hours=100)).isoformat()  # > 48h injury freshness limit


def _dc_row(player_id, espn_id, name, team, pos, rank, ts=FRESH_DC_TS):
    return {
        "player_id": player_id, "espn_id": espn_id, "player_display_name": name,
        "canonical_team": team, "position_group": pos, "source_position": pos,
        "depth_rank": rank, "depth_chart_source_timestamp": ts,
    }


def _inj_row(espn_id, team, classification, designation=None):
    return {
        "espn_athlete_id": espn_id, "espn_team_abbrev": team,
        "availability_classification": classification,
        "injury_designation": designation or classification,
    }


def _meta(retrieved_at=FRESH_INJ_TS, failed_teams=None):
    return {"retrieved_at": retrieved_at, "failed_team_abbrevs": failed_teams or []}


def _compute(dc_rows, inj_rows, meta=None, overrides_df=None, now=NOW):
    dc = pd.DataFrame(dc_rows)
    inj = pd.DataFrame(inj_rows) if inj_rows else pd.DataFrame()
    return compute_role_context(dc, inj, injury_run_metadata=meta or _meta(), overrides_df=overrides_df, now=now)


def _row(result, player_id):
    matches = result[result["player_id"] == player_id]
    assert len(matches) == 1, f"expected exactly one row for {player_id}, got {len(matches)}"
    return matches.iloc[0]


# ---------------------------------------------------------------------------
# Standard eligible tiers - eligible regardless of anyone else's status
# ---------------------------------------------------------------------------
def test_standard_tier_eligible_regardless_of_others_injury_status():
    dc = [
        _dc_row("00-1", "1", "RB1 Out Guy", "KC", "RB", 1),
        _dc_row("00-2", "2", "RB2 Healthy Guy", "KC", "RB", 2),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        _inj_row("2", "KC", "available"),
    ]
    result = _compute(dc, inj)

    rb1 = _row(result, "00-1")
    assert rb1["role_classification"] == INACTIVE
    assert rb1["role_eligible_for_pool"] == False
    assert rb1["role_eligible_for_top_values"] == False

    rb2 = _row(result, "00-2")
    assert rb2["role_classification"] == STANDARD_ELIGIBLE_ROTATION
    assert rb2["role_eligible_for_pool"] == True
    assert rb2["role_eligible_for_top_values"] == True


def test_rank_1_is_confirmed_starter_label():
    dc = [_dc_row("00-1", "1", "The QB1", "KC", "QB", 1)]
    inj = [_inj_row("1", "KC", "available")]
    result = _compute(dc, inj)
    assert _row(result, "00-1")["role_classification"] == CONFIRMED_STARTER


# ---------------------------------------------------------------------------
# The literal spec example: QB1 Out and QB2 Out => QB3 may become
# injury_elevated_backup.
# ---------------------------------------------------------------------------
def test_qb1_out_and_qb2_out_elevates_qb3():
    dc = [
        _dc_row("00-1", "1", "Kirk Cousins", "ATL", "QB", 1),
        _dc_row("00-2", "2", "Backup Two", "ATL", "QB", 2),
        _dc_row("00-3", "3", "Backup Three", "ATL", "QB", 3),
    ]
    inj = [
        _inj_row("1", "ATL", "confirmed_unavailable", "Out"),
        _inj_row("2", "ATL", "confirmed_unavailable", "Out"),
        _inj_row("3", "ATL", "available"),
    ]
    result = _compute(dc, inj)

    qb1 = _row(result, "00-1")
    assert qb1["role_classification"] == INACTIVE

    # QB2 is itself Out - "inactive" takes priority over "elevated", it is
    # never both. Only a player who is themselves available/conditional can
    # be promoted.
    qb2 = _row(result, "00-2")
    assert qb2["role_classification"] == INACTIVE
    assert qb2["role_eligible_for_top_values"] == False

    # QB3 is available, and both higher-ranked players (QB1, QB2) are
    # confirmed_unavailable -> QB3 is the one who gets elevated.
    qb3 = _row(result, "00-3")
    assert qb3["role_classification"] == INJURY_ELEVATED_BACKUP
    assert qb3["role_eligible_for_top_values"] == True
    assert "Kirk Cousins" in qb3["eligibility_reason"]
    assert "Backup Two" in qb3["eligibility_reason"]


def test_elevated_backup_reason_never_calls_the_backup_by_the_starters_rank():
    dc = [
        _dc_row("00-1", "1", "Kirk Cousins", "ATL", "QB", 1),
        _dc_row("00-2", "2", "Backup Two", "ATL", "QB", 2),
    ]
    inj = [
        _inj_row("1", "ATL", "confirmed_unavailable", "Out"),
        _inj_row("2", "ATL", "available"),
    ]
    result = _compute(dc, inj)
    qb2 = _row(result, "00-2")
    assert qb2["role_classification"] == INJURY_ELEVATED_BACKUP
    assert qb2["role_classification"] != CONFIRMED_STARTER
    assert "Elevated role" in qb2["eligibility_reason"]
    assert "Kirk Cousins (QB1) is Out" in qb2["eligibility_reason"]


# ---------------------------------------------------------------------------
# bench_no_clear_path: any available/unknown higher-ranked player blocks
# ---------------------------------------------------------------------------
def test_bench_no_clear_path_when_any_higher_blocker_is_available():
    dc = [
        _dc_row("00-1", "1", "WR1", "KC", "WR", 1),
        _dc_row("00-2", "2", "WR2", "KC", "WR", 2),
        _dc_row("00-3", "3", "WR3", "KC", "WR", 3),
        _dc_row("00-4", "4", "WR4 Bench", "KC", "WR", 4),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        _inj_row("2", "KC", "confirmed_unavailable", "Out"),
        _inj_row("3", "KC", "available"),
        _inj_row("4", "KC", "available"),
    ]
    result = _compute(dc, inj)
    wr4 = _row(result, "00-4")
    assert wr4["role_classification"] == BENCH_NO_CLEAR_PATH
    assert wr4["role_eligible_for_pool"] == False
    assert wr4["role_eligible_for_top_values"] == False


def test_bench_no_clear_path_when_a_higher_blocker_is_unknown():
    # A blocker with no injury-source record at all ("unknown") must block
    # promotion exactly like an available player - never silently treated
    # as confirmed_unavailable.
    dc = [
        _dc_row("00-1", "1", "TE1", "KC", "TE", 1),
        _dc_row("00-2", "2", "TE2", "KC", "TE", 2),
        _dc_row("00-3", "3", "TE3 Bench", "KC", "TE", 3),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        # TE2 has no injury record at all.
        _inj_row("3", "KC", "available"),
    ]
    result = _compute(dc, inj)
    te2 = _row(result, "00-2")
    assert te2["role_classification"] == ROLE_UNRESOLVED  # TE2 itself unresolved
    te3 = _row(result, "00-3")
    assert te3["role_classification"] == BENCH_NO_CLEAR_PATH


# ---------------------------------------------------------------------------
# injury_elevated_backup: every higher-ranked player confirmed_unavailable
# ---------------------------------------------------------------------------
def test_injury_elevated_backup_when_all_higher_blockers_confirmed_unavailable():
    dc = [
        _dc_row("00-1", "1", "TE1", "KC", "TE", 1),
        _dc_row("00-2", "2", "TE2", "KC", "TE", 2),
        _dc_row("00-3", "3", "TE3 Bench", "KC", "TE", 3),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        _inj_row("2", "KC", "confirmed_unavailable", "IR"),
        _inj_row("3", "KC", "available"),
    ]
    result = _compute(dc, inj)
    te3 = _row(result, "00-3")
    assert te3["role_classification"] == INJURY_ELEVATED_BACKUP
    assert te3["role_eligible_for_pool"] == True
    assert te3["role_eligible_for_top_values"] == True
    assert "TE1" in te3["eligibility_reason"] and "TE2" in te3["eligibility_reason"]


# ---------------------------------------------------------------------------
# contingent_backup: monitor-only, never top-values-eligible
# ---------------------------------------------------------------------------
def test_contingent_backup_when_blockers_are_only_conditional():
    dc = [
        _dc_row("00-1", "1", "RB1", "KC", "RB", 1),
        _dc_row("00-2", "2", "RB2", "KC", "RB", 2),
        _dc_row("00-3", "3", "RB3 Bench", "KC", "RB", 3),
    ]
    inj = [
        _inj_row("1", "KC", "conditional", "Questionable"),
        _inj_row("2", "KC", "conditional", "Doubtful"),
        _inj_row("3", "KC", "available"),
    ]
    result = _compute(dc, inj)
    rb3 = _row(result, "00-3")
    assert rb3["role_classification"] == CONTINGENT_BACKUP
    assert rb3["role_eligible_for_pool"] == True
    assert rb3["role_eligible_for_top_values"] == False
    assert rb3["is_conditional_monitor"] == True
    assert "Contingent value" in rb3["eligibility_reason"]


def test_contingent_backup_mix_of_confirmed_and_conditional_blockers():
    # None available/unknown, but not ALL confirmed_unavailable either -
    # still only a contingent (monitor), never an automatic elevation.
    dc = [
        _dc_row("00-1", "1", "RB1", "KC", "RB", 1),
        _dc_row("00-2", "2", "RB2", "KC", "RB", 2),
        _dc_row("00-3", "3", "RB3 Bench", "KC", "RB", 3),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        _inj_row("2", "KC", "conditional", "Questionable"),
        _inj_row("3", "KC", "available"),
    ]
    result = _compute(dc, inj)
    rb3 = _row(result, "00-3")
    assert rb3["role_classification"] == CONTINGENT_BACKUP
    assert rb3["role_eligible_for_top_values"] == False


# ---------------------------------------------------------------------------
# Fail-closed: role_unresolved on unknown availability, staleness, source
# failure, and team mismatch
# ---------------------------------------------------------------------------
def test_role_unresolved_when_player_not_found_in_injury_source():
    dc = [_dc_row("00-1", "1", "Mystery Guy", "KC", "QB", 1)]
    result = _compute(dc, inj_rows=[])
    row = _row(result, "00-1")
    assert row["role_classification"] == ROLE_UNRESOLVED
    assert row["role_eligible_for_pool"] == False
    assert row["role_eligible_for_top_values"] == False
    assert "could not be confirmed" in row["eligibility_reason"]


def test_role_unresolved_when_depth_chart_is_stale():
    dc = [_dc_row("00-1", "1", "Stale Guy", "KC", "QB", 1, ts=STALE_DC_TS)]
    inj = [_inj_row("1", "KC", "available")]
    result = _compute(dc, inj)
    row = _row(result, "00-1")
    assert row["role_classification"] == ROLE_UNRESOLVED
    assert "depth chart data is stale" in row["eligibility_reason"]
    assert "injury data" not in row["eligibility_reason"]  # correct attribution, not a generic blame


def test_role_unresolved_when_injury_data_is_stale_not_depth_chart():
    dc = [_dc_row("00-1", "1", "Fresh Depth Guy", "KC", "QB", 1, ts=FRESH_DC_TS)]
    inj = [_inj_row("1", "KC", "available")]
    result = _compute(dc, inj, meta=_meta(retrieved_at=STALE_INJ_TS))
    row = _row(result, "00-1")
    assert row["role_classification"] == ROLE_UNRESOLVED
    assert "injury data is stale" in row["eligibility_reason"]
    assert "depth chart data is stale" not in row["eligibility_reason"]  # regression guard


def test_role_unresolved_when_teams_injury_fetch_failed():
    # Even though the player has an "available" row in injury_df, a failed
    # fetch for their team must still degrade the whole team to unresolved -
    # no silently trusting whatever data happened to already be present.
    dc = [_dc_row("00-1", "1", "Failed Team Guy", "KC", "QB", 1)]
    inj = [_inj_row("1", "KC", "available")]
    result = _compute(dc, inj, meta=_meta(failed_teams=["KC"]))
    row = _row(result, "00-1")
    assert row["role_classification"] == ROLE_UNRESOLVED
    assert "ESPN fetch failed" in row["eligibility_reason"]


def test_role_unresolved_on_team_mismatch_between_depth_chart_and_injury_source():
    dc = [_dc_row("00-1", "1", "Traded Guy", "KC", "QB", 1)]
    inj = [_inj_row("1", "BUF", "confirmed_unavailable", "Out")]  # different team for same espn_id
    result = _compute(dc, inj)
    row = _row(result, "00-1")
    assert row["role_classification"] == ROLE_UNRESOLVED
    assert row["availability_classification"] == "unknown"


# ---------------------------------------------------------------------------
# Adversarial identity safety: no cross-team / cross-position contamination
# ---------------------------------------------------------------------------
def test_injury_never_crosses_team_boundary_between_two_same_named_players():
    # Two different real players could share an espn_id space collision in
    # theory only if IDs matched, which they never do across real players -
    # this proves the join is purely by espn_id+team, never by name.
    dc = [
        _dc_row("00-1", "101", "Same Name", "NE", "QB", 1),
        _dc_row("00-2", "202", "Same Name", "NYJ", "QB", 1),
    ]
    inj = [
        _inj_row("101", "NE", "confirmed_unavailable", "Out"),
        _inj_row("202", "NYJ", "available"),
    ]
    result = _compute(dc, inj)
    ne_qb = _row(result, "00-1")
    nyj_qb = _row(result, "00-2")
    assert ne_qb["role_classification"] == INACTIVE
    assert nyj_qb["role_classification"] == CONFIRMED_STARTER  # unaffected by NE's injury


def test_bench_promotion_never_crosses_position_group_within_same_team():
    dc = [
        _dc_row("00-1", "1", "The RB1", "KC", "RB", 1),
        _dc_row("00-2", "2", "The RB2", "KC", "RB", 2),
        _dc_row("00-3", "3", "The RB3 Bench", "KC", "RB", 3),
        _dc_row("00-9", "9", "A WR1", "KC", "WR", 1),
    ]
    inj = [
        _inj_row("1", "KC", "confirmed_unavailable", "Out"),
        _inj_row("2", "KC", "confirmed_unavailable", "Out"),
        _inj_row("3", "KC", "available"),
        _inj_row("9", "KC", "confirmed_unavailable", "Out"),
    ]
    result = _compute(dc, inj)
    rb3 = _row(result, "00-3")
    # RB3's blockers must be [RB1, RB2] only - the WR1 injury must have zero
    # effect on RB3's classification despite being on the same team.
    assert rb3["role_classification"] == INJURY_ELEVATED_BACKUP
    assert "WR" not in rb3["blocking_player_names"]


# ---------------------------------------------------------------------------
# No leakage: every non-{confirmed_starter, standard_eligible_rotation,
# injury_elevated_backup} classification is never top-values-eligible
# ---------------------------------------------------------------------------
def test_only_the_three_eligible_classifications_are_ever_top_values_eligible():
    dc = [
        _dc_row("00-1", "1", "Starter", "KC", "QB", 1),
        _dc_row("00-2", "2", "Rotation Guy", "KC", "RB", 2),
        _dc_row("00-3", "3", "Rotation Guy2", "KC", "RB", 1),
        _dc_row("00-4", "4", "Elevated Guy", "KC", "TE", 2),
        _dc_row("00-5", "5", "Elevated Guy Blocker", "KC", "TE", 1),
        _dc_row("00-6", "6", "Contingent Guy", "KC", "WR", 4),
        _dc_row("00-7", "7", "Contingent Guy Blocker1", "KC", "WR", 1),
        _dc_row("00-71", "71", "Contingent Guy Blocker2", "KC", "WR", 2),
        _dc_row("00-72", "72", "Contingent Guy Blocker3", "KC", "WR", 3),
        _dc_row("00-8", "8", "No Path Guy", "BUF", "WR", 4),
        _dc_row("00-81", "81", "No Path Blocker", "BUF", "WR", 1),
        _dc_row("00-9", "9", "Inactive Guy", "BUF", "QB", 1),
        _dc_row("00-10", "10", "Unresolved Guy", "BUF", "RB", 1),
    ]
    inj = [
        _inj_row("1", "KC", "available"),
        _inj_row("2", "KC", "available"),
        _inj_row("3", "KC", "available"),
        _inj_row("4", "KC", "available"),
        _inj_row("5", "KC", "confirmed_unavailable", "Out"),
        _inj_row("6", "KC", "available"),
        _inj_row("7", "KC", "conditional", "Questionable"),
        _inj_row("71", "KC", "confirmed_unavailable", "Out"),
        _inj_row("72", "KC", "confirmed_unavailable", "Out"),
        _inj_row("8", "BUF", "available"),
        _inj_row("81", "BUF", "available"),
        _inj_row("9", "BUF", "confirmed_unavailable", "Out"),
        # "10" deliberately absent -> unresolved
    ]
    result = _compute(dc, inj)
    for _, row in result.iterrows():
        if row["role_classification"] in ELIGIBLE_ROLE_CLASSIFICATIONS:
            assert row["role_eligible_for_top_values"] == True, row["role_classification"]
        else:
            assert row["role_eligible_for_top_values"] == False, row["role_classification"]

    assert _row(result, "00-6")["role_classification"] == CONTINGENT_BACKUP
    assert _row(result, "00-8")["role_classification"] == BENCH_NO_CLEAR_PATH
    assert _row(result, "00-9")["role_classification"] == INACTIVE
    assert _row(result, "00-10")["role_classification"] == ROLE_UNRESOLVED


# ---------------------------------------------------------------------------
# Manual overrides: strictly scoped identity, never a guess
# ---------------------------------------------------------------------------
def _override_row(player_id, team, position, status, reason="Beat writer report"):
    return {
        "season": None, "week": None, "team": team, "player_id": player_id,
        "player_name": "Whoever", "position": position, "override_status": status,
        "reason": reason, "expires_at": None, "updated_at": None,
    }


def test_override_reclassifies_uniquely_identified_player():
    dc = [
        _dc_row("00-1", "1", "WR1", "KC", "WR", 1),
        _dc_row("00-2", "2", "WR2", "KC", "WR", 2),
        _dc_row("00-3", "3", "WR3", "KC", "WR", 3),
        _dc_row("00-4", "4", "WR4 Bench", "KC", "WR", 4),
    ]
    inj = [
        _inj_row("1", "KC", "available"), _inj_row("2", "KC", "available"),
        _inj_row("3", "KC", "available"), _inj_row("4", "KC", "available"),
    ]
    overrides = pd.DataFrame([_override_row("00-4", "KC", "WR", INJURY_ELEVATED_BACKUP)])
    result = _compute(dc, inj, overrides_df=overrides)
    wr4 = _row(result, "00-4")
    assert wr4["role_classification"] == INJURY_ELEVATED_BACKUP
    assert wr4["role_eligible_for_top_values"] == True
    assert wr4["manual_override_applied"] == True
    assert "Beat writer report" in wr4["eligibility_reason"]


def test_override_never_applies_across_team_boundary():
    dc = [_dc_row("00-1", "1", "WR1", "KC", "WR", 1)]
    inj = [_inj_row("1", "KC", "available")]
    # Same player_id/position but wrong team - must not match.
    overrides = pd.DataFrame([_override_row("00-1", "BUF", "WR", INACTIVE)])
    result = _compute(dc, inj, overrides_df=overrides)
    wr1 = _row(result, "00-1")
    assert wr1["role_classification"] == CONFIRMED_STARTER
    assert wr1["manual_override_applied"] == False


def test_override_never_applies_across_position_boundary():
    dc = [_dc_row("00-1", "1", "Two Way Guy", "KC", "WR", 1)]
    inj = [_inj_row("1", "KC", "available")]
    overrides = pd.DataFrame([_override_row("00-1", "KC", "RB", INACTIVE)])
    result = _compute(dc, inj, overrides_df=overrides)
    row = _row(result, "00-1")
    assert row["role_classification"] == CONFIRMED_STARTER
    assert row["manual_override_applied"] == False


def test_override_contingent_backup_is_pool_eligible_but_not_top_values():
    dc = [_dc_row("00-1", "1", "Bench Guy", "KC", "WR", 4)]
    inj = [_inj_row("1", "KC", "available")]
    overrides = pd.DataFrame([_override_row("00-1", "KC", "WR", CONTINGENT_BACKUP)])
    result = _compute(dc, inj, overrides_df=overrides)
    row = _row(result, "00-1")
    assert row["role_classification"] == CONTINGENT_BACKUP
    assert row["role_eligible_for_pool"] == True
    assert row["role_eligible_for_top_values"] == False
    assert row["is_conditional_monitor"] == True


# ---------------------------------------------------------------------------
# attach_role_context_to_dk_rows - Streamlit-time join safety
# ---------------------------------------------------------------------------
def _role_context_result():
    dc = [
        _dc_row("00-1", "1", "Josh Allen", "BUF", "QB", 1),
        _dc_row("00-2", "2", "Same Name", "NE", "QB", 1),
        _dc_row("00-3", "3", "Same Name", "NYJ", "QB", 1),
    ]
    inj = [
        _inj_row("1", "BUF", "available"),
        _inj_row("2", "NE", "confirmed_unavailable", "Out"),
        _inj_row("3", "NYJ", "available"),
    ]
    return _compute(dc, inj)


def test_attach_defaults_to_role_unresolved_for_unmatched_dk_row():
    rc = _role_context_result()
    dk_rows = pd.DataFrame([{"Name": "Totally Unknown Guy", "TeamAbbrev": "KC", "Position": "QB"}])
    out = attach_role_context_to_dk_rows(dk_rows, rc)
    assert out.iloc[0]["role_classification"] == ROLE_UNRESOLVED
    assert out.iloc[0]["role_eligible_for_pool"] == False
    assert out.iloc[0]["role_eligible_for_top_values"] == False


def test_attach_matches_exact_dk_row():
    rc = _role_context_result()
    dk_rows = pd.DataFrame([{"Name": "Josh Allen", "TeamAbbrev": "BUF", "Position": "QB"}])
    out = attach_role_context_to_dk_rows(dk_rows, rc)
    assert out.iloc[0]["role_classification"] == CONFIRMED_STARTER
    assert out.iloc[0]["role_eligible_for_top_values"] == True


def test_attach_never_lets_a_nyj_row_inherit_a_ne_players_injury():
    rc = _role_context_result()
    dk_rows = pd.DataFrame([{"Name": "Same Name", "TeamAbbrev": "NYJ", "Position": "QB"}])
    out = attach_role_context_to_dk_rows(dk_rows, rc)
    assert out.iloc[0]["role_classification"] == CONFIRMED_STARTER  # NYJ's own, healthy
    assert out.iloc[0]["role_eligible_for_top_values"] == True


def test_attach_dk_row_for_the_ne_player_correctly_shows_inactive():
    rc = _role_context_result()
    dk_rows = pd.DataFrame([{"Name": "Same Name", "TeamAbbrev": "NE", "Position": "QB"}])
    out = attach_role_context_to_dk_rows(dk_rows, rc)
    assert out.iloc[0]["role_classification"] == INACTIVE
    assert out.iloc[0]["role_eligible_for_top_values"] == False


def test_attach_handles_empty_role_context_without_crashing():
    dk_rows = pd.DataFrame([{"Name": "Anyone", "TeamAbbrev": "KC", "Position": "QB"}])
    out = attach_role_context_to_dk_rows(dk_rows, pd.DataFrame())
    assert out.iloc[0]["role_classification"] == ROLE_UNRESOLVED


def test_compute_role_context_empty_depth_chart_returns_empty_frame():
    result = compute_role_context(pd.DataFrame(), pd.DataFrame())
    assert result.empty
    assert "role_classification" in result.columns
