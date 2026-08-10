import nflreadpy as nfl

season = 2025
weeks = [1, 2, 3, 4]

# Load all available player stats for 2025
df = nfl.load_player_stats([season])

# Filter only the weeks you want
df = df.filter(df['week'].is_in(weeks))

# Save to CSV
df.write_csv(f"player_stats_{season}_wk{weeks[0]}_{weeks[-1]}.csv")
print(f"Saved stats for {season} weeks {weeks}")


