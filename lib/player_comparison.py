"""
Player Comparison / Case Detail UX (Matchup Analyzer Expanded).

Presentation-only layer: every function here reads fields already produced
by `lib.matchup_analyzer.build_matchup_analyzer_table` and
`lib.player_case_summary.build_case_summary` - it computes no new
projection, eligibility, opportunity classification, or case-summary
signal, and introduces no composite score or automatic "winner." See each
function's docstring for exactly which existing column(s) it reads.

Reuses, never reimplements, the existing research-section membership
functions (`lib.matchup_analyzer.valid_player_pool` / `featured_top_value` /
`plays_to_monitor` / `excluded_by_role_context` / `needs_review_rows` /
`inactive_players`) for `player_section_label` below, so a player's
eligibility status in a comparison is always identical to its status
everywhere else on the page.
"""

import pandas as pd

MAX_COMPARISON_PLAYERS = 4
UNAVAILABLE = "Unavailable"

# The composite identity a DK slate row is already treated as unique by
# elsewhere in this module (see lib.matchup_analyzer.rising_opportunity_
# section's own drop_duplicates subset) - reused here as the "stable
# identifier" a player-name string alone can never provide (two different
# players can share a name; they cannot share name+team+position+salary on
# the same slate).
_UID_COLUMNS = ["Name", "TeamAbbrev", "Position", "Salary"]


def slate_row_uid(row) -> str:
    """One DK slate row's stable identifier for this loaded slate - never a
    bare player name. Deterministic from the row's own already-existing
    identity fields; two rows only collide if they're genuinely identical
    on every one of name/team/position/salary."""
    return "|".join(str(row.get(c, "")) for c in _UID_COLUMNS)


def attach_row_uid(df: pd.DataFrame) -> pd.DataFrame:
    """Add `slate_row_uid` to every row of an already-built matchup table.
    Safe on an empty frame."""
    out = df.copy()
    if out.empty:
        out["slate_row_uid"] = pd.Series(dtype="object")
        return out
    out["slate_row_uid"] = out.apply(slate_row_uid, axis=1)
    return out


def comparison_label(row) -> str:
    """Selector/column label - ALWAYS shows team + position so two players
    sharing a name are never ambiguous, exactly matching this task's
    requirement that duplicate names must be distinguishable."""
    name = row.get("Name")
    name = name if pd.notna(name) and name != "" else "Unknown Player"
    team = row.get("TeamAbbrev")
    team = team if pd.notna(team) and team != "" else "FA"
    position = row.get("Position")
    position = position if pd.notna(position) and position != "" else "?"
    salary = row.get("Salary")
    salary_text = f"${int(salary):,}" if pd.notna(salary) else "salary unavailable"
    return f"{name} ({team} - {position}, {salary_text})"


def candidate_players(df: pd.DataFrame, position: str = None) -> pd.DataFrame:
    """
    Rows eligible to be ADDED to a comparison - optionally restricted to one
    position (the default "same-position comparison" behavior; pass None or
    "All" to lift it). Deliberately does NOT filter by role eligibility,
    match confidence, or any other status - an inactive/excluded/unresolved
    player must remain selectable so their restriction is visibly shown,
    never silently hidden from comparison.
    """
    if df.empty:
        return df
    out = df
    if position and position != "All":
        out = out[out["Position"] == position]
    return out


def resolve_selected_uids(df_with_uid: pd.DataFrame, selected_uids) -> tuple:
    """
    Split a previously-selected uid list into (still_valid, stale) against
    the CURRENT slate's uids - used both to decide what the comparison
    selector can legally show as selected (Streamlit raises if a widget's
    value contains an option that no longer exists) and to explain a salary-
    file change that silently would have dropped a selection. Order of
    `still_valid` matches the input order, never resorted.
    """
    current = set(df_with_uid["slate_row_uid"]) if not df_with_uid.empty else set()
    still_valid = [u for u in selected_uids if u in current]
    stale = [u for u in selected_uids if u not in current]
    return still_valid, stale


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _fmt_text(value) -> str:
    return UNAVAILABLE if _missing(value) else str(value)


def _fmt_money(value) -> str:
    return UNAVAILABLE if _missing(value) else f"${float(value):,.0f}"


def _fmt1(value) -> str:
    return UNAVAILABLE if _missing(value) else f"{float(value):.1f}"


def _fmt2(value) -> str:
    return UNAVAILABLE if _missing(value) else f"{float(value):.2f}"


def _fmt_int(value) -> str:
    return UNAVAILABLE if _missing(value) else f"{int(value)}"


# ---------------------------------------------------------------------------
# Eligibility section (reused, never reimplemented) - see module docstring.
# ---------------------------------------------------------------------------
def _section_funcs():
    # Imported lazily to avoid a circular import (lib.matchup_analyzer does
    # not import this module), and so test doubles can monkeypatch
    # lib.matchup_analyzer's functions if ever needed.
    from lib.matchup_analyzer import (
        excluded_by_role_context,
        featured_top_value,
        inactive_players,
        needs_review_rows,
        plays_to_monitor,
        valid_player_pool,
    )

    return [
        ("Inactive", inactive_players),
        ("Needs Review", needs_review_rows),
        ("Excluded by Role Context", excluded_by_role_context),
        ("Plays to Monitor", plays_to_monitor),
        ("Featured / Top Value", featured_top_value),
        ("Valid Player Pool", valid_player_pool),
    ]


def player_section_label(row) -> str:
    """
    Which existing research section (Valid Player Pool / Featured-Top Value /
    Plays to Monitor / Excluded by Role Context / Needs Review / Inactive)
    this one player's row belongs to - decided by calling the EXACT SAME
    section-membership functions the rest of the page already uses (see
    lib.matchup_analyzer), on a one-row frame, never a reimplementation of
    their conditions. A row can match more than one section's criteria in
    rare edge cases (e.g. Needs Review vs Excluded); the first match in the
    checked order above is reported, consistent with how the page's own
    category list is ordered.
    """
    one_row = pd.DataFrame([row]) if not isinstance(row, pd.DataFrame) else row
    for label, func in _section_funcs():
        if not func(one_row).empty:
            return label
    return "Unclassified"


# ---------------------------------------------------------------------------
# Comparison table - metrics as rows, players as columns.
# ---------------------------------------------------------------------------
COMPARISON_METRICS = [
    ("Salary", lambda r: _fmt_money(r.get("Salary"))),
    ("Projected Fantasy Points", lambda r: _fmt1(r.get("projected_points"))),
    ("Projected Value (pts/$1k)", lambda r: _fmt2(r.get("projected_value"))),
    ("Role Classification", lambda r: _fmt_text(r.get("role_display", r.get("role_classification")))),
    ("Role Data Freshness", lambda r: _fmt_text(r.get("role_data_freshness"))),
    ("Eligibility Section", None),  # filled in from player_section_label, not a plain column read
    ("Eligibility Context", lambda r: _fmt_text(r.get("eligibility_reason"))),
    ("Opportunity Label", lambda r: _fmt_text(r.get("opportunity_label_display", r.get("opportunity_label")))),
    ("Opportunity Reason", lambda r: _fmt_text(r.get("opportunity_reason"))),
    ("Opportunity Confidence", lambda r: _fmt_text(r.get("opportunity_confidence_display", r.get("confidence_label")))),
    ("Matchup Favorability Percentile", lambda r: _fmt1(r.get("position_percentile_most_favorable"))),
    ("Avg PPR / Opposing Player Appearance", lambda r: _fmt2(r.get("fantasy_points_allowed_per_game"))),
    ("Distinct Defensive Games in Sample", lambda r: _fmt_int(r.get("defensive_games_played"))),
    ("Team Recent Form", lambda r: _fmt_text(r.get("team_recent_form_label"))),
    ("Team Offensive Momentum (yds)", lambda r: _fmt1(r.get("team_offensive_momentum_yards"))),
    ("Signal Alignment (Case Summary)", lambda r: _fmt_text(r.get("signal_alignment"))),
]

# Columns this comparison reads from an already-built matchup/case row.
# Used by the page to schema-guard BEFORE rendering - see lib.schema_guard.
# Listed by real source mart so a missing one names exactly which pipeline
# output is stale (never silently substituted).
REQUIRED_COMPARISON_COLUMNS = [
    "Name", "TeamAbbrev", "Position", "Salary",                       # DK slate itself
    "projected_points", "projected_value",                            # lib.dk_helper projection formula
    "role_classification", "role_data_freshness", "eligibility_reason",  # player_role_context.parquet
    "opportunity_label", "opportunity_reason", "confidence_label",    # player_opportunity_reporting.parquet
    "position_percentile_most_favorable", "fantasy_points_allowed_per_game",
    "defensive_games_played",                                         # defense_reporting.parquet
    "team_recent_form_label", "team_offensive_momentum_yards",        # team_reporting.parquet
    "signal_alignment",                                               # lib.player_case_summary
]


def build_comparison_table(selected_rows: pd.DataFrame) -> pd.DataFrame:
    """
    Side-by-side comparison: metrics as rows, players as columns, in
    selection order. Every cell traces to one already-existing field on the
    matchup/case-summary row (see COMPARISON_METRICS) - nothing here
    computes a new projection, classification, or composite score, and no
    column is ranked, highlighted, or declared a "winner." A missing value
    always renders as "Unavailable", never a fabricated 0 or blank, and
    missing evidence is never recast as a negative finding - this table
    only ever shows what IS known, formatted plainly.
    """
    if selected_rows.empty:
        return pd.DataFrame()

    series_list = []
    for _, row in selected_rows.iterrows():
        col_label = comparison_label(row)
        section = player_section_label(row)
        values = {}
        for metric_label, getter in COMPARISON_METRICS:
            values[metric_label] = _fmt_text(section) if getter is None else getter(row)
        series_list.append(pd.Series(values, name=col_label))

    order = [label for label, _ in COMPARISON_METRICS]
    result = pd.concat(series_list, axis=1)
    return result.reindex(order)
