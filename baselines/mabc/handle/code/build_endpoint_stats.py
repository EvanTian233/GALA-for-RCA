import json
import os
from collections import defaultdict
from datetime import datetime
from math import inf

# ===== Path Configuration =====
INPUT_RECORDS_FILE = "data/records_3/ops/records_5.jsonl"
OUTPUT_STATS_FILE = "data/metric/endpoint_stats.json"
# gRPC code 14 is commonly UNAVAILABLE/DEADLINE_EXCEEDED, can be used as timeout code
TIMEOUT_STATUS_CODES = {14}
# If you want to use only duration for timeout, change this to empty set: set()
# ===========================


def load_records(path):
    """Read JSONL records line by line."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def build_timeout_thresholds(records):
    """
    Calculate adaptive timeout threshold for each endpoint.

    Approach:
    - Collect all durations for endpoint and sort ascending
    - Find max multiplier jump between adjacent durations:
        rates[i] = durations[i] / durations[i - 1]
      Requests after jump point are abnormally slow, use durations[i] as threshold
    - If max jump < 3, no obvious long tail, set threshold to +inf (use statusCode only)

    This gives each endpoint an adaptive abnormally slow boundary.
    """
    durations_by_ep = defaultdict(list)
    for r in records:
        ep = r.get("endpointName")
        if ep is None:
            continue
        dur = r.get("duration")
        if dur is None:
            continue
        durations_by_ep[ep].append(float(dur))

    thresholds = {}
    for ep, durs in durations_by_ep.items():
        if not durs:
            thresholds[ep] = inf
            continue
        durs = sorted(durs)
        if len(durs) == 1:
            thresholds[ep] = durs[0]
            continue

        # Small smoothing to avoid division by zero
        shifted = [x for x in durs]
        rates = [-1.0]
        for i in range(1, len(shifted)):
            prev = shifted[i - 1]
            curr = shifted[i]
            if prev <= 0:
                rates.append(-1.0)
            else:
                rates.append(curr / prev)

        max_rate = max(rates)
        idx = rates.index(max_rate)
        thr = shifted[idx]

        if max_rate < 2.5:
            thr = inf

        thresholds[ep] = thr

    return thresholds


def main():
    # First pass: load all records into memory (used multiple times later)
    records = list(load_records(INPUT_RECORDS_FILE))
    if not records:
        raise RuntimeError(f"No records found in {INPUT_RECORDS_FILE}")

    # Build timeout threshold for each endpoint
    timeout_thresholds = build_timeout_thresholds(records)

    # Second pass: aggregate by (endpoint, minute)
    stats = defaultdict(lambda: defaultdict(lambda: {
        "calls": 0,
        "success_cnt": 0,
        "error_cnt": 0,
        "timeout_cnt": 0,
        "total_duration": 0.0,
    }))

    for r in records:
        ep = r.get("endpointName")
        if ep is None:
            continue

        start_ms = r.get("startTime")
        if start_ms is None:
            continue

        # Convert millisecond timestamp to "YYYY-MM-DD HH:MM:SS", aggregate by minute
        dt = datetime.fromtimestamp(start_ms / 1000.0)
        minute_str = dt.strftime("%Y-%m-%d %H:%M:00")

        bucket = stats[ep][minute_str]
        bucket["calls"] += 1

        duration = float(r.get("duration", 0.0))
        bucket["total_duration"] += duration

        # Handle statusCode: treat empty as 0
        raw_code = r.get("statusCode", 0)
        try:
            status_code = int(raw_code)
        except (TypeError, ValueError):
            status_code = 0

        # error: statusCode != 0 counts as error
        is_error = status_code != 0
        if is_error:
            bucket["error_cnt"] += 1
        else:
            bucket["success_cnt"] += 1

        # timeout: either condition satisfies
        # 1) statusCode is a timeout code (e.g., gRPC 14)
        # 2) duration exceeds endpoint's timeout threshold
        thr = timeout_thresholds.get(ep, inf)
        is_timeout = (status_code in TIMEOUT_STATUS_CODES) or (duration >= thr)
        if is_timeout:
            bucket["timeout_cnt"] += 1

    # Third step: compute various rates
    endpoint_stats = {}
    for ep, by_minute in stats.items():
        endpoint_stats[ep] = {}
        for minute_str, b in by_minute.items():
            calls = b["calls"]
            if calls == 0:
                continue

            success_rate = 100.0 * b["success_cnt"] / calls
            error_rate = 100.0 * b["error_cnt"] / calls
            average_duration = b["total_duration"] / calls
            timeout_rate = 100.0 * b["timeout_cnt"] / calls

            endpoint_stats[ep][minute_str] = {
                "calls": calls,
                "success_rate": success_rate,
                "error_rate": error_rate,
                "average_duration": average_duration,
                "timeout_rate": timeout_rate,
            }

    os.makedirs(os.path.dirname(OUTPUT_STATS_FILE), exist_ok=True)
    with open(OUTPUT_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(endpoint_stats, f, ensure_ascii=False, indent=2)

    print(f"endpoint_stats.json written to: {OUTPUT_STATS_FILE}")


if __name__ == "__main__":
    main()
