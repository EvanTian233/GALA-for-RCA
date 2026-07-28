"""
Calculate benchmark statistics from an agent result CSV.

Usage:
    python calc_stats.py Dataset3
    python calc_stats.py RE2-OB
    python calc_stats.py RE2-TT
    python calc_stats.py Bank
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from rca.benchmark_paths import resolve_result_dir

METRIC_COLS = [
    "component_acc@1",
    "component_acc@2",
    "component_acc@3",
    "component_acc@4",
    "component_acc@5",
    "component_mrr",
    "component_avg@3",
    "component_avg@5",
]


def select_result_csv(result_dir: Path) -> Path:
    csv_files = sorted(result_dir.glob("agent-*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not csv_files:
        raise FileNotFoundError(f"No agent result CSV found in {result_dir}")
    return csv_files[0]


def calc_stats(dataset_name: str, csv_path: Path | None = None):
    result_dir = resolve_result_dir(dataset_name)
    if not result_dir.exists():
        raise FileNotFoundError(f"Result directory does not exist: {result_dir}")

    csv_path = csv_path or select_result_csv(result_dir)
    df = pd.read_csv(csv_path)

    print("=" * 60)
    print(f"{dataset_name} Statistics")
    print("=" * 60)
    print(f"File: {csv_path}")
    print(f"Total cases: {len(df)}")
    print()

    stats = {
        "dataset": dataset_name,
        "total_cases": len(df),
        "model": csv_path.stem.replace("agent-rca-", ""),
    }

    print("=== Token Statistics ===")
    for col in ["input_tokens", "output_tokens", "total_tokens"]:
        if col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(series) > 0:
                stats[f"avg_{col}"] = round(float(series.mean()), 2)
                stats[f"sum_{col}"] = int(series.sum())
                print(f"{col}:")
                print(f"  Mean:  {series.mean():,.2f}")
                print(f"  Sum:   {series.sum():,.0f}")
    print()

    if "runtime_sec" in df.columns:
        print("=== Runtime Statistics ===")
        runtime = pd.to_numeric(df["runtime_sec"], errors="coerce").dropna()
        if len(runtime) > 0:
            stats["avg_runtime_sec"] = round(float(runtime.mean()), 2)
            stats["total_runtime_sec"] = round(float(runtime.sum()), 2)
            print(f"Mean runtime: {runtime.mean():.2f} sec")
            print(f"Total runtime: {runtime.sum():.2f} sec ({runtime.sum() / 60:.2f} min)")
    print()

    print("=== Accuracy Metrics ===")
    for col in METRIC_COLS:
        if col in df.columns:
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(series) > 0:
                stats[col] = round(float(series.mean()), 4)
                print(f"{col}: {series.mean():.4f} ({series.mean() * 100:.2f}%)")
    print()

    if "score" in df.columns:
        print("=== Score Distribution ===")
        score = pd.to_numeric(df["score"], errors="coerce").dropna()
        if len(score) > 0:
            n_eq_1 = int((score == 1.0).sum())
            n_between = int(((score > 0.0) & (score < 1.0)).sum())
            n_eq_0 = int((score == 0.0).sum())
            total = len(score)

            stats["count_score_eq_1"] = n_eq_1
            stats["count_score_between"] = n_between
            stats["count_score_eq_0"] = n_eq_0

            print(f"Score == 1:     {n_eq_1} ({n_eq_1 / total * 100:.2f}%)")
            print(f"0 < Score < 1:  {n_between} ({n_between / total * 100:.2f}%)")
            print(f"Score == 0:     {n_eq_0} ({n_eq_0 / total * 100:.2f}%)")

    print("=" * 60)

    output_file = result_dir / "statistics-summary.csv"
    pd.DataFrame([stats]).to_csv(output_file, index=False)
    print(f"\nStatistics saved to: {output_file}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Calculate token and ranking metrics for a dataset run.")
    parser.add_argument("dataset_name", help="Dataset name such as Dataset3, RE2-OB, RE2-TT, or Bank.")
    parser.add_argument(
        "--csv",
        default="",
        help="Optional explicit agent result CSV. If omitted, the newest agent-*.csv under test/result/<dataset> is used.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve() if args.csv else None
    calc_stats(args.dataset_name, csv_path)


if __name__ == "__main__":
    main()
