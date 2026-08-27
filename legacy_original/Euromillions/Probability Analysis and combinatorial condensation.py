import pandas as pd
import random

# Load the historical EuroMillions lottery data from the provided CSV file
file_path = r'D:\Project\lottery-1\files\euromillions-draw-history.csv'
  # Update if needed
euromillions_data = pd.read_csv(file_path)

# Extract the main numbers and Lucky Stars
main_numbers = euromillions_data[['Ball 1', 'Ball 2', 'Ball 3', 'Ball 4', 'Ball 5']]
lucky_stars = euromillions_data[['Lucky Star 1', 'Lucky Star 2']]

# Flatten and count frequencies
main_list = main_numbers.values.flatten()
main_freq = pd.Series(main_list).value_counts()
main_prob = (main_freq / len(main_list)).sort_values(ascending=False)

lucky_list = lucky_stars.values.flatten()
lucky_freq = pd.Series(lucky_list).value_counts()
lucky_prob = (lucky_freq / len(lucky_list)).sort_values(ascending=False)

# Number generator function using condensation
def generate_numbers(probabilities, total_numbers=50, subset_size=5, removal_range=2):
    weighted = []
    for number, prob in probabilities.items():
        weighted.extend([number] * int(prob * 1000))
    selected = []
    while len(selected) < subset_size:
        n = random.choice(weighted)
        if n not in selected:
            selected.append(n)
            weighted = [x for x in weighted if abs(x - n) > removal_range]
    return sorted(selected)

# Generate and print 15 combinations
for _ in range(15):
    main = generate_numbers(main_prob)
    lucky = generate_numbers(lucky_prob, total_numbers=12, subset_size=2)
    print(*main, *lucky)

