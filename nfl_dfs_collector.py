import nflreadpy as nfl
import pandas as pd
from datetime import datetime
import os

class NFLDataCollector:
    """Automated NFL data collection for DFS Power BI reporting"""
    
    def __init__(self, base_path="./data"):
        self.base_path = base_path
        self.season = 2025
        self.current_week = 17 # Update this weekly
        
        # Create directories if they don't exist
        os.makedirs(f"{base_path}/player_stats", exist_ok=True)
        os.makedirs(f"{base_path}/team_stats", exist_ok=True)
        os.makedirs(f"{base_path}/consolidated", exist_ok=True)
    
    def collect_player_stats(self, weeks=None):
        """
        Collect player statistics for specified weeks
        If weeks=None, collects all available weeks up to current_week
        """
        if weeks is None:
            weeks = list(range(1, self.current_week + 1))
        
        print(f"Collecting player stats for {self.season} weeks {weeks}")
        
        try:
            # Load all player stats for the season
            # nflreadpy returns Polars DataFrame by default
            df_polars = nfl.load_player_stats([self.season])
            
            # Convert Polars to Pandas
            df = df_polars.to_pandas()
            
            print(f"Loaded {len(df)} total rows")
            
            # Filter for specified weeks
            week_list = weeks if isinstance(weeks, list) else [weeks]
            df = df[df['week'].isin(week_list)]
            print(f"Filtered to {len(df)} rows for weeks {week_list}")
            
            # DIAGNOSTIC: Show which weeks we actually got
            print("\n=== WEEK COLLECTION DIAGNOSTIC ===")
            if not df.empty:
                weeks_found = sorted(df['week'].unique())
                print(f"Weeks found in data: {weeks_found}")
                print(f"Weeks requested: {week_list}")
                missing_weeks = set(week_list) - set(weeks_found)
                if missing_weeks:
                    print(f"⚠️  MISSING WEEKS: {sorted(missing_weeks)}")
                print("\nRows per week:")
                print(df.groupby('week').size().to_string())
            print("="*35 + "\n")
            
        except Exception as e:
            print(f"Error loading player stats: {e}")
            raise Exception(f"Failed to load player stats: {e}")
        
        if len(df) == 0:
            raise Exception(f"No data returned for weeks {weeks}")
        
        # Save consolidated file
        week_start = weeks[0] if isinstance(weeks, list) else weeks
        week_end = weeks[-1] if isinstance(weeks, list) else weeks
        output_file = f"{self.base_path}/player_stats/player_stats_{self.season}_wk{week_start}_{week_end}.csv"
        df.to_csv(output_file, index=False)
        print(f"Saved: {output_file} ({len(df)} rows)")
        
        # Also save individual week files for incremental updates
        week_list = weeks if isinstance(weeks, list) else [weeks]
        for week in week_list:
            week_df = df[df['week'] == week]
            if len(week_df) > 0:
                week_file = f"{self.base_path}/player_stats/player_stats_{self.season}_wk{week}.csv"
                week_df.to_csv(week_file, index=False)
                print(f"Saved: {week_file} ({len(week_df)} rows)")
            else:
                print(f"⚠️  WARNING: No data for week {week}")
        
        return df
    
    def collect_team_stats(self, weeks=None):
        """
        Collect team statistics for specified weeks from nflreadpy
        This includes offensive, defensive, and special teams stats
        """
        if weeks is None:
            weeks = list(range(1, self.current_week + 1))
        
        print(f"\nCollecting team stats for {self.season} weeks {weeks}")
        
        try:
            # Load team stats (returns Polars DataFrame)
            df_polars = nfl.load_team_stats([self.season])
            
            # Convert Polars to Pandas
            df = df_polars.to_pandas()
            
            print(f"Loaded {len(df)} total rows")
            
            # Filter for specified weeks
            week_list = weeks if isinstance(weeks, list) else [weeks]
            df = df[df['week'].isin(week_list)]
            print(f"Filtered to {len(df)} rows for weeks {week_list}")
            
        except Exception as e:
            print(f"Error loading team stats: {e}")
            print("Note: Team stats may not be available yet for this week")
            return pd.DataFrame()  # Return empty DataFrame if team stats not available
        
        if len(df) == 0:
            print("Warning: No team stats data returned")
            return df
        
        # Save consolidated file
        week_start = weeks[0] if isinstance(weeks, list) else weeks
        week_end = weeks[-1] if isinstance(weeks, list) else weeks
        output_file = f"{self.base_path}/team_stats/team_stats_{self.season}_wk{week_start}_{week_end}.csv"
        df.to_csv(output_file, index=False)
        print(f"Saved: {output_file} ({len(df)} rows)")
        
        # Also save individual week files
        week_list = weeks if isinstance(weeks, list) else [weeks]
        for week in week_list:
            week_df = df[df['week'] == week]
            if len(week_df) > 0:
                week_file = f"{self.base_path}/team_stats/team_stats_{self.season}_wk{week}.csv"
                week_df.to_csv(week_file, index=False)
                print(f"Saved: {week_file} ({len(week_df)} rows)")
        
        return df
    
    def collect_roster_data(self):
        """
        Collect current roster information
        Useful for getting up-to-date player info like injury status
        """
        print(f"\nCollecting roster data for {self.season}")
        
        try:
            # Load roster data (returns Polars DataFrame)
            rosters_polars = nfl.load_rosters([self.season])
            
            # Convert to pandas
            rosters = rosters_polars.to_pandas()
            
            # Save roster file
            roster_file = f"{self.base_path}/consolidated/rosters_{self.season}.csv"
            rosters.to_csv(roster_file, index=False)
            print(f"Saved: {roster_file} ({len(rosters)} rows)")
            
            return rosters
        except Exception as e:
            print(f"Error loading rosters: {e}")
            return pd.DataFrame()
    
    def collect_weekly_update(self):
        """
        Collect only the latest week's data for incremental updates
        Call this function every Monday/Tuesday after games complete
        """
        print(f"\n{'='*50}")
        print(f"WEEKLY UPDATE: Week {self.current_week}")
        print(f"{'='*50}\n")
        
        # Collect current week player stats
        player_df = self.collect_player_stats(weeks=[self.current_week])
        
        # Collect current week team stats
        team_df = self.collect_team_stats(weeks=[self.current_week])
        
        # Update roster data (injuries, status changes)
        roster_df = self.collect_roster_data()
        
        # Append to master files
        self.append_to_master(player_df, 'player')
        self.append_to_master(team_df, 'team')
        
        print(f"\n{'='*50}")
        print(f"Week {self.current_week} data collection complete!")
        print(f"Ready for Power BI refresh.")
        print(f"{'='*50}\n")
        
        return player_df, team_df
    
    def append_to_master(self, new_data, data_type='player'):
        """
        Append new week's data to master consolidated file
        data_type: 'player' or 'team'
        """
        master_file = f"{self.base_path}/consolidated/{data_type}_stats_master.csv"
        
        if os.path.exists(master_file):
            # Load existing data
            existing_df = pd.read_csv(master_file)
            
            # Remove any duplicate rows (in case of re-running)
            existing_df = existing_df[
                ~((existing_df['season'] == self.season) & 
                  (existing_df['week'] == self.current_week))
            ]
            
            # Append new data
            master_df = pd.concat([existing_df, new_data], ignore_index=True)
        else:
            master_df = new_data
        
        # Save master file
        master_df.to_csv(master_file, index=False)
        print(f"Updated {data_type} master file: {len(master_df)} total rows")
    
    def create_summary_stats(self):
        """
        Create pre-aggregated summary tables for Power BI performance
        """
        player_master = f"{self.base_path}/consolidated/player_stats_master.csv"
        team_master = f"{self.base_path}/consolidated/team_stats_master.csv"
        
        if not os.path.exists(player_master):
            print("No player master file found. Run collect_player_stats first.")
            return
        
        df = pd.read_csv(player_master)
        
        print("\nCreating summary statistics...")
        
        # ==========================================
        # PLAYER ROLLING AVERAGES
        # ==========================================
        print("- Player rolling averages...")
        
        # Sort by player and week
        df_sorted = df.sort_values(['player_id', 'week'])
        
        # Calculate rolling averages for each player
        rolling_columns = {
            'fantasy_points_ppr': 'fantasy_points_ppr',
            'fantasy_points': 'fantasy_points',
            'targets': 'targets',
            'carries': 'carries',
            'receiving_yards': 'receiving_yards',
            'rushing_yards': 'rushing_yards',
            'receptions': 'receptions'
        }
        
        for col, original_col in rolling_columns.items():
            if original_col in df.columns:
                # 3-week rolling average
                df_sorted[f'{col}_3wk_avg'] = df_sorted.groupby('player_id')[original_col].transform(
                    lambda x: x.rolling(window=3, min_periods=1).mean()
                )
                # 5-week rolling average
                df_sorted[f'{col}_5wk_avg'] = df_sorted.groupby('player_id')[original_col].transform(
                    lambda x: x.rolling(window=5, min_periods=1).mean()
                )
        
        # Save rolling stats
        rolling_file = f"{self.base_path}/consolidated/player_stats_rolling.csv"
        df_sorted.to_csv(rolling_file, index=False)
        print(f"  Saved: {rolling_file}")
        
        # ==========================================
        # POSITION WEEKLY SUMMARIES
        # ==========================================
        print("- Position weekly summaries...")
        
        position_summary = df.groupby(['week', 'position', 'team']).agg({
            'fantasy_points_ppr': ['mean', 'max', 'sum', 'std'],
            'player_id': 'count'
        }).reset_index()
        position_summary.columns = ['week', 'position', 'team', 
                                    'avg_fantasy_points', 'max_fantasy_points', 
                                    'total_fantasy_points', 'std_fantasy_points',
                                    'player_count']
        
        summary_file = f"{self.base_path}/consolidated/position_weekly_summary.csv"
        position_summary.to_csv(summary_file, index=False)
        print(f"  Saved: {summary_file}")
        
        # ==========================================
        # TEAM DEFENSE RANKINGS (if team data exists)
        # ==========================================
        if os.path.exists(team_master):
            print("- Team defense rankings...")
            team_df = pd.read_csv(team_master)
            
            # Calculate defensive rankings (points allowed, yards allowed, etc.)
            defense_cols = [col for col in team_df.columns if 'def_' in col or 'opp_' in col]
            
            if defense_cols:
                team_defense = team_df.groupby('team')[defense_cols].mean().reset_index()
                defense_file = f"{self.base_path}/consolidated/team_defense_rankings.csv"
                team_defense.to_csv(defense_file, index=False)
                print(f"  Saved: {defense_file}")
        
        # ==========================================
        # MATCHUP HISTORY
        # ==========================================
        print("- Player vs opponent matchup history...")
        
        matchup_history = df.groupby(['player_id', 'player_display_name', 'position', 
                                     'team', 'opponent_team']).agg({
            'fantasy_points_ppr': ['mean', 'max', 'min', 'count'],
            'targets': 'mean',
            'carries': 'mean'
        }).reset_index()
        
        matchup_history.columns = ['player_id', 'player_name', 'position', 'team', 
                                  'opponent', 'avg_points', 'max_points', 'min_points',
                                  'games_played', 'avg_targets', 'avg_carries']
        
        matchup_file = f"{self.base_path}/consolidated/player_matchup_history.csv"
        matchup_history.to_csv(matchup_file, index=False)
        print(f"  Saved: {matchup_file}")
        
        print("\nSummary statistics complete!")
        
        return df_sorted
    
    def get_hot_players(self, top_n=50, weeks_lookback=3, min_games=2):
        """
        Identify trending 'hot' players based on recent performance
        """
        master_file = f"{self.base_path}/consolidated/player_stats_master.csv"
        
        if not os.path.exists(master_file):
            print("No master file found. Run collect_player_stats first.")
            return None
        
        df = pd.read_csv(master_file)
        
        # Filter to recent weeks
        recent_weeks = df[df['week'] > (self.current_week - weeks_lookback)]
        
        # Calculate metrics for hot players
        hot_players = recent_weeks.groupby(['player_id', 'player_display_name', 
                                            'position', 'team']).agg({
            'fantasy_points_ppr': ['mean', 'max', 'sum'],
            'week': 'count',
            'targets': 'sum',
            'carries': 'sum',
            'receptions': 'sum',
            'receiving_yards': 'sum',
            'rushing_yards': 'sum',
            'receiving_tds': 'sum',
            'rushing_tds': 'sum'
        }).reset_index()
        
        hot_players.columns = ['player_id', 'player_name', 'position', 'team', 
                              'avg_fantasy_points', 'max_fantasy_points', 'total_fantasy_points',
                              'games_played', 'total_targets', 'total_carries', 'total_receptions',
                              'total_rec_yards', 'total_rush_yards', 'total_rec_tds', 'total_rush_tds']
        
        # Filter for players who played in recent weeks
        hot_players = hot_players[hot_players['games_played'] >= min_games]
        
        # Calculate momentum score (weighted toward recent performance)
        # Get last week's performance
        last_week_df = df[df['week'] == self.current_week][
            ['player_id', 'fantasy_points_ppr']
        ].rename(columns={'fantasy_points_ppr': 'last_week_points'})
        
        hot_players = hot_players.merge(last_week_df, on='player_id', how='left')
        hot_players['last_week_points'] = hot_players['last_week_points'].fillna(0)
        
        # Momentum score: 50% last week, 50% average
        hot_players['momentum_score'] = (
            hot_players['last_week_points'] * 0.5 + 
            hot_players['avg_fantasy_points'] * 0.5
        )
        
        # Calculate total touches
        hot_players['total_touches'] = hot_players['total_targets'] + hot_players['total_carries']
        
        # Sort by momentum score
        hot_players = hot_players.sort_values('momentum_score', ascending=False)
        
        # Add rank
        hot_players['rank'] = range(1, len(hot_players) + 1)
        
        print(f"\n{'='*80}")
        print(f"TOP {top_n} HOT PLAYERS (Last {weeks_lookback} weeks)")
        print(f"{'='*80}")
        print(hot_players.head(top_n)[['rank', 'player_name', 'position', 'team', 
                                       'avg_fantasy_points', 'last_week_points', 
                                       'momentum_score', 'games_played']].to_string(index=False))
        print(f"{'='*80}\n")
        
        # Save hot players list
        hot_file = f"{self.base_path}/consolidated/hot_players_wk{self.current_week}.csv"
        hot_players.to_csv(hot_file, index=False)
        print(f"Saved hot players: {hot_file}")
        
        return hot_players
    
    def get_defense_matchup_rankings(self):
        """
        Create defense vs position rankings for matchup analysis
        """
        player_master = f"{self.base_path}/consolidated/player_stats_master.csv"
        
        if not os.path.exists(player_master):
            print("No player master file found.")
            return None
        
        df = pd.read_csv(player_master)
        
        print("\nCalculating defense matchup rankings...")
        
        # Group by opponent (defense) and position to see points allowed
        defense_rankings = df.groupby(['opponent_team', 'position']).agg({
            'fantasy_points_ppr': ['mean', 'sum', 'count']
        }).reset_index()
        
        defense_rankings.columns = ['defense_team', 'position', 
                                   'avg_points_allowed', 'total_points_allowed', 'player_games']
        
        # Rank defenses by position (1 = allows most points = best matchup for offense)
        for position in defense_rankings['position'].unique():
            mask = defense_rankings['position'] == position
            defense_rankings.loc[mask, 'matchup_rank'] = defense_rankings.loc[mask, 'avg_points_allowed'].rank(ascending=False)
            
            # Create rating (1-5 scale, 5 = best matchup)
            max_rank = defense_rankings.loc[mask, 'matchup_rank'].max()
            defense_rankings.loc[mask, 'matchup_rating'] = (
                ((defense_rankings.loc[mask, 'matchup_rank'] - 1) / (max_rank - 1) * 4 + 1)
                if max_rank > 1 else 3
            )
        
        defense_rankings = defense_rankings.sort_values(['position', 'matchup_rank'])
        
        # Save rankings
        rankings_file = f"{self.base_path}/consolidated/defense_matchup_rankings.csv"
        defense_rankings.to_csv(rankings_file, index=False)
        print(f"Saved defense rankings: {rankings_file}")
        
        return defense_rankings


# ==========================================
# MAIN EXECUTION FUNCTIONS
# ==========================================

def initial_setup():
    """
    Run this once to set up your initial data collection
    Collects all available weeks for the season
    """
    collector = NFLDataCollector()
    
    # Collect all weeks through current week
    print("Starting initial data collection...\n")
    collector.current_week = 8  # Updated to week 8
    
    # Collect player stats for ALL weeks 1-8
    player_df = collector.collect_player_stats(weeks=list(range(1, 9)))  # 1-8 inclusive
    
    # Collect team stats
    team_df = collector.collect_team_stats(weeks=list(range(1, 9)))
    
    # Create master files immediately after collecting
    print("\nCreating master files...")
    player_master_file = f"{collector.base_path}/consolidated/player_stats_master.csv"
    player_df.to_csv(player_master_file, index=False)
    print(f"Created: {player_master_file} ({len(player_df)} rows)")
    
    if len(team_df) > 0:
        team_master_file = f"{collector.base_path}/consolidated/team_stats_master.csv"
        team_df.to_csv(team_master_file, index=False)
        print(f"Created: {team_master_file} ({len(team_df)} rows)")
    
    # Collect roster data
    collector.collect_roster_data()
    
    # Create summary statistics
    collector.create_summary_stats()
    
    # Get hot players
    collector.get_hot_players()
    
    # Get defense matchup rankings
    collector.get_defense_matchup_rankings()
    
    print(f"\n{'='*80}")
    print("INITIAL SETUP COMPLETE!")
    print(f"{'='*80}")
    print("\nFiles created:")
    print("  📁 data/player_stats/ - Weekly player statistics")
    print("  📁 data/team_stats/ - Weekly team statistics")
    print("  📁 data/consolidated/ - Master files for Power BI")
    print("\nNext steps:")
    print("  1. Open Power BI Desktop")
    print("  2. Import data/consolidated/player_stats_master.csv")
    print("  3. Import data/consolidated/team_stats_master.csv")
    print("  4. Follow the Power BI setup guide")
    print(f"{'='*80}\n")


def weekly_update(week_number):
    """
    Run this every Monday/Tuesday to update with the latest week's data
    Example: weekly_update(8)
    """
    collector = NFLDataCollector()
    collector.current_week = week_number
    
    # Collect new week's data
    collector.collect_weekly_update()
    
    # Update summary statistics
    collector.create_summary_stats()
    
    # Get updated hot players
    collector.get_hot_players()
    
    # Update defense rankings
    collector.get_defense_matchup_rankings()
    
    print(f"\n{'='*80}")
    print(f"WEEK {week_number} UPDATE COMPLETE!")
    print(f"{'='*80}")
    print("\nNext step:")
    print("  → Open Power BI and click 'Refresh' to update your report")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    # For initial setup (collects weeks 1-8):
    # initial_setup()
    
    # For weekly updates going forward, comment out initial_setup() and use:
    weekly_update(16)  # Update this number each week