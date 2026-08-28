# NFL DFS Dashboard

A self-updating NFL DFS analytics app: a weekly GitHub Actions job pulls
player/team stats from [nflreadpy](https://github.com/nflverse/nflreadpy),
computes rolling form, momentum, and defense-matchup analytics, and a
multi-page Streamlit app reads the results - no manual CSV wrangling or
Power BI refresh required.

## Architecture

```
dfs_data_pipeline.py   Pure-function pipeline: nflreadpy -> Parquet in /data
refresh_role_context.py  Standalone CLI: depth chart + injury role/eligibility refresh
load_dk_salaries.py     Standalone CLI: validate + load a DK salary CSV as the committed backend slate
lib/data.py             Cached Parquet/JSON loaders shared by every page
lib/dk_helper.py        DraftKings CSV parsing, player matching, projections
lib/dk_salary_loader.py Shared DK salary CSV validation + slate metadata I/O (app + CLI use the same code)
lib/player_identity.py  Canonical player identity, team-code aliasing, strict DK<->role matching
lib/role_config.py      Single config module for eligibility tiers, injury vocabulary, freshness limits
lib/espn_injuries.py    ESPN roster/injury ingestion (HTTP retries, schema validation, status classification)
lib/eligibility.py      Role/eligibility engine (depth-chart + injury -> role_classification)
lib/manual_overrides.py Loader/validator for the manually-maintained eligibility override CSV
lib/team_trends.py      Reusable, non-UI filter/sort/KPI/formatting transforms for Team Trends
lib/defense_trends.py   Reusable, non-UI filter/pivot/sort/formatting transforms for Defense vs Position
app.py                  Home page (multi-page Streamlit entry point)
pages/
  1_Position_Explorer.py   Per-position efficiency, volume/efficiency, trend
  2_Defense_Matchups.py    Defense-vs-position DvP matrix, position detail, recent-trend chart
  3_DFS_Lineup_Helper.py   Committed/uploaded DK salary CSV -> matched, projected, role-filtered player pool
  4_Team_Trends.py         Team offensive trends/rankings - yards, pass rate, momentum, WoW change
tests/                  pytest suite for the pipeline, matching/projection, salary loading, team/defense trends, and role/eligibility logic
data/manual/eligibility_overrides.csv   Manually-maintained, validated, expiring eligibility overrides
data/dk_salaries/current.csv            Committed backend salary slate (default active slate on startup)
data/dk_salaries/archive/               Past current.csv snapshots, one per reload
data/dk_slate_metadata.json             season/week/source/updated_at_utc/row_count/filename for current.csv
.github/workflows/refresh_data.yml           Weekly (+ manual) player/team stat refresh
.github/workflows/refresh_role_context.yml   Separate, faster-cadence depth-chart/injury refresh
```

**Raw vs. derived data.** The pipeline writes two kinds of Parquet output:

| File | Grain | Contents |
|---|---|---|
| `data/players_weekly.parquet` | one row per player per **completed** week | raw box-score stats + `touches` |
| `data/players_current.parquet` | one row per player | season-to-date aggregates + analytics (momentum, consistency, etc.) |
| `data/defense_reporting.parquet` | one row per (season, defense, position) | DvP mart - points allowed, league average, matchup index/delta, rank/percentile, recent trend - see "Defense vs Position" below |
| `data/defense_position_weekly.parquet` | one row per (season, defense, position, week) | week-by-week points allowed, feeds the recent-trend chart and DvP's recent-form window |
| `data/team_summary.parquet` | one row per team | pass/rush volume, pass rate, sacks & turnovers forced per game, defensive pressure events/game |
| `data/team_stats.parquet` | one row per team per completed week | raw team-week stats from nflreadpy |
| `data/team_reporting.parquet` | one row per team | offensive trends/rankings mart - see "Team Trends" below |
| `data/metadata.json` | - | season, latest completed week, next slate week, refresh status/timestamp |
| `data/depth_charts_current.parquet` | one row per (player, position group) | canonical identity crosswalk + depth rank, from nflreadpy's `load_depth_charts()` |
| `data/injuries_current.parquet` | one row per rostered ESPN athlete | ESPN roster/injury status, classified into the role-engine's availability vocabulary |
| `data/player_role_context.parquet` | one row per (player, position group) | `role_classification`, eligibility flags, and the reasoning behind them - see "Depth chart & injury role/eligibility engine" below |
| `data/depth_chart_metadata.json` / `data/injury_metadata.json` | - | per-source retrieval timestamp, success/failure detail, staleness inputs |

The pipeline is a pure function of nflreadpy's source data - every run
recomputes every output from scratch and overwrites the Parquet files, so
re-running it never produces duplicate rows. Depth-chart/injury/role-context
files follow the same "pure recomputation" rule for `player_role_context.parquet`
specifically, but the two *source* snapshots (`depth_charts_current.parquet`,
`injuries_current.parquet`) are the one exception - see the fail-closed
refresh behavior below.

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

## DraftKings salary data (Lineup Helper page)

Required columns (DraftKings' standard salary export):

```
Position, Name, Salary, Game Info, TeamAbbrev, AvgPointsPerGame
```

The Lineup Helper page has **two** salary data sources, and always makes
clear which one is active:

- **Committed backend salary file** - `data/dk_salaries/current.csv`. This
  is the **default active slate on startup**, loaded automatically whenever
  it exists, with no upload needed. It's a real file in the repo (not
  gitignored), so it works on any deployment - including Streamlit Community
  Cloud, which has no writable/persistent filesystem for the running app
  itself to save an upload to.
- **Session upload override** - the page's file uploader always remains
  available. Uploading a CSV there overrides the committed file for **that
  browser session only** - it's read into memory and is **never** written
  back to disk or committed, on any deployment.

The page shows a small status row - **Salary data source**, **Slate
season**, **Slate week**, **File last updated** - so it's always obvious
which file is active and how stale it is. Session uploads don't carry
season/week metadata (there's nothing to read it from), so those show as
`—` for an active upload; the committed file's season/week/timestamp come
from `data/dk_slate_metadata.json` (see below).

If neither a committed file nor a session upload is present, the page shows
a clear empty-state prompt instead of an error - `data/dk_salaries/` and
`data/dk_slate_metadata.json` don't exist in this repo until you load a
real slate (see below); no placeholder/fake salary data is ever checked in.

### Manually updating the committed salary slate

There is no automated fetch of DraftKings' salary data (DK has no public
API for it) - loading a new slate is a manual, local, one-command step:

```bash
# 1. Download the current week's salary CSV from draftkings.com yourself.

# 2. Validate it and load it as the committed backend slate:
python load_dk_salaries.py path/to/DKSalaries.csv --season 2026 --week 1

# 3. Review what changed, then commit it:
git status
git add data/dk_salaries/ data/dk_slate_metadata.json
git commit -m "Load Week 1 DK salary slate"
git push
```

What `load_dk_salaries.py` does (`lib/dk_salary_loader.py` holds the actual
validation/metadata logic, shared with the app so both apply identical
rules):

1. **Validates** the source CSV - all six required columns present, and at
   least one player row (a header-only or fully-blank export is rejected
   with a specific error, nothing is written).
2. **Archives** the existing `data/dk_salaries/current.csv`, if any, to
   `data/dk_salaries/archive/<old-season>-wk<old-week>-<timestamp>.csv` -
   past slates are preserved, not overwritten silently.
3. **Copies** the validated CSV to `data/dk_salaries/current.csv`.
4. **Writes** `data/dk_slate_metadata.json`: `season`, `week`, `source`
   (`manual_copy` by default, overridable with `--source`), `updated_at_utc`,
   `row_count`, `filename`.

`--season` and `--week` are required arguments - a DK salary CSV doesn't
self-describe which slate it's for, so this is never guessed at.

**Safety notes:**

- The script **only** ever touches `data/dk_salaries/` and
  `data/dk_slate_metadata.json` - nothing else in the repo.
- It never runs automatically (no scheduled workflow calls it) and never
  touches an in-session upload - those two paths are completely independent.
- **Never place personal lineups, exposure/ownership data, contest
  entries/standings, or bankroll figures anywhere under `data/dk_salaries/`**
  - only the public DraftKings salary export belongs there, and this
  directory is committed to the repo. `.gitignore` includes filename guards
  (`*lineup*.csv`, `*exposure*.csv`, `*entries*.csv`, `*contest-standings*.csv`,
  `*bankroll*.csv`) as a backstop, but the real safeguard is simply not
  putting that data in this folder.

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
  `defense_reporting.parquet` - see "Defense vs Position" below). Looked up
  from the DK row's own `Position` and the opponent parsed out of `Game Info`,
  independent of whether the player itself matched, so a bad name match
  doesn't also cost you matchup context. It's left **null** unless that
  lookup actually resolves (a parseable opponent *and* matchup history for
  that defense/position) - never defaulted to a "neutral" 0.0 guess. A
  confidently-matched player with an unresolvable matchup still gets a
  projection (the matchup term just contributes 0 internally), but the
  displayed `matchup_delta` stays null so it's clear that piece is missing.
  `matchup_index`, `position_rank_most_favorable`, and
  `position_percentile_most_favorable` are carried onto the same row purely
  for display (never fed into the formula) - the Player Pool table shows
  them alongside `matchup_delta`.

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

`team_summary.parquet`'s `sacks_per_game`, `turnovers_forced_per_game`, and
`defensive_pressure_events_per_game` are a team's **own** defensive
production (from that team's `def_sacks` / `def_interceptions` /
`def_fumbles_forced` / `def_qb_hits` - what its defense did, not what was
done to it). This table is informational context only and is **never** read
by the projection formula above. Matchup quality in projections comes
exclusively from `defense_reporting.parquet` (`matchup_delta`), which is
computed only from fantasy points an opposing defense has allowed to a
position - a completely separate calculation. See "Defense vs Position"
below for the full DvP mart, and its own section for why
`defensive_pressure_events_per_game` is named an events-per-game count, not
a "rate."

## Defense vs Position (`data/defense_reporting.parquet`, `pages/2_Defense_Matchups.py`)

A modern, testable replacement for the old Power BI "Defense vs Position"
research workflow, computed once in the pipeline
(`dfs_data_pipeline.build_defense_reporting` /
`build_defense_position_weekly`) and only filtered/pivoted/formatted in the
page (`lib/defense_trends.py`, a non-UI module - the page never re-runs a
groupby on a widget interaction). "Defense" always means the offensive
player's `opponent_team`. These are trend **signals** for research, not a
DFS projection or composite score, even though `matchup_delta` also feeds
the Lineup Helper's projection formula (see "Projection formula" above).

### The core rule: everything is scoped within position, never globally

QB, RB, WR, and TE score fantasy points on completely different scales, so
**every** metric here - league average, matchup index, matchup delta, rank,
percentile, and color - is computed independently within each position.
A defense's QB numbers never influence, get compared to, or share a color
scale with its RB/WR/TE numbers. This is enforced structurally (every
`groupby` in `build_defense_reporting` includes `position`), not just by
convention, and is directly covered by
`tests/test_defense_reporting.py::test_no_global_ranking_or_color_normalization_across_positions`.

### Definitions

Season DvP (completed regular-season games only):

```
fantasy_points_allowed_per_game =
    mean(fantasy_points_ppr) grouped by opponent_team + position

league_avg_points_allowed_for_position =
    mean(fantasy_points_ppr) grouped by position, across all defenses

matchup_index =
    fantasy_points_allowed_per_game / league_avg_points_allowed_for_position * 100

matchup_delta =
    fantasy_points_allowed_per_game - league_avg_points_allowed_for_position
```

This exactly preserves the original Power BI DAX's `AVERAGE(PlayerStats[fantasy_points_ppr])`
semantics - a week where a defense faced 2 WRs contributes 2 rows to that
average, so `games_in_sample` is a **raw row count**, not a distinct-week
count (kept deliberately, per the original report's own behavior - see
"How byes are handled" below for where this differs for recent-DvP).

`position_rank_most_favorable` (dense rank, 1 = most favorable - allows the
**most** to that position) and `position_percentile_most_favorable` (0-100,
higher = more favorable) are both ranked **within position** by
`fantasy_points_allowed_per_game`, descending. Higher fantasy points
allowed, matchup index, matchup delta, and percentile **always** mean a more
favorable matchup for the offensive DFS player - never the inverse.

### Recent DvP - last 3 played weeks

Recent-DvP fields (`last_3_games_*`, `dvp_recent_trend_delta`,
`dvp_trend_label`) are computed from `data/defense_position_weekly.parquet`
(one row per defense/position/**week**, source columns first averaged
within a week - so a week where a defense faced 2 WRs counts as **one**
game here, unlike the raw-row-count season number above) rather than from
the raw per-player rows directly:

- **Last 3** always means the defense's last 3 **PLAYED** completed weeks
  against that position - bye weeks are simply absent rows, never treated
  as zero or a drop.
- `last_3_games_points_allowed_per_game` (and the index/delta built from it)
  use whatever's available, even a single game.
- `dvp_recent_trend_delta` (last-3 vs season) requires at least 2 games to
  be a real number - "do not fabricate a trend with fewer than 2 relevant
  games."
- `dvp_trend_label` (`becoming_more_favorable` / `stable` / `becoming_tougher`
  / `insufficient_sample`) is held to the stricter, full 3-game bar before
  it will name a direction - even if the raw delta already exists at 2
  games, the label still reads "Insufficient Sample" until the third game.
  A positive trend means the matchup is **improving for the offense**
  (allowing more), not that the defense is playing better. Threshold
  constants (`DEFENSE_TREND_MORE_FAVORABLE_PCT` /
  `DEFENSE_TREND_TOUGHER_PCT`, ±7% of the season average) are a documented
  design choice in `dfs_data_pipeline.py`, mirroring - but independently
  tunable from - Team Trends' equivalent constants.

### How byes are handled

A bye week is simply an absent row in `defense_position_weekly` - never
zero points allowed, never connected across as if a game happened. The
Defense vs Position page's recent-trend chart reindexes each series across
the full completed-week range with an explicit null for any missing week
(`lib.defense_trends.weekly_series_with_bye_gaps`) specifically so the line
chart shows a genuine break at a bye instead of a misleading straight line
through it.

### Preseason / Week 1 baseline behavior

Before the active season has any completed games, `defense_reporting.parquet`
is built from the immediately prior season's full completed regular season
(`reporting_mode = "preseason_baseline"`) - season DvP fields stay populated
(a completed season's DvP is still useful research context), but every
recent-DvP field is nulled and `dvp_trend_label` reads
`"not_applicable_preseason"`. The page shows: *"Week 1 baseline: prior-season
defensive matchup data. Personnel and scheme changes are not yet reflected."*
- it never claims this is current-season defensive form. Once the active
season has completed games, `defense_reporting.parquet` switches to
current-season-only DvP automatically; seasons are never silently blended.

### Defensive pressure - a corrected label, not a rate

The old Power BI "Offensive EPA Per Play"-style measure for pressure was
`(SUM(def_sacks) + SUM(def_qb_hits)) / DISTINCTCOUNT(week)` labeled a "rate" -
but dividing by games is not a per-play rate. `team_summary.parquet`'s
`defensive_pressure_events_per_game` preserves the exact same calculation
(both `def_sacks` and `def_qb_hits` confirmed present in nflreadpy's source)
but names it honestly as an **events-per-game count**. A true pressure
*rate* would need a reliable opponent-dropbacks denominator, which isn't in
the current source data, so it's intentionally not computed - see "Known
limitations." This metric lives in `team_summary.parquet` (team-level, the
table that already houses a team's own defensive production), never in
`defense_reporting.parquet` (which is position-vs-defense DvP, a different
grain) and never as an input to the DvP formulas above.

### Integration with the Lineup Helper

`compute_projections` (`lib/dk_helper.py`) joins each DK row's own
`Position` + parsed opponent against `defense_reporting.parquet` by
`(defense_team, position)`. Only `matchup_delta` feeds the projection
formula; `matchup_index`, `position_rank_most_favorable`,
`position_percentile_most_favorable`, and `fantasy_points_allowed_per_game`
are carried onto the same row purely for display. Every one of these is
**null**, never a fabricated 0, whenever the join can't resolve (unparseable
opponent, or no reporting data for that defense/position) - see
`tests/test_dk_helper.py`'s `test_projection_defense_context_null_when_unresolvable_never_zero`.

## Team Trends (`data/team_reporting.parquet`, `pages/4_Team_Trends.py`)

A modern, testable replacement for the old Power BI "Team Offense Rankings"
report's research workflow - team-level yardage, pass rate, recent form,
and week-over-week change, computed once in the pipeline
(`dfs_data_pipeline.build_team_reporting`) and only filtered/sorted/
formatted in the page (`lib/team_trends.py`, a non-UI module so none of that
formatting logic re-runs a groupby on every widget click). This is a
**trend-signal report for research**, not a DFS projection, matchup rating,
or composite score - it never feeds the Lineup Helper's projections and
isn't intended to replace them.

### Definitions

All season-aggregate metrics use completed regular-season games only
(`season_type == "REG"`, `week <= latest_completed_week`), from
`nfl.load_team_stats()`'s actual columns - `attempts`, `passing_yards`,
`carries`, `rushing_yards`, `passing_epa`, and `sacks_suffered` were all
confirmed present in the real source before use; nothing here is guessed.

- **Total/Pass/Rush Yards per Game** = season sum of the respective yardage
  column / games played.
- **Pass Rate** = `attempts / (attempts + carries) * 100`.
- **Offensive Momentum** = `last_3_total_yards_per_game - season_total_yards_per_game`
  (also expressed as `offensive_momentum_pct`, that difference as a fraction
  of the season average). "Last 3" always means the team's last 3 **PLAYED**
  completed games - see "How byes are handled" below.
- **WoW (week-over-week) change** = the most recent played game's value
  minus the previous played game's value, for passing yards, rushing yards,
  total yards, and pass rate.

### Passing EPA rate - the corrected metric

The old Power BI "Offensive EPA Per Play" measure was actually
`AVERAGE(passing_epa)` across weekly rows - an average of **weekly totals**,
which is not a per-play rate at all (a week with more dropbacks contributes
the same weight to that average as a week with far fewer). This app rebuilds
it honestly:

```
season_passing_epa_per_dropback_or_attempt =
    SUM(passing_epa) / SUM(dropbacks)

dropbacks = attempts + sacks_suffered
```

`sacks_suffered` (sacks taken BY this team's offense - distinct from
`def_sacks`, that team's own defensive sack production) was confirmed
present in the source and used as the honest denominator; a `last_3_`
version of the same rate is computed the same way over the last 3 played
games. `season_passing_epa_denominator` is stored as an explicit text label
(`"dropbacks (attempts + sacks_suffered)"`) right alongside the rate, so the
denominator is never left implicit - and if `passing_epa`/`sacks_suffered`
were ever unavailable from the source, these fields are left **null**, never
silently computed from something else.

`season_points_per_game` was considered and **intentionally omitted** -
nflreadpy's team-stats source has no scoring column (the same reason
`team_summary.parquet` omits a points-allowed metric), and fabricating one
from other fields would be misleading.

### How byes are handled

A bye week is simply an absent row in `team_stats` - it is never treated as
zero yards, zero plays, or counted as a "drop" in a trend. "Last 3 played
games" and "the previous played game" (for WoW) always mean the last rows
actually present, skipping any bye naturally, not a strict calendar-week
lookback.

### Sample-size and low-sample handling

- `sample_size_label`: `insufficient_sample` (< 3 games), `limited_sample`
  (3-5 games), `full_sample` (6+ games) - tunable in
  `dfs_data_pipeline.py`'s `SAMPLE_SIZE_LIMITED_MIN_GAMES` /
  `SAMPLE_SIZE_FULL_MIN_GAMES`.
- With fewer than 3 played games, `last_3_*` fields are computed from
  whatever games exist (never padded/fabricated), `last_3_games_count`
  says exactly how many, and `recent_form_label` reads
  `insufficient_sample` rather than a confident trend label.
- With fewer than 2 played games, every WoW field is **null** (not zero -
  there is no "previous game" to compare against) and `wow_change_label`
  reads `insufficient_sample`.
- `recent_form_label` (`heating_up` / `stable` / `cooling_off` /
  `insufficient_sample`) and `wow_change_label` (`increasing` / `steady` /
  `decreasing` / `insufficient_sample`) are both driven by named, documented
  threshold constants in `dfs_data_pipeline.py`
  (`RECENT_FORM_HEATING_UP_PCT`/`RECENT_FORM_COOLING_OFF_PCT` = ±7% of
  season total yards/game; `WOW_INCREASING_YARDS`/`WOW_DECREASING_YARDS` =
  ±20 total yards) - a documented design choice (the old Power BI report had
  no such labels), tunable in one place.

### Preseason baseline mode

Before the active season has any completed games, `team_reporting.parquet`
is built from the immediately prior season's **full** completed regular
season (mirroring `players_prior_season_baseline.parquet`'s behavior) -
`reporting_mode = "preseason_baseline"`. Season-aggregate fields (yards/game,
pass rate, EPA rate) stay populated, since a real completed season's rates
are still useful research context, but every recency/momentum/WoW field is
**nulled** and `recent_form_label`/`wow_change_label` both read
`"not_applicable_preseason"` - last season's Week 18 is never presented as
this season's momentum. The Team Trends page shows a prominent warning
banner in this mode. If neither current-season nor prior-season team stats
are available at all, `team_reporting.parquet` is empty and
`metadata.json["team_reporting_mode"] = "no_current_season_data"`.

### Trend signals vs. DFS projections

Team Trends is deliberately separate from player-level DFS work: it never
reads from or writes to `players_current.parquet`, `defense_reporting.parquet`,
or any DK-salary/eligibility file, and nothing in the Lineup Helper's
projection formula reads `team_reporting.parquet` (only `defense_reporting.parquet`'s
`matchup_delta` feeds it - see "Defense vs Position" above). A team "heating up" here
describes recent yardage volume - it says nothing about which player benefits,
whether that player is actually startable this week (see the role/eligibility
engine below), or DK pricing/value. Use it to build research context, not as
a standalone lineup decision.

## Player identity & the DK / nflreadpy / ESPN crosswalk

DraftKings' salary CSV, nflreadpy's player stats, nflreadpy's depth charts,
and ESPN's roster/injury API each have their own identifier space, and none
of them was assumed to line up without checking. What's actually available,
inspected directly rather than guessed:

- **DK salary CSV**: no stable cross-source player ID at all - just `Name`
  (display string), `TeamAbbrev` (DK's own team code, sometimes different
  from nflreadpy's), and `Position`. A DK export's own `ID` column is
  DK-internal (stable only across DK's own exports) and has no known mapping
  to nflreadpy or ESPN, so it's never used as a join key.
- **nflreadpy player stats**: `player_id` (a stable "gsis" ID, e.g.
  `00-0033873`) is the backbone ID for everything else in this app
  (`players_current.parquet`, `players_weekly.parquet`).
- **nflreadpy depth charts** (`nflreadpy.load_depth_charts()`): a real,
  roughly-daily-updated depth chart for all 32 teams where - critically -
  each row carries **both** `gsis_id` (nflreadpy's ID space) **and**
  `espn_id` (ESPN's athlete ID space) together. This is a verified,
  source-provided crosswalk between nflreadpy and ESPN identity, not a name
  guess, and it's what `lib/player_identity.build_identity_crosswalk` uses
  directly. (The DynastyProcess player-ID crosswalk, `nfl.load_ff_playerids()`,
  was evaluated and consistently returns `403 Forbidden` - it is not used or
  relied on anywhere in this app.)
- **ESPN roster/injury responses**: each athlete carries ESPN's own numeric
  athlete `id`, `displayName`, and position/team context - the same ID space
  captured as `espn_id` in nflreadpy's depth charts above.

Because DK's CSV carries no stable ID, a DK row is joined to role/injury
context through **normalized name + canonical team + position** -
`lib/player_identity.match_dk_row_to_role_context` - deliberately stricter
(zero fuzzy tolerance) than the stats matcher described above, because a
wrong match here doesn't just cost a bad stat lookup, it can put a real
injury or promotion label on the wrong player. Zero or multiple exact
candidates both resolve to "no match," never a guess.

## Depth chart & injury role/eligibility engine

Projected value alone doesn't mean a player has a path to fantasy relevance -
a talented backup buried on the depth chart can still show up with a strong
`projected_value` from a good matchup, even though there's no real chance
they see the field. The role/eligibility engine (`lib/eligibility.py`) exists
specifically to keep bench players with no documented path out of the
default **Top Value Plays**, while surfacing genuinely plausible backups
(via injury) as clearly-labeled, evidence-backed plays.

**Scope.** The engine is built around the NFL Sunday main slate (1pm-4pm
ET) - it evaluates whoever the current depth chart and injury data say is
active for that slate, not primetime/international games specifically.

**Availability vocabulary** (`lib/espn_injuries.normalize_availability_classification`,
statuses configured in `lib/role_config.py`):

| `availability_classification` | Raw ESPN statuses | Meaning |
|---|---|---|
| `confirmed_unavailable` | Out, IR/Injured Reserve, Suspended, PUP, officially inactive, etc. | The **only** bucket that can elevate a backup |
| `conditional` | Questionable, Doubtful | Monitor-only - **never** treated as confirmed-unavailable, never triggers an automatic elevation on its own |
| `available` | Healthy, Active, Probable, or no injury entry listed at all | Normal availability |
| `unknown` | Unrecognized status string, or no record at all for this player | Never treated as confirmation of anything either way |

**Role classifications** (`role_classification`, computed per (team,
position group), in priority order):

1. **`role_unresolved`** - depth-chart data is stale/missing, this player's
   own injury status is `unknown`, or the depth chart and injury source
   disagree about this player's team (a safety check against a source that
   hasn't caught up to a trade). Never eligible for anything. This is the
   engine's fail-closed default - every DK row starts here and is only
   overwritten by an exact identity match to role context, so any unresolved,
   ambiguous, or unmatched player defaults to excluded, not "probably fine."
2. **`inactive`** - the player is themselves `confirmed_unavailable`.
   Never eligible, regardless of depth rank.
3. **`confirmed_starter`** (depth rank 1) / **`standard_eligible_rotation`**
   (rank within the configured tier) - eligible **regardless of anyone
   else's injury status**; these are never mislabeled as an injury
   replacement. Default eligible tiers (`DEFAULT_ELIGIBLE_DEPTH_RANK` in
   `lib/role_config.py`, the one place to change them): QB1, RB1-2, WR1-3,
   TE1-2.
4. **`injury_elevated_backup`** - a bench-tier player where **every**
   higher-ranked player at the same team+position is `confirmed_unavailable`.
   Eligible, and always labeled "elevated" with the specific blocker(s) named
   in `eligibility_reason` (e.g. *"Elevated role - Kirk Cousins (QB1) is
   Out."*) - never relabeled with the injured starter's own rank.
5. **`contingent_backup`** - a bench-tier player where no higher-ranked
   player is available/unknown, but not every one of them is confirmed
   unavailable either (at least one is merely `conditional`). Shown in the
   **Plays to Monitor** section, included in the Player Pool only via the
   "Include conditional injury replacements" toggle (**off by default**),
   and **never** eligible for Top Value Plays regardless of that toggle.
6. **`bench_no_clear_path`** - a bench-tier player with at least one
   available-or-unknown player still ahead of them. Never eligible.

Depth rank is deliberately **not** used as a stand-in for target share, snap
share, or route rate - it's role context (who's ahead of whom), shown as its
own auditable column, never smoothed into a fabricated opportunity metric.

**Fail-closed freshness.** `INJURY_FRESHNESS_HOURS` (48) and
`DEPTH_CHART_FRESHNESS_HOURS` (168, i.e. 7 days) in `lib/role_config.py`
bound how old each source can be before it's treated as stale; stale data
degrades to `role_unresolved` exactly like a failed fetch - the engine never
asserts a confident classification off data that might no longer reflect
reality, and the Lineup Helper page's freshness banner shows the same
timestamps the engine used. A per-team ESPN fetch failure marks only that
team's players `role_unresolved` (see `lib/espn_injuries.fetch_espn_injuries`)
- there is no silent "assume healthy" fallback anywhere in this path, and a
failed/empty refresh never overwrites the last-known-good depth-chart or
injury Parquet file (`dfs_data_pipeline.write_snapshot_with_fallback`)
- `player_role_context.parquet` is the one file always safe to write fresh,
since it's a pure recomputation of whatever source data (fresh or preserved)
is currently on disk.

**A failed ESPN fetch reuses the preserved snapshot for role context, not an
empty one.** `write_snapshot_with_fallback` keeps `injuries_current.parquet`
on disk untouched when a fetch fails, but `player_role_context.parquet` is
always recomputed fresh every run - so it must be built from whatever is
actually ON DISK, never from the empty in-memory result of the failed fetch
itself. `dfs_data_pipeline._resolve_role_injury_snapshot` is the single place
that reconciles the two, with three outcomes recorded in
`injury_metadata.json`:

| `role_context_source` | When | Role classifications |
|---|---|---|
| `fresh_fetch` | Latest ESPN fetch succeeded | Built from the fresh data, as normal |
| `fallback_snapshot` | Fetch failed, a preserved snapshot exists | Built from the preserved snapshot - a starter who was `Out` in that snapshot still elevates their backup, exactly as if the fetch had succeeded |
| `unavailable` | Fetch failed, no snapshot ever existed | Fails closed to `role_unresolved`, as before |

The preserved snapshot's own real retrieval timestamp (from its
`source_retrieved_at` column, never "now") feeds the same staleness check
described above - a fallback that's past `INJURY_FRESHNESS_HOURS` still
degrades to `role_unresolved` exactly like a stale fresh fetch would, so a
stale fallback can never grant eligibility on its own. `injury_metadata.json`
additionally records `used_fallback_snapshot`, `fallback_snapshot_retrieved_at`,
`fallback_snapshot_age_hours`, and `fallback_snapshot_is_stale` so this is
auditable without recomputing anything, and the Lineup Helper's freshness
banner reads these same fields - a failed fetch with a good fallback shows
*"fresh (fallback, Xh old)"*, not a blanket "unavailable," so one transient
ESPN outage can never make a previously-usable Player Pool look broken.

**On the Lineup Helper page:** the player pool is split into **Player Pool**
(eligible plays), **Plays to Monitor** (`contingent_backup` - conditional
injury paths), **Excluded by Role Context** (`inactive` / `bench_no_clear_path`,
with the blocking player(s) named), **Needs Review** (unmatched/low-confidence
stat rows, now also covering `role_unresolved` identity matches), and a
collapsed **Inactive** section - each with the specific reason and, for
elevated/monitor rows, the exact evidence chain.

## Manual eligibility overrides

`data/manual/eligibility_overrides.csv` is the one place a human can correct
the automated engine - e.g. a beat-writer report that lands after the last
scheduled refresh. It's loaded and validated exactly as strictly as any
external source (`lib/manual_overrides.load_overrides`):

- Required columns: `season, week, team, player_id, player_name, position,
  override_status, reason, expires_at, updated_at`.
- `player_id`, `reason`, `expires_at`, `team`, and `position` must all be
  present and non-blank; `season`/`week` may be left blank to apply to any
  active season/week, or filled in to scope the override to a specific slate.
- `override_status` must be one of the engine's own real classifications
  (`VALID_OVERRIDE_STATUSES` in `lib/role_config.py`) - **`role_unresolved`
  is deliberately not a valid override value**, since it's a fail-closed
  default, not something a human should need to assert.
- `expires_at` must be a parseable, non-expired timestamp - an override
  silently outliving its relevance is exactly the kind of stale-trust
  failure this whole engine is designed to avoid.
- Every row in the file gets a trace line (applied or dropped-with-reason)
  printed by the pipeline, so a malformed or expired override is never
  silently ignored.
- An override is applied **only** when it uniquely identifies exactly one
  `(player_id, canonical_team, position_group)` row in the current role
  context - never across teams or positions, never a guess (see
  `lib/eligibility._apply_overrides`).

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

### Depth chart / injury role-context refresh

`.github/workflows/refresh_role_context.yml` is a **separate** workflow from
the one above, on its own faster cadence and its own concurrency group
(`refresh-role-context`) - depth charts and injury reports change more often,
and closer to kickoff, than season-long player stats. It runs
`refresh_role_context.py` (a thin CLI wrapper around
`dfs_data_pipeline.run_role_refresh`) on:

- Friday ~4pm EST (after that week's final injury report typically posts)
- Sunday morning through early afternoon EST, ahead of the 1pm-4pm main
  slate (8:00am, 10:00am, 11:30am, 12:45pm)
- `workflow_dispatch` (manual trigger)

and commits only the 5 role-context files
(`depth_charts_current.parquet`, `injuries_current.parquet`,
`player_role_context.parquet`, `depth_chart_metadata.json`,
`injury_metadata.json`) if they changed.

**Known limitation - cron is UTC, not DST-aware.** GitHub Actions cron
schedules run in UTC and don't shift for daylight saving time. The times
above are anchored to EST (UTC-5); during EDT portions of the season (early
and late season) each run lands about an hour earlier in local ET than
intended. The app never claims this data is real-time regardless of drift -
it always displays the actual source retrieval timestamps and a staleness
warning (see the freshness banner on the Lineup Helper page and the
freshness limits in `lib/role_config.py`) rather than implying a schedule
that isn't quite what it says.

**Sandbox/CI limitation - ESPN's API is unreachable from some restricted
network environments.** `site.api.espn.com` is an undocumented, unofficial
endpoint; some sandboxed development environments block outbound requests to
it entirely. `lib/espn_injuries.fetch_espn_injuries` was verified against
that exact failure mode - it degrades gracefully (returns `source_success:
False` with a specific error, never raises, never fabricates data) - but
genuine end-to-end ESPN responses could only be exercised against synthetic
payloads during development; real traffic only actually reaches ESPN once
this runs in GitHub Actions or another environment with normal outbound
access.

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

The role/eligibility engine has its own dedicated test files:

- `tests/test_player_identity.py` - the identity crosswalk (latest-snapshot
  selection, fantasy-position filtering, missing-ID handling) and the
  adversarial DK-row matcher (cross-team and cross-position rejection,
  ambiguous-duplicate fail-closed, DK team-alias resolution).
- `tests/test_espn_injuries.py` - the full status-classification vocabulary
  (Questionable/Doubtful are never confirmed-unavailable), HTTP retry/backoff
  behavior (5xx retried, 4xx not, exponential backoff), and per-team failure
  isolation (one team's roster failure never drops another team's players or
  flips the whole run to "healthy").
- `tests/test_eligibility.py` - every role classification and the exact
  edge cases from the product spec (e.g. "QB1 Out and QB2 Out -> QB3 may
  become `injury_elevated_backup`"), cross-team/cross-position identity
  safety (a NYJ player can never inherit a same-named NE player's injury),
  fail-closed staleness/failure/team-mismatch handling with correct
  per-source attribution, manual-override scoping, and a comprehensive check
  that only the three eligible classifications ever carry
  `role_eligible_for_top_values = True`.
- `tests/test_manual_overrides.py` - override-file validation (missing
  columns, missing fields, unrecognized/`role_unresolved` status, unparseable
  or expired timestamps, season/week scoping) with a trace line for every row.
- `tests/test_role_refresh.py` - the ESPN-fetch-fallback reconciliation
  (`_resolve_role_injury_snapshot`): a successful fetch, a failed fetch with
  a valid preserved snapshot (role classifications - including an
  `injury_elevated_backup` - survive unchanged, the on-disk file stays
  untouched), a failed fetch with no snapshot at all (fails closed), a
  failed fetch with a stale preserved snapshot (labeled stale, still fails
  closed, never grants eligibility), and a full `run_role_refresh`
  integration test proving one failed refresh cannot turn a populated Player
  Pool into an all-`role_unresolved` one.

The persistent backend salary loading feature has its own dedicated test
files too:

- `tests/test_dk_salary_loader.py` - CSV validation (valid file accepted,
  every missing required column named, header-only/blank CSV rejected,
  unparseable bytes rejected), slate metadata read/write round-tripping, and
  `load_dk_salaries.py` exercised as a real subprocess against a scratch
  directory (successful load + metadata, archiving the previous
  `current.csv` on reload, rejecting an invalid CSV without writing
  anything, rejecting a missing source file).
- `tests/test_lineup_helper_salary_source.py` - `AppTest`-driven page tests:
  the committed `current.csv` loads with zero uploader interaction; a
  session upload overrides it (source label flips, season/week show `—`)
  while the committed file on disk is provably untouched; a CSV missing
  required columns shows a specific error naming them; a header-only CSV
  shows a specific "no player rows" error; and the true empty state (neither
  file present) shows the empty-state prompt rather than crashing. This
  suite backs up and restores whatever is really at `data/dk_salaries/` and
  `data/dk_slate_metadata.json` around every test, so running it never
  affects your actual committed slate.

Team Trends has its own dedicated test files too:

- `tests/test_team_reporting.py` - season totals/rates, safe division for
  zero attempts/carries and zero dropbacks, last-3-played-games and WoW
  change across a bye week, null WoW with fewer than 2 games, insufficient-
  sample labeling with 1-2 games, the exact offensive-momentum calculation
  and its documented heating-up/cooling-off thresholds, exclusion of
  in-progress/future weeks and non-REG/other-season rows, preseason-baseline
  mode (season aggregates kept, recency/WoW nulled and relabeled), the
  honest EPA-per-dropback calculation (proven distinct from the old wrong
  "average weekly EPA" approach) with graceful nulling when source columns
  are missing, and output schema/no-duplicate-team-row checks.
- `tests/test_team_trends.py` - the non-UI filter/sort/KPI/display module:
  team/min-games/sample-size filtering, sort direction (and a safe no-op on
  an unknown column), KPI standout-team selection (and safe empty/all-null
  input), display formatting (null as `"—"`, signed `+`/`-` for change
  metrics), and the weekly-trend chart's data prep (bye/future-week
  exclusion).

Defense vs Position has its own dedicated test files too:

- `tests/test_defense_reporting.py` - the raw DvP formulas matching the
  original Power BI DAX semantics exactly (including that a multi-player
  week counts multiple rows in the season average, but only one game in the
  recent-DvP window), position-scoped league averages (proven distinct
  across positions, never one shared number), rank/percentile direction
  (higher points allowed is always more favorable, dense-rank ties handled),
  proof there is no global/shared ranking or color scale across positions,
  last-3-played-weeks and its bye-skipping across `build_defense_position_weekly`,
  the two-tier insufficient-sample rule (a real trend number at 2 games, but
  a real trend *label* only at the full 3-game window), the documented
  trend-label thresholds, current-season vs. preseason-baseline behavior,
  exclusion of other-season/non-REG rows, and output schema/no-duplicate-row
  checks.
- `tests/test_defense_trends.py` - the non-UI filter/pivot/sort/display
  module: position/team/min-games/sample-size filtering, matrix pivoting
  (position-independent, null - not 0 - for a defense/position pair with no
  data), sort direction, detail-table formatting (null as `"—"`, icon+text
  trend labels), and the recent-trend chart's data prep - proving a bye week
  produces a genuine `NaN` gap in the reindexed series, and that the
  league-position-average reference line never leaks another position's data.
- `tests/test_dk_helper.py` additions - the DK-row-to-defense-reporting join:
  every carried display field (`matchup_index`, rank, percentile, raw points
  allowed) resolves correctly alongside `matchup_delta`; every one of them is
  null - never a fabricated 0 - when the join can't resolve; the join keys on
  `(opponent, Position)` together (a matching opponent with the wrong
  position never matches); and an empty `defense_reporting` doesn't crash the
  projection formula.
- `tests/test_pipeline.py` additions - `defensive_pressure_events_per_game`'s
  exact calculation and its graceful, honest nulling (never a fabricated 0)
  when `def_qb_hits` isn't present in the source.

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
- **ESPN is an external, unofficial, imperfect source.** It is not an
  official game-day inactive list, is not guaranteed real-time, and its
  response schema could change without notice - see "Depth chart & injury
  role/eligibility engine" above for how staleness, per-team failures, and
  schema drift all fail closed rather than silently assuming health.
- **Cron scheduling is UTC-fixed, not DST-aware** (see "GitHub Actions
  refresh" above) - the role-context refresh workflow's Friday/Sunday times
  drift by about an hour in local ET depending on the time of year.
- **Manual overrides require a human to maintain them.** There is
  intentionally no automated write path into
  `data/manual/eligibility_overrides.csv` - it's a deliberate, auditable,
  expiring correction mechanism, not another automated data source.
- **Main-slate scope.** The role/eligibility engine is built around the
  Sunday 1pm-4pm ET main slate; it does not special-case Thursday/Monday
  night or international games.
- **DK salary data requires a manual step, on purpose.** DraftKings has no
  public API for salary exports, so there's no automated fetch - `python
  load_dk_salaries.py` (see "Manually updating the committed salary slate"
  above) is a deliberate manual, local, one-command step, not a gap to
  eventually automate away. `--season`/`--week` are required arguments for
  the same reason `data/manual/eligibility_overrides.csv` requires a human:
  a DK CSV doesn't self-describe which slate it's for, and guessing would
  violate the same fail-closed principle as everything else in this app.
- **Team Trends is a research/trend-signal report, not a projection.** It
  intentionally has no read or write path to player-level projections,
  matchup ratings, or DK salary/eligibility data - see "Trend signals vs.
  DFS projections" above. It also doesn't build the full "Matchup Analyzer
  Expanded" report or a composite DFS score from the old Power BI workbook -
  those are explicitly out of scope for this pass.
- **`recent_form_label`/`wow_change_label` thresholds are a documented
  design choice**, not sourced from the old Power BI report (which had no
  such labels) - see `RECENT_FORM_HEATING_UP_PCT`/`RECENT_FORM_COOLING_OFF_PCT`/
  `WOW_INCREASING_YARDS`/`WOW_DECREASING_YARDS` in `dfs_data_pipeline.py` if
  you want to tune them.
- **Defense vs Position is also a research/trend-signal report, not a
  composite score.** It doesn't build the full "Matchup Analyzer Expanded"
  report from the old Power BI workbook - explicitly out of scope for this
  pass. `dvp_trend_label`'s thresholds
  (`DEFENSE_TREND_MORE_FAVORABLE_PCT`/`DEFENSE_TREND_TOUGHER_PCT` in
  `dfs_data_pipeline.py`) are the same kind of documented design choice as
  Team Trends' thresholds above, independently tunable.
- **No true defensive pressure rate.** `defensive_pressure_events_per_game`
  is an honest events-per-game count (see "Defensive pressure - a corrected
  label, not a rate" above), not a per-play/per-dropback rate - the current
  nflreadpy team-stats source has no reliable opponent-dropbacks denominator
  to compute one against. If that becomes available, a true rate could be
  added as a separate, explicitly-denominated field without touching this one.
- **`defense_matchups.parquet` (pre-existing before this pass) has been
  fully replaced by `defense_reporting.parquet`** and is no longer produced
  by the pipeline - `build_defense_matchups` no longer exists. If you have a
  stale local copy of the old file from before this change, it's safe to
  delete; nothing in the app reads it anymore.
