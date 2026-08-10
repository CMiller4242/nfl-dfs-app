import pandas as pd
import pytest

from lib.player_identity import (
    build_identity_crosswalk,
    match_dk_row_to_role_context,
    normalize_name,
    normalize_team,
)


def _raw_depth_row(gsis_id, espn_id, name, team, pos_abb, pos_rank, dt="2026-09-01T00:00:00Z"):
    return {
        "dt": dt, "team": team, "player_name": name, "espn_id": espn_id, "gsis_id": gsis_id,
        "pos_grp_id": 1, "pos_grp": "Offense", "pos_id": 1, "pos_name": pos_abb,
        "pos_abb": pos_abb, "pos_slot": pos_rank, "pos_rank": pos_rank,
    }


def test_build_identity_crosswalk_uses_only_latest_snapshot():
    raw = pd.DataFrame([
        _raw_depth_row("00-1", "1", "Old Snapshot Guy", "KC", "QB", 1, dt="2026-08-01T00:00:00Z"),
        _raw_depth_row("00-1", "1", "New Snapshot Guy", "KC", "QB", 1, dt="2026-09-01T00:00:00Z"),
    ])
    crosswalk = build_identity_crosswalk(raw)
    assert len(crosswalk) == 1
    assert crosswalk.iloc[0]["player_display_name"] == "New Snapshot Guy"


def test_build_identity_crosswalk_excludes_non_fantasy_positions():
    raw = pd.DataFrame([
        _raw_depth_row("00-1", "1", "QB Guy", "KC", "QB", 1),
        _raw_depth_row("00-2", "2", "Lineman Guy", "KC", "LT", 1),
        _raw_depth_row("00-3", "3", "Defender Guy", "KC", "MLB", 1),
    ])
    crosswalk = build_identity_crosswalk(raw)
    assert set(crosswalk["position_group"]) == {"QB"}


def test_build_identity_crosswalk_drops_missing_gsis_id():
    raw = pd.DataFrame([
        _raw_depth_row("00-1", "1", "Has ID", "KC", "QB", 1),
        _raw_depth_row(None, "2", "No ID", "KC", "QB", 2),
    ])
    crosswalk = build_identity_crosswalk(raw)
    assert len(crosswalk) == 1
    assert crosswalk.iloc[0]["player_display_name"] == "Has ID"


def test_build_identity_crosswalk_empty_input():
    crosswalk = build_identity_crosswalk(pd.DataFrame())
    assert crosswalk.empty
    assert "player_id" in crosswalk.columns
    assert "espn_id" in crosswalk.columns


def test_build_identity_crosswalk_normalizes_team_via_shared_alias_map():
    # nflreadpy's own team codes are already canonical, but this proves the
    # SAME normalize_team used by DK matching is applied here too - a single
    # shared identity mapping, not two independent ones.
    raw = pd.DataFrame([_raw_depth_row("00-1", "1", "Rams Guy", "LA", "WR", 1)])
    crosswalk = build_identity_crosswalk(raw)
    assert crosswalk.iloc[0]["canonical_team"] == normalize_team("LA")


# ---------------------------------------------------------------------------
# match_dk_row_to_role_context - adversarial identity safety
# ---------------------------------------------------------------------------
def _role_context_df(rows):
    df = pd.DataFrame(rows)
    df["_norm_name"] = df["player_name"].apply(normalize_name)
    return df


def test_match_exact_hit():
    rc = _role_context_df([{"player_name": "Josh Allen", "canonical_team": "BUF", "position_group": "QB"}])
    match = match_dk_row_to_role_context("Josh Allen", "BUF", "QB", rc)
    assert match is not None
    assert match["canonical_team"] == "BUF"


def test_match_cross_team_never_matches_same_name_different_team():
    # Two different real players could plausibly share a normalized name -
    # the match must never cross team boundaries.
    rc = _role_context_df([
        {"player_name": "Same Name", "canonical_team": "NYJ", "position_group": "QB"},
    ])
    match = match_dk_row_to_role_context("Same Name", "NE", "QB", rc)
    assert match is None  # NE query must not match the NYJ row


def test_match_cross_position_never_matches():
    rc = _role_context_df([{"player_name": "Multi Sport", "canonical_team": "KC", "position_group": "WR"}])
    match = match_dk_row_to_role_context("Multi Sport", "KC", "RB", rc)
    assert match is None


def test_match_ambiguous_exact_candidates_fails_closed():
    # Two rows with the identical normalized name + team + position (data
    # quality edge case) must not be arbitrarily resolved to either one.
    rc = _role_context_df([
        {"player_name": "Duplicate Guy", "canonical_team": "KC", "position_group": "WR"},
        {"player_name": "Duplicate Guy", "canonical_team": "KC", "position_group": "WR"},
    ])
    match = match_dk_row_to_role_context("Duplicate Guy", "KC", "WR", rc)
    assert match is None


def test_match_empty_role_context_returns_none():
    assert match_dk_row_to_role_context("Anyone", "KC", "QB", pd.DataFrame()) is None


def test_match_dk_team_alias_resolves_to_canonical():
    rc = _role_context_df([{"player_name": "Rams Guy", "canonical_team": "LA", "position_group": "WR"}])
    match = match_dk_row_to_role_context("Rams Guy", "LAR", "WR", rc)  # DK alias for the Rams
    assert match is not None
