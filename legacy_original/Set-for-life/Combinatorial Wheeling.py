import pandas as pd
import itertools
import random

# Load Set for Life draw history
file_path = r'D:\Project\lottery-1\files\set-for-life-draw-history.csv' # Replace with your CSV file path
df = pd.read_csv(file_path)

# Extract balls
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
life_balls = df[['Life Ball']]

# Frequency analysis
main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
life_freq = pd.Series(life_balls.values.flatten()).value_counts().sort_values(ascending=False)

# Base pools
base_main = list(main_freq.head(10).index)  # Top 10 main numbers
base_life = list(life_freq.head(3).index)   # Top 3 Life Balls

# Full wheel: all C(10, 5) = 252 combinations
main_combos = list(itertools.combinations(base_main, 5))

# Pair each with a Life Ball
tickets = [(sorted(combo), lb) for combo in main_combos for lb in base_life]

# Optional: sample smaller set
sampled_tickets = random.sample(tickets, 2)  # Adjust to your needs

# Print
for main, life in sampled_tickets:
    print(*main, life)
