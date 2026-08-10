"""
Single configuration module for the role/eligibility engine. Every tunable
number or vocabulary list used by depth-chart eligibility, ESPN injury
classification, and manual overrides lives here - nowhere else, and never
hardcoded inline in a Streamlit page.
"""

# ---------------------------------------------------------------------------
# Default eligible depth tiers
#
# A player at or above (numerically at-or-below) this depth_rank for their
# position is "standard_eligible_rotation" (or "confirmed_starter" at rank 1)
# regardless of anyone else's injury status - they don't need an injury path
# to be eligible. A player ranked BELOW this tier only becomes eligible via
# the injury-elevation logic in lib/eligibility.py.
# ---------------------------------------------------------------------------
DEFAULT_ELIGIBLE_DEPTH_RANK = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 2,
}

FANTASY_POSITIONS = ["QB", "RB", "WR", "TE"]

# ---------------------------------------------------------------------------
# ESPN injury status vocabulary
#
# Raw ESPN status strings -> canonical availability_classification. Anything
# not listed here falls through to "unknown" (see
# lib.espn_injuries.normalize_availability_classification) - an unrecognized
# status is never silently treated as either confirmed-unavailable or
# available.
#
# Doubtful and Questionable are explicitly NOT confirmed-unavailable - they
# are "conditional" and only ever produce a monitor/contingent scenario, per
# product decision.
# ---------------------------------------------------------------------------
CONFIRMED_UNAVAILABLE_STATUSES = {
    "out", "ir", "injured reserve", "suspended", "suspension", "inactive",
    "did not report", "reserve/retired", "physically unable to perform", "pup",
}
CONDITIONAL_STATUSES = {"questionable", "doubtful"}
AVAILABLE_STATUSES = {"healthy", "active", "probable"}

# ---------------------------------------------------------------------------
# Freshness limits
#
# Data older than these limits (or missing entirely) is treated as STALE.
# Per product decision, stale data is handled the same as a failed fetch for
# eligibility purposes (fail-closed): role_classification degrades to
# "role_unresolved" rather than asserting a confident classification off of
# data that might no longer reflect reality. This is a UI staleness warning
# too (see lib/data.py), not just an eligibility-engine internal.
# ---------------------------------------------------------------------------
INJURY_FRESHNESS_HOURS = 48
DEPTH_CHART_FRESHNESS_HOURS = 168  # 7 days - depth charts change slower than injury reports

# ---------------------------------------------------------------------------
# ESPN HTTP behavior
# ---------------------------------------------------------------------------
ESPN_REQUEST_TIMEOUT_SECONDS = 10
ESPN_MAX_RETRIES = 3
ESPN_RETRY_BACKOFF_SECONDS = 2  # exponential: 2s, 4s, 8s between attempts

# ---------------------------------------------------------------------------
# Manual overrides
# ---------------------------------------------------------------------------
MANUAL_OVERRIDE_PATH = "data/manual/eligibility_overrides.csv"
MANUAL_OVERRIDE_REQUIRED_COLUMNS = [
    "season", "week", "team", "player_id", "player_name", "position",
    "override_status", "reason", "expires_at", "updated_at",
]
# Valid values for the override_status column - the same role_classification
# vocabulary the engine itself produces, so an override always lands the
# player in a real, well-defined state.
VALID_OVERRIDE_STATUSES = {
    "confirmed_starter", "standard_eligible_rotation", "injury_elevated_backup",
    "contingent_backup", "bench_no_clear_path", "inactive",
}
