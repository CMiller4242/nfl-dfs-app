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
    "positives", "concerns", "data_quality_notes", "recommendation_context",
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
            concerns.append(
                "Player Pool only — bench depth with no confirmed injury path; included only via rising workload."
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
    return {
        "positives": positives, "concerns": concerns,
        "strong_positive": label == "rising_opportunity",
        "mild_positive": label == "stable_opportunity",
        "concern": label in ("declining_opportunity", "limited_opportunity") or label not in (
            "rising_opportunity", "stable_opportunity"
        ),
        "low_confidence": low_confidence,
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
    `games_in_sample` from defense_reporting.parquet (via lib.matchup_analyzer's
    existing join). Every statement keeps its sample size."""
    pctile = row.get("position_percentile_most_favorable")
    sample = row.get("games_in_sample")
    fppg_allowed = row.get("fantasy_points_allowed_per_game")
    league_avg = row.get("league_avg_points_allowed_for_position")

    positives, concerns = [], []
    sample_note = f" (sample: {int(sample)} games)" if pd.notna(sample) else " (sample size unknown)"
    low_sample = pd.isna(sample) or sample < MIN_DVP_SAMPLE_GAMES
    favorable = pd.notna(pctile) and pctile >= MATCHUP_FAVORABLE_PERCENTILE
    concern = pd.notna(pctile) and pctile <= MATCHUP_CONCERN_PERCENTILE

    allowed_note = ""
    if pd.notna(fppg_allowed) and pd.notna(league_avg):
        allowed_note = f" ({fppg_allowed:.1f} vs {league_avg:.1f} league-average PPR allowed)"

    if favorable:
        positives.append(
            f"Favorable matchup — opponent is {_ordinal(pctile)} percentile in PPR allowed to this position{sample_note}.{allowed_note}"
        )
    elif concern:
        concerns.append(
            f"Tough matchup — opponent is {_ordinal(pctile)} percentile in PPR allowed to this position{sample_note}.{allowed_note}"
        )
    elif pd.isna(pctile):
        concerns.append("Matchup data unavailable for this opponent/position.")

    return {"positives": positives, "concerns": concerns, "favorable": favorable, "concern": concern, "low_sample": low_sample}


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
            "data_quality_notes": "; ".join(concerns),
            "recommendation_context": RECOMMENDATION_CONTEXT_TEMPLATES[("Insufficient Data", None)],
        }

    opp_eval = _evaluate_opportunity(row)
    value_eval = _evaluate_value(row)
    matchup_eval = _evaluate_matchup(row)
    team_eval = _evaluate_team(row)

    positives = role_eval["positives"] + opp_eval["positives"] + value_eval["positives"] + matchup_eval["positives"] + team_eval["positives"]
    concerns = role_eval["concerns"] + opp_eval["concerns"] + value_eval["concerns"] + matchup_eval["concerns"] + team_eval["concerns"]

    alignment = _decide_alignment(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    primary_positive = positives[0] if positives else None
    primary_concern_cat = _primary_concern_category(role_eval, opp_eval, value_eval, matchup_eval, team_eval)
    primary_concern = concerns[0] if concerns else None
    if primary_concern_cat is None and not concerns:
        primary_concern_cat = "none"

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

    return {
        "signal_alignment": alignment,
        "case_summary": summary,
        "primary_positive": primary_positive,
        "primary_concern": primary_concern,
        "positives": positives,
        "concerns": concerns,
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


def build_case_csv_export(df: pd.DataFrame) -> pd.DataFrame:
    """Audit-ready export: stable identity + every case-summary field (raw,
    including the full positives/concerns lists as readable text)."""
    out = df.copy()
    if "positives" in out.columns:
        out["positives"] = out["positives"].apply(positives_text)
    if "concerns" in out.columns:
        out["concerns"] = out["concerns"].apply(positives_text)
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
        f"DvP FPPG Allowed: {_get('fantasy_points_allowed_per_game')} vs league avg "
        f"{_get('league_avg_points_allowed_for_position')}. Sample: {_get('games_in_sample')} games."
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
        "role_context": role_context,
        "recent_context": recent_context,
        "salary_context": salary_context,
        "matchup_context": matchup_context,
        "team_context": team_context,
        "data_quality_notes": _get("data_quality_notes", "None noted."),
        "recommendation_context": _get("recommendation_context", ""),
    }
