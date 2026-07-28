#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract (service, cause, inject_time) from the RE2-OB dataset directory.

Assumed directory structure (as described):
RE2-OB/
  <faulty_service>_<fault_type>/
    <instance_id>/               # e.g., "2", "3", ...
      inject_time.txt            # contains an epoch timestamp (may be s/ms/us/ns; see below)
      logs/
      traces/
      simple_metrics/

Timezone clarification (IMPORTANT):
- The numeric epoch timestamps in this dataset behave like standard Unix epoch time in UTC.
  Example: 1708475052560 (ms) -> 2024-02-21 00:24:12 UTC.
- Therefore this script outputs BOTH:
  - inject_time_utc: UTC datetime string
  - inject_time_beijing: UTC+8 (Asia/Shanghai) datetime string

Output CSV columns:
- service
- cause
- case_name
- instance_id
- inject_time_raw
- inject_time_unit
- inject_time_seconds
- inject_time_utc
- inject_time_beijing
- inject_time_file
- status
- note
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
        # Python 3.9+
        from zoneinfo import ZoneInfo  # type: ignore
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        # Fallback (common in many RCA/DevOps envs)
        import pytz  # type: ignore
        return pytz.timezone("Asia/Shanghai")


UTC_TZ = timezone.utc
BJ_TZ = _get_beijing_tz()


def parse_case_name(case_name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse case folder name: '<service>_<cause>'
    Split on the FIRST underscore so causes like 'network_latency' still work.
    """
    service, sep, cause = case_name.partition("_")
    if not sep or not service or not cause:
        return None, None
    return service, cause


def read_first_int(inject_file: Path) -> Tuple[Optional[int], Optional[str]]:
    """
    Read inject_time.txt and extract the first integer number.
    Returns (raw_int, error_note).
    """
    try:
        content = inject_file.read_text(encoding="utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        # Windows-created text sometimes uses GBK; keep a forgiving fallback.
        content = inject_file.read_text(encoding="gbk", errors="ignore").strip()

    m = re.search(r"\d+", content)
    if not m:
        return None, f"No integer timestamp found in file content: {content[:80]!r}"

    try:
        raw = int(m.group(0))
    except Exception as e:
        return None, f"Failed to parse integer timestamp: {e}"

    return raw, None


def normalize_epoch_to_seconds(raw: int) -> Tuple[float, str]:
    """
    Normalize a raw epoch-like integer to seconds.

    Heuristic by magnitude:
    - >= 1e18 : nanoseconds
    - >= 1e15 : microseconds
    - >= 1e12 : milliseconds
    - else    : seconds

    Returns: (seconds_float, unit_label)
    """
    if raw >= 10**18:
        return raw / 1_000_000_000.0, "ns"
    if raw >= 10**15:
        return raw / 1_000_000.0, "us"
    if raw >= 10**12:
        return raw / 1_000.0, "ms"
    return float(raw), "s"


def ts_to_str(ts_seconds: float, tz) -> str:
    dt = datetime.fromtimestamp(ts_seconds, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def extract(root_dir: Path) -> pd.DataFrame:
    rows = []

    if not root_dir.exists():
        raise FileNotFoundError(f"Root directory does not exist: {root_dir}")

    # Case folders: <service>_<cause>
    for case_dir in sorted([p for p in root_dir.iterdir() if p.is_dir()]):
        service, cause = parse_case_name(case_dir.name)
        if service is None or cause is None:
            # Skip folders that don't match the pattern
            continue

        # Instance folders: usually numeric like "2", "3", ...
        instance_dirs = [p for p in case_dir.iterdir() if p.is_dir()]
        for inst_dir in sorted(instance_dirs):
            is_numeric = inst_dir.name.isdigit()

            inject_file = inst_dir / "inject_time.txt"
            if not inject_file.exists():
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": inst_dir.name,
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

            raw, err = read_first_int(inject_file)
            if raw is None:
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": inst_dir.name,
                        "inject_time_raw": None,
                        "inject_time_unit": None,
                        "inject_time_seconds": None,
                        "inject_time_utc": None,
                        "inject_time_beijing": None,
                        "inject_time_file": str(inject_file),
                        "status": "parse_error",
                        "note": (err or "unknown parse error")
                        + ("" if is_numeric else " (instance_id is not numeric)"),
                    }
                )
                continue

            ts_seconds, unit = normalize_epoch_to_seconds(raw)

            try:
                utc_str = ts_to_str(ts_seconds, UTC_TZ)
                bj_str = ts_to_str(ts_seconds, BJ_TZ)
            except (OverflowError, OSError, ValueError) as e:
                rows.append(
                    {
                        "service": service,
                        "cause": cause,
                        "case_name": case_dir.name,
                        "instance_id": inst_dir.name,
                        "inject_time_raw": raw,
                        "inject_time_unit": unit,
                        "inject_time_seconds": None,
                        "inject_time_utc": None,
                        "inject_time_beijing": None,
                        "inject_time_file": str(inject_file),
                        "status": "convert_error",
                        "note": f"Failed to convert epoch to datetime: {e}",
                    }
                )
                continue

            rows.append(
                {
                    "service": service,
                    "cause": cause,
                    "case_name": case_dir.name,
                    "instance_id": inst_dir.name,
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
            by=["service", "cause", "case_name", "instance_id"], kind="stable"
        ).reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Extract RE2-OB (service, cause, inject_time) into a CSV."
    )
    parser.add_argument(
        "--root",
        type=str,
        required=True,
        help='Path to the RE2-OB root directory, for example "the/path/to/RE2-OB".',
    )
    parser.add_argument(
        "--output",
        type=str,
        default="re2_ob_inject_time_index.csv",
        help="Output CSV path (default: re2_ob_inject_time_index.csv in current directory).",
    )
    args = parser.parse_args()

    root_dir = Path(args.root)
    out_path = Path(args.output)

    df = extract(root_dir)

    # Use UTF-8 with BOM for Excel friendliness on Windows.
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    total = len(df)
    ok = int((df["status"] == "ok").sum()) if total else 0
    print(f"Saved: {out_path}")
    print(f"Rows: {total}, ok: {ok}, issues: {total - ok}")


if __name__ == "__main__":
    main()
