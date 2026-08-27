import os
import warnings
import numpy as np
import pandas as pd

from sklearn.multiclass import OneVsRestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import MultiLabelBinarizer

warnings.filterwarnings("ignore")

# =========================================================
# CONFIG
# =========================================================
FILE_PATH = r"D:\Project\lottery-1\files\euromillions-hotpicks-draw-history.csv"

WINDOW_SIZE = 10
MAIN_POOL = 50
TOP_MAIN = 5
RANDOM_STATE = 42

# =========================================================
# LOAD DATA
# =========================================================
def load_data(file_path: str) -> pd.DataFrame:
    print(f"Checking file: {file_path}")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    df = pd.read_csv(file_path)
    print("CSV loaded successfully.")
    print("Columns found:", df.columns.tolist())
    print(f"Total rows loaded: {len(df)}")

    required_cols = ["Ball 1", "Ball 2", "Ball 3", "Ball 4", "Ball 5"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    if "DrawDate" in df.columns:
        df["DrawDate"] = pd.to_datetime(df["DrawDate"], errors="coerce", dayfirst=True)
        df = df.sort_values("DrawDate").reset_index(drop=True)

    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required_cols).reset_index(drop=True)
    print(f"Rows remaining after cleanup: {len(df)}")

    return df

# =========================================================
# FEATURE ENGINEERING
# =========================================================
def build_feature_vector(history_df: pd.DataFrame) -> np.ndarray:
    main_freq = np.zeros(MAIN_POOL, dtype=float)
    recency_main = np.zeros(MAIN_POOL, dtype=float)

    odd_count = 0
    even_count = 0
    low_count = 0
    high_count = 0
    draw_sums = []

    total_rows = len(history_df)

    for idx, row in history_df.iterrows():
        weight = (idx + 1) / total_rows

        mains = [int(row[f"Ball {i}"]) for i in range(1, 6)]

        for m in mains:
            main_freq[m - 1] += 1
            recency_main[m - 1] += weight

            if m % 2 == 0:
                even_count += 1
            else:
                odd_count += 1

            if m <= 25:
                low_count += 1
            else:
                high_count += 1

        draw_sums.append(sum(mains))

    total_main_balls = max(1, total_rows * 5)

    main_freq /= total_main_balls

    if recency_main.sum() > 0:
        recency_main /= recency_main.sum()

    stats = np.array([
        odd_count / total_main_balls,
        even_count / total_main_balls,
        low_count / total_main_balls,
        high_count / total_main_balls,
        np.mean(draw_sums) if draw_sums else 0,
        np.std(draw_sums) if len(draw_sums) > 1 else 0,
    ], dtype=float)

    return np.concatenate([main_freq, recency_main, stats])

def build_training_sets(df: pd.DataFrame, window_size: int):
    X = []
    y_main = []

    for t in range(window_size, len(df)):
        history = df.iloc[t - window_size:t]
        target = df.iloc[t]

        x_vec = build_feature_vector(history)

        target_main = [
            int(target["Ball 1"]),
            int(target["Ball 2"]),
            int(target["Ball 3"]),
            int(target["Ball 4"]),
            int(target["Ball 5"]),
        ]

        X.append(x_vec)
        y_main.append(target_main)

    return np.array(X), y_main

# =========================================================
# TRAIN MODEL
# =========================================================
def train_model(X: np.ndarray, y_main):
    mlb_main = MultiLabelBinarizer(classes=list(range(1, MAIN_POOL + 1)))
    Y_main = mlb_main.fit_transform(y_main)

    model = OneVsRestClassifier(
        LogisticRegression(max_iter=2000, random_state=RANDOM_STATE)
    )

    print("Training main-number model...")
    model.fit(X, Y_main)

    return model

# =========================================================
# PREDICTION
# =========================================================
def get_next_feature_vector(df: pd.DataFrame, window_size: int) -> np.ndarray:
    latest_window = df.iloc[-window_size:]
    return build_feature_vector(latest_window).reshape(1, -1)

def pick_top_numbers(probabilities: np.ndarray, top_n: int):
    ranked_idx = np.argsort(probabilities)[::-1][:top_n]
    picks = sorted([int(i + 1) for i in ranked_idx])
    return picks

def print_rankings(title: str, probs: np.ndarray, top_n: int):
    print(f"\n{title}")
    ranked = np.argsort(probs)[::-1][:top_n]
    for idx in ranked:
        print(f"Number {idx + 1}: {probs[idx]:.4f}")

# =========================================================
# MAIN
# =========================================================
def main():
    print("Starting EuroMillions ML predictor...")

    df = load_data(FILE_PATH)

    if len(df) <= WINDOW_SIZE:
        raise ValueError(
            f"Not enough rows. Need more than WINDOW_SIZE={WINDOW_SIZE} draws, but found {len(df)}."
        )

    X, y_main = build_training_sets(df, WINDOW_SIZE)
    print(f"Training rows created: {len(X)}")

    if len(X) == 0:
        raise ValueError("No training data was generated.")

    model = train_model(X, y_main)

    x_next = get_next_feature_vector(df, WINDOW_SIZE)
    main_probs = model.predict_proba(x_next)[0]

    main_picks = pick_top_numbers(main_probs, TOP_MAIN)

    print_rankings("Top 15 predicted main numbers", main_probs, 15)

    print("\nSuggested ML-ranked EuroMillions main line")
    print(f"Main Numbers: {main_picks}")
    print("\nLucky Stars: Not available in this file")

if __name__ == "__main__":
    main()