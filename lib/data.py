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


def data_freshness_caption() -> str:
    meta = load_metadata()
    if not meta:
        return "No data found yet - run `python dfs_data_pipeline.py` to generate it."
    updated = meta.get("last_updated", "unknown")
    try:
        updated = pd.to_datetime(updated).strftime("%b %d, %Y %I:%M %p UTC")
    except Exception:
        pass
    return f"Data as of Week {meta.get('current_week', '?')}, {meta.get('season', '?')} season · last refreshed {updated}"
