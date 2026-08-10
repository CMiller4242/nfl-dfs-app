"""
Standalone entrypoint for the depth-chart + ESPN injury + role/eligibility
refresh, independent of the weekly player-stat pipeline in
dfs_data_pipeline.py.

Injury reports and depth charts change on their own cadence - this is
callable on its own schedule (see .github/workflows for the Friday +
Sunday-morning-through-early-afternoon schedule ahead of the 1pm-4pm ET
main slate) without re-pulling the full season's player stats. `python
dfs_data_pipeline.py` also calls this once at the end of its own run for
local-dev convenience, so a single local run refreshes everything; this
script exists so CI doesn't have to run the (slower) full pipeline just to
get fresher injury/depth data close to lock.
"""

from dfs_data_pipeline import determine_active_season, determine_app_mode, determine_week_status, run_role_refresh
from dfs_data_pipeline import _try_load_schedule

if __name__ == "__main__":
    active_season = determine_active_season()
    schedule_df = _try_load_schedule(active_season)
    if not schedule_df.empty:
        latest_completed_week, next_slate_week = determine_week_status(schedule_df, active_season)
    else:
        latest_completed_week, next_slate_week = None, 1

    role_week = next_slate_week if next_slate_week is not None else (latest_completed_week or 1)
    print(f"Refreshing role/eligibility context for season {active_season}, week {role_week}")
    run_role_refresh(active_season, role_week)
