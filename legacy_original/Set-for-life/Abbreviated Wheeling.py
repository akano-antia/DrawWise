import pandas as pd
import random

# Load Set for Life history
file_path = r'D:\Project\lottery-1\files\set-for-life-draw-history.csv'
df = pd.read_csv(file_path)

# Frequency analysis
main_balls = df[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
life_balls = df[['Life Ball']]

main_freq = pd.Series(main_balls.values.flatten()).value_counts().sort_values(ascending=False)
life_freq = pd.Series(life_balls.values.flatten()).value_counts().sort_values(ascending=False)

# Top selections
base_main = list(main_freq.head(8).index)     # Top 8 main numbers
base_life = list(life_freq.head(3).index)     # Top 3 Life Balls

# Abbreviated wheel (8-number base, 14 lines)
wheel_indices = [
    [0,1,2,3,4],
    [0,1,2,5,6],
    
    
  
]

# Generate wheeled tickets
for indices in wheel_indices:
    main_pick = sorted([base_main[i] for i in indices])
    life_pick = random.choice(base_life)
    print(*main_pick, life_pick)
