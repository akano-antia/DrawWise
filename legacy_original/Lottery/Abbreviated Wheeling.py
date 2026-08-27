import pandas as pd
import random

# Load UK Lotto draw history
file_path = r'D:\Project\lottery-1\files\lotto-draw-history.csv'
df = pd.read_csv(file_path)

# Extract main balls
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5', 'Ball 6']]
main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)

# Select top 9 most frequent numbers
base_numbers = list(main_freq.head(9).index)

# Abbreviated wheel: 20 combinations from 9 numbers
wheel_indices = [
    [0,1,2,3,4,5], [0,1,2,3,4,6], [0,1,2,3,4,7], [0,1,2,3,4,8],
    [0,1,2,3,5,6], [0,1,2,3,6,7], [0,1,2,3,7,8], [0,1,2,3,5,8],
   
]

# Print the 20 abbreviated wheeled lines
for idx_set in wheel_indices:
    ticket = sorted([base_numbers[i] for i in idx_set])
    print(*ticket)
