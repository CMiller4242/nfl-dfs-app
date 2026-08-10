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
schedule, so there's nothing to hand-edit week to week. Verified behavior
across the season lifecycle (see `tests/test_pipeline.py`):

- **Before Week 1 / no games played yet:** `latest_completed_week` is
  `null`, `next_slate_week` is `1`, and the pipeline still runs
  successfully - it writes empty-but-correctly-shaped Parquet files and
  sets `metadata.json["status"] = "no_completed_weeks_yet"` instead of
  failing.
- **During Week 1 (or any week) with some games played and some not:** that
  week is *not* counted as completed until every game in it has a final
  score - `latest_completed_week` stays at the previous fully-finished
  week (or `null`, during Week 1 itself).
- **After the regular season ends:** `latest_completed_week` is the final
  week and `next_slate_week` is `null` ("season complete, no further
  slates" in the app).

The app always describes this as **"Data through Week X"** (=
`latest_completed_week`) and, separately, **"Building Week Y"** (=
`next_slate_week`) - the two are never conflated.

Every pipeline run ends by logging the row count of each Parquet file it
wrote, so a stale-looking refresh is easy to diagnose from the logs alone.

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
`unmatched` and shown in the **Needs Review** table on the Lineup Helper page
(downloadable as CSV) rather than in the Player Pool - see "Value plays only
come from confident matches" below.

Every row carries `match_method`, `match_score`, `matched_player_name` (the
confirmed match, if any), and `best_candidate_name` (the closest fuzzy
candidate even when it scored below the threshold, so a near-miss is easy to
spot and fix at the source), so a projection - or the lack of one - is always
auditable back to how the player was matched.

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
  to this position, minus that position's league average (from
  `defense_matchups.parquet` - see "Team defense stats vs. matchup ratings"
  below). Looked up from the DK row's own `Position` and the opponent parsed
  out of `Game Info`, independent of whether the player itself matched, so a
  bad name match doesn't also cost you matchup context. It's left **null**
  unless that lookup actually resolves (a parseable opponent *and* matchup
  history for that defense/position) - never defaulted to a "neutral" 0.0
  guess. A confidently-matched player with an unresolvable matchup still gets
  a projection (the matchup term just contributes 0 internally), but the
  displayed `matchup_delta` stays null so it's clear that piece is missing.

Weights are named constants in `lib/dk_helper.py`, tunable in one place:

```python
MOMENTUM_ADJUSTMENT_WEIGHT = 0.20
MATCHUP_ADJUSTMENT_WEIGHT = 0.30
```

### No fallback projections - `projection_status` meanings

An unmatched or low-confidence row gets **no projection at all** - not even
a labeled one. `player_avg`, `momentum_score`, `projected_points`, and
`projected_value` are all null; DK's own `AvgPointsPerGame` is never
substituted in. `projection_status` says exactly why:

| Status | Meaning | In value tables/plots? |
|---|---|---|
| `ok` | Confident match (exact or fuzzy above threshold), real season average, usable salary | **Yes** - the only status that is |
| `review_required` | No confident player match (unmatched, or the best fuzzy candidate scored below `FUZZY_MATCH_THRESHOLD`) | No |
| `no_player_average` | Matched, but no season average is available | No |
| `no_salary` | Missing/zero/negative salary | No |

### Value plays only come from confident matches

The **Player Pool** table, **Top Value Plays** cards, and best-value
rankings all show only `projection_status == "ok"` rows - filtered to a
position first, then ranked by projected value within that subset. Every
other row (`review_required`, `no_player_average`, `no_salary`) lives
exclusively in the **Needs Review** table, so a review-required guess or a
$0-salary row can never appear as a "top value play." Needs Review shows: DK
Name, Position, Team, parsed Opponent, Salary, `match_method`, `match_score`,
the matched player name (or best fuzzy candidate if there wasn't a confident
match), and `projection_status` - and is downloadable as CSV.

### Team defense stats vs. matchup ratings

`team_summary.parquet`'s `sacks_per_game` and `turnovers_forced_per_game`
are a team's **own** defensive production (from that team's
`def_sacks` / `def_interceptions` / `def_fumbles_forced` - what its defense
did, not what was done to it). This table is informational context only and
is **never** read by the projection formula above. Matchup quality in
projections comes exclusively from `defense_matchups.parquet`
(`matchup_delta`), which is computed only from fantasy points an opposing
defense has allowed to a position - a completely separate calculation.

## GitHub Actions refresh

`.github/workflows/refresh_data.yml` runs `dfs_data_pipeline.py` every
Tuesday at 11:00 UTC (after Monday Night Football has finished) and commits
any changed Parquet/metadata files back to the branch it runs on. Only
`data/*.parquet` and `data/metadata.json` are ever staged, and the commit
step is skipped entirely (`git diff --cached --quiet`) when the pipeline
produced no changes - it never force-commits or touches anything else in
the repo. A `concurrency` group (`refresh-dfs-data`, `cancel-in-progress:
false`) means an overlapping scheduled + manual run queues instead of
racing another run's git push. Nothing in the workflow suppresses errors -
a pipeline failure or a git failure fails the step and the run shows red in
the Actions tab.

**Manual trigger:** GitHub repo -> Actions tab -> "Refresh DFS Data" ->
"Run workflow". Locally, just run `python dfs_data_pipeline.py` and commit
the changed files under `data/` yourself.

**If the app's "last refreshed" timestamp looks stale:** GitHub repo ->
Actions tab -> "Refresh DFS Data" -> check the most recent run. A red X
means the pipeline or the commit step failed (open the run for the actual
error); a run still queued or in progress means it's waiting behind the
concurrency group or hasn't reached its scheduled time yet.

## Testing

```bash
python -m pytest
```

(Use `python -m pytest`, not a bare `pytest` invocation, if you have more
than one Python environment on your machine - it guarantees the same
interpreter that has the project's dependencies installed is the one
running the tests.)

The suite (`tests/`) covers: completed-week detection across the season
lifecycle (before Week 1, mid-Week-1 with a partial slate, after the season
ends, no-completed-weeks-yet), duplicate player-week row handling, momentum
scoring for 1/2/3+ games and across a bye week, week-over-week touches
across a bye, safe division never producing infinities, defense-vs-position
deltas and position-specific percentiles, team_summary's own-defense
semantics, DK opponent parsing (home/away/alias/malformed), the full
match-method ladder (exact/fuzzy-restricted/low-confidence-unmatched) with
best-candidate tracking, the no-fallback projection policy (`review_required`
gets null projections, matchup_delta null-vs-resolved), and best-value-per-
position filtering that excludes every non-`"ok"` row.

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
  spelling can still land in the Needs Review table with no projection at
  all - that's by design (see "No fallback projections" above), not a bug.
  Check `best_candidate_name` there before assuming it's a genuine miss.
- **Matching assumes one active player per (team, position) name.** If two
  players share a normalized name on the same team and position in a given
  season, the exact-match step arbitrarily returns the first one found.
