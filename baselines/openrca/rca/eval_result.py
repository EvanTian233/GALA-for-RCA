import argparse

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Summarize score distribution from an agent result CSV.")
    parser.add_argument("--csv", required=True, help="Path to an agent result CSV.")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    if "score" not in df.columns:
        raise ValueError("CSV does not contain a 'score' column.")

    score_num = pd.to_numeric(df["score"], errors="coerce")
    valid = score_num.dropna()
    total_valid = len(valid)

    if total_valid == 0:
        print("No valid numeric scores found. All values are NaN or non-numeric.")
        return

    n_eq_1 = int((valid == 1.0).sum())
    n_between = int(((valid > 0.0) & (valid < 1.0)).sum())
    n_eq_0 = int((valid == 0.0).sum())
    n_good = n_eq_1 + n_between

    print("==== Score Statistics ====")
    print(f"File: {args.csv}")
    print(f"Total rows: {len(df)}")
    print(f"Valid numeric scores: {total_valid}")
    print(f"Invalid/non-numeric scores: {int(score_num.isna().sum())}")
    print()
    print(f"Count(score == 1): {n_eq_1}")
    print(f"Ratio(score == 1): {n_eq_1 / total_valid:.4f} ({n_eq_1 / total_valid:.2%})")
    print()
    print(f"Count(0 < score < 1): {n_between}")
    print(f"Ratio(0 < score < 1): {n_between / total_valid:.4f} ({n_between / total_valid:.2%})")
    print()
    print(f"Count(score == 1 OR 0 < score < 1): {n_good}")
    print(f"Ratio(score == 1 OR 0 < score < 1): {n_good / total_valid:.4f} ({n_good / total_valid:.2%})")
    print()
    print(f"Count(score == 0): {n_eq_0}")
    print(f"Ratio(score == 0): {n_eq_0 / total_valid:.4f} ({n_eq_0 / total_valid:.2%})")


if __name__ == "__main__":
    main()
