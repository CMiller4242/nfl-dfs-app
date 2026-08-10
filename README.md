# NFL DFS Dashboard

A self-updating NFL DFS analytics app: a weekly GitHub Actions job pulls
player/team stats from [nflreadpy](https://github.com/nflverse/nflreadpy),
computes rolling form, momentum, and defense-matchup analytics, and a
multi-page Streamlit app reads the results - no manual CSV wrangling or
Power BI refresh required.

## Architecture

```
dfs_data_pipeline.py   Pure-function pipeline: nflreadpy -> Parquet in /data
lib/data.py             Cached Parquet loaders shared by every page
lib/dk_helper.py        DraftKings CSV parsing, player matching, projections
app.py                  Home page (multi-page Streamlit entry point)
pages/
  1_Position_Explorer.py   Per-position efficiency, volume/efficiency, trend
  2_Defense_Matchups.py    Defense-vs-position heatmap + detail
  3_DFS_Lineup_Helper.py   DK salary upload -> matched, projected player pool
tests/                  pytest suite for the pipeline and matching/projection logic
.github/workflows/refresh_data.yml   Weekly (+ manual) data refresh
```

**Raw vs. derived data.** The pipeline writes two kinds of Parquet output:

| File | Grain | Contents |
|---|---|---|
| `data/players_weekly.parquet` | one row per player per **completed** week | raw box-score stats + `touches` |
| `data/players_current.parquet` | one row per player | season-to-date aggregates + analytics (momentum, consistency, etc.) |
| `data/defense_matchups.parquet` | one row per defense x position | fantasy points allowed, league average, matchup delta/percentile |
| `data/team_summary.parquet` | one row per team | pass/rush volume, pass rate, sacks & turnovers forced per game |
| `data/team_stats.parquet` | one row per team per completed week | raw team-week stats from nflreadpy |
| `data/metadata.json` | - | season, latest completed week, next slate week, refresh status/timestamp |

The pipeline is a pure function of nflreadpy's source data - every run
recomputes every output from scratch and overwrites the Parquet files, so
re-running it never produces duplicate rows.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
```

## Run the pipeline

```bash
python dfs_data_pipeline.py
```

This auto-detects the current season and the **latest fully-completed
week** (every game in a week must have a final score - a week with a game
still in progress is never treated as complete) directly from the
schedule, so there's nothing to hand-edit week to week. If no week has
finished yet this season, the pipeline still runs successfully: it writes
empty-but-correctly-shaped Parquet files and sets
`metadata.json["status"] = "no_completed_weeks_yet"` so the app can show a
clear message instead of an error.

## Run the app

```bash
streamlit run app.py
```

## Data files the pipeline generates

See the architecture table above for the file list and grain. All
Parquet files live under `/data` and are read via `st.cache_data`-wrapped
loaders in `lib/data.py`, so they're only re-read from disk once per
process (not on every widget interaction).

## DraftKings salary CSV upload (Lineup Helper page)

Required columns (DraftKings' standard salary export):

```
Position, Name, Salary, Game Info, TeamAbbrev, AvgPointsPerGame
```

An uploaded file is used only for that browser session - it's read into
memory and is never written to disk or committed to the repo.

### How player matching works

Matching is intentionally conservative - a bad guess is worse than an
honest "couldn't match." Team codes are normalized through one shared alias
map (handles DK/nflreadpy differences like `LAR`/`LA`, `JAC`/`JAX`,
`WSH`/`WAS`, `LV`/`OAK`, `SD`/`LAC`, `STL`/`LA`), and names are normalized
for case, punctuation, apostrophes, hyphens, and suffixes (Jr/Sr/II/III/IV).

Matching is tried in this exact order, and stops at the first hit:

1. normalized name + canonical team + position - exact match
2. normalized name + canonical team - exact match (position ignored)
3. fuzzy name match, but only against candidates that already share the
   same canonical team **and** position, accepted only if the best score
   is at or above `FUZZY_MATCH_THRESHOLD` (88/100, in `lib/dk_helper.py`)

There is deliberately no unrestricted, name-only fuzzy fallback across all
teams. Anything that doesn't clear one of the three bars above is left
`unmatched` and shown in a separate review table on the Lineup Helper page
(downloadable as CSV), along with the best fuzzy score it did find, so you
can eyeball whether it's a near-miss worth fixing in the source data.

Every row carries `match_method`, `match_score`, and `matched_player_name`
so a projection is always auditable back to how (or whether) it was matched.

## Projection formula

```
projected_points = player_avg
                    + (momentum_score - player_avg) * MOMENTUM_ADJUSTMENT_WEIGHT
                    + matchup_delta * MATCHUP_ADJUSTMENT_WEIGHT

projected_value = projected_points / (Salary / 1000)
```

- **player_avg**: season-to-date average fantasy points (PPR), from completed games.
- **momentum_score**: weighted average of a player's most recent *played*
  games (50% most recent / 30% second-most-recent / 20% third-most-recent;
  bye weeks are skipped, not zeroed - a player who played weeks 1, 2, and 4
  uses those three games, not "week minus 1/2/3"). With fewer than 3 games
  played, the leading weights are renormalized to sum to 1.0.
- **matchup_delta**: the upcoming opponent's average fantasy points allowed
  to this position, minus that position's league average. Looked up from
  the DK row's own `Position` and the opponent parsed out of `Game Info` -
  independent of whether the player itself matched, so a bad name match
  doesn't also cost you matchup context.

Weights are named constants in `lib/dk_helper.py`, tunable in one place:

```python
MOMENTUM_ADJUSTMENT_WEIGHT = 0.20
MATCHUP_ADJUSTMENT_WEIGHT = 0.30
```

**Labeling, not silent guessing.** `projection_status` marks every row:
- `ok` - confident match, valid salary, real player average
- `unmatched_fallback` - no confident player match; DK's own
  `AvgPointsPerGame` is used for both `player_avg` and `momentum_score`
  (so the momentum term is exactly 0), never presented as equally confident
- `no_player_average` - no average available from any source
- `no_salary` - missing/zero/negative salary; no projection is computed

Best-value cards filter to a position **first**, then rank by projected
value within that subset, and always exclude `no_salary` and
`unmatched_fallback` rows - so a fallback projection or a $0-salary row can
never appear as a "top value play."

## GitHub Actions refresh

`.github/workflows/refresh_data.yml` runs `dfs_data_pipeline.py` every
Tuesday at 11:00 UTC (after Monday Night Football has finished) and commits
any changed Parquet/metadata files back to the branch it runs on.

**Manual trigger:** GitHub repo -> Actions tab -> "Refresh DFS Data" ->
"Run workflow". Locally, just run `python dfs_data_pipeline.py` and commit
the changed files under `data/` yourself.

## Testing

```bash
python -m pytest
```

(Use `python -m pytest`, not a bare `pytest` invocation, if you have more
than one Python environment on your machine - it guarantees the same
interpreter that has the project's dependencies installed is the one
running the tests.)

The suite (`tests/`) covers: completed-week detection (including
in-progress and no-completed-weeks-yet cases), momentum scoring for 1/2/3+
games and across a bye week, week-over-week touches across a bye, safe
division never producing infinities, defense-vs-position deltas and
position-specific percentiles, DK opponent parsing (home/away/alias/
malformed), the full match-method ladder (exact/fuzzy-restricted/
low-confidence-unmatched), the projection formula, and best-value-per-
position filtering.

## Known limitations

- **Early-season small samples.** With 1-2 games played, `consistency_score`
  is intentionally left null (it requires >= 2 games and a non-zero point
  spread), and `momentum_score` uses whatever games exist with renormalized
  weights - treat early-season momentum and matchup reads with real caution.
  `games_played` is surfaced in the UI specifically so small samples are
  never hidden.
- **Regular season only.** Both the pipeline and matchup analytics are
  scoped to `season_type == "REG"`; postseason slates aren't covered.
- **No team-level points-allowed.** nflreadpy's team-week stats have no
  scoring column, so `team_summary.parquet` deliberately doesn't include a
  points-allowed metric rather than approximate one from other fields.
- **DK/nflreadpy name and team drift.** The alias map and name
  normalization cover the common cases (team relocations/abbreviation
  differences, suffixes, punctuation), but a DK export with an unusual
  spelling can still land in the unmatched review table - that's by design
  (see "How player matching works" above), not a bug.
- **Matching assumes one active player per (team, position) name.** If two
  players share a normalized name on the same team and position in a given
  season, the exact-match step arbitrarily returns the first one found.
