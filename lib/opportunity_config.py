"""
Single configuration module for the in-season Opportunity Model
(lib/opportunity_model.py). Every tunable window, threshold, or display
label lives here - never hardcoded inline in a Streamlit page or inside the
model itself - mirroring the lib/role_config.py convention for the role
engine.
"""

# ---------------------------------------------------------------------------
# Recent-game windows
#
# ENABLED_WINDOWS is what's actually computed/displayed today. SUPPORTED_WINDOWS
# is the full set the schema/column-naming convention
# (`{metric}_last_{n}_...`) is designed to extend to later without an API or
# schema redesign - adding 4/5 to ENABLED_WINDOWS and re-running the pipeline
# is the only change required, since lib.opportunity_model's window
# aggregator is already generic over `n`.
# ---------------------------------------------------------------------------
ENABLED_WINDOWS = [2, 3]
SUPPORTED_WINDOWS = [2, 3, 4, 5]
MAXIMUM_WINDOW = 5

# ---------------------------------------------------------------------------
# Sample-size gates
# ---------------------------------------------------------------------------
# Fewer played games than this -> insufficient_sample, no classification at all.
MIN_GAMES_FOR_ANY_CLASSIFICATION = 2
# At or above this many played games, a classification is read with normal
# confidence ("established_sample"). Below it (i.e. exactly
# MIN_GAMES_FOR_ANY_CLASSIFICATION games), a classification is still
# computed from whatever games exist, but labeled "early_sample" - never
# read with the same confidence as a mature sample.
MIN_GAMES_FOR_ESTABLISHED_SAMPLE = 3

# ---------------------------------------------------------------------------
# RB classification thresholds - absolute per-game changes, last-2 vs season.
# ---------------------------------------------------------------------------
RB_RISING_TOUCHES_DELTA = 3.0
RB_RISING_CARRIES_DELTA = 3.0
RB_RISING_TARGETS_DELTA = 2.0
RB_DECLINING_TOUCHES_DELTA = -3.0
RB_DECLINING_CARRIES_DELTA = -3.0
RB_DECLINING_TARGETS_DELTA = -2.0

# ---------------------------------------------------------------------------
# WR/TE classification thresholds.
# ---------------------------------------------------------------------------
WR_TE_RISING_TARGETS_DELTA = 2.0
WR_TE_RISING_TARGET_SHARE_DELTA_PCT_POINTS = 5.0
WR_TE_RISING_AIR_YARDS_SHARE_DELTA_PCT_POINTS = 5.0
WR_TE_DECLINING_TARGETS_DELTA = -2.0
WR_TE_DECLINING_TARGET_SHARE_DELTA_PCT_POINTS = -5.0
WR_TE_DECLINING_AIR_YARDS_SHARE_DELTA_PCT_POINTS = -5.0

# ---------------------------------------------------------------------------
# QB classification thresholds - passing attempts + rushing attempts
# ("carries", the same raw field RBs use), the only confirmed volume fields
# for QB. Passing yards alone is never a primary opportunity signal (a big
# per-play outlier isn't more opportunity).
# ---------------------------------------------------------------------------
QB_RISING_ATTEMPTS_DELTA = 4.0
QB_RISING_RUSH_ATTEMPTS_DELTA = 2.0
QB_DECLINING_ATTEMPTS_DELTA = -4.0
QB_DECLINING_RUSH_ATTEMPTS_DELTA = -2.0

# ---------------------------------------------------------------------------
# "Limited opportunity" floors - below this last-2 primary-volume-per-game,
# a player is "limited_opportunity" rather than "stable_opportunity" even
# with a flat trend, since a flat-but-tiny role isn't usefully "stable."
# Primary volume metric: RB=touches/game, WR/TE=targets/game, QB=pass
# attempts/game.
# ---------------------------------------------------------------------------
RB_LIMITED_OPPORTUNITY_TOUCHES_FLOOR = 5.0
WR_TE_LIMITED_OPPORTUNITY_TARGETS_FLOOR = 2.0
QB_LIMITED_OPPORTUNITY_ATTEMPTS_FLOOR = 10.0

# ---------------------------------------------------------------------------
# Workload Watchlist / Role Review policy (role-eligibility integration).
#
# Hardening note (Reporting Integrity pass): a prior version of this policy
# let a "bench_no_clear_path" player's rising workload promote them into
# `opportunity_pool_eligible = True` - i.e. into the Valid Player Pool - by
# default. That violated the standing rule that a workload signal ALONE must
# never override bench_no_clear_path (no confirmed injury path ahead of
# them opened up): the role engine's own "no clear path" finding is role
# SAFETY, not a soft default to be out-voted by volume. It has been fixed:
# by default (OPPORTUNITY_POOL_PROMOTION_ENABLED = False below),
# bench_no_clear_path can NEVER gain `opportunity_pool_eligible` or
# `role_eligible_for_top_values` from workload alone, regardless of how
# large the workload gets. The gates below (min games, rising label,
# workload floor) now control a SEPARATE, strictly research-only signal -
# `workload_watchlist_eligible` / `workload_watchlist_reason` /
# `role_review_required` - surfaced in a clearly labeled "Workload
# Watchlist / Role Review" section, never merged into the Player Pool or
# Top Value views. See lib.opportunity_model.compute_role_safety_gate.
#
# OPPORTUNITY_POOL_PROMOTION_ENABLED is a disabled-by-default HOOK for a
# future, explicitly-approved exception (same pattern as
# OPPORTUNITY_TOP_VALUE_PROMOTION_ENABLED below): flipping it to True would
# let a player who clears every Workload Watchlist gate ALSO gain
# `opportunity_pool_eligible = True` (Player Pool only, never Top Value).
# Leave it False unless a human has explicitly decided that exception
# should ship - it must never be enabled implicitly by this pass or any
# future automated change.
# ---------------------------------------------------------------------------
OPPORTUNITY_POOL_PROMOTION_ENABLED = False
OPPORTUNITY_POOL_PROMOTION_MIN_GAMES = 2
OPPORTUNITY_POOL_PROMOTION_WORKLOAD_FLOOR = {
    "RB": 8.0,   # touches/game, last 2
    "WR": 4.0,   # targets/game, last 2
    "TE": 4.0,   # targets/game, last 2
    "QB": 20.0,  # pass attempts/game, last 2
}

# Top-Value eligibility is explicitly NOT extended by this pass - this flag
# (and the gate it would require) exists only as a hook for a future pass.
# Leaving it False means `opportunity_top_value_eligible` always mirrors the
# existing `role_eligible_for_top_values` unchanged - the opportunity model
# never promotes a player into Top Value Plays.
OPPORTUNITY_TOP_VALUE_PROMOTION_ENABLED = False
OPPORTUNITY_TOP_VALUE_PROMOTION_MIN_GAMES = 3
OPPORTUNITY_TOP_VALUE_PROMOTION_WORKLOAD_FLOOR = {
    "RB": 12.0, "WR": 6.0, "TE": 6.0, "QB": 28.0,
}

# ---------------------------------------------------------------------------
# Display labels (never show a raw enum value as the primary UI label).
# ---------------------------------------------------------------------------
OPPORTUNITY_LABEL_DISPLAY = {
    "rising_opportunity": "Rising Opportunity",
    "stable_opportunity": "Stable Opportunity",
    "declining_opportunity": "Declining Opportunity",
    "limited_opportunity": "Limited Opportunity",
    "insufficient_sample": "Insufficient Sample",
    "not_applicable": "Not Applicable",
}
CONFIDENCE_LABEL_DISPLAY = {
    "early_sample": "Early Sample",
    "established_sample": "Established Sample",
    "insufficient_sample": "Insufficient Sample",
}
POOL_PROMOTION_DISPLAY_LABEL = "Player Pool Only — Rising Workload"
WORKLOAD_WATCHLIST_DISPLAY_LABEL = "Workload Watchlist — Role Review (Not Pool-Eligible)"
