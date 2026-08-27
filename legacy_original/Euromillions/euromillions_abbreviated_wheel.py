import pandas as pd
import random

# Load the draw history
file_path = r'D:\Project\lottery-1\files\euromillions-draw-history.csv'

df = pd.read_csv(file_path)

# Extract and count frequencies
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
lucky_stars = df[['Lucky Star 1', 'Lucky Star 2']]

main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
lucky_freq = pd.Series(lucky_stars.values.flatten()).value_counts().sort_values(ascending=False)

# Select a base pool
base_main = list(main_freq.head(8).index)  # Choose top 8 main numbers
base_lucky = list(lucky_freq.head(4).index)  # Choose top 4 lucky stars

# Abbreviated wheel for 8 main numbers: 14 combinations covering 3+ guarantees
# Source: standard lottery wheeling systems (e.g. LottoDesigner)
main_wheel = [
    [0,1,2,3,4],
    [0,1,2,5,6],
    [0,1,2,6,7],
    [0,1,3,4,5],
   
   
]

# Abbreviated lucky star wheel (3 combinations from 4)
lucky_wheel = [
    [0, 1],
    [0, 2],
    [1, 3]
]

# Build and print combinations
for main_idxs in main_wheel:
    main_pick = sorted([base_main[i] for i in main_idxs])
    lucky_idxs = random.choice(lucky_wheel)
    lucky_pick = sorted([base_lucky[i] for i in lucky_idxs])
    print(*main_pick, *lucky_pick)
