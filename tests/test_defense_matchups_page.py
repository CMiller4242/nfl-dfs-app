import os

import streamlit as st
from streamlit.testing.v1 import AppTest

import lib.data as data_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(REPO_ROOT, "pages", "2_Defense_Matchups.py")


def test_page_renders_a_refresh_message_not_a_keyerror_on_the_real_committed_data():
    """
    IMPORTANT: the real, currently-committed `data/defense_reporting.parquet`
    predates the prior Reporting Integrity pass's pipeline change and
    genuinely lacks `defensive_games_played`/`player_game_row_count` (the
    pipeline CODE was fixed, but the mart was never regenerated - that
    requires network access to nflreadpy this environment doesn't have).

    Before this pass's schema guard, this page raised a raw, unhandled
    `KeyError: 'defensive_games_played'` on real data (verified against the
    prior commit). This test locks in the fix: the SAME real data now
    produces an actionable refresh message, never a crash.
    """
    real_defense_reporting = data_module.load_defense_reporting()
    assert "defensive_games_played" not in real_defense_reporting.columns, (
        "data/defense_reporting.parquet now has defensive_games_played - the mart was "
        "regenerated since this test was written. Re-check whether the legacy-schema "
        "scenario below still needs a monkeypatch to exercise the guard."
    )

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


def test_page_legacy_schema_guard_never_substitutes_player_row_counts():
    # Explicit proof of the task's "do not replace missing
    # defensive_games_played with player-row counts" requirement: even
    # though `games_in_sample` (the old raw-row-count field) IS present on
    # this real legacy frame, the guard must still fire on the missing
    # `defensive_games_played`/`player_game_row_count` rather than silently
    # reading the row count in their place.
    real_defense_reporting = data_module.load_defense_reporting()
    assert "games_in_sample" in real_defense_reporting.columns
    assert "defensive_games_played" not in real_defense_reporting.columns

    st.cache_data.clear()
    at = AppTest.from_file(PAGE_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("defensive_games_played" in w.value for w in at.warning)
    assert len(at.dataframe) == 0  # never rendered a matrix/table built from the wrong column


def test_page_renders_normally_once_the_mart_has_the_required_columns(monkeypatch):
    # Once a (simulated) regenerated mart has the required columns, the
    # guard must get out of the way and the page must render its normal
    # controls - proving the guard only blocks on a genuine schema gap, not
    # unconditionally.
    real_defense_reporting = data_module.load_defense_reporting()

    def _regenerated_defense_reporting():
        out = real_defense_reporting.copy()
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
