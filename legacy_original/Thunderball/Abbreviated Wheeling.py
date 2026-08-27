import pandas as pd
import random
import os

# Load Thunderball draw history
file_path = r'D:\Project\lottery-2\files\thunderball-draw-history.csv'

if not os.path.exists(file_path):
    raise FileNotFoundError(f"File not found: {file_path}")

df = pd.read_csv(file_path)

# Frequency analysis
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
thunderballs = df[['Thunderball']]

main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
thunder_freq = pd.Series(thunderballs.values.flatten()).value_counts().sort_values(ascending=False)

# Select base numbers
base_main = list(main_freq.head(8).index)
base_thunder = list(thunder_freq.head(3).index)

print("Top main numbers:", base_main)
print("Top thunderballs:", base_thunder)

# Abbreviated wheel (8 lines)
main_wheel_indices = [
    [0,1,2,3,4],
    [0,1,2,5,6],
    [0,1,2,6,7],
    [0,1,3,4,5],
    [0,1,3,5,7],
    [0,1,4,5,6],
    [0,1,4,6,7],
    [0,2,3,4,6],
]

print("\nGenerated Lines:\n")

for idx_set in main_wheel_indices:
    main = sorted([base_main[i] for i in idx_set])
    thunder = random.choice(base_thunder)
    print(*main, "| Thunderball:", thunder)
