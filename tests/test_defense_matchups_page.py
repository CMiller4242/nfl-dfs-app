import os

import streamlit as st
from streamlit.testing.v1 import AppTest

import lib.data as data_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "2_Defense_Matchups.py")


def test_page_shows_refresh_message_not_a_keyerror_on_simulated_legacy_schema(
    monkeypatch, synthetic_legacy_defense_reporting
):
    """
    Before the schema guard existed, this page raised a raw, unhandled
    `KeyError: 'defensive_games_played'` on exactly this mart shape
    (verified against an earlier commit, back when the real committed mart
    itself was still on this old schema). This test locks in the fix with
    an isolated, synthetic fixture (see tests/conftest.py) rather than
    depending on the real mart's current - possibly already-regenerated -
    schema.
    """
    legacy = synthetic_legacy_defense_reporting
    assert "defensive_games_played" not in legacy.columns
    assert "games_in_sample" in legacy.columns

    monkeypatch.setattr(data_module, "load_defense_reporting", lambda: legacy)
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any(
        "older pipeline schema" in w.value and "defensive_games_played" in w.value for w in at.warning
    )
    # The guard must stop rendering the rest of the page (controls/matrix),
    # never fall through to a component that would KeyError on the missing
    # column.
    assert len(at.selectbox) == 0


def test_page_legacy_schema_guard_never_substitutes_player_row_counts(
    monkeypatch, synthetic_legacy_defense_reporting
):
    # Explicit proof of "do not replace missing defensive_games_played with
    # player-row counts": even though `games_in_sample` (the old raw-row-
    # count field) IS present on this synthetic legacy frame, the guard
    # must still fire on the missing `defensive_games_played`/
    # `player_game_row_count` rather than silently reading the row count in
    # their place.
    monkeypatch.setattr(data_module, "load_defense_reporting", lambda: synthetic_legacy_defense_reporting)
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("defensive_games_played" in w.value for w in at.warning)
    assert len(at.dataframe) == 0  # never rendered a matrix/table built from the wrong column


def test_page_renders_normally_on_simulated_current_schema(
    monkeypatch, synthetic_current_defense_reporting
):
    # Current-schema success coverage, isolated from real data state: once
    # a mart has the required columns, the guard must get out of the way
    # and the page must render its normal controls - proving the guard
    # only blocks on a genuine schema gap, not unconditionally. This test
    # passes identically whether or not the real committed mart has been
    # regenerated, since it never reads real data at all.
    current = synthetic_current_defense_reporting
    assert "defensive_games_played" in current.columns

    monkeypatch.setattr(data_module, "load_defense_reporting", lambda: current)
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.selectbox) > 0
    assert not any("older pipeline schema" in w.value for w in at.warning)


def test_page_renders_normally_once_the_real_mart_has_the_required_columns(
    monkeypatch, synthetic_legacy_defense_reporting
):
    # Same success-path proof, but derived from whatever the REAL mart
    # currently has on disk (pre- or post-refresh) plus the two columns a
    # real pipeline run would add - covers an actual regenerated-mart shape
    # (real column set/dtypes) when one is present on disk; falls back to
    # the synthetic base fixture (never a skip) when the real mart is
    # empty, so this test is deterministic and always runs either way.
    real_defense_reporting = data_module.load_defense_reporting()
    base = real_defense_reporting if not real_defense_reporting.empty else synthetic_legacy_defense_reporting

    def _regenerated_defense_reporting():
        out = base.copy()
        if "defensive_games_played" not in out.columns:
            out["player_game_row_count"] = out["games_in_sample"]
            out["defensive_games_played"] = out["games_in_sample"]
        return out

    monkeypatch.setattr(data_module, "load_defense_reporting", _regenerated_defense_reporting)
    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert len(at.selectbox) > 0
    assert not any("older pipeline schema" in w.value for w in at.warning)
