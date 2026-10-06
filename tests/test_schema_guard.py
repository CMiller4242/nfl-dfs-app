import pandas as pd

from lib.schema_guard import missing_columns, refresh_message


def test_missing_columns_empty_when_all_present():
    df = pd.DataFrame({"a": [1], "b": [2]})
    assert missing_columns(df, ["a", "b"]) == []


def test_missing_columns_names_exactly_whats_absent():
    df = pd.DataFrame({"a": [1]})
    assert missing_columns(df, ["a", "b", "c"]) == ["b", "c"]


def test_missing_columns_ignores_null_values_only_cares_about_presence():
    # A column that exists but is entirely null is NOT "missing" - that's a
    # normal missing-VALUE case the UI renders as "Unavailable," not a
    # schema problem requiring a pipeline refresh.
    df = pd.DataFrame({"a": [None, None]})
    assert missing_columns(df, ["a"]) == []


def test_missing_columns_on_none_dataframe_reports_everything_missing():
    assert missing_columns(None, ["a", "b"]) == ["a", "b"]


def test_missing_columns_on_empty_dataframe_with_no_columns():
    df = pd.DataFrame()
    assert missing_columns(df, ["a", "b"]) == ["a", "b"]


def test_refresh_message_names_the_missing_columns_and_the_fix():
    msg = refresh_message(["defensive_games_played", "player_game_row_count"])
    assert "defensive_games_played" in msg
    assert "player_game_row_count" in msg
    assert "dfs_data_pipeline.py" in msg
