"""
Player Case Summary / Signal Alignment layer.

This module assembles a descriptive, auditable "case" for each player on the
current DK slate from signals that ALREADY EXIST elsewhere in the app - it
computes nothing new about a player's stats, role, projection, matchup, or
team environment. It consumes the row shape `lib.matchup_analyzer.
build_matchup_analyzer_table` already produces (DK slate + identity match +
projection/value + role/eligibility + Opportunity Model + defense + team
reporting, all already joined with the app's existing safety rules) and adds
five descriptive fields on top: `signal_alignment`, `case_summary`,
`primary_positive`, `primary_concern`, `positives`, `concerns`,
`data_quality_notes`, `recommendation_context`.

Deliberately NOT a score. `signal_alignment` is decided by explicit boolean
conditions over each signal category (role, opportunity, value, matchup,
team) - never a weighted sum or hidden numeric aggregation. Every condition
that fired is kept in `positives`/`concerns` as a plain, readable sentence,
so the label is always traceable to the exact evidence behind it.

Signal hierarchy (in the order positives/concerns are listed and the order
`primary_positive`/`primary_concern` are chosen from):
  A. Role / availability  - lib.eligibility's existing role_classification
  B. Opportunity           - lib.opportunity_model's existing opportunity_label
  C. Salary / value        - lib.dk_helper's existing projected_value (unchanged formula)
  D. Matchup               - lib.dk_helper/defense_reporting's existing DvP fields
  E. Team environment      - dfs_data_pipeline's existing team_reporting fields

This module reuses, and never recomputes, any of those - see each
`_evaluate_*` function's docstring for exactly which existing field(s) it
reads. It also never overrides role safety: an unresolved/unmatched/inactive
player is always routed to "Insufficient Data" before any other evaluation
runs (see `build_case_for_row`).

Evidence categories (Reporting Integrity hardening - Issue 3). Every
`_evaluate_*` function's output is one of four kinds of evidence, and the
module is careful never to blur them together:
  - POSITIVE evidence -> `positives` (e.g. "Favorable matchup...").
  - NEGATIVE evidence -> `concerns` (e.g. "Tough matchup...",
    "Declining Opportunity...") - a real, stated reason the case is weaker.
  - MISSING evidence -> `missing_evidence` - a condition required for the
    NEXT HIGHER alignment tier that simply isn't confirmed either way (not a
    concern, because nothing negative was found; not a positive, because
    nothing was confirmed). A "Mostly Supported" case with zero concerns is
    only reachable because real corroboration for "Strongly Supported" is
    absent - `missing_evidence` names exactly what that absent corroboration
    is. This module never manufactures a concern just to explain a lower
    tier; the honest alternative is to say what's missing.
  - INSUFFICIENT-SAMPLE evidence -> `sample_warnings` - a real signal exists
    but its sample (DvP distinct defensive games, or current-season games
    played for the opportunity classification) is too small to read with
    full confidence. Distinct from both a concern (no negative direction is
    claimed) and missing evidence (a direction IS present, just not yet
    reliable).

Classification precedence (deterministic, evaluated in this exact order by
`_decide_alignment` - never a weighted sum):
  1. "Insufficient Data" - role/identity could not be resolved at all
     (checked in `build_case_for_row`, before any other evaluation runs).
  2. "High Variance" - a fragile role (the disabled-by-default
     bench_no_clear_path promotion hook, or contingent_backup) OR a
     strongly-rising opportunity read that is itself low-confidence
     (early/insufficient current-season sample).
  3. "Strongly Supported" - role safe AND value favorable AND opportunity
     STRONGLY rising (not just stable) AND matchup-or-team favorable AND
     zero concerns anywhere.
  4. "Mostly Supported" - role safe AND value favorable AND opportunity
     rising-or-stable with no opportunity concern AND at most one concern
     total anywhere (that one concern, if present, is named as the primary
     concern) - OR zero concerns anywhere with at least one real positive
     (in which case `missing_evidence` explains why this isn't "Strongly
     Supported" instead).
  5. "Weak Case" - two or more concerns with little/no positive evidence.
  6. "Mixed Signals" - real positives AND real concerns both present,
     genuinely in tension.
  7. "Weak Case" (fallback) - no material positive evidence at all.
"""

import pandas as pd

# ---------------------------------------------------------------------------
# Configurable thresholds (transparent, tunable in this one place - never a
# hidden number buried in a formula). These describe EXISTING fields
# (projected_value, position_percentile_most_favorable, team_recent_form_label)
# in plain language; they introduce no new calculation.
# ---------------------------------------------------------------------------
VALUE_FAVORABLE_THRESHOLD_PTS_PER_1K = 3.0
VALUE_CONCERN_THRESHOLD_PTS_PER_1K = 2.0

MATCHUP_FAVORABLE_PERCENTILE = 65.0
MATCHUP_CONCERN_PERCENTILE = 35.0
MIN_DVP_SAMPLE_GAMES = 3

# role_classification values the existing role engine already treats as
# "safe" (role_eligible_for_pool AND role_eligible_for_top_values together -
# see lib.eligibility.ELIGIBLE_ROLE_CLASSIFICATIONS, reused by name here
# rather than redefined).
ROLE_SAFE_CLASSIFICATIONS = {"confirmed_starter", "standard_eligible_rotation", "injury_elevated_backup"}

SIGNAL_ALIGNMENT_VALUES = [
    "Strongly Supported", "Mostly Supported", "Mixed Signals",
    "High Variance", "Weak Case", "Insufficient Data",
]

CASE_SUMMARY_COLUMNS = [
    "signal_alignment", "case_summary", "primary_positive", "primary_concern",
    "positives", "concerns", "missing_evidence", "sample_warnings",
    "alignment_trigger_codes", "classification_reason",
    "data_quality_notes", "recommendation_context",
]

RECOMMENDATION_CONTEXT_TEMPLATES = {
    ("Strongly Supported", None): "Role, opportunity, value, and matchup/team signals are aligned - a strong evidence-based case, not a guarantee.",
    ("Mostly Supported", "matchup"): "Role, opportunity, and value support this play; the matchup is a concern but not a veto on its own.",
    ("Mostly Supported", "team"): "Role, opportunity, and value support this play; team context is a concern but not a veto on its own.",
    ("Mostly Supported", "none"): "Signals support this play with no major concern flagged, though not every signal is independently confirmed.",
    ("Mostly Supported", None): "Most signals support this play, with one flagged concern worth weighing.",
    ("Mixed Signals", "opportunity"): "A favorable matchup or role does not guarantee recent workload translates into volume or production.",
    ("Mixed Signals", None): "Evidence exists on both sides - weigh the positives against the concerns before using this play.",
    ("High Variance", None): "The positive case rests on a fragile role or a low-confidence/early-sample signal - treat with caution.",
    ("Weak Case", None): "Multiple meaningful concerns outweigh the available positives.",
    ("Insufficient Data", None): "Role, identity, or match data could not be confidently resolved - no honest case can be built yet.",
}


def _evaluate_role(row) -> dict:
    """Role/availability (A) - reads the EXISTING, unmodified role fields
    (`role_classification`, `role_data_freshness`, `match_method`,
    `opportunity_pool_eligible`) from lib.eligibility / lib.opportunity_model.
    Never recomputes role eligibility itself."""
    role = row.get("role_classification")
    freshness = row.get("role_data_freshness")
    match_method = row.get("match_method")
    promoted = bool(row.get("opportunity_pool_eligible")) and role == "bench_no_clear_path"
    watchlisted = bool(row.get("workload_watchlist_eligible")) and role == "bench_no_clear_path"

    if match_method == "unmatched" or role in ("role_unresolved", None) or pd.isna(role):
        return {
            "role_safe": False, "fragile": False, "insufficient": True,
            "positives": [], "concerns": [
                "Identity/match issue — player identity or role/injury data could not be confidently resolved."
            ],
        }
    if role == "inactive":
        return {
            "role_safe": False, "fragile": False, "insufficient": True,
            "positives": [], "concerns": ["Inactive — player is confirmed unavailable."],
        }

    positives, concerns = [], []
    if pd.notna(freshness) and freshness != "fresh":
        concerns.append("Role data is stale — depth chart/injury context may not reflect the latest information.")

    if role == "confirmed_starter":
        positives.append("Confirmed Starter.")
    elif role == "injury_elevated_backup":
        positives.append("Injury-Elevated role due to a confirmed absence ahead of them on the depth chart.")
    elif role == "standard_eligible_rotation":
        pass  # safe, but not a standout positive signal on its own
    elif role == "contingent_backup":
        concerns.append("Monitor Injury Status — role depends on a Questionable/Doubtful player ahead of them actually sitting out.")
    elif role == "bench_no_clear_path":
        if promoted:
            # Only reachable when OPPORTUNITY_POOL_PROMOTION_ENABLED has been
            # explicitly turned on (disabled by default) - see
            # lib.opportunity_config / lib.opportunity_model.compute_role_safety_gate.
            concerns.append(
                "Player Pool only — bench depth with no confirmed injury path; included only via an "
                "explicitly-enabled rising-workload exception."
            )
        elif watchlisted:
            concerns.append(
                "Excluded by role — bench depth with no confirmed injury path. Rising workload is noted "
                "in the Workload Watchlist for research visibility only; this is NOT Player Pool "
                "eligibility or a supported actionable play."
            )
        else:
            concerns.append("Depth / role limitation — no clear opportunity path on the current depth chart.")

    role_safe = role in ROLE_SAFE_CLASSIFICATIONS or promoted
    fragile = promoted or role == "contingent_backup"
    return {"role_safe": role_safe, "fragile": fragile, "insufficient": False, "positives": positives, "concerns": concerns}


def _evaluate_opportunity(row) -> dict:
    """Opportunity (B) - reads the EXISTING `opportunity_label`/
    `opportunity_reason`/`confidence_label` from lib.opportunity_model.
    Never recomputes the classification itself."""
    label = row.get("opportunity_label")
    confidence = row.get("confidence_label")
    reason = row.get("opportunity_reason")

    positives, concerns = [], []
    if label == "rising_opportunity":
        positives.append(reason if pd.notna(reason) and reason else "Rising Opportunity.")
    elif label == "stable_opportunity":
        positives.append("Stable Opportunity — workload holding steady versus the season average.")
    elif label == "declining_opportunity":
        concerns.append(reason if pd.notna(reason) and reason else "Declining Opportunity.")
    elif label == "limited_opportunity":
        concerns.append(reason if pd.notna(reason) and reason else "Limited Opportunity — low recent volume.")
    else:
        concerns.append("Insufficient sample to classify recent workload trend.")

    low_confidence = confidence in ("early_sample", "insufficient_sample") or pd.isna(confidence)
    sample_warnings = []
    if low_confidence and label not in ("insufficient_sample", "not_applicable"):
        confidence_text = confidence if pd.notna(confidence) else "unknown"
        sample_warnings.append(
            f"Opportunity classification ({label}) is based on a current-season sample read as "
            f"'{confidence_text}' - not yet an established sample."
        )
    return {
        "positives": positives, "concerns": concerns,
        "strong_positive": label == "rising_opportunity",
        "mild_positive": label == "stable_opportunity",
        "concern": label in ("declining_opportunity", "limited_opportunity") or label not in (
            "rising_opportunity", "stable_opportunity"
        ),
        "low_confidence": low_confidence,
        "sample_warnings": sample_warnings,
    }


def _evaluate_value(row) -> dict:
    """Salary/value (C) - reads the EXISTING `projected_value`
    (lib.dk_helper.compute_projections's unchanged formula). Reports it
    transparently against a configured display threshold; never scores it."""
    value = row.get("projected_value")
    positives, concerns = [], []
    favorable = pd.notna(value) and value >= VALUE_FAVORABLE_THRESHOLD_PTS_PER_1K
    concern = pd.isna(value) or value < VALUE_CONCERN_THRESHOLD_PTS_PER_1K

    if favorable:
        positives.append(
            f"Projected value of {value:.2f} pts/$1k is above the {VALUE_FAVORABLE_THRESHOLD_PTS_PER_1K:.1f} favorable threshold."
        )
    elif pd.isna(value):
        concerns.append("No usable projection/value is available.")
    elif concern:
        concerns.append(
            f"Projected value of {value:.2f} pts/$1k is below the {VALUE_CONCERN_THRESHOLD_PTS_PER_1K:.1f} threshold — salary is rich relative to projection."
        )
    return {"positives": positives, "concerns": concerns, "favorable": favorable, "concern": concern}


def _ordinal(value) -> str:
    n = int(round(value))
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _evaluate_matchup(row) -> dict:
    """Matchup (D) - reads the EXISTING `position_percentile_most_favorable`/
    `fantasy_points_allowed_per_game`/`league_avg_points_allowed_for_position`/
    `defensive_games_played` from defense_reporting.parquet (via
    lib.matchup_analyzer's existing join). `defensive_games_played` is the
    DISTINCT-defensive-game count (never the raw opposing-player-appearance
    count `games_in_sample`/`player_game_row_count`) - a large appearance
    count is never read as a large sample of defensive games. Every
    statement keeps its real sample size."""
    pctile = row.get("position_percentile_most_favorable")
    sample = row.get("defensive_games_played")
    fppg_allowed = row.get("fantasy_points_allowed_per_game")
    league_avg = row.get("league_avg_points_allowed_for_position")

    positives, concerns, sample_warnings = [], [], []
    sample_note = f" (sample: {int(sample)} defensive games)" if pd.notna(sample) else " (defensive-game sample size unknown)"
    low_sample = pd.isna(sample) or sample < MIN_DVP_SAMPLE_GAMES
    if low_sample:
        sample_warnings.append(
            f"Matchup sample is only {int(sample) if pd.notna(sample) else 0} distinct defensive game(s) "
            f"against this position - below the {int(MIN_DVP_SAMPLE_GAMES)}-game confidence bar."
        )
    # Insufficient defensive-game evidence caps this matchup at "not a
    # high-confidence favorable matchup," regardless of how large the
    # underlying player-row/appearance count is - see module docstring.
    favorable = pd.notna(pctile) and pctile >= MATCHUP_FAVORABLE_PERCENTILE and not low_sample
    concern = pd.notna(pctile) and pctile <= MATCHUP_CONCERN_PERCENTILE

    allowed_note = ""
    if pd.notna(fppg_allowed) and pd.notna(league_avg):
        allowed_note = f" ({fppg_allowed:.1f} vs {league_avg:.1f} league-average PPR allowed per opposing player appearance)"

    if favorable:
        positives.append(
            f"Favorable matchup — opponent is {_ordinal(pctile)} percentile in PPR allowed to this position{sample_note}.{allowed_note}"
        )
    elif concern:
        concerns.append(
            f"Tough matchup — opponent is {_ordinal(pctile)} percentile in PPR allowed to this position{sample_note}.{allowed_note}"
        )
    elif pd.notna(pctile) and pctile >= MATCHUP_FAVORABLE_PERCENTILE and low_sample:
        concerns.append(
            f"Matchup looks favorable by percentile ({_ordinal(pctile)}), but with only "
            f"{int(sample) if pd.notna(sample) else 0} distinct defensive game(s) in sample it cannot be "
            "read as a high-confidence favorable matchup yet."
        )
    elif pd.isna(pctile):
        concerns.append("Matchup data unavailable for this opponent/position.")

    return {
        "positives": positives, "concerns": concerns, "favorable": favorable, "concern": concern,
        "low_sample": low_sample, "sample_warnings": sample_warnings,
    }


def _evaluate_team(row) -> dict:
    """Team environment (E) - reads the EXISTING `team_recent_form_label`/
    `team_offensive_momentum_yards` from team_reporting.parquet (via
    lib.matchup_analyzer's existing join, itself built from
    dfs_data_pipeline's documented RECENT_FORM_HEATING_UP_PCT/
    RECENT_FORM_COOLING_OFF_PCT thresholds - reused, not redefined). Never
    infers game script, spread, implied team total, or Vegas environment -
    none of those fields exist in this project."""
    form = row.get("team_recent_form_label")
    momentum = row.get("team_offensive_momentum_yards")
    positives, concerns = [], []
    favorable = form == "heating_up"
    concern = form == "cooling_off"
    momentum_note = f", momentum {momentum:+.0f} yds/game vs season" if pd.notna(momentum) else ""

    if favorable:
        positives.append(f"Team offense is trending up (recent form: heating up{momentum_note}).")
    elif concern:
        concerns.append(f"Team offense is trending down (recent form: cooling off{momentum_note}).")
    return {"positives": positives, "concerns": concerns, "favorable": favorable, "concern": concern}


def _decide_alignment(role_eval, opp_eval, value_eval, matchup_eval, team_eval) -> str:
    """
    Descriptive classification, not a mathematical score - every branch
    below is an explicit boolean condition over the five signal
    evaluations above, never a weighted sum. See the module docstring for
    the signal hierarchy and lib/matchup_analyzer tests for the exact
    required example shapes this logic is built to satisfy.
    """
    n_concerns = sum([
        bool(role_eval["concerns"]), opp_eval["concern"], value_eval["concern"],
        matchup_eval["concern"], team_eval["concern"],
    ])
    matchup_or_team_favorable = matchup_eval["favorable"] or team_eval["favorable"]
    # A genuine opportunity case for "Strongly/Mostly Supported" requires
    # either a real rise, or a stable workload independently corroborated by
    # a favorable matchup/team signal - a merely-stable, uncorroborated
    # opportunity reading is not strong enough evidence on its own.
    strong_opportunity_case = opp_eval["strong_positive"] or (opp_eval["mild_positive"] and matchup_or_team_favorable)
    positive_strength = sum([
        role_eval["role_safe"], strong_opportunity_case, value_eval["favorable"], matchup_or_team_favorable,
    ])

    # High Variance preempts everything else: a fragile role (the narrow
    # bench_no_clear_path promotion, or a contingent/monitor-only player) or
    # a positive case that rests entirely on a rising-but-low-confidence
    # opportunity reading is never shown as confidently supported.
    if role_eval["fragile"] or (opp_eval["strong_positive"] and opp_eval["low_confidence"]):
        return "High Variance"

    if (
        role_eval["role_safe"] and value_eval["favorable"] and strong_opportunity_case
        and matchup_or_team_favorable and n_concerns == 0
    ):
        return "Strongly Supported"

    if (
        role_eval["role_safe"] and value_eval["favorable"]
        and (opp_eval["strong_positive"] or opp_eval["mild_positive"]) and not opp_eval["concern"]
        and n_concerns <= 1
    ):
        return "Mostly Supported"

    # With zero concerns raised anywhere, there's nothing to be "mixed"
    # against - treat as Mostly Supported when there's at least some
    # positive evidence, otherwise an unremarkable (but not concerning) case.
    if n_concerns == 0:
        return "Mostly Supported" if positive_strength >= 1 else "Weak Case"

    if n_concerns >= 2 and positive_strength <= 1:
        return "Weak Case"

    if positive_strength >= 1:
        return "Mixed Signals"

    return "Weak Case"


def _missing_evidence(role_eval, opp_eval, value_eval, matchup_eval, team_eval, alignment) -> list:
    """
    MISSING evidence (Issue 3) - what's absent for the NEXT HIGHER alignment
    tier, named explicitly only when nothing negative was already found in
    that category (a real concern is negative evidence, already visible in
    `concerns`; this never duplicates it as "missing" too). Only populated
    for "Mostly Supported" and "Mixed Signals" - the two tiers where a real
    gap to "Strongly Supported" exists without necessarily being a stated
    concern. Never manufactures a concern to explain the lower tier.
    """
    if alignment not in ("Mostly Supported", "Mixed Signals"):
        return []

    matchup_or_team_favorable = matchup_eval["favorable"] or team_eval["favorable"]
    strong_opportunity_case = opp_eval["strong_positive"] or (opp_eval["mild_positive"] and matchup_or_team_favorable)

    missing = []
    if not role_eval["role_safe"] and not role_eval["concerns"]:
        missing.append("Role is not confirmed safe (no Confirmed Starter/Standard Rotation/Injury-Elevated classification).")
    if not strong_opportunity_case and not opp_eval["concern"]:
        missing.append(
            "Opportunity is not independently confirmed rising - a stable read alone is not the same "
            "corroboration a Strongly Supported case requires."
        )
    if not value_eval["favorable"] and not value_eval["concern"]:
        missing.append("Projected value has not cleared the favorable threshold (though it isn't a concern either).")
    if not matchup_or_team_favorable and not matchup_eval["concern"] and not team_eval["concern"]:
        missing.append("Neither the matchup nor the team environment is independently confirmed favorable.")
    return missing


_TRIGGER_CODES = {
    "role_safe": lambda r, o, v, m, t: r["role_safe"],
    "role_concern": lambda r, o, v, m, t: bool(r["concerns"]),
    "role_fragile": lambda r, o, v, m, t: r["fragile"],
    "opportunity_rising": lambda r, o, v, m, t: o["strong_positive"],
    "opportunity_stable": lambda r, o, v, m, t: o["mild_positive"],
    "opportunity_concern": lambda r, o, v, m, t: o["concern"],
    "opportunity_low_confidence": lambda r, o, v, m, t: o["low_confidence"],
    "value_favorable": lambda r, o, v, m, t: v["favorable"],
    "value_concern": lambda r, o, v, m, t: v["concern"],
    "matchup_favorable": lambda r, o, v, m, t: m["favorable"],
    "matchup_concern": lambda r, o, v, m, t: m["concern"],
    "matchup_low_sample": lambda r, o, v, m, t: m["low_sample"],
    "team_favorable": lambda r, o, v, m, t: t["favorable"],
    "team_concern": lambda r, o, v, m, t: t["concern"],
}


def _alignment_trigger_codes(role_eval, opp_eval, value_eval, matchup_eval, team_eval) -> list:
    """Short, auditable codes for every condition that actually fired across
    the five signal evaluations - the machine-readable trace behind
    `classification_reason`. Order is fixed (role, opportunity, value,
    matchup, team) so the same evidence always produces the same code list."""
    return [
        code for code, check in _TRIGGER_CODES.items()
        if check(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    ]


def _classification_reason(alignment, role_eval, opp_eval, value_eval, matchup_eval, team_eval, missing, n_concerns) -> str:
    """Plain-language statement of why THIS alignment tier was chosen, over
    the precedence order documented in the module docstring - never a score,
    always traceable to the exact evaluations above."""
    if alignment == "Insufficient Data":
        return "Insufficient Data - role/identity could not be resolved, so no other signal is evaluated."
    if alignment == "High Variance":
        if role_eval["fragile"]:
            return "High Variance - the positive case rests on a fragile/conditional role classification."
        return "High Variance - the positive case rests on a rising-opportunity read with a low-confidence (early/insufficient) sample."
    if alignment == "Strongly Supported":
        return "Strongly Supported - role safe, value favorable, opportunity strongly rising, and matchup/team favorable, with zero concerns."
    if alignment == "Mostly Supported":
        base = f"Mostly Supported - positive evidence present with {n_concerns} concern(s) at most one allowed."
        if missing:
            base += " Missing for Strongly Supported: " + "; ".join(missing)
        return base
    if alignment == "Mixed Signals":
        base = "Mixed Signals - real positive evidence and real concerns both present, genuinely in tension."
        if missing:
            base += " Also missing for Strongly Supported: " + "; ".join(missing)
        return base
    return f"Weak Case - {n_concerns} concern(s) outweigh the available positive evidence."


_CONCERN_CATEGORY_ORDER = ["role", "opportunity", "value", "matchup", "team"]


def _primary_concern_category(role_eval, opp_eval, value_eval, matchup_eval, team_eval):
    flags = {
        "role": bool(role_eval["concerns"]), "opportunity": opp_eval["concern"],
        "value": value_eval["concern"], "matchup": matchup_eval["concern"], "team": team_eval["concern"],
    }
    for cat in _CONCERN_CATEGORY_ORDER:
        if flags[cat]:
            return cat
    return None


def build_case_for_row(row) -> dict:
    """
    The full case-summary dict for one already-built matchup-analyzer row.
    Returns exactly the `CASE_SUMMARY_COLUMNS` keys. Role safety is checked
    FIRST and unconditionally short-circuits to "Insufficient Data" for an
    unmatched/role_unresolved/inactive player - no other signal is ever
    allowed to override that.
    """
    role_eval = _evaluate_role(row)
    if role_eval["insufficient"]:
        concerns = role_eval["concerns"]
        return {
            "signal_alignment": "Insufficient Data",
            "case_summary": "Insufficient Data — " + (concerns[0] if concerns else "data could not be resolved."),
            "primary_positive": None,
            "primary_concern": concerns[0] if concerns else None,
            "positives": [],
            "concerns": concerns,
            "missing_evidence": [],
            "sample_warnings": [],
            "alignment_trigger_codes": ["insufficient_data"],
            "classification_reason": _classification_reason("Insufficient Data", role_eval, None, None, None, None, [], len(concerns)),
            "data_quality_notes": "; ".join(concerns),
            "recommendation_context": RECOMMENDATION_CONTEXT_TEMPLATES[("Insufficient Data", None)],
        }

    opp_eval = _evaluate_opportunity(row)
    value_eval = _evaluate_value(row)
    matchup_eval = _evaluate_matchup(row)
    team_eval = _evaluate_team(row)

    positives = role_eval["positives"] + opp_eval["positives"] + value_eval["positives"] + matchup_eval["positives"] + team_eval["positives"]
    concerns = role_eval["concerns"] + opp_eval["concerns"] + value_eval["concerns"] + matchup_eval["concerns"] + team_eval["concerns"]
    sample_warnings = opp_eval["sample_warnings"] + matchup_eval["sample_warnings"]

    alignment = _decide_alignment(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    primary_positive = positives[0] if positives else None
    primary_concern_cat = _primary_concern_category(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    primary_concern = concerns[0] if concerns else None
    if primary_concern_cat is None and not concerns:
        primary_concern_cat = "none"

    n_concerns = len(concerns)
    missing = _missing_evidence(role_eval, opp_eval, value_eval, matchup_eval, team_eval, alignment)
    trigger_codes = _alignment_trigger_codes(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    classification_reason = _classification_reason(
        alignment, role_eval, opp_eval, value_eval, matchup_eval, team_eval, missing, n_concerns
    )

    data_quality_bits = []
    if role_eval.get("fragile"):
        data_quality_bits.append("Role is fragile/conditional - treat eligibility as provisional.")
    if opp_eval["low_confidence"]:
        data_quality_bits.append("Opportunity classification is based on an early or insufficient sample.")
    if matchup_eval["low_sample"]:
        data_quality_bits.append("Matchup (DvP) sample size is small - read the matchup with caution.")
    data_quality_notes = "; ".join(data_quality_bits)

    context = RECOMMENDATION_CONTEXT_TEMPLATES.get(
        (alignment, primary_concern_cat), RECOMMENDATION_CONTEXT_TEMPLATES.get((alignment, None), "")
    )

    summary = f"{alignment} — {primary_positive}" if primary_positive else alignment
    if primary_concern:
        summary += f" Main concern: {primary_concern}"
    elif missing:
        summary += f" Not yet Strongly Supported — missing: {missing[0]}"

    return {
        "signal_alignment": alignment,
        "case_summary": summary,
        "primary_positive": primary_positive,
        "primary_concern": primary_concern,
        "positives": positives,
        "concerns": concerns,
        "missing_evidence": missing,
        "sample_warnings": sample_warnings,
        "alignment_trigger_codes": trigger_codes,
        "classification_reason": classification_reason,
        "data_quality_notes": data_quality_notes,
        "recommendation_context": context,
    }


def build_case_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Append the case-summary columns to an already-built matchup-analyzer
    table (see lib.matchup_analyzer.build_matchup_analyzer_table). Never
    mutates `df`; safe to call on an empty frame.
    """
    if df.empty:
        out = df.copy()
        for col in CASE_SUMMARY_COLUMNS:
            out[col] = pd.Series(dtype="object")
        return out

    cases = df.apply(build_case_for_row, axis=1, result_type="expand")
    out = pd.concat([df.reset_index(drop=True), cases.reset_index(drop=True)], axis=1)
    return out


def positives_text(value) -> str:
    """Join a `positives`/`concerns` list cell into one display string -
    used by the UI/CSV export so a list column renders as readable text."""
    if isinstance(value, (list, tuple)):
        return " | ".join(str(v) for v in value)
    return value if pd.notna(value) else ""


# ---------------------------------------------------------------------------
# UI integration helpers (Matchup Analyzer Expanded) - display columns,
# filters, and the "Mixed Signals / Review" bucket. Kept in this module
# (not lib.matchup_analyzer) since they're specifically about the case-
# summary layer; `mixed_signals_review_section` reuses
# lib.matchup_analyzer.valid_player_pool rather than redefining Valid Pool.
# ---------------------------------------------------------------------------
CASE_DISPLAY_COLUMNS = {
    "signal_alignment": "Signal Alignment",
    "primary_positive": "Primary Positive",
    "primary_concern": "Primary Concern",
}


def build_case_display_table(df: pd.DataFrame) -> pd.DataFrame:
    """The case-summary columns, display-ready (positives/concerns rendered
    as readable text, not raw Python lists)."""
    out = df.copy()
    if "positives" in out.columns:
        out["positives"] = out["positives"].apply(positives_text)
    if "concerns" in out.columns:
        out["concerns"] = out["concerns"].apply(positives_text)
    available = [c for c in CASE_DISPLAY_COLUMNS if c in out.columns]
    return out[available].rename(columns=CASE_DISPLAY_COLUMNS)


def filter_by_signal_alignment(df: pd.DataFrame, alignments=None) -> pd.DataFrame:
    """Signal-alignment multi-select filter. A no-op when nothing is selected."""
    if not alignments or "signal_alignment" not in df.columns:
        return df
    return df[df["signal_alignment"].isin(alignments)]


def mixed_signals_review_section(df: pd.DataFrame, include_conditional: bool = False) -> pd.DataFrame:
    """
    "Mixed Signals / Review" compact view (item 20): Valid Player Pool rows
    only (the exact same role-safety gate as Rising Opportunity - see
    lib.matchup_analyzer.valid_player_pool - so monitor-only, inactive,
    unresolved, and excluded players never appear here by default) whose
    case has real tension (Mixed Signals) or a fragile/low-confidence
    positive case (High Variance) worth a second look before use.
    """
    from lib.matchup_analyzer import valid_player_pool

    pool = valid_player_pool(df, include_conditional=include_conditional)
    if "signal_alignment" not in pool.columns:
        return pool.iloc[0:0]
    return pool[pool["signal_alignment"].isin(["Mixed Signals", "High Variance"])]


_LIST_VALUED_CASE_COLUMNS = ["positives", "concerns", "missing_evidence", "sample_warnings", "alignment_trigger_codes"]


def build_case_csv_export(df: pd.DataFrame) -> pd.DataFrame:
    """Audit-ready export: stable identity + every case-summary field (raw,
    including the full positives/concerns/missing_evidence/sample_warnings/
    alignment_trigger_codes lists as readable text)."""
    out = df.copy()
    for col in _LIST_VALUED_CASE_COLUMNS:
        if col in out.columns:
            out[col] = out[col].apply(positives_text)
    cols = ["Name", "Position", "TeamAbbrev", "opponent", "Salary", "projected_points", "projected_value",
            "role_classification", "opportunity_label", "stat_player_id", "match_method"] + CASE_SUMMARY_COLUMNS
    available = [c for c in cols if c in out.columns]
    return out[available].reset_index(drop=True)


def format_case_detail(row) -> dict:
    """
    The full player-detail breakdown (item 20's expander): one string per
    section, reusing fields already present on an already-built, already-
    cased matchup-analyzer row - computes nothing new.
    """
    def _get(col, default="—"):
        value = row.get(col)
        return value if pd.notna(value) and value != "" else default

    why_liked = positives_text(row.get("positives")) or "No supporting positives identified."
    what_could_break = positives_text(row.get("concerns")) or "No concerns identified."
    missing_evidence = positives_text(row.get("missing_evidence")) or "Nothing missing - every Strongly Supported condition is met or a real concern explains the gap."
    sample_warnings = positives_text(row.get("sample_warnings")) or "No sample-size warnings."

    role_context = (
        f"Role: {_get('role_display')}. {_get('eligibility_reason', '')} "
        f"Blocking context: {_get('blocking_player_names', 'none')}. "
        f"Data freshness: {_get('role_data_freshness')}."
    ).strip()

    recent_context = (
        f"Recent 2 vs Season: {_get('recent_2_vs_season_display')}. "
        f"Recent 3 vs Season: {_get('recent_3_vs_season_display')}. "
        f"{_get('latest_2_games_summary', '')} {_get('latest_3_games_summary', '')}"
    ).strip()

    salary_context = (
        f"Salary: {_get('Salary')}. Projected Points: {_get('projected_points')}. "
        f"Projected Value: {_get('projected_value')} pts/$1k. "
        f"Current Season FPPG: {_get('player_avg')}. Prior Season FPPG: {_get('prior_season_fppg')}."
    )

    matchup_context = (
        f"Matchup Index: {_get('matchup_index')}. Percentile: {_get('position_percentile_most_favorable')}. "
        f"Avg PPR allowed per opposing player appearance: {_get('fantasy_points_allowed_per_game')} vs league avg "
        f"{_get('league_avg_points_allowed_for_position')}. Sample: {_get('defensive_games_played')} distinct "
        f"defensive games ({_get('player_game_row_count')} opposing player appearances)."
    )

    team_context = (
        f"Team Total Yards/Game: {_get('team_season_total_yards_per_game')}. "
        f"Team Pass Rate: {_get('team_season_pass_rate_pct')}. "
        f"Team Offensive Momentum: {_get('team_offensive_momentum_yards')}. "
        f"Recent Form: {_get('team_recent_form_label')}."
    )

    return {
        "why_liked": why_liked,
        "what_could_break": what_could_break,
        "missing_evidence": missing_evidence,
        "sample_warnings": sample_warnings,
        "role_context": role_context,
        "recent_context": recent_context,
        "salary_context": salary_context,
        "matchup_context": matchup_context,
        "team_context": team_context,
        "data_quality_notes": _get("data_quality_notes", "None noted."),
        "classification_reason": _get("classification_reason", ""),
        "recommendation_context": _get("recommendation_context", ""),
    }
