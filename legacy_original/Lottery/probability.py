import random

def generate_low_duplication_line():
    # Numbers people rarely pick
    high_pool = list(range(32, 60))  # avoids birthdays
    avoid = {7, 9, 11, 13, 21}       # common "lucky" numbers

    # Remove avoid numbers if they appear in the pool
    high_pool = [n for n in high_pool if n not in avoid]

    # Randomly choose 6 unique numbers
    line = random.sample(high_pool, 6)

    # Sort for readability
    return sorted(line)

def generate_multiple_lines(count=5):
    lines = []
    for _ in range(count):
        lines.append(generate_low_duplication_line())
    return lines

# Example usage
if __name__ == "__main__":
    print("Generated low-duplication Lotto lines:\n")
    for line in generate_multiple_lines(5):
        print(line)

