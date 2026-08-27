import pandas as pd
import itertools
import random

# Load UK Lotto draw history
file_path = r'D:\Project\lottery-1\files\lotto-draw-history.csv'
df = pd.read_csv(file_path)

# Extract main balls (exclude Bonus Ball)
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5', 'Ball 6']]

# Frequency analysis
main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)

# Select top 12 numbers as base
base_pool = list(main_freq.head(12).index)

# Generate all C(12,6) combinations (924 total)
all_combos = list(itertools.combinations(base_pool, 6))

# Optional: sample smaller set
sampled_combos = random.sample(all_combos, 10)  # Adjust how many to print

# Print the results
for combo in sampled_combos:
    print(*sorted(combo))
