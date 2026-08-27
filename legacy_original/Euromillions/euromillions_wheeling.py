import pandas as pd
import itertools
import random

# Load historical EuroMillions data
file_path = r'D:\Project\lottery-1\files\euromillions-draw-history.csv'
  # Adjust if necessary
df = pd.read_csv(file_path)

# Extract main and lucky star numbers
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
lucky_stars = df[['Lucky Star 1', 'Lucky Star 2']]

# Frequency analysis
main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
lucky_freq = pd.Series(lucky_stars.values.flatten()).value_counts().sort_values(ascending=False)

# Select a base pool of top numbers (you can change the count)
top_main_numbers = list(main_freq.head(10).index)  # Choose top 10 main numbers
top_lucky_numbers = list(lucky_freq.head(4).index)  # Choose top 4 lucky stars

# Generate all combinations
main_combinations = list(itertools.combinations(top_main_numbers, 5))  # C(10,5) = 252
lucky_combinations = list(itertools.combinations(top_lucky_numbers, 2))  # C(4,2) = 6

# Combine each main combo with each lucky combo
tickets = []
for main in main_combinations:
    for lucky in lucky_combinations:
        tickets.append((sorted(main), sorted(lucky)))

# Randomly sample if needed
sample_size = 5  # Adjust to how many wheeled tickets you want
sampled_tickets = random.sample(tickets, sample_size)

# Print the results
for main, lucky in sampled_tickets:
    print(*main, *lucky)
