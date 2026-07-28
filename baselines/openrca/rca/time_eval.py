import argparse
import re

import pandas as pd


DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def contains_date(text: str) -> bool:
    if text is None:
        return False
    return DATE_RE.search(str(text)) is not None


def main():
    parser = argparse.ArgumentParser(
        description="Compute time-detection pass ratio from passed/failed date presence."
    )
    parser.add_argument("--csv", required=True, help="Path to an agent result CSV.")
    parser.add_argument(
        "--save_report",
        default="",
        help="Optional path for a per-row classification report CSV.",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    if "passed" not in df.columns or "failed" not in df.columns:
        raise ValueError("CSV must contain 'passed' and 'failed' columns.")

    passed_col = df["passed"].fillna("").astype(str)
    failed_col = df["failed"].fillna("").astype(str)

    pass_has_date = passed_col.apply(contains_date)
    fail_has_date = failed_col.apply(contains_date)

    eligible = pass_has_date | fail_has_date
    is_pass = eligible & pass_has_date & (~fail_has_date)
    is_fail = eligible & fail_has_date

    n_eligible = int(eligible.sum())
    n_pass = int(is_pass.sum())
    n_fail = int(is_fail.sum())
    denom = n_pass + n_fail
    ratio = (n_pass / denom) if denom > 0 else 0.0

    print("==== Injection Time Detection Accuracy (date-based) ====")
    print(f"CSV: {args.csv}")
    print(f"Eligible cases: {n_eligible}")
    print(f"PASS (date in passed only): {n_pass}")
    print(f"FAIL (date in failed, including both-pass-and-fail): {n_fail}")
    print(f"Pass ratio among eligible: {ratio:.4f} ({ratio:.2%})")

    if args.save_report:
        report = df.copy()
        report["pass_has_date"] = pass_has_date
        report["fail_has_date"] = fail_has_date
        report["eligible_time_detection"] = eligible

        classification = []
        for pass_flag, fail_flag, eligible_flag in zip(pass_has_date, fail_has_date, eligible):
            if not eligible_flag:
                classification.append("NOT_COUNTED")
            elif fail_flag:
                classification.append("FAIL")
            else:
                classification.append("PASS")

        report["time_detection_class"] = classification
        report.to_csv(args.save_report, index=False)
        print(f"Per-row report saved to: {args.save_report}")


if __name__ == "__main__":
    main()
