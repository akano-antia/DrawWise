import pandas as pd
from collections import Counter
from itertools import combinations
from datetime import datetime
import os

# =========================================================
# CONFIG
# =========================================================

CSV_FILE_PATH = r"D:\Project\lottery-1\files\lotto-draw-history.csv"

POOL_SIZE = 12
TARGET_LINES = 25
LINE_SIZE = 6

NUM_MIN = 1
NUM_MAX = 59

# =========================================================
# LOAD DATA
# =========================================================

def load_lotto_history(filepath: str):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    df = pd.read_csv(filepath)

    required_cols = ["Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5", "Ball 6"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    main_draws = df[required_cols].copy()
    return df, main_draws

# =========================================================
# SCORE NUMBERS
# =========================================================

def score_numbers(main_draws: pd.DataFrame):
    overall_counter = Counter()
    recency_scores = Counter()

    for idx, row in main_draws.iterrows():
        draw_numbers = [int(x) for x in row.tolist()]
        recency_weight = idx + 1

        for n in draw_numbers:
            overall_counter[n] += 1
            recency_scores[n] += recency_weight

    scored = []
    for n in range(NUM_MIN, NUM_MAX + 1):
        freq = overall_counter.get(n, 0)
        rec = recency_scores.get(n, 0)
        score = (freq * 1000) + rec
        scored.append((n, freq, rec, score))

    scored.sort(key=lambda x: (-x[3], x[0]))
    return scored

# =========================================================
# BUILD BASE POOL
# =========================================================

def build_base_pool(scored_numbers, pool_size=12):
    selected = []

    for n, freq, rec, score in scored_numbers:
        if len(selected) >= pool_size:
            break

        if any(abs(n - existing) <= 1 for existing in selected):
            continue

        selected.append(n)

    if len(selected) < pool_size:
        for n, freq, rec, score in scored_numbers:
            if n not in selected:
                selected.append(n)
            if len(selected) == pool_size:
                break

    selected.sort()
    return selected

# =========================================================
# SCORE LINES
# =========================================================

def line_score(line):
    line = sorted(line)
    spread = max(line) - min(line)
    odd_count = sum(1 for x in line if x % 2 == 1)
    even_count = LINE_SIZE - odd_count
    low_count = sum(1 for x in line if x <= 31)

    gaps = [b - a for a, b in zip(line, line[1:])]
    min_gap = min(gaps)

    score = 0

    if spread >= 30:
        score += 3
    elif spread >= 24:
        score += 2

    if (odd_count, even_count) in [(3, 3), (2, 4), (4, 2)]:
        score += 3
    elif (odd_count, even_count) in [(1, 5), (5, 1)]:
        score += 1

    if low_count <= 3:
        score += 2
    elif low_count == 4:
        score += 1

    if min_gap >= 2:
        score += 2
    if min_gap >= 3:
        score += 1

    return score

# =========================================================
# GENERATE WHEEL
# =========================================================

def generate_abbreviated_wheel(base_pool, target_lines=25):
    all_combos = list(combinations(base_pool, LINE_SIZE))

    scored_lines = []
    for combo in all_combos:
        scored_lines.append((combo, line_score(combo)))

    scored_lines.sort(key=lambda x: (-x[1], x[0]))

    selected = []
    used = set()

    for combo, score in scored_lines:
        if len(selected) >= target_lines:
            break

        key = tuple(sorted(combo))
        if key in used:
            continue

        selected.append(key)
        used.add(key)

    return selected

# =========================================================
# SAVE OUTPUT
# =========================================================

def save_output(base_pool, lines, prefix="lotto_abbrev_wheel"):
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{prefix}_{today}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write("SATURDAY LOTTO - HISTORY-BASED ABBREVIATED WHEEL\n")
        f.write("=" * 55 + "\n")
        f.write(f"Generated: {today}\n\n")
        f.write(f"Base pool ({len(base_pool)} numbers): {base_pool}\n\n")

        for i, line in enumerate(lines, start=1):
            f.write(f"Line {i:02d}: {list(line)}\n")

    return filename

# =========================================================
# MAIN
# =========================================================

def main():
    print("Starting Lotto wheel generator...\n")

    df, main_draws = load_lotto_history(CSV_FILE_PATH)

    scored_numbers = score_numbers(main_draws)
    base_pool = build_base_pool(scored_numbers, POOL_SIZE)
    wheel_lines = generate_abbreviated_wheel(base_pool, TARGET_LINES)

    print("SATURDAY LOTTO - HISTORY-BASED ABBREVIATED WHEEL")
    print("=" * 55)
    print(f"History file: {CSV_FILE_PATH}")
    print(f"Base pool ({len(base_pool)} numbers): {base_pool}")

    print("\nTop main numbers from history:")
    for n, freq, rec, score in scored_numbers[:15]:
        print(f"Number {n:2d} | Frequency: {freq:3d} | Recency: {rec:5d} | Total score: {score}")

    print(f"\nGenerated {len(wheel_lines)} lines:\n")
    for i, line in enumerate(wheel_lines, start=1):
        print(f"Line {i:02d}: {list(line)}")

    saved_file = save_output(base_pool, wheel_lines)
    print(f"\nSaved to: {saved_file}")

if __name__ == "__main__":
    main()