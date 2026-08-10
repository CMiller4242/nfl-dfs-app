"""Cached Parquet loaders shared by every page of the app."""

import json
import os

import pandas as pd
import streamlit as st

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

POSITIONS = ["QB", "RB", "WR", "TE"]

POSITION_COLORS = {
    "QB": "#6366F1",
    "RB": "#22C55E",
    "WR": "#F59E0B",
    "TE": "#EC4899",
}


@st.cache_data(show_spinner=False)
def load_metadata() -> dict:
    path = os.path.join(DATA_DIR, "metadata.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_players_weekly() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "players_weekly.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_players_current() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "players_current.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_defense_matchups() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "defense_matchups.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_team_stats() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "team_stats.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_team_summary() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "team_summary.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_players_prior_season_baseline() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "players_prior_season_baseline.parquet")
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_parquet(path)


def get_app_mode() -> str:
    """"in_season" or "preseason_week_1_baseline" - see dfs_data_pipeline.determine_app_mode."""
    return load_metadata().get("app_mode", "in_season")


def data_freshness_caption() -> str:
    meta = load_metadata()
    if not meta:
        return "No data found yet - run `python dfs_data_pipeline.py` to generate it."

    updated = meta.get("last_updated", "unknown")
    try:
        updated = pd.to_datetime(updated).strftime("%b %d, %Y %I:%M %p UTC")
    except Exception:
        pass

    season = meta.get("season", "?")
    if meta.get("app_mode") == "preseason_week_1_baseline":
        source_season = meta.get("source_season", "?")
        return (
            f"{season} season - no completed week yet. Week 1 Baseline Mode is active on the "
            f"Lineup Helper page, using {source_season} prior-season production. Last checked {updated}."
        )
    if meta.get("status") != "ok" or meta.get("latest_completed_week") is None:
        return (
            f"{season} season - no completed week yet (data pulls run but no games have "
            f"finished). Last checked {updated}."
        )

    # "Data through Week X" always reflects latest_completed_week (a week only
    # counts once every game in it has a final score); "Building Week Y"
    # always reflects next_slate_week (the week that hasn't happened yet).
    week_line = f"Data through Week {meta['latest_completed_week']}, {season} season"
    next_week = meta.get("next_slate_week")
    if next_week is not None:
        week_line += f" · Building Week {next_week}"
    else:
        week_line += " · season complete, no further slates"
    return f"{week_line} · last refreshed {updated}"
