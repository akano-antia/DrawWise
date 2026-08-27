import pandas as pd
from collections import Counter
from itertools import combinations
from datetime import datetime
import random
import os


# =========================================================
# CONFIG
# =========================================================

# Change this if your filename is different
CSV_FILE_PATH = r"D:\Project\lottery-1\files\set-for-life-draw-history.csv"

# How many main numbers to keep in your pool
POOL_SIZE = 11

# How many wheel lines to generate
TARGET_LINES = 30

# Numbers per Set For Life line
LINE_SIZE = 5

# Number range
MAIN_MIN = 1
MAIN_MAX = 47

# Life Ball range
LIFE_MIN = 1
LIFE_MAX = 10


# =========================================================
# LOAD DATA
# =========================================================

def load_draw_history(filepath: str):
    if not os.path.exists(filepath):
        raise FileNotFoundError(
            f"File not found: {filepath}\n"
            f"Check the path and make sure the file has the correct .csv extension."
        )

    df = pd.read_csv(filepath)

    required_main_cols = ["Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"]
    required_life_col = "Life Ball"

    missing = [col for col in required_main_cols + [required_life_col] if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns in CSV: {missing}")

    main_draws = df[required_main_cols].copy()
    life_draws = df[required_life_col].copy()

    return df, main_draws, life_draws


# =========================================================
# SCORING NUMBERS FROM PAST RESULTS
# =========================================================

def score_main_numbers(main_draws: pd.DataFrame):
    """
    Score numbers using:
    1. overall frequency
    2. recency weighting (recent draws count more)
    """
    total_draws = len(main_draws)

    overall_counter = Counter()
    recency_scores = Counter()

    for idx, row in main_draws.iterrows():
        draw_numbers = row.tolist()

        # More recent rows get slightly higher weight.
        # If your CSV is newest-first, this still works reasonably.
        recency_weight = idx + 1

        for n in draw_numbers:
            overall_counter[int(n)] += 1
            recency_scores[int(n)] += recency_weight

    scored = []
    for n in range(MAIN_MIN, MAIN_MAX + 1):
        freq = overall_counter.get(n, 0)
        rec = recency_scores.get(n, 0)

        # Weighted score: frequency matters most, recency helps break ties
        score = (freq * 1000) + rec
        scored.append((n, freq, rec, score))

    scored.sort(key=lambda x: (-x[3], x[0]))
    return scored


def score_life_balls(life_draws: pd.Series):
    counter = Counter(int(x) for x in life_draws.tolist())
    scored = []
    for n in range(LIFE_MIN, LIFE_MAX + 1):
        freq = counter.get(n, 0)
        scored.append((n, freq))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


# =========================================================
# PICK BASE POOL
# =========================================================

def build_base_pool(scored_numbers, pool_size=11):
    """
    Pick top scored numbers, but also enforce some spread so you
    don't end up with a horrible cluster.
    """
    selected = []

    for n, freq, rec, score in scored_numbers:
        if len(selected) >= pool_size:
            break

        # Soft spacing rule: avoid too many numbers right beside each other
        if any(abs(n - existing) <= 1 for existing in selected):
            continue

        selected.append(n)

    # If spacing rule made pool too small, fill from top list
    if len(selected) < pool_size:
        for n, freq, rec, score in scored_numbers:
            if n not in selected:
                selected.append(n)
            if len(selected) == pool_size:
                break

    selected.sort()
    return selected


# =========================================================
# ABBREVIATED WHEEL GENERATION
# =========================================================

def line_score(line):
    """
    Score a 5-number line:
    - prefer good spread
    - prefer balanced odd/even
    - avoid tight clustering
    """
    line = sorted(line)
    spread = max(line) - min(line)
    odd_count = sum(1 for x in line if x % 2 == 1)
    even_count = LINE_SIZE - odd_count

    gaps = [b - a for a, b in zip(line, line[1:])]
    min_gap = min(gaps)

    score = 0

    # spread
    if spread >= 20:
        score += 3
    elif spread >= 15:
        score += 2
    else:
        score += 0

    # odd/even balance
    if (odd_count, even_count) in [(2, 3), (3, 2)]:
        score += 3
    elif (odd_count, even_count) in [(1, 4), (4, 1)]:
        score += 1

    # clustering
    if min_gap >= 2:
        score += 2
    if min_gap >= 3:
        score += 1

    return score


def generate_abbreviated_wheel(base_pool, target_lines=30):
    """
    Generate all 5-number combinations from the base pool,
    score them, then keep the best ones.
    """
    all_combos = list(combinations(base_pool, LINE_SIZE))

    scored_lines = []
    for combo in all_combos:
        scored_lines.append((combo, line_score(combo)))

    scored_lines.sort(key=lambda x: (-x[1], x[0]))

    selected = []
    used_lines = set()

    for combo, score in scored_lines:
        if len(selected) >= target_lines:
            break

        combo_key = tuple(sorted(combo))
        if combo_key in used_lines:
            continue

        selected.append(combo_key)
        used_lines.add(combo_key)

    return selected


# =========================================================
# LIFE BALL ASSIGNMENT
# =========================================================

def assign_life_balls(lines, scored_life_balls):
    """
    Rotate through the top 3 life balls from history.
    """
    top_life_balls = [n for n, freq in scored_life_balls[:3]]
    if not top_life_balls:
        top_life_balls = [random.randint(LIFE_MIN, LIFE_MAX)]

    assigned = []
    for i, line in enumerate(lines):
        life_ball = top_life_balls[i % len(top_life_balls)]
        assigned.append((list(line), life_ball))

    return assigned


# =========================================================
# SAVE OUTPUT
# =========================================================

def save_output(base_pool, tickets, filepath_prefix="set_for_life_abbrev_wheel"):
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{filepath_prefix}_{today}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write("SET FOR LIFE - ABBREVIATED WHEEL\n")
        f.write("=" * 40 + "\n")
        f.write(f"Generated: {today}\n\n")
        f.write(f"Base pool ({len(base_pool)} numbers): {base_pool}\n\n")

        for i, (line, life_ball) in enumerate(tickets, start=1):
            f.write(f"Line {i:02d}: {line} + Life Ball: {life_ball}\n")

    return filename


# =========================================================
# MAIN
# =========================================================

def main():
    try:
        df, main_draws, life_draws = load_draw_history(CSV_FILE_PATH)

        scored_numbers = score_main_numbers(main_draws)
        scored_life_balls = score_life_balls(life_draws)

        base_pool = build_base_pool(scored_numbers, POOL_SIZE)
        wheel_lines = generate_abbreviated_wheel(base_pool, TARGET_LINES)
        tickets = assign_life_balls(wheel_lines, scored_life_balls)

        print("\nSET FOR LIFE - HISTORY-BASED ABBREVIATED WHEEL")
        print("=" * 50)
        print(f"Draw history file: {CSV_FILE_PATH}")
        print(f"Base pool ({len(base_pool)} numbers): {base_pool}")
        print("\nTop main numbers from history:")
        for n, freq, rec, score in scored_numbers[:15]:
            print(f"Number {n:2d} | Frequency: {freq:3d} | Recency score: {rec:5d} | Total score: {score}")

        print("\nTop Life Balls from history:")
        for n, freq in scored_life_balls[:5]:
            print(f"Life Ball {n} | Frequency: {freq}")

        print(f"\nGenerated {len(tickets)} wheel lines:\n")
        for i, (line, life_ball) in enumerate(tickets, start=1):
            print(f"Line {i:02d}: {line} + Life Ball: {life_ball}")

        saved_file = save_output(base_pool, tickets)
        print(f"\nSaved to: {saved_file}")

    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()