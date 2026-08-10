"""
NFL Depth Chart Scraper - Enhanced for Fantasy Football
Includes realistic depth considerations for RBBC and multi-WR sets
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import os

def get_comprehensive_depth_chart():
    """
    Create a comprehensive depth chart with realistic fantasy considerations
    
    Position Rules:
    - QB: Top 1 only (starters)
    - RB: Top 3 (accounts for RBBC and handcuffs)
    - WR: Top 4 (3-4 WR sets are standard)
    - TE: Top 2 (12 personnel usage)
    """
    print("Building comprehensive depth chart...")
    
    depth_chart = [
        # AFC EAST
        ## Buffalo Bills
        {'team': 'BUF', 'position': 'QB', 'player_name': 'Josh Allen', 'depth_rank': 1},
        {'team': 'BUF', 'position': 'RB', 'player_name': 'James Cook', 'depth_rank': 1},
        {'team': 'BUF', 'position': 'RB', 'player_name': 'Ty Johnson', 'depth_rank': 2},
        {'team': 'BUF', 'position': 'RB', 'player_name': 'Ray Davis', 'depth_rank': 3},
        {'team': 'BUF', 'position': 'WR', 'player_name': 'Khalil Shakir', 'depth_rank': 1},
        {'team': 'BUF', 'position': 'WR', 'player_name': 'Keon Coleman', 'depth_rank': 2},
        {'team': 'BUF', 'position': 'WR', 'player_name': 'Mack Hollins', 'depth_rank': 3},
        {'team': 'BUF', 'position': 'WR', 'player_name': 'Curtis Samuel', 'depth_rank': 4},
        {'team': 'BUF', 'position': 'TE', 'player_name': 'Dalton Kincaid', 'depth_rank': 1},
        {'team': 'BUF', 'position': 'TE', 'player_name': 'Dawson Knox', 'depth_rank': 2},
        
        ## Miami Dolphins
        {'team': 'MIA', 'position': 'QB', 'player_name': 'Tua Tagovailoa', 'depth_rank': 1},
        {'team': 'MIA', 'position': 'RB', 'player_name': 'De\'Von Achane', 'depth_rank': 1},
        {'team': 'MIA', 'position': 'RB', 'player_name': 'Raheem Mostert', 'depth_rank': 2},
        {'team': 'MIA', 'position': 'RB', 'player_name': 'Jaylen Wright', 'depth_rank': 3},
        {'team': 'MIA', 'position': 'WR', 'player_name': 'Tyreek Hill', 'depth_rank': 1},
        {'team': 'MIA', 'position': 'WR', 'player_name': 'Jaylen Waddle', 'depth_rank': 2},
        {'team': 'MIA', 'position': 'WR', 'player_name': 'Odell Beckham Jr.', 'depth_rank': 3},
        {'team': 'MIA', 'position': 'WR', 'player_name': 'Malik Washington', 'depth_rank': 4},
        {'team': 'MIA', 'position': 'TE', 'player_name': 'Jonnu Smith', 'depth_rank': 1},
        {'team': 'MIA', 'position': 'TE', 'player_name': 'Durham Smythe', 'depth_rank': 2},
        
        ## New York Jets
        {'team': 'NYJ', 'position': 'QB', 'player_name': 'Aaron Rodgers', 'depth_rank': 1},
        {'team': 'NYJ', 'position': 'RB', 'player_name': 'Breece Hall', 'depth_rank': 1},
        {'team': 'NYJ', 'position': 'RB', 'player_name': 'Braelon Allen', 'depth_rank': 2},
        {'team': 'NYJ', 'position': 'RB', 'player_name': 'Isaiah Davis', 'depth_rank': 3},
        {'team': 'NYJ', 'position': 'WR', 'player_name': 'Garrett Wilson', 'depth_rank': 1},
        {'team': 'NYJ', 'position': 'WR', 'player_name': 'Davante Adams', 'depth_rank': 2},
        {'team': 'NYJ', 'position': 'WR', 'player_name': 'Allen Lazard', 'depth_rank': 3},
        {'team': 'NYJ', 'position': 'WR', 'player_name': 'Mike Williams', 'depth_rank': 4},
        {'team': 'NYJ', 'position': 'TE', 'player_name': 'Tyler Conklin', 'depth_rank': 1},
        {'team': 'NYJ', 'position': 'TE', 'player_name': 'Jeremy Ruckert', 'depth_rank': 2},
        
        ## New England Patriots
        {'team': 'NE', 'position': 'QB', 'player_name': 'Drake Maye', 'depth_rank': 1},
        {'team': 'NE', 'position': 'RB', 'player_name': 'Rhamondre Stevenson', 'depth_rank': 1},
        {'team': 'NE', 'position': 'RB', 'player_name': 'Antonio Gibson', 'depth_rank': 2},
        {'team': 'NE', 'position': 'RB', 'player_name': 'JaMycal Hasty', 'depth_rank': 3},
        {'team': 'NE', 'position': 'WR', 'player_name': 'DeMario Douglas', 'depth_rank': 1},
        {'team': 'NE', 'position': 'WR', 'player_name': 'Kayshon Boutte', 'depth_rank': 2},
        {'team': 'NE', 'position': 'WR', 'player_name': 'Kendrick Bourne', 'depth_rank': 3},
        {'team': 'NE', 'position': 'WR', 'player_name': 'Ja\'Lynn Polk', 'depth_rank': 4},
        {'team': 'NE', 'position': 'TE', 'player_name': 'Hunter Henry', 'depth_rank': 1},
        {'team': 'NE', 'position': 'TE', 'player_name': 'Austin Hooper', 'depth_rank': 2},
        
        # AFC NORTH
        ## Baltimore Ravens
        {'team': 'BAL', 'position': 'QB', 'player_name': 'Lamar Jackson', 'depth_rank': 1},
        {'team': 'BAL', 'position': 'RB', 'player_name': 'Derrick Henry', 'depth_rank': 1},
        {'team': 'BAL', 'position': 'RB', 'player_name': 'Justice Hill', 'depth_rank': 2},
        {'team': 'BAL', 'position': 'RB', 'player_name': 'Keaton Mitchell', 'depth_rank': 3},
        {'team': 'BAL', 'position': 'WR', 'player_name': 'Zay Flowers', 'depth_rank': 1},
        {'team': 'BAL', 'position': 'WR', 'player_name': 'Rashod Bateman', 'depth_rank': 2},
        {'team': 'BAL', 'position': 'WR', 'player_name': 'Nelson Agholor', 'depth_rank': 3},
        {'team': 'BAL', 'position': 'WR', 'player_name': 'Diontae Johnson', 'depth_rank': 4},
        {'team': 'BAL', 'position': 'TE', 'player_name': 'Mark Andrews', 'depth_rank': 1},
        {'team': 'BAL', 'position': 'TE', 'player_name': 'Isaiah Likely', 'depth_rank': 2},
        
        ## Cincinnati Bengals
        {'team': 'CIN', 'position': 'QB', 'player_name': 'Joe Burrow', 'depth_rank': 1},
        {'team': 'CIN', 'position': 'RB', 'player_name': 'Chase Brown', 'depth_rank': 1},
        {'team': 'CIN', 'position': 'RB', 'player_name': 'Zack Moss', 'depth_rank': 2},
        {'team': 'CIN', 'position': 'RB', 'player_name': 'Trayveon Williams', 'depth_rank': 3},
        {'team': 'CIN', 'position': 'WR', 'player_name': 'Ja\'Marr Chase', 'depth_rank': 1},
        {'team': 'CIN', 'position': 'WR', 'player_name': 'Tee Higgins', 'depth_rank': 2},
        {'team': 'CIN', 'position': 'WR', 'player_name': 'Andrei Iosivas', 'depth_rank': 3},
        {'team': 'CIN', 'position': 'WR', 'player_name': 'Jermaine Burton', 'depth_rank': 4},
        {'team': 'CIN', 'position': 'TE', 'player_name': 'Mike Gesicki', 'depth_rank': 1},
        {'team': 'CIN', 'position': 'TE', 'player_name': 'Drew Sample', 'depth_rank': 2},
        
        ## Cleveland Browns
        {'team': 'CLE', 'position': 'QB', 'player_name': 'Deshaun Watson', 'depth_rank': 1},
        {'team': 'CLE', 'position': 'RB', 'player_name': 'Nick Chubb', 'depth_rank': 1},
        {'team': 'CLE', 'position': 'RB', 'player_name': 'Jerome Ford', 'depth_rank': 2},
        {'team': 'CLE', 'position': 'RB', 'player_name': 'D\'Onta Foreman', 'depth_rank': 3},
        {'team': 'CLE', 'position': 'WR', 'player_name': 'Jerry Jeudy', 'depth_rank': 1},
        {'team': 'CLE', 'position': 'WR', 'player_name': 'Elijah Moore', 'depth_rank': 2},
        {'team': 'CLE', 'position': 'WR', 'player_name': 'Cedric Tillman', 'depth_rank': 3},
        {'team': 'CLE', 'position': 'WR', 'player_name': 'Jamari Thrash', 'depth_rank': 4},
        {'team': 'CLE', 'position': 'TE', 'player_name': 'David Njoku', 'depth_rank': 1},
        {'team': 'CLE', 'position': 'TE', 'player_name': 'Jordan Akins', 'depth_rank': 2},
        
        ## Pittsburgh Steelers
        {'team': 'PIT', 'position': 'QB', 'player_name': 'Russell Wilson', 'depth_rank': 1},
        {'team': 'PIT', 'position': 'RB', 'player_name': 'Najee Harris', 'depth_rank': 1},
        {'team': 'PIT', 'position': 'RB', 'player_name': 'Jaylen Warren', 'depth_rank': 2},
        {'team': 'PIT', 'position': 'RB', 'player_name': 'Cordarrelle Patterson', 'depth_rank': 3},
        {'team': 'PIT', 'position': 'WR', 'player_name': 'George Pickens', 'depth_rank': 1},
        {'team': 'PIT', 'position': 'WR', 'player_name': 'Van Jefferson', 'depth_rank': 2},
        {'team': 'PIT', 'position': 'WR', 'player_name': 'Calvin Austin III', 'depth_rank': 3},
        {'team': 'PIT', 'position': 'WR', 'player_name': 'Mike Williams', 'depth_rank': 4},
        {'team': 'PIT', 'position': 'TE', 'player_name': 'Pat Freiermuth', 'depth_rank': 1},
        {'team': 'PIT', 'position': 'TE', 'player_name': 'Darnell Washington', 'depth_rank': 2},
        
        # AFC SOUTH
        ## Houston Texans
        {'team': 'HOU', 'position': 'QB', 'player_name': 'C.J. Stroud', 'depth_rank': 1},
        {'team': 'HOU', 'position': 'RB', 'player_name': 'Joe Mixon', 'depth_rank': 1},
        {'team': 'HOU', 'position': 'RB', 'player_name': 'Dameon Pierce', 'depth_rank': 2},
        {'team': 'HOU', 'position': 'RB', 'player_name': 'Dare Ogunbowale', 'depth_rank': 3},
        {'team': 'HOU', 'position': 'WR', 'player_name': 'Nico Collins', 'depth_rank': 1},
        {'team': 'HOU', 'position': 'WR', 'player_name': 'Tank Dell', 'depth_rank': 2},
        {'team': 'HOU', 'position': 'WR', 'player_name': 'Robert Woods', 'depth_rank': 3},
        {'team': 'HOU', 'position': 'WR', 'player_name': 'Xavier Hutchinson', 'depth_rank': 4},
        {'team': 'HOU', 'position': 'TE', 'player_name': 'Dalton Schultz', 'depth_rank': 1},
        {'team': 'HOU', 'position': 'TE', 'player_name': 'Cade Stover', 'depth_rank': 2},
        
        ## Indianapolis Colts
        {'team': 'IND', 'position': 'QB', 'player_name': 'Anthony Richardson', 'depth_rank': 1},
        {'team': 'IND', 'position': 'RB', 'player_name': 'Jonathan Taylor', 'depth_rank': 1},
        {'team': 'IND', 'position': 'RB', 'player_name': 'Trey Sermon', 'depth_rank': 2},
        {'team': 'IND', 'position': 'RB', 'player_name': 'Tyler Goodson', 'depth_rank': 3},
        {'team': 'IND', 'position': 'WR', 'player_name': 'Michael Pittman Jr.', 'depth_rank': 1},
        {'team': 'IND', 'position': 'WR', 'player_name': 'Josh Downs', 'depth_rank': 2},
        {'team': 'IND', 'position': 'WR', 'player_name': 'Alec Pierce', 'depth_rank': 3},
        {'team': 'IND', 'position': 'WR', 'player_name': 'Adonai Mitchell', 'depth_rank': 4},
        {'team': 'IND', 'position': 'TE', 'player_name': 'Kylen Granson', 'depth_rank': 1},
        {'team': 'IND', 'position': 'TE', 'player_name': 'Mo Alie-Cox', 'depth_rank': 2},
        
        ## Jacksonville Jaguars
        {'team': 'JAX', 'position': 'QB', 'player_name': 'Trevor Lawrence', 'depth_rank': 1},
        {'team': 'JAX', 'position': 'RB', 'player_name': 'Travis Etienne Jr.', 'depth_rank': 1},
        {'team': 'JAX', 'position': 'RB', 'player_name': 'Tank Bigsby', 'depth_rank': 2},
        {'team': 'JAX', 'position': 'RB', 'player_name': 'D\'Ernest Johnson', 'depth_rank': 3},
        {'team': 'JAX', 'position': 'WR', 'player_name': 'Brian Thomas Jr.', 'depth_rank': 1},
        {'team': 'JAX', 'position': 'WR', 'player_name': 'Christian Kirk', 'depth_rank': 2},
        {'team': 'JAX', 'position': 'WR', 'player_name': 'Gabe Davis', 'depth_rank': 3},
        {'team': 'JAX', 'position': 'WR', 'player_name': 'Parker Washington', 'depth_rank': 4},
        {'team': 'JAX', 'position': 'TE', 'player_name': 'Evan Engram', 'depth_rank': 1},
        {'team': 'JAX', 'position': 'TE', 'player_name': 'Brenton Strange', 'depth_rank': 2},
        
        ## Tennessee Titans
        {'team': 'TEN', 'position': 'QB', 'player_name': 'Will Levis', 'depth_rank': 1},
        {'team': 'TEN', 'position': 'RB', 'player_name': 'Tony Pollard', 'depth_rank': 1},
        {'team': 'TEN', 'position': 'RB', 'player_name': 'Tyjae Spears', 'depth_rank': 2},
        {'team': 'TEN', 'position': 'RB', 'player_name': 'Julius Chestnut', 'depth_rank': 3},
        {'team': 'TEN', 'position': 'WR', 'player_name': 'Calvin Ridley', 'depth_rank': 1},
        {'team': 'TEN', 'position': 'WR', 'player_name': 'DeAndre Hopkins', 'depth_rank': 2},
        {'team': 'TEN', 'position': 'WR', 'player_name': 'Tyler Boyd', 'depth_rank': 3},
        {'team': 'TEN', 'position': 'WR', 'player_name': 'Nick Westbrook-Ikhine', 'depth_rank': 4},
        {'team': 'TEN', 'position': 'TE', 'player_name': 'Chig Okonkwo', 'depth_rank': 1},
        {'team': 'TEN', 'position': 'TE', 'player_name': 'Josh Whyle', 'depth_rank': 2},
        
        # AFC WEST
        ## Denver Broncos
        {'team': 'DEN', 'position': 'QB', 'player_name': 'Bo Nix', 'depth_rank': 1},
        {'team': 'DEN', 'position': 'RB', 'player_name': 'Javonte Williams', 'depth_rank': 1},
        {'team': 'DEN', 'position': 'RB', 'player_name': 'Jaleel McLaughlin', 'depth_rank': 2},
        {'team': 'DEN', 'position': 'RB', 'player_name': 'Audric Estime', 'depth_rank': 3},
        {'team': 'DEN', 'position': 'WR', 'player_name': 'Courtland Sutton', 'depth_rank': 1},
        {'team': 'DEN', 'position': 'WR', 'player_name': 'Devaughn Vele', 'depth_rank': 2},
        {'team': 'DEN', 'position': 'WR', 'player_name': 'Marvin Mims Jr.', 'depth_rank': 3},
        {'team': 'DEN', 'position': 'WR', 'player_name': 'Lil\'Jordan Humphrey', 'depth_rank': 4},
        {'team': 'DEN', 'position': 'TE', 'player_name': 'Adam Trautman', 'depth_rank': 1},
        {'team': 'DEN', 'position': 'TE', 'player_name': 'Nate Adkins', 'depth_rank': 2},
        
        ## Kansas City Chiefs
        {'team': 'KC', 'position': 'QB', 'player_name': 'Patrick Mahomes', 'depth_rank': 1},
        {'team': 'KC', 'position': 'RB', 'player_name': 'Kareem Hunt', 'depth_rank': 1},
        {'team': 'KC', 'position': 'RB', 'player_name': 'Samaje Perine', 'depth_rank': 2},
        {'team': 'KC', 'position': 'RB', 'player_name': 'Carson Steele', 'depth_rank': 3},
        {'team': 'KC', 'position': 'WR', 'player_name': 'DeAndre Hopkins', 'depth_rank': 1},
        {'team': 'KC', 'position': 'WR', 'player_name': 'Xavier Worthy', 'depth_rank': 2},
        {'team': 'KC', 'position': 'WR', 'player_name': 'JuJu Smith-Schuster', 'depth_rank': 3},
        {'team': 'KC', 'position': 'WR', 'player_name': 'Justin Watson', 'depth_rank': 4},
        {'team': 'KC', 'position': 'TE', 'player_name': 'Travis Kelce', 'depth_rank': 1},
        {'team': 'KC', 'position': 'TE', 'player_name': 'Noah Gray', 'depth_rank': 2},
        
        ## Los Angeles Chargers
        {'team': 'LAC', 'position': 'QB', 'player_name': 'Justin Herbert', 'depth_rank': 1},
        {'team': 'LAC', 'position': 'RB', 'player_name': 'J.K. Dobbins', 'depth_rank': 1},
        {'team': 'LAC', 'position': 'RB', 'player_name': 'Gus Edwards', 'depth_rank': 2},
        {'team': 'LAC', 'position': 'RB', 'player_name': 'Kimani Vidal', 'depth_rank': 3},
        {'team': 'LAC', 'position': 'WR', 'player_name': 'Ladd McConkey', 'depth_rank': 1},
        {'team': 'LAC', 'position': 'WR', 'player_name': 'Quentin Johnston', 'depth_rank': 2},
        {'team': 'LAC', 'position': 'WR', 'player_name': 'Joshua Palmer', 'depth_rank': 3},
        {'team': 'LAC', 'position': 'WR', 'player_name': 'DJ Chark', 'depth_rank': 4},
        {'team': 'LAC', 'position': 'TE', 'player_name': 'Will Dissly', 'depth_rank': 1},
        {'team': 'LAC', 'position': 'TE', 'player_name': 'Stone Smartt', 'depth_rank': 2},
        
        ## Las Vegas Raiders
        {'team': 'LV', 'position': 'QB', 'player_name': 'Gardner Minshew', 'depth_rank': 1},
        {'team': 'LV', 'position': 'RB', 'player_name': 'Alexander Mattison', 'depth_rank': 1},
        {'team': 'LV', 'position': 'RB', 'player_name': 'Zamir White', 'depth_rank': 2},
        {'team': 'LV', 'position': 'RB', 'player_name': 'Ameer Abdullah', 'depth_rank': 3},
        {'team': 'LV', 'position': 'WR', 'player_name': 'Jakobi Meyers', 'depth_rank': 1},
        {'team': 'LV', 'position': 'WR', 'player_name': 'Tre Tucker', 'depth_rank': 2},
        {'team': 'LV', 'position': 'WR', 'player_name': 'DJ Turner', 'depth_rank': 3},
        {'team': 'LV', 'position': 'WR', 'player_name': 'Jalen Guyton', 'depth_rank': 4},
        {'team': 'LV', 'position': 'TE', 'player_name': 'Brock Bowers', 'depth_rank': 1},
        {'team': 'LV', 'position': 'TE', 'player_name': 'Michael Mayer', 'depth_rank': 2},
        
        # NFC EAST
        ## Dallas Cowboys
        {'team': 'DAL', 'position': 'QB', 'player_name': 'Dak Prescott', 'depth_rank': 1},
        {'team': 'DAL', 'position': 'RB', 'player_name': 'Rico Dowdle', 'depth_rank': 1},
        {'team': 'DAL', 'position': 'RB', 'player_name': 'Ezekiel Elliott', 'depth_rank': 2},
        {'team': 'DAL', 'position': 'RB', 'player_name': 'Dalvin Cook', 'depth_rank': 3},
        {'team': 'DAL', 'position': 'WR', 'player_name': 'CeeDee Lamb', 'depth_rank': 1},
        {'team': 'DAL', 'position': 'WR', 'player_name': 'Jalen Tolbert', 'depth_rank': 2},
        {'team': 'DAL', 'position': 'WR', 'player_name': 'KaVontae Turpin', 'depth_rank': 3},
        {'team': 'DAL', 'position': 'WR', 'player_name': 'Ryan Flournoy', 'depth_rank': 4},
        {'team': 'DAL', 'position': 'TE', 'player_name': 'Jake Ferguson', 'depth_rank': 1},
        {'team': 'DAL', 'position': 'TE', 'player_name': 'Luke Schoonmaker', 'depth_rank': 2},
        
        ## New York Giants
        {'team': 'NYG', 'position': 'QB', 'player_name': 'Daniel Jones', 'depth_rank': 1},
        {'team': 'NYG', 'position': 'RB', 'player_name': 'Tyrone Tracy Jr.', 'depth_rank': 1},
        {'team': 'NYG', 'position': 'RB', 'player_name': 'Devin Singletary', 'depth_rank': 2},
        {'team': 'NYG', 'position': 'RB', 'player_name': 'Eric Gray', 'depth_rank': 3},
        {'team': 'NYG', 'position': 'WR', 'player_name': 'Malik Nabers', 'depth_rank': 1},
        {'team': 'NYG', 'position': 'WR', 'player_name': 'Wan\'Dale Robinson', 'depth_rank': 2},
        {'team': 'NYG', 'position': 'WR', 'player_name': 'Darius Slayton', 'depth_rank': 3},
        {'team': 'NYG', 'position': 'WR', 'player_name': 'Jalin Hyatt', 'depth_rank': 4},
        {'team': 'NYG', 'position': 'TE', 'player_name': 'Daniel Bellinger', 'depth_rank': 1},
        {'team': 'NYG', 'position': 'TE', 'player_name': 'Theo Johnson', 'depth_rank': 2},
        
        ## Philadelphia Eagles
        {'team': 'PHI', 'position': 'QB', 'player_name': 'Jalen Hurts', 'depth_rank': 1},
        {'team': 'PHI', 'position': 'RB', 'player_name': 'Saquon Barkley', 'depth_rank': 1},
        {'team': 'PHI', 'position': 'RB', 'player_name': 'Kenneth Gainwell', 'depth_rank': 2},
        {'team': 'PHI', 'position': 'RB', 'player_name': 'Will Shipley', 'depth_rank': 3},
        {'team': 'PHI', 'position': 'WR', 'player_name': 'A.J. Brown', 'depth_rank': 1},
        {'team': 'PHI', 'position': 'WR', 'player_name': 'DeVonta Smith', 'depth_rank': 2},
        {'team': 'PHI', 'position': 'WR', 'player_name': 'Jahan Dotson', 'depth_rank': 3},
        {'team': 'PHI', 'position': 'WR', 'player_name': 'Johnny Wilson', 'depth_rank': 4},
        {'team': 'PHI', 'position': 'TE', 'player_name': 'Dallas Goedert', 'depth_rank': 1},
        {'team': 'PHI', 'position': 'TE', 'player_name': 'Grant Calcaterra', 'depth_rank': 2},
        
        ## Washington Commanders
        {'team': 'WAS', 'position': 'QB', 'player_name': 'Jayden Daniels', 'depth_rank': 1},
        {'team': 'WAS', 'position': 'QB', 'player_name': 'Marcus Mariota', 'depth_rank': 2},
        {'team': 'WAS', 'position': 'RB', 'player_name': 'Brian Robinson Jr.', 'depth_rank': 1},
        {'team': 'WAS', 'position': 'RB', 'player_name': 'Austin Ekeler', 'depth_rank': 2},
        {'team': 'WAS', 'position': 'RB', 'player_name': 'Jeremy McNichols', 'depth_rank': 3},
        {'team': 'WAS', 'position': 'WR', 'player_name': 'Terry McLaurin', 'depth_rank': 1},
        {'team': 'WAS', 'position': 'WR', 'player_name': 'Noah Brown', 'depth_rank': 2},
        {'team': 'WAS', 'position': 'WR', 'player_name': 'Olamide Zaccheaus', 'depth_rank': 3},
        {'team': 'WAS', 'position': 'WR', 'player_name': 'Dyami Brown', 'depth_rank': 4},
        {'team': 'WAS', 'position': 'TE', 'player_name': 'Zach Ertz', 'depth_rank': 1},
        {'team': 'WAS', 'position': 'TE', 'player_name': 'Ben Sinnott', 'depth_rank': 2},
        
        # NFC NORTH
        ## Chicago Bears
        {'team': 'CHI', 'position': 'QB', 'player_name': 'Caleb Williams', 'depth_rank': 1},
        {'team': 'CHI', 'position': 'RB', 'player_name': 'D\'Andre Swift', 'depth_rank': 1},
        {'team': 'CHI', 'position': 'RB', 'player_name': 'Roschon Johnson', 'depth_rank': 2},
        {'team': 'CHI', 'position': 'RB', 'player_name': 'Travis Homer', 'depth_rank': 3},
        {'team': 'CHI', 'position': 'WR', 'player_name': 'DJ Moore', 'depth_rank': 1},
        {'team': 'CHI', 'position': 'WR', 'player_name': 'Rome Odunze', 'depth_rank': 2},
        {'team': 'CHI', 'position': 'WR', 'player_name': 'Keenan Allen', 'depth_rank': 3},
        {'team': 'CHI', 'position': 'WR', 'player_name': 'Tyler Scott', 'depth_rank': 4},
        {'team': 'CHI', 'position': 'TE', 'player_name': 'Cole Kmet', 'depth_rank': 1},
        {'team': 'CHI', 'position': 'TE', 'player_name': 'Gerald Everett', 'depth_rank': 2},
        
        ## Detroit Lions (RBBC Example)
        {'team': 'DET', 'position': 'QB', 'player_name': 'Jared Goff', 'depth_rank': 1},
        {'team': 'DET', 'position': 'RB', 'player_name': 'Jahmyr Gibbs', 'depth_rank': 1},
        {'team': 'DET', 'position': 'RB', 'player_name': 'David Montgomery', 'depth_rank': 2},
        {'team': 'DET', 'position': 'RB', 'player_name': 'Craig Reynolds', 'depth_rank': 3},
        {'team': 'DET', 'position': 'WR', 'player_name': 'Amon-Ra St. Brown', 'depth_rank': 1},
        {'team': 'DET', 'position': 'WR', 'player_name': 'Jameson Williams', 'depth_rank': 2},
        {'team': 'DET', 'position': 'WR', 'player_name': 'Kalif Raymond', 'depth_rank': 3},
        {'team': 'DET', 'position': 'WR', 'player_name': 'Tim Patrick', 'depth_rank': 4},
        {'team': 'DET', 'position': 'TE', 'player_name': 'Sam LaPorta', 'depth_rank': 1},
        {'team': 'DET', 'position': 'TE', 'player_name': 'Brock Wright', 'depth_rank': 2},
        
        ## Green Bay Packers
        {'team': 'GB', 'position': 'QB', 'player_name': 'Jordan Love', 'depth_rank': 1},
        {'team': 'GB', 'position': 'RB', 'player_name': 'Josh Jacobs', 'depth_rank': 1},
        {'team': 'GB', 'position': 'RB', 'player_name': 'Emanuel Wilson', 'depth_rank': 2},
        {'team': 'GB', 'position': 'RB', 'player_name': 'MarShawn Lloyd', 'depth_rank': 3},
        {'team': 'GB', 'position': 'WR', 'player_name': 'Jayden Reed', 'depth_rank': 1},
        {'team': 'GB', 'position': 'WR', 'player_name': 'Christian Watson', 'depth_rank': 2},
        {'team': 'GB', 'position': 'WR', 'player_name': 'Romeo Doubs', 'depth_rank': 3},
        {'team': 'GB', 'position': 'WR', 'player_name': 'Dontayvion Wicks', 'depth_rank': 4},
        {'team': 'GB', 'position': 'TE', 'player_name': 'Tucker Kraft', 'depth_rank': 1},
        {'team': 'GB', 'position': 'TE', 'player_name': 'Luke Musgrave', 'depth_rank': 2},
        
        ## Minnesota Vikings
        {'team': 'MIN', 'position': 'QB', 'player_name': 'Sam Darnold', 'depth_rank': 1},
        {'team': 'MIN', 'position': 'RB', 'player_name': 'Aaron Jones', 'depth_rank': 1},
        {'team': 'MIN', 'position': 'RB', 'player_name': 'Ty Chandler', 'depth_rank': 2},
        {'team': 'MIN', 'position': 'RB', 'player_name': 'Cam Akers', 'depth_rank': 3},
        {'team': 'MIN', 'position': 'WR', 'player_name': 'Justin Jefferson', 'depth_rank': 1},
        {'team': 'MIN', 'position': 'WR', 'player_name': 'Jordan Addison', 'depth_rank': 2},
        {'team': 'MIN', 'position': 'WR', 'player_name': 'Jalen Nailor', 'depth_rank': 3},
        {'team': 'MIN', 'position': 'WR', 'player_name': 'Brandon Powell', 'depth_rank': 4},
        {'team': 'MIN', 'position': 'TE', 'player_name': 'T.J. Hockenson', 'depth_rank': 1},
        {'team': 'MIN', 'position': 'TE', 'player_name': 'Josh Oliver', 'depth_rank': 2},
        
        # NFC SOUTH
        ## Atlanta Falcons
        {'team': 'ATL', 'position': 'QB', 'player_name': 'Kirk Cousins', 'depth_rank': 1},
        {'team': 'ATL', 'position': 'RB', 'player_name': 'Bijan Robinson', 'depth_rank': 1},
        {'team': 'ATL', 'position': 'RB', 'player_name': 'Tyler Allgeier', 'depth_rank': 2},
        {'team': 'ATL', 'position': 'RB', 'player_name': 'Jase McClellan', 'depth_rank': 3},
        {'team': 'ATL', 'position': 'WR', 'player_name': 'Drake London', 'depth_rank': 1},
        {'team': 'ATL', 'position': 'WR', 'player_name': 'Darnell Mooney', 'depth_rank': 2},
        {'team': 'ATL', 'position': 'WR', 'player_name': 'Ray-Ray McCloud III', 'depth_rank': 3},
        {'team': 'ATL', 'position': 'WR', 'player_name': 'Casey Washington', 'depth_rank': 4},
        {'team': 'ATL', 'position': 'TE', 'player_name': 'Kyle Pitts', 'depth_rank': 1},
        {'team': 'ATL', 'position': 'TE', 'player_name': 'Charlie Woerner', 'depth_rank': 2},
        
        ## Carolina Panthers
        {'team': 'CAR', 'position': 'QB', 'player_name': 'Bryce Young', 'depth_rank': 1},
        {'team': 'CAR', 'position': 'RB', 'player_name': 'Chuba Hubbard', 'depth_rank': 1},
        {'team': 'CAR', 'position': 'RB', 'player_name': 'Miles Sanders', 'depth_rank': 2},
        {'team': 'CAR', 'position': 'RB', 'player_name': 'Jonathon Brooks', 'depth_rank': 3},
        {'team': 'CAR', 'position': 'WR', 'player_name': 'Diontae Johnson', 'depth_rank': 1},
        {'team': 'CAR', 'position': 'WR', 'player_name': 'Xavier Legette', 'depth_rank': 2},
        {'team': 'CAR', 'position': 'WR', 'player_name': 'Adam Thielen', 'depth_rank': 3},
        {'team': 'CAR', 'position': 'WR', 'player_name': 'Jalen Coker', 'depth_rank': 4},
        {'team': 'CAR', 'position': 'TE', 'player_name': 'Ja\'Tavion Sanders', 'depth_rank': 1},
        {'team': 'CAR', 'position': 'TE', 'player_name': 'Tommy Tremble', 'depth_rank': 2},
        
        ## New Orleans Saints
        {'team': 'NO', 'position': 'QB', 'player_name': 'Derek Carr', 'depth_rank': 1},
        {'team': 'NO', 'position': 'RB', 'player_name': 'Alvin Kamara', 'depth_rank': 1},
        {'team': 'NO', 'position': 'RB', 'player_name': 'Jamaal Williams', 'depth_rank': 2},
        {'team': 'NO', 'position': 'RB', 'player_name': 'Kendre Miller', 'depth_rank': 3},
        {'team': 'NO', 'position': 'WR', 'player_name': 'Chris Olave', 'depth_rank': 1},
        {'team': 'NO', 'position': 'WR', 'player_name': 'Marquez Valdes-Scantling', 'depth_rank': 2},
        {'team': 'NO', 'position': 'WR', 'player_name': 'Mason Tipton', 'depth_rank': 3},
        {'team': 'NO', 'position': 'WR', 'player_name': 'Cedrick Wilson Jr.', 'depth_rank': 4},
        {'team': 'NO', 'position': 'TE', 'player_name': 'Taysom Hill', 'depth_rank': 1},
        {'team': 'NO', 'position': 'TE', 'player_name': 'Foster Moreau', 'depth_rank': 2},
        
        ## Tampa Bay Buccaneers
        {'team': 'TB', 'position': 'QB', 'player_name': 'Baker Mayfield', 'depth_rank': 1},
        {'team': 'TB', 'position': 'RB', 'player_name': 'Bucky Irving', 'depth_rank': 1},
        {'team': 'TB', 'position': 'RB', 'player_name': 'Rachaad White', 'depth_rank': 2},
        {'team': 'TB', 'position': 'RB', 'player_name': 'Sean Tucker', 'depth_rank': 3},
        {'team': 'TB', 'position': 'WR', 'player_name': 'Mike Evans', 'depth_rank': 1},
        {'team': 'TB', 'position': 'WR', 'player_name': 'Chris Godwin', 'depth_rank': 2},
        {'team': 'TB', 'position': 'WR', 'player_name': 'Jalen McMillan', 'depth_rank': 3},
        {'team': 'TB', 'position': 'WR', 'player_name': 'Sterling Shepard', 'depth_rank': 4},
        {'team': 'TB', 'position': 'TE', 'player_name': 'Cade Otton', 'depth_rank': 1},
        {'team': 'TB', 'position': 'TE', 'player_name': 'Payne Durham', 'depth_rank': 2},
        
        # NFC WEST
        ## Arizona Cardinals
        {'team': 'ARI', 'position': 'QB', 'player_name': 'Kyler Murray', 'depth_rank': 1},
        {'team': 'ARI', 'position': 'RB', 'player_name': 'James Conner', 'depth_rank': 1},
        {'team': 'ARI', 'position': 'RB', 'player_name': 'Trey Benson', 'depth_rank': 2},
        {'team': 'ARI', 'position': 'RB', 'player_name': 'Emari Demercado', 'depth_rank': 3},
        {'team': 'ARI', 'position': 'WR', 'player_name': 'Marvin Harrison Jr.', 'depth_rank': 1},
        {'team': 'ARI', 'position': 'WR', 'player_name': 'Michael Wilson', 'depth_rank': 2},
        {'team': 'ARI', 'position': 'WR', 'player_name': 'Greg Dortch', 'depth_rank': 3},
        {'team': 'ARI', 'position': 'WR', 'player_name': 'Zay Jones', 'depth_rank': 4},
        {'team': 'ARI', 'position': 'TE', 'player_name': 'Trey McBride', 'depth_rank': 1},
        {'team': 'ARI', 'position': 'TE', 'player_name': 'Elijah Higgins', 'depth_rank': 2},
        
        ## Los Angeles Rams
        {'team': 'LAR', 'position': 'QB', 'player_name': 'Matthew Stafford', 'depth_rank': 1},
        {'team': 'LAR', 'position': 'RB', 'player_name': 'Kyren Williams', 'depth_rank': 1},
        {'team': 'LAR', 'position': 'RB', 'player_name': 'Blake Corum', 'depth_rank': 2},
        {'team': 'LAR', 'position': 'RB', 'player_name': 'Ronnie Rivers', 'depth_rank': 3},
        {'team': 'LAR', 'position': 'WR', 'player_name': 'Puka Nacua', 'depth_rank': 1},
        {'team': 'LAR', 'position': 'WR', 'player_name': 'Cooper Kupp', 'depth_rank': 2},
        {'team': 'LAR', 'position': 'WR', 'player_name': 'Demarcus Robinson', 'depth_rank': 3},
        {'team': 'LAR', 'position': 'WR', 'player_name': 'Tutu Atwell', 'depth_rank': 4},
        {'team': 'LAR', 'position': 'TE', 'player_name': 'Colby Parkinson', 'depth_rank': 1},
        {'team': 'LAR', 'position': 'TE', 'player_name': 'Davis Allen', 'depth_rank': 2},
        
        ## San Francisco 49ers
        {'team': 'SF', 'position': 'QB', 'player_name': 'Brock Purdy', 'depth_rank': 1},
        {'team': 'SF', 'position': 'RB', 'player_name': 'Christian McCaffrey', 'depth_rank': 1},
        {'team': 'SF', 'position': 'RB', 'player_name': 'Jordan Mason', 'depth_rank': 2},
        {'team': 'SF', 'position': 'RB', 'player_name': 'Isaac Guerendo', 'depth_rank': 3},
        {'team': 'SF', 'position': 'WR', 'player_name': 'Deebo Samuel', 'depth_rank': 1},
        {'team': 'SF', 'position': 'WR', 'player_name': 'Brandon Aiyuk', 'depth_rank': 2},
        {'team': 'SF', 'position': 'WR', 'player_name': 'Jauan Jennings', 'depth_rank': 3},
        {'team': 'SF', 'position': 'WR', 'player_name': 'Ricky Pearsall', 'depth_rank': 4},
        {'team': 'SF', 'position': 'TE', 'player_name': 'George Kittle', 'depth_rank': 1},
        {'team': 'SF', 'position': 'TE', 'player_name': 'Eric Saubert', 'depth_rank': 2},
        
        ## Seattle Seahawks
        {'team': 'SEA', 'position': 'QB', 'player_name': 'Geno Smith', 'depth_rank': 1},
        {'team': 'SEA', 'position': 'RB', 'player_name': 'Kenneth Walker III', 'depth_rank': 1},
        {'team': 'SEA', 'position': 'RB', 'player_name': 'Zach Charbonnet', 'depth_rank': 2},
        {'team': 'SEA', 'position': 'RB', 'player_name': 'Kenny McIntosh', 'depth_rank': 3},
        {'team': 'SEA', 'position': 'WR', 'player_name': 'DK Metcalf', 'depth_rank': 1},
        {'team': 'SEA', 'position': 'WR', 'player_name': 'Tyler Lockett', 'depth_rank': 2},
        {'team': 'SEA', 'position': 'WR', 'player_name': 'Jaxon Smith-Njigba', 'depth_rank': 3},
        {'team': 'SEA', 'position': 'WR', 'player_name': 'Jake Bobo', 'depth_rank': 4},
        {'team': 'SEA', 'position': 'TE', 'player_name': 'Noah Fant', 'depth_rank': 1},
        {'team': 'SEA', 'position': 'TE', 'player_name': 'Pharaoh Brown', 'depth_rank': 2},
    ]
    
    df = pd.DataFrame(depth_chart)
    df['is_starter'] = df['depth_rank'] == 1
    df['is_fantasy_relevant'] = df.apply(
        lambda row: (
            (row['position'] == 'QB' and row['depth_rank'] == 1) or
            (row['position'] == 'RB' and row['depth_rank'] <= 3) or
            (row['position'] == 'WR' and row['depth_rank'] <= 4) or
            (row['position'] == 'TE' and row['depth_rank'] <= 2)
        ),
        axis=1
    )
    df['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return df


def save_depth_chart(df, output_dir='data/depth_charts'):
    """Save depth chart data to CSV"""
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, 'current_depth_chart.csv')
    df.to_csv(output_path, index=False)
    
    date_str = datetime.now().strftime('%Y%m%d')
    backup_path = os.path.join(output_dir, f'depth_chart_{date_str}.csv')
    df.to_csv(backup_path, index=False)
    
    print(f"\n✓ Saved {len(df)} depth chart entries to {output_path}")
    print(f"✓ Backup saved to {backup_path}")
    
    return output_path


def print_depth_summary(df):
    """Print summary of depth chart data"""
    print("\n" + "="*60)
    print("DEPTH CHART SUMMARY")
    print("="*60)
    
    print(f"\nTotal Entries: {len(df)}")
    print(f"Fantasy Relevant Players: {df['is_fantasy_relevant'].sum()}")
    
    print("\n--- By Position ---")
    for pos in ['QB', 'RB', 'WR', 'TE']:
        pos_total = len(df[df['position'] == pos])
        pos_relevant = len(df[(df['position'] == pos) & (df['is_fantasy_relevant'])])
        print(f"{pos}: {pos_total} total, {pos_relevant} fantasy-relevant")
    
    print("\n--- Sample: Detroit Lions (RBBC) ---")
    det_rbs = df[(df['team'] == 'DET') & (df['position'] == 'RB')]
    for _, rb in det_rbs.iterrows():
        relevant = "✓" if rb['is_fantasy_relevant'] else "✗"
        print(f"{relevant} #{rb['depth_rank']} {rb['player_name']}")
    
    print("\n--- Sample: Buffalo Bills WRs (Multi-WR Set) ---")
    buf_wrs = df[(df['team'] == 'BUF') & (df['position'] == 'WR')]
    for _, wr in buf_wrs.iterrows():
        relevant = "✓" if wr['is_fantasy_relevant'] else "✗"
        print(f"{relevant} #{wr['depth_rank']} {wr['player_name']}")
    
    print("\n" + "="*60)


if __name__ == "__main__":
    print("Starting Comprehensive NFL Depth Chart Collection...")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Generate comprehensive depth chart
    depth_df = get_comprehensive_depth_chart()
    
    if not depth_df.empty:
        save_depth_chart(depth_df)
        print_depth_summary(depth_df)
        print("\n✓ Depth chart collection complete!")
    else:
        print("\n✗ Failed to generate depth chart")