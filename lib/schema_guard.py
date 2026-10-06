"""
Schema-safety guard for reporting components (Player Comparison page
additions, Defense vs Position). Presentation-only: detects that a mart on
disk predates a pipeline change (e.g. was generated before
`defensive_games_played` existed) BEFORE a component indexes into it, so
the failure mode is one actionable message, never a raw KeyError deep
inside a render call. Never substitutes a different, misleading column for
a missing one (e.g. never reads `games_in_sample`/`player_game_row_count`
in place of a missing `defensive_games_played`) - the only remedy offered
is regenerating the mart.
"""

import pandas as pd

PIPELINE_REFRESH_MESSAGE = (
    "This reporting data appears to be from an older pipeline schema and is missing required "
    "column(s): {columns}. Run `python dfs_data_pipeline.py` to regenerate the current marts, "
    "then reload this page."
)


def missing_columns(df: pd.DataFrame, required) -> list:
    """Required columns absent from `df` - an empty list means it's safe to
    read every one of `required` from this frame. A None/empty `df` is
    treated as missing everything, never as vacuously fine."""
    if df is None:
        return list(required)
    return [c for c in required if c not in df.columns]


def refresh_message(missing) -> str:
    """The actionable message to show in place of rendering a component
    that would otherwise KeyError on one of `missing`."""
    return PIPELINE_REFRESH_MESSAGE.format(columns=", ".join(missing))
