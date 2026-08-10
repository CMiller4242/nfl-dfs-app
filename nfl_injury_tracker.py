"""
NFL Injury Tracker
Fetches current injury data from ESPN API
Run this daily or before each DFS slate
"""

import requests
import pandas as pd
from datetime import datetime
import os

def get_nfl_injuries():
    """Get current NFL injury report from ESPN"""
    print("Fetching injury data from ESPN...")
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
    
    all_injuries = []
    
    try:
        # Get all teams
        response = requests.get(url)
        teams = response.json()['sports'][0]['leagues'][0]['teams']
        
        for team in teams:
            team_id = team['team']['id']
            team_name = team['team']['displayName']
            team_abbr = team['team']['abbreviation']
            
            print(f"Processing {team_name}...")
            
            # Get team roster with injuries
            roster_url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
            roster_response = requests.get(roster_url)
            
            if roster_response.status_code == 200:
                roster_data = roster_response.json()
                
                for athlete in roster_data.get('athletes', []):
                    for player in athlete.get('items', []):
                        injury_status = player.get('injuries', [])
                        
                        player_info = {
                            'player_name': player['displayName'],
                            'position': player['position']['abbreviation'],
                            'team': team_abbr,
                            'team_full': team_name,
                            'jersey': player.get('jersey', ''),
                            'injury_status': 'Healthy',
                            'injury_type': None,
                            'injury_details': None,
                            'is_active': True
                        }
                        
                        if injury_status:
                            latest_injury = injury_status[0]
                            status = latest_injury.get('status', 'Unknown')
                            player_info['injury_status'] = status
                            player_info['injury_type'] = latest_injury.get('type', 'Unknown')
                            player_info['injury_details'] = latest_injury.get('details', {}).get('detail', '')
                            
                            # FIXED: Mark as inactive if Out, IR, Doubtful, or Suspended
                            # Added 'Injured Reserve' to the list
                            player_info['is_active'] = status not in [
                                'Out', 
                                'IR', 
                                'Injured Reserve',  # ADDED THIS
                                'Doubtful', 
                                'Suspended',
                                'Suspension'
                            ]
                        
                        all_injuries.append(player_info)
        
        df = pd.DataFrame(all_injuries)
        df['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return df
    
    except Exception as e:
        print(f"Error fetching injury data: {e}")
        return pd.DataFrame()


def save_injury_data(df, output_dir='data/injuries'):
    """Save injury data to CSV"""
    # Create directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Save current injuries
    output_path = os.path.join(output_dir, 'current_injuries.csv')
    df.to_csv(output_path, index=False)
    
    # Also save a dated backup
    date_str = datetime.now().strftime('%Y%m%d')
    backup_path = os.path.join(output_dir, f'injuries_{date_str}.csv')
    df.to_csv(backup_path, index=False)
    
    print(f"\n✓ Saved {len(df)} player records to {output_path}")
    print(f"✓ Backup saved to {backup_path}")
    
    return output_path


def print_injury_summary(df):
    """Print summary of injury data"""
    print("\n" + "="*60)
    print("INJURY REPORT SUMMARY")
    print("="*60)
    
    print(f"\nTotal Players: {len(df)}")
    print(f"Active Players: {df['is_active'].sum()}")
    print(f"Inactive Players: {(~df['is_active']).sum()}")
    
    print("\n--- Injury Status Breakdown ---")
    print(df['injury_status'].value_counts())
    
    print("\n--- Injured Players by Position ---")
    injured = df[df['injury_status'] != 'Healthy']
    if not injured.empty:
        print(injured.groupby('position')['injury_status'].count().sort_values(ascending=False))
    
    print("\n--- KEY FANTASY POSITIONS - INACTIVE PLAYERS ---")
    inactive = df[~df['is_active']]
    fantasy_positions = ['QB', 'RB', 'WR', 'TE']
    
    for pos in fantasy_positions:
        pos_inactive = inactive[inactive['position'] == pos]
        if not pos_inactive.empty:
            print(f"\n{pos} ({len(pos_inactive)}):")
            for _, player in pos_inactive.iterrows():
                print(f"  {player['player_name']} ({player['team']}) - {player['injury_status']}")
    
    print("\n" + "="*60)


if __name__ == "__main__":
    print("Starting NFL Injury Data Collection...")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Fetch injury data
    injury_df = get_nfl_injuries()
    
    if not injury_df.empty:
        # Save to CSV
        save_injury_data(injury_df)
        
        # Print summary
        print_injury_summary(injury_df)
        
        print("\n✓ Injury data collection complete!")
    else:
        print("\n✗ Failed to collect injury data")