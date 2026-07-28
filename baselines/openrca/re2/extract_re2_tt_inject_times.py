#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract (service, cause, inject_time) from the RE2-TT dataset directory.

Assumed directory structure:
RE2-TT/
  <faulty_service>_<fault_type>/
    <instance_id>/
      inject_time.txt
      logs.csv
      traces.csv
      simple_metrics.csv
      ...
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd


def _get_beijing_tz():
    """Return a tzinfo for Asia/Shanghai with a safe fallback."""
    try:
        from zoneinfo import ZoneInfo  # type: ignore

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        import pytz  # type: ignore

        return pytz.timezone("Asia/Shanghai")


UTC_TZ = timezone.utc
BJ_TZ = _get_beijing_tz()


def parse_case_name(case_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse a case folder name like 'ts-auth-service_cpu'."""
    parts = case_name.rsplit("_", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None, None
    return parts[0], parts[1]


def read_first_int(inject_file: Path) -> Tuple[Optional[int], Optional[str]]:
    """Read inject_time.txt and extract the first integer value."""
    try:
        content = inject_file.read_text(encoding="utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        content = inject_file.read_text(encoding="gbk", errors="ignore").strip()

    match = re.search(r"\d+", content)
    if not match:
        return None, f"No integer timestamp found in file content: {content[:80]!r}"

    try:
        return int(match.group(0)), None
    except Exception as error:
        return None, f"Failed to parse integer timestamp: {error}"


def normalize_epoch_to_seconds(raw: int) -> Tuple[float, str]:
    """Normalize a raw epoch-like integer to seconds."""
    if raw >= 10**18:
        return raw / 1_000_000_000.0, "ns"
    if raw >= 10**15:
        return raw / 1_000_000.0, "us"
    if raw >= 10**12:
        return raw / 1_000.0, "ms"
    return float(raw), "s"


def ts_to_str(ts_seconds: float, tz) -> str:
    return datetime.fromtimestamp(ts_seconds, tz=tz).strftime("%Y-%m-%d %H:%M:%S")


def extract(root_dir: Path) -> pd.DataFrame:
    rows = []
    if not root_dir.exists():
        raise FileNotFoundError(f"Root directory does not exist: {root_dir}")

    for case_dir in sorted([path for path in root_dir.iterdir() if path.is_dir()]):
        service, cause = parse_case_name(case_dir.name)
        if service is None or cause is None:
            continue

        for instance_dir in sorted([path for path in case_dir.iterdir() if path.is_dir()]):
            inject_file = instance_dir / "inject_time.txt"
            is_numeric = instance_dir.name.isdigit()

            if not inject_file.exists():
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": instance_dir.name,
                        "inject_time_raw": None,
                        "inject_time_unit": None,
                        "inject_time_seconds": None,
                        "inject_time_utc": None,
                        "inject_time_beijing": None,
                        "inject_time_file": str(inject_file),
                        "status": "missing_inject_time",
                        "note": "inject_time.txt not found"
                        + ("" if is_numeric else " (instance_id is not numeric)"),
                    }
                )
                continue

            raw, error = read_first_int(inject_file)
            if raw is None:
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": instance_dir.name,
                        "inject_time_raw": None,
                        "inject_time_unit": None,
                        "inject_time_seconds": None,
                        "inject_time_utc": None,
                        "inject_time_beijing": None,
                        "inject_time_file": str(inject_file),
                        "status": "parse_error",
                        "note": (error or "unknown parse error")
                        + ("" if is_numeric else " (instance_id is not numeric)"),
                    }
                )
                continue

            ts_seconds, unit = normalize_epoch_to_seconds(raw)
            try:
                utc_str = ts_to_str(ts_seconds, UTC_TZ)
                bj_str = ts_to_str(ts_seconds, BJ_TZ)
            except (OverflowError, OSError, ValueError) as error:
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": instance_dir.name,
                        "inject_time_raw": raw,
                        "inject_time_unit": unit,
                        "inject_time_seconds": None,
                        "inject_time_utc": None,
                        "inject_time_beijing": None,
                        "inject_time_file": str(inject_file),
                        "status": "convert_error",
                        "note": f"Failed to convert epoch to datetime: {error}",
                    }
                )
                continue

            rows.append(
                {
                    "service": service,
                    "cause": cause,
                    "case_name": case_dir.name,
                    "instance_id": instance_dir.name,
                    "inject_time_raw": raw,
                    "inject_time_unit": unit,
                    "inject_time_seconds": ts_seconds,
                    "inject_time_utc": utc_str,
                    "inject_time_beijing": bj_str,
                    "inject_time_file": str(inject_file),
                    "status": "ok",
                    "note": "" if is_numeric else "instance_id is not numeric",
                }
            )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            by=["service", "cause", "case_name", "instance_id"],
            kind="stable",
        ).reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Extract RE2-TT (service, cause, inject_time) into a CSV."
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help='Path to the RE2-TT root directory, for example "the/path/to/RE2-TT/RE2-TT".',
    )
    parser.add_argument(
        "--output",
        type=str,
        default="re2_tt_inject_time_index.csv",
        help="Output CSV path (default: re2_tt_inject_time_index.csv in the current directory).",
    )
    args = parser.parse_args()

    root_dir = Path(args.root)
    out_path = Path(args.output)

    df = extract(root_dir)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    total = len(df)
    ok = int((df["status"] == "ok").sum()) if total else 0
    print(f"Saved: {out_path}")
    print(f"Rows: {total}, ok: {ok}, issues: {total - ok}")


if __name__ == "__main__":
    main()
