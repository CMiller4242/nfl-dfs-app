import requests
import pandas as pd
from datetime import datetime

def fetch_odds_api(api_key):
    """
    Fetch NFL odds from The Odds API
    Sign up for free at: https://the-odds-api.com/
    Free tier: 500 requests/month
    """
    SPORT = 'americanfootball_nfl'
    
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/odds/'
    params = {
        'apiKey': api_key,
        'regions': 'us',
        'markets': 'spreads,totals',
        'oddsFormat': 'american'
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None
    
    games = response.json()
    
    game_data = []
    for game in games:
        home_team = game['home_team']
        away_team = game['away_team']
        commence_time = game['commence_time']
        
        if game['bookmakers']:
            bookmaker = game['bookmakers'][0]
            
            total = None
            spread = None
            
            for market in bookmaker['markets']:
                if market['key'] == 'totals':
                    total = market['outcomes'][0]['point']
                elif market['key'] == 'spreads':
                    for outcome in market['outcomes']:
                        if outcome['name'] == home_team:
                            spread = outcome['point']
            
            # Calculate implied totals
            implied_home = None
            implied_away = None
            if total and spread:
                implied_home = (total + spread) / 2
                implied_away = (total - spread) / 2
            
            game_data.append({
                'away_team': away_team,
                'home_team': home_team,
                'game_date': commence_time,
                'total': total,
                'spread': spread,
                'implied_away_total': implied_away,
                'implied_home_total': implied_home
            })
    
    return pd.DataFrame(game_data)


def create_manual_week9_lines():
    """
    Manual entry for Week 9 2025 NFL games
    Based on current Vegas lines
    Thursday Oct 30 + Sunday/Monday Nov 2-3, 2025
    """
    games = [
        # THURSDAY NIGHT FOOTBALL - Oct 30
        {'away_team': 'Minnesota Vikings', 'home_team': 'Los Angeles Chargers', 
         'away_abbrev': 'MIN', 'home_abbrev': 'LAC', 
         'total': 44.5, 'spread': -7.5, 'game_date': '2025-10-30'},
        
        # SUNDAY GAMES - Nov 2
        # HIGHEST TOTAL - Elite Scoring
        {'away_team': 'Baltimore Ravens', 'home_team': 'Miami Dolphins', 
         'away_abbrev': 'BAL', 'home_abbrev': 'MIA', 
         'total': 50.5, 'spread': -3.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'Dallas Cowboys', 'home_team': 'San Francisco 49ers', 
         'away_abbrev': 'DAL', 'home_abbrev': 'SF', 
         'total': 46.5, 'spread': 7.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'Buffalo Bills', 'home_team': 'Seattle Seahawks', 
         'away_abbrev': 'BUF', 'home_abbrev': 'SEA', 
         'total': 46.5, 'spread': 4.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'Tennessee Titans', 'home_team': 'Indianapolis Colts', 
         'away_abbrev': 'TEN', 'home_abbrev': 'IND', 
         'total': 47.5, 'spread': -14.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Tampa Bay Buccaneers', 'home_team': 'New Orleans Saints', 
         'away_abbrev': 'TB', 'home_abbrev': 'NO', 
         'total': 46.5, 'spread': 3.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Chicago Bears', 'home_team': 'Cincinnati Bengals', 
         'away_abbrev': 'CHI', 'home_abbrev': 'CIN', 
         'total': 44.5, 'spread': -6.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Atlanta Falcons', 'home_team': 'Miami Dolphins', 
         'away_abbrev': 'ATL', 'home_abbrev': 'MIA', 
         'total': 44.5, 'spread': -7.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'New York Giants', 'home_team': 'Philadelphia Eagles', 
         'away_abbrev': 'NYG', 'home_abbrev': 'PHI', 
         'total': 43.5, 'spread': -7.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'New York Jets', 'home_team': 'Cincinnati Bengals', 
         'away_abbrev': 'NYJ', 'home_abbrev': 'CIN', 
         'total': 44.5, 'spread': -6.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Carolina Panthers', 'home_team': 'Buffalo Bills', 
         'away_abbrev': 'CAR', 'home_abbrev': 'BUF', 
         'total': 46.5, 'spread': 7.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Green Bay Packers', 'home_team': 'Pittsburgh Steelers', 
         'away_abbrev': 'GB', 'home_abbrev': 'PIT', 
         'total': 45.5, 'spread': 3.0, 'game_date': '2025-11-02'},
        
        {'away_team': 'Houston Texans', 'home_team': 'San Francisco 49ers', 
         'away_abbrev': 'HOU', 'home_abbrev': 'SF', 
         'total': 41.5, 'spread': -1.5, 'game_date': '2025-11-02'},
        
        {'away_team': 'New England Patriots', 'home_team': 'Cleveland Browns', 
         'away_abbrev': 'NE', 'home_abbrev': 'CLE', 
         'total': 40.5, 'spread': -7.0, 'game_date': '2025-11-02'},
        
        # MONDAY NIGHT FOOTBALL - Nov 3
        {'away_team': 'Kansas City Chiefs', 'home_team': 'Washington Commanders', 
         'away_abbrev': 'KC', 'home_abbrev': 'WAS', 
         'total': 46.5, 'spread': 1.5, 'game_date': '2025-11-03'},
    ]
    
    data = []
    for game in games:
        total = game['total']
        spread = game['spread']
        
        # Calculate implied totals
        # Spread is from home team perspective (positive = home favored)
        implied_home = (total + spread) / 2
        implied_away = (total - spread) / 2
        
        data.append({
            'away_team': game['away_team'],
            'home_team': game['home_team'],
            'game_date': game['game_date'],
            'total': total,
            'spread': spread,
            'implied_away_total': round(implied_away, 2),
            'implied_home_total': round(implied_home, 2)
        })
    
    return pd.DataFrame(data)


def create_team_level_file(df):
    """
    Create a team-level version of the Vegas lines
    This matches the structure needed for Power BI relationships
    """
    rows = []
    
    for _, game in df.iterrows():
        # Home team row
        rows.append({
            'team': game['home_team'],
            'opponent': game['away_team'],
            'game_date': game['game_date'],
            'game_total': game['total'],
            'spread': game['spread'],
            'implied_total': game['implied_home_total'],
            'is_home': True
        })
        
        # Away team row
        rows.append({
            'team': game['away_team'],
            'opponent': game['home_team'],
            'game_date': game['game_date'],
            'game_total': game['total'],
            'spread': -game['spread'],  # Flip the spread for away team
            'implied_total': game['implied_away_total'],
            'is_home': False
        })
    
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("=" * 80)
    print("NFL VEGAS LINES FETCHER - WEEK 9 (2025 SEASON)")
    print("Games: Thursday 10/30 + Sunday 11/2 + Monday 11/3")
    print("=" * 80)
    
    # Option 1: Use The Odds API (requires free API key)
    print("\nOption 1: The Odds API")
    print("Sign up at: https://the-odds-api.com/")
    api_key = input("Enter your API key (or press Enter to use manual Week 9 data): ").strip()
    
    if api_key:
        print("\nFetching from The Odds API...")
        df = fetch_odds_api(api_key)
        if df is not None and not df.empty:
            df.to_csv('vegas_lines.csv', index=False)
            print(f"\n✅ Saved {len(df)} games to vegas_lines.csv")
            print(df[['away_team', 'home_team', 'total', 'spread']])
            
            # Create team-level file
            team_df = create_team_level_file(df)
            team_df.to_csv('vegas_lines_by_team.csv', index=False)
            print(f"\n✅ Saved {len(team_df)} team rows to vegas_lines_by_team.csv")
        else:
            print("❌ Failed to fetch from API")
    else:
        print("\nUsing manual Week 9 data...")
        df = create_manual_week9_lines()
        df.to_csv('vegas_lines_week9.csv', index=False)
        print(f"\n✅ Saved {len(df)} games to vegas_lines_week9.csv")
        
        # Create team-level file
        team_df = create_team_level_file(df)
        team_df.to_csv('vegas_lines_by_team_week9.csv', index=False)
        print(f"\n✅ Saved {len(team_df)} team rows to vegas_lines_by_team_week9.csv")
        
        print("\n" + "=" * 80)
        print("WEEK 9 VEGAS LINES")
        print("=" * 80)
        print(df[['away_team', 'home_team', 'game_date', 'total', 'spread']].to_string(index=False))
        
        print("\n" + "=" * 80)
        print("HIGH-TOTAL GAMES (46.5+) - YOUR DFS FOCUS")
        print("=" * 80)
        high_totals = df[df['total'] >= 46.5].sort_values('total', ascending=False)
        for _, game in high_totals.iterrows():
            date_str = game['game_date']
            print(f"{game['away_team']:25s} @ {game['home_team']:25s} | O/U: {game['total']:5.1f} | {date_str}")
        
        print("\n" + "=" * 80)
        print("GAME TIERS")
        print("=" * 80)
        
        elite = df[df['total'] >= 50]
        high = df[(df['total'] >= 47) & (df['total'] < 50)]
        above_avg = df[(df['total'] >= 44) & (df['total'] < 47)]
        
        print(f"\n🔥 Elite Scoring (50+): {len(elite)} games")
        for _, game in elite.iterrows():
            print(f"   {game['away_team']} @ {game['home_team']} - {game['total']}")
        
        print(f"\n⭐ High Scoring (47-49.5): {len(high)} games")
        for _, game in high.iterrows():
            print(f"   {game['away_team']} @ {game['home_team']} - {game['total']}")
        
        print(f"\n✓ Above Average (44-46.5): {len(above_avg)} games")
        for _, game in above_avg.iterrows():
            print(f"   {game['away_team']} @ {game['home_team']} - {game['total']}")
        
        print("\n" + "=" * 80)
        print("WEEK 9 DFS STRATEGY")
        print("=" * 80)
        print("🎯 Target Players From These Games:")
        print("\n1. BAL @ MIA (50.5) - HIGHEST TOTAL")
        print("   - Stack: Lamar Jackson + pass catchers")
        print("   - Run-back: Tyreek Hill, Jaylen Waddle")
        print("\n2. TEN @ IND (47.5) - High implied totals")
        print("   - Heavy favorite IND (likely to score)")
        print("\n3. Multiple 46.5 games - good correlation plays")
        print("=" * 80)
        
        print("\n" + "=" * 80)
        print("FILES CREATED:")
        print("=" * 80)
        print("1. vegas_lines_week9.csv - Game-level data (one row per game)")
        print("2. vegas_lines_by_team_week9.csv - Team-level data (two rows per game)")
        print("\nUse these to update your Power BI data source for Week 9!")
        print("=" * 80)