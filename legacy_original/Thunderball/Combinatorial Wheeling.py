import pandas as pd
import itertools
import random

# Load Thunderball draw history
file_path = r'D:\lottery\files\thunderball-draw-history.csv'
df = pd.read_csv(file_path)

# Extract main balls and thunderball
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
thunderballs = df[['Thunderball']]

# Frequency analysis
main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
thunder_freq = pd.Series(thunderballs.values.flatten()).value_counts().sort_values(ascending=False)

# Select base numbers
base_main = list(main_freq.head(10).index)       # Top 10 main balls
base_thunder = list(thunder_freq.head(4).index)  # Top 4 thunderballs

# Generate all combinations of 5 from 10 (C(10,5) = 252)
main_combos = list(itertools.combinations(base_main, 5))

# Pair each with each thunderball
tickets = [(sorted(combo), t) for combo in main_combos for t in base_thunder]

# Optional: sample subset
sampled_tickets = random.sample(tickets, 5)

# Print results
for main, thunder in sampled_tickets:
    print(*main, thunder)
