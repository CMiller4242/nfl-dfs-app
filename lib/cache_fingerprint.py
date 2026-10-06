"""
Explicit, hashed input fingerprints for cached slate-specific reporting
(Reporting Integrity hardening - Issue 4).

`st.cache_data` keys a cache entry on its FUNCTION ARGUMENTS, not on
anything the function reads from disk - a cached builder that only takes
`file_bytes` as its key but internally calls several zero-argument
`lib.data.load_*` functions will silently keep serving a stale result if
one of those underlying parquet/json files changes within the same running
process (e.g. a pipeline refresh, a role-context update, or a new DK salary
slate's metadata), because none of that is part of the cache key.

The fix here is NOT to read file contents inside a cached function and hope
Streamlit notices - it never will. Instead, every file a cached reporting
builder actually depends on is `stat()`-ed (path + mtime + size) OUTSIDE the
cache boundary, combined into one short hashed string, and passed into the
`@st.cache_data` function as an ordinary string argument. A changed file's
mtime/size changes that string, which changes the cache key, which forces a
real recompute - exactly the "ordinary hashed cache argument" the audit
calls for, never a no-argument cached function relying on disk reads alone.
"""

import hashlib
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LIB_DIR = os.path.dirname(os.path.abspath(__file__))

# Every data file the Matchup Analyzer / Lineup Helper cached builders read
# BESIDES the DK salary CSV bytes themselves (which are already their own,
# correct cache argument - see pages/3 and pages/5). Slate metadata is
# listed separately from the CSV bytes because it's a distinct file
# (season/week/source/updated_at_utc), not part of the CSV content.
REPORTING_SOURCE_FILES = [
    "metadata.json",
    "dk_slate_metadata.json",
    "players_current.parquet",
    "players_prior_season_baseline.parquet",
    "defense_reporting.parquet",
    "team_reporting.parquet",
    "player_opportunity_reporting.parquet",
    "player_role_context.parquet",
]

# Configuration modules whose tunables change the OUTPUT of the cached
# reporting builders (role-safety gates, signal-alignment thresholds) even
# though they're Python source, not data files - a config edit followed by
# a process restart is the normal deploy path, but fingerprinting them here
# means a hot-reloaded/dev-server edit is never silently served stale.
CONFIG_SOURCE_FILES = [
    os.path.join(LIB_DIR, "opportunity_config.py"),
    os.path.join(LIB_DIR, "role_config.py"),
]


def _file_token(path: str) -> str:
    """One file's identity for fingerprinting: its path plus mtime+size, or
    an explicit "missing" token - a file that disappears is a real input
    change too, never silently ignored."""
    try:
        stat = os.stat(path)
        return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"{path}:missing"


def fingerprint_files(paths) -> str:
    """A short, stable hash over a set of files' (path, mtime, size) -
    order-independent, safe to pass as an `st.cache_data` argument."""
    tokens = sorted(_file_token(p) for p in paths)
    return hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:16]


def reporting_inputs_fingerprint() -> str:
    """
    The fingerprint argument for the Matchup Analyzer's cached table builder
    (and any other cached reporting that reads the same marts): every
    player/opportunity/team/defense/role-context/slate-metadata file it
    depends on, plus the role-safety/signal-alignment config modules.
    Callers pass this alongside `file_bytes` (the DK salary CSV's own
    content) so the full set of real inputs is reflected in the cache key.
    """
    paths = [os.path.join(DATA_DIR, name) for name in REPORTING_SOURCE_FILES] + CONFIG_SOURCE_FILES
    return fingerprint_files(paths)
