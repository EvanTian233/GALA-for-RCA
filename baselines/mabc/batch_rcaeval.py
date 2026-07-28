import csv
import json
import os
import sys
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from math import inf
from pathlib import Path
import subprocess
import argparse
import pandas as pd
import numpy as np

from config_paths import MABC_ROOT, get_dataset_paths

# Dataset paths (override with --benchmark-data-root or MABC_BENCHMARK_DATA_ROOT)
DATASET_PATHS = get_dataset_paths()

# Global variables (will be set based on --dataset argument)
BASE_DATA_PATH = None
OUTPUT_ROOT = None
CURRENT_DATASET = None

TIMEOUT_STATUS_CODES = {14}

BASELINE_SECONDS = 5 * 60
ZSCORE_K = 3.0

WORSE_IF_HIGH = [
    r"latency-90", r"latency-95", r"latency-99", r"latency-50",
    r"error", r"errors",
    r"workload", r"qps", r"requests",
    r"cpu", r"mem", r"memory", r"diskio", r"bytes", r"socket"
]


def is_up_to_date(output_file: Path, inputs: list[Path]) -> bool:
    if not output_file.exists() or output_file.stat().st_size == 0:
        return False
    out_mtime = output_file.stat().st_mtime
    for ip in inputs:
        if ip.exists() and ip.stat().st_mtime > out_mtime:
            return False
    return True


def parse_args():
    p = argparse.ArgumentParser(description="Batch process RE2-OB / RE2-TT benchmark datasets")
    p.add_argument("--dataset", choices=["RE2-OB", "RE2-TT"], default="RE2-OB",
                   help="Choose dataset to process: RE2-OB (Online Boutique) or RE2-TT (TrainTicket). Default: RE2-OB")
    p.add_argument("--prepare-only", action="store_true",
                   help="Only build label + endpoint_stats + endpoint_maps; do NOT run mABC/main/main.py (no LLM).")
    p.add_argument("--force-rebuild", action="store_true",
                   help="Force rebuild endpoint_stats.json and endpoint_maps.json (ignore cache).")
    p.add_argument("--max-cases", type=int, default=0,
                   help="Max number of cases to process (0 means no limit).")
    p.add_argument("--model-tag", type=str, default="",
                   help="Tag to append to output directory (e.g., 'gpt4.1mini' -> output/RE2-OB-gpt4.1mini/)")
    p.add_argument("--skip-completed", action="store_true",
                   help="Skip cases that have already been completed (main_stdout.txt exists). Use for resuming after interruption.")
    p.add_argument("--benchmark-data-root", type=str, default="",
                   help="Directory containing RE2/ (overrides MABC_BENCHMARK_DATA_ROOT; default: <mABC>/data).")
    return p.parse_args()


def is_case_completed(case_out_dir: Path) -> bool:
    """
    Check if a case has already been completed (LLM has run).
    A case is considered complete if main_stdout.txt exists and is non-empty.
    """
    stdout_file = case_out_dir / "main_stdout.txt"
    usage_summary = case_out_dir / "llm_usage_summary.json"
    
    if not stdout_file.exists() or stdout_file.stat().st_size == 0:
        return False
    if not usage_summary.exists():
        return False
    return True


def load_existing_usage_summary(case_out_dir: Path) -> dict | None:
    """Load existing llm_usage_summary.json for a completed case."""
    usage_file = case_out_dir / "llm_usage_summary.json"
    if not usage_file.exists():
        return None
    try:
        with usage_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def init_dataset_config(dataset_name: str, model_tag: str = ""):
    """Initialize global dataset configuration based on selected dataset and model tag."""
    global BASE_DATA_PATH, OUTPUT_ROOT, CURRENT_DATASET
    
    if dataset_name not in DATASET_PATHS:
        raise ValueError(f"Unknown dataset: {dataset_name}. Available: {list(DATASET_PATHS.keys())}")
    
    CURRENT_DATASET = dataset_name
    BASE_DATA_PATH = DATASET_PATHS[dataset_name]
    
    # Add model tag to output directory if specified
    if model_tag:
        OUTPUT_ROOT = MABC_ROOT / "output" / f"{dataset_name}-{model_tag}"
    else:
        OUTPUT_ROOT = MABC_ROOT / "output" / dataset_name
    
    print(f"[CONFIG] Dataset: {CURRENT_DATASET}")
    print(f"[CONFIG] Data path: {BASE_DATA_PATH}")
    print(f"[CONFIG] Output path: {OUTPUT_ROOT}")
    if model_tag:
        print(f"[CONFIG] Model tag: {model_tag}")


def minute_key_utc_from_seconds(epoch_s: int) -> str:
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc).replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def minute_key_utc_from_millis(epoch_ms: float) -> str:
    epoch_s = epoch_ms / 1000.0
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc).replace(second=0, microsecond=0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def read_injected_time_s(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        token = f.read().strip().split()[0]
    if re.fullmatch(r"\d{10}", token):
        return int(token)
    if re.fullmatch(r"\d{13}", token):
        return int(int(token) / 1000)
    dt = pd.to_datetime(token, utc=True, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"Cannot parse injected_time: {token}")
    return int(dt.timestamp())


def split_endpoint_metric(col: str):
    """
    Split column name into endpoint and metric.
    
    RE2-OB format: "adservice_cpu" -> ("adservice", "cpu")
    RE2-TT format: "ts-auth-service_cpu" -> ("ts-auth-service", "cpu")
    
    For RE2-TT, service names contain '-', so we use rfind to find the last '_'
    """
    if "_" not in col:
        return col.strip().lower(), ""
    
    # For RE2-TT (TrainTicket), service names contain multiple '-'
    # e.g., "ts-auth-service_cpu" -> split from the LAST underscore
    if CURRENT_DATASET == "RE2-TT":
        i = col.rfind("_")  # Find last underscore
    else:
        # For RE2-OB, use first underscore (original behavior)
        i = col.index("_")
    
    ep = col[:i].strip().lower()
    m = col[i + 1:].strip()
    return ep, m


def pick_metric_columns(cols):
    cand = [c for c in cols if c != "time"]

    def score(c):
        _, m = split_endpoint_metric(c)
        for rank, pat in enumerate(WORSE_IF_HIGH):
            if re.search(pat, m, flags=re.IGNORECASE):
                return len(WORSE_IF_HIGH) - rank
        return 0

    scored = [(c, score(c)) for c in cand]
    scored = [x for x in scored if x[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored]


def build_ep2metrics(cols):
    mcols = pick_metric_columns(cols)
    ep2m = {}
    for c in mcols:
        ep, _ = split_endpoint_metric(c)
        ep2m.setdefault(ep, []).append(c)
    return ep2m


def compute_baseline_stats(df_base, ep2m):
    stats = {}
    for ep, mcols in ep2m.items():
        inner = {}
        for mc in mcols:
            s = pd.to_numeric(df_base[mc], errors="coerce")
            mu = s.mean(skipna=True)
            sigma = s.std(skipna=True, ddof=1)
            if not np.isfinite(sigma) or sigma <= 1e-12:
                sigma = 1e-12
            inner[mc] = (float(mu), float(sigma))
        stats[ep] = inner
    return stats


def detect_first_anomaly(df_det, ep2m, stats, k=ZSCORE_K):
    for _, row in df_det.iterrows():
        t = int(row["time"])
        for ep, mcols in ep2m.items():
            if ep not in stats:
                continue
            for mc in mcols:
                mu, sigma = stats[ep][mc]
                val = pd.to_numeric(row.get(mc, np.nan), errors="coerce")
                if pd.isna(val):
                    continue
                thr = mu + k * sigma
                if val > thr:
                    return t, ep, mc, float(val), float(thr)
    return 0, "", "", 0.0, 0.0


def generate_label_for_case(case_dir: Path) -> Path | None:
    inject_path = case_dir / "inject_time.txt"
    metrics_csv = case_dir / "simple_metrics.csv"
    out_label = case_dir / "label.json"

    if not inject_path.exists() or not metrics_csv.exists():
        print(f"[WARN] {case_dir} lacks inject_time.txt or simple_metrics.csv; cannot build label.json")
        return None

    if is_up_to_date(out_label, [inject_path, metrics_csv]):
        print(f"[CACHE HIT] label.json is up-to-date → {out_label}")
        return out_label

    inj_s = read_injected_time_s(inject_path)

    df = pd.read_csv(metrics_csv)
    if "time" not in df.columns:
        raise KeyError(f"{metrics_csv} must have 'time' column")
    df = df.sort_values("time").reset_index(drop=True)

    ep2m = build_ep2metrics(df.columns.tolist())
    if not ep2m:
        raise RuntimeError(f"{metrics_csv} has no usable metrics columns")

    t0 = inj_s - BASELINE_SECONDS
    df_base = df[(df["time"] >= t0) & (df["time"] < inj_s)].copy()
    if len(df_base) < 5:
        raise RuntimeError(f"{metrics_csv} baseline too small (<5 rows)")
    df_det = df[df["time"] >= inj_s].copy()
    if df_det.empty:
        raise RuntimeError(f"{metrics_csv} has no data after injected_time")

    stats = compute_baseline_stats(df_base, ep2m)

    ts, ep, mc, val, thr = detect_first_anomaly(df_det, ep2m, stats, k=ZSCORE_K)
    if ts == 0:
        print(f"[{metrics_csv}] NO ABNORMAL; skip label.json generation")
        return None

    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[ANOMALY] time={ts} ({iso}) endpoint={ep} metric={mc} value={val:.6g} threshold={thr:.6g}")

    minute = minute_key_utc_from_seconds(ts)
    label_obj = {minute: {ep: [[]]}}

    out_label.parent.mkdir(parents=True, exist_ok=True)
    with out_label.open("w", encoding="utf-8") as f:
        json.dump(label_obj, f, ensure_ascii=False, indent=2)

    print(f"[OK] written label.json to → {out_label}")
    return out_label


def load_metrics_minute_aggregates(metrics_csv: Path) -> dict:
    """
    Return:
      metrics_map[endpoint][minute_str][metric_name] = mean_value
    endpoint is lowercased; minute_str in UTC "YYYY-MM-DD HH:MM:SS"
    """
    df = pd.read_csv(metrics_csv)
    if "time" not in df.columns:
        raise KeyError(f"{metrics_csv} must have 'time' column")

    df = df.sort_values("time").reset_index(drop=True)
    df["_minute"] = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True).dt.floor("min").dt.strftime("%Y-%m-%d %H:%M:%S")

    metric_cols = [c for c in df.columns if c not in ("time", "_minute")]
    for c in metric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    g = df.groupby("_minute")[metric_cols].mean(numeric_only=True)

    metrics_map = defaultdict(lambda: defaultdict(dict))
    for minute_str, row in g.iterrows():
        for col, val in row.items():
            if pd.isna(val):
                continue
            ep, m = split_endpoint_metric(col)
            if not m:
                continue
            metrics_map[ep][minute_str][m] = float(val)

    return metrics_map


def load_spans_from_csv(csv_path: Path):
    spans = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trace_id = (row.get("traceID") or "").strip()
            span_id = (row.get("spanID") or "").strip()
            if not trace_id or not span_id:
                continue

            parent_span_id = (row.get("parentSpanID") or "").strip()
            if parent_span_id == "" or parent_span_id.lower() in ("null", "none"):
                parent_span_id = None

            service_name = (row.get("serviceName") or "").strip()
            operation_name = (row.get("operationName") or "").strip()

            # endpoint normalized to service-level (lowercase) to match metrics/label
            if service_name:
                endpoint = service_name.strip().lower()
            elif operation_name:
                endpoint = operation_name.strip().lower()
            else:
                endpoint = "unknown"

            start_ms_raw = (row.get("startTimeMillis") or row.get("startTime") or "").strip()
            if start_ms_raw == "":
                continue
            try:
                start_ms = int(float(start_ms_raw))
            except ValueError:
                continue

            duration_raw = (row.get("duration") or "0").strip()
            try:
                duration_us = float(duration_raw)
            except ValueError:
                duration_us = 0.0
            duration_ms = duration_us / 1000.0

            status_raw = (row.get("statusCode") or "").strip()
            if status_raw == "" or status_raw.lower() in ("null", "none"):
                status_code = 0
            else:
                try:
                    status_code = int(float(status_raw))
                except ValueError:
                    status_code = 0

            spans.append(
                {
                    "traceId": trace_id,
                    "spanId": span_id,
                    "parentSpanId": parent_span_id,
                    "endpoint": endpoint,
                    "start_ms": start_ms,
                    "duration_ms": duration_ms,
                    "statusCode": status_code,
                }
            )
    return spans


def build_timeout_thresholds(spans):
    durs_by_ep = defaultdict(list)
    for s in spans:
        ep = s["endpoint"]
        durs_by_ep[ep].append(float(s["duration_ms"]))

    thresholds = {}
    for ep, durs in durs_by_ep.items():
        if not durs:
            thresholds[ep] = inf
            continue
        durs = sorted(durs)
        if len(durs) == 1:
            thresholds[ep] = durs[0]
            continue

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
        if max_rate < 3.0:
            thr = inf
        thresholds[ep] = thr

    return thresholds


def build_endpoint_stats(spans, out_path: Path, metrics_csv: Path | None = None):
    timeout_thresholds = build_timeout_thresholds(spans)

    stats = defaultdict(
        lambda: defaultdict(
            lambda: {
                "calls": 0,
                "success_cnt": 0,
                "error_cnt": 0,
                "timeout_cnt": 0,
                "total_duration": 0.0,
            }
        )
    )

    for s in spans:
        ep = s["endpoint"]
        start_ms = s["start_ms"]
        duration = float(s["duration_ms"])
        status_code = s["statusCode"]

        minute_str = minute_key_utc_from_millis(start_ms)

        bucket = stats[ep][minute_str]
        bucket["calls"] += 1
        bucket["total_duration"] += duration

        if status_code != 0:
            bucket["error_cnt"] += 1
        else:
            bucket["success_cnt"] += 1

        thr = timeout_thresholds.get(ep, inf)
        is_timeout = (status_code in TIMEOUT_STATUS_CODES) or (duration >= thr)
        if is_timeout:
            bucket["timeout_cnt"] += 1

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

    # merge metrics (minute mean) into endpoint_stats entries
    if metrics_csv is not None and metrics_csv.exists():
        try:
            metrics_map = load_metrics_minute_aggregates(metrics_csv)
        except Exception as e:
            print(f"[WARN] load_metrics_minute_aggregates failed: {e}")
            metrics_map = {}

        for ep, by_minute in metrics_map.items():
            endpoint_stats.setdefault(ep, {})
            for minute_str, mvals in by_minute.items():
                if minute_str not in endpoint_stats[ep]:
                    endpoint_stats[ep][minute_str] = {
                        "calls": 0,
                        "success_rate": 0.0,
                        "error_rate": 0.0,
                        "average_duration": 0.0,
                        "timeout_rate": 0.0,
                    }
                for k, v in mvals.items():
                    endpoint_stats[ep][minute_str][k] = v

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(endpoint_stats, f, ensure_ascii=False, indent=2)


def build_endpoint_maps(spans, out_path: Path):
    endpoint_by_span = {}
    for s in spans:
        endpoint_by_span[(s["traceId"], s["spanId"])] = s["endpoint"]

    endpoint_maps = defaultdict(lambda: defaultdict(set))

    for s in spans:
        child = s["endpoint"]
        trace_id = s["traceId"]
        parent_span_id = s["parentSpanId"]

        if not parent_span_id:
            parent = "None"
        else:
            parent = endpoint_by_span.get((trace_id, parent_span_id), "None")

        minute_str = minute_key_utc_from_millis(s["start_ms"])
        endpoint_maps[parent][minute_str].add(child)

    maps_json = {}
    for parent, by_minute in endpoint_maps.items():
        maps_json[parent] = {}
        for minute_str, children in by_minute.items():
            maps_json[parent][minute_str] = sorted(children)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(maps_json, f, ensure_ascii=False, indent=2)


def iter_trace_cases():
    """
    Iterate through all trace cases in the current dataset.
    Works for both RE2-OB and RE2-TT directory structures.
    """
    if BASE_DATA_PATH is None:
        raise RuntimeError("Dataset not initialized. Call init_dataset_config() first.")
    
    for service_dir in BASE_DATA_PATH.iterdir():
        if not service_dir.is_dir():
            continue

        trace_csv_direct = service_dir / "traces.csv"
        if trace_csv_direct.exists():
            yield service_dir.name, "root", trace_csv_direct

        for sub in service_dir.iterdir():
            if not sub.is_dir():
                continue
            trace_csv = sub / "traces.csv"
            if trace_csv.exists():
                yield service_dir.name, sub.name, trace_csv


# =========================
# NEW: summarize LLM usage jsonl
# =========================
def summarize_llm_usage_jsonl(usage_log: Path) -> dict:
    """
    Expect jsonl where each line is a JSON object containing a 'usage' field.
    Support both:
      - ChatCompletion: usage.prompt_tokens / usage.completion_tokens / usage.total_tokens
      - Responses API:  usage.input_tokens / usage.output_tokens / usage.total_tokens
    """
    summary = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "usage_log": str(usage_log),
        "missing_or_invalid_lines": 0,
    }

    if (not usage_log.exists()) or usage_log.stat().st_size == 0:
        return summary

    with usage_log.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                summary["missing_or_invalid_lines"] += 1
                continue

            usage = obj.get("usage")
            if not isinstance(usage, dict):
                summary["missing_or_invalid_lines"] += 1
                continue

            # Responses API style
            in_tok = usage.get("input_tokens")
            out_tok = usage.get("output_tokens")
            tot_tok = usage.get("total_tokens")

            # ChatCompletion style fallback
            if in_tok is None and out_tok is None and tot_tok is None:
                in_tok = usage.get("prompt_tokens")
                out_tok = usage.get("completion_tokens")
                tot_tok = usage.get("total_tokens")

            try:
                in_tok_i = int(in_tok) if in_tok is not None else 0
                out_tok_i = int(out_tok) if out_tok is not None else 0
                tot_tok_i = int(tot_tok) if tot_tok is not None else (in_tok_i + out_tok_i)
            except Exception:
                summary["missing_or_invalid_lines"] += 1
                continue

            summary["calls"] += 1
            summary["input_tokens"] += in_tok_i
            summary["output_tokens"] += out_tok_i
            summary["total_tokens"] += tot_tok_i

    return summary


def run_main_for_case(stats_file: Path, maps_file: Path, label_file: Path, case_out_dir: Path) -> dict:
    env = os.environ.copy()
    env["METRIC_STATS_FILE"] = str(stats_file)
    env["TOPOLOGY_MAPS_FILE"] = str(maps_file)
    env["LABEL_FILE"] = str(label_file)

    main_py = MABC_ROOT / "main" / "main.py"

    project_root = str(MABC_ROOT.parent)  # parent of mABC package (for PYTHONPATH)
    env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

    case_out_dir.mkdir(parents=True, exist_ok=True)

    # NEW: per-case usage log file (written by llm.py if it respects LLM_USAGE_LOG)
    usage_log = case_out_dir / "llm_usage.jsonl"
    if usage_log.exists():
        usage_log.unlink()
    env["LLM_USAGE_LOG"] = str(usage_log)

    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(main_py)],
        cwd=str(MABC_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - t0

    stdout_text = result.stdout if isinstance(result.stdout, str) else ""
    stderr_text = result.stderr if isinstance(result.stderr, str) else ""

    (case_out_dir / "main_stdout.txt").write_text(stdout_text, encoding="utf-8")
    (case_out_dir / "main_stderr.txt").write_text(stderr_text, encoding="utf-8")

    usage_summary = summarize_llm_usage_jsonl(usage_log)
    usage_summary["elapsed_seconds"] = float(elapsed)

    (case_out_dir / "llm_usage_summary.json").write_text(
        json.dumps(usage_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return usage_summary


def process_one_case(service_name: str, case_name: str, trace_csv: Path, args):
    case_dir = trace_csv.parent
    case_out_dir = OUTPUT_ROOT / service_name / case_name

    label_file = case_dir / "label.json"
    stats_file = case_out_dir / "endpoint_stats.json"
    maps_file = case_out_dir / "endpoint_maps.json"

    inject_path = case_dir / "inject_time.txt"
    metrics_csv = case_dir / "simple_metrics.csv"

    print(f"=== Processing {service_name} / {case_name} @ {case_dir} ===")

    # Check if case is already completed (for --skip-completed mode)
    if args.skip_completed and is_case_completed(case_out_dir):
        print("[SKIP] Case already completed (main_stdout.txt exists). Loading existing summary.")
        existing_summary = load_existing_usage_summary(case_out_dir)
        if existing_summary:
            existing_summary["_skipped"] = True
            print(f"=== Skipped {service_name} / {case_name} (already done) ===\n")
            return existing_summary
        else:
            print("[WARN] Could not load existing summary, will re-run.")

    # stats depends on BOTH trace and metrics now
    label_ready = is_up_to_date(label_file, [inject_path, metrics_csv])
    stats_ready = (not args.force_rebuild) and is_up_to_date(stats_file, [trace_csv, metrics_csv])
    maps_ready = (not args.force_rebuild) and is_up_to_date(maps_file, [trace_csv])

    cache_ready = label_ready and stats_ready and maps_ready

    if not cache_ready:
        print("[CACHE MISS] Will build missing label/stats/maps and SKIP LLM this run.")

        # 1) Build/Reuse label
        try:
            label_path = generate_label_for_case(case_dir)
        except Exception as e:
            print(f"[ERROR] generate label.json failed: {e}")
            return None
        if label_path is None:
            print(f"[WARN] {case_dir} has no anomaly; skipping stats/maps + main.py")
            return None

        # 2) Build stats/maps if needed
        need_stats = args.force_rebuild or not is_up_to_date(stats_file, [trace_csv, metrics_csv])
        need_maps = args.force_rebuild or not is_up_to_date(maps_file, [trace_csv])

        if need_stats or need_maps:
            spans = load_spans_from_csv(trace_csv)
            if not spans:
                print(f"[WARN] No spans found in {trace_csv}, skip.")
                return None

            case_out_dir.mkdir(parents=True, exist_ok=True)

            if need_stats:
                build_endpoint_stats(spans, stats_file, metrics_csv=metrics_csv if metrics_csv.exists() else None)
                print(f"[OK] built stats (trace + metrics) → {stats_file}")
            else:
                print(f"[CACHE HIT] stats is up-to-date → {stats_file}")

            if need_maps:
                build_endpoint_maps(spans, maps_file)
                print(f"[OK] built maps  → {maps_file}")
            else:
                print(f"[CACHE HIT] maps is up-to-date → {maps_file}")

        if args.prepare_only:
            print("[PREPARE ONLY] Done cache building. main.py will NOT run.\n")
            return None

        print("[CACHE BUILT] First-run behavior: main.py (LLM) is skipped. Run again to execute LLM.\n")
        return None

    if args.prepare_only:
        print("[PREPARE ONLY] Cache is ready; main.py will NOT run.\n")
        return None

    print("[CACHE HIT] label/stats/maps all ready. Running main.py now (LLM).")
    summary = run_main_for_case(stats_file, maps_file, label_file, case_out_dir)
    print(f"=== Done {service_name} / {case_name}, outputs in {case_out_dir} ===\n")
    return summary


def main():
    args = parse_args()
    global DATASET_PATHS
    if getattr(args, "benchmark_data_root", "") and str(args.benchmark_data_root).strip():
        DATASET_PATHS = get_dataset_paths(Path(args.benchmark_data_root).expanduser().resolve())

    # Initialize dataset configuration based on --dataset and --model-tag arguments
    init_dataset_config(args.dataset, args.model_tag)

    print(f"\n{'='*60}")
    print(f"DATASET     = {CURRENT_DATASET}")
    print(f"DATA_PATH   = {BASE_DATA_PATH}")
    print(f"MABC_ROOT   = {MABC_ROOT}")
    print(f"OUTPUT_ROOT = {OUTPUT_ROOT}")
    if args.model_tag:
        print(f"MODEL_TAG   = {args.model_tag}")
    print(f"{'='*60}")
    print(f"Args: prepare_only={args.prepare_only}, force_rebuild={args.force_rebuild}, "
          f"max_cases={args.max_cases}, skip_completed={args.skip_completed}, "
          f"benchmark_data_root={getattr(args, 'benchmark_data_root', '')!r}\n")

    idx = 0

    # Accumulators for batch averages
    n_llm = 0
    n_skipped = 0
    sum_elapsed = 0.0

    sum_in = 0
    sum_out = 0
    sum_total = 0

    # if some cases miss usage tokens, we compute token averages on available ones
    n_tok = 0

    for service_name, case_name, trace_csv in iter_trace_cases():
        idx += 1
        summary = process_one_case(service_name, case_name, trace_csv, args)

        if isinstance(summary, dict):
            # Check if this was a skipped case (loaded from cache)
            was_skipped = summary.get("_skipped", False)
            if was_skipped:
                n_skipped += 1
            
            elapsed = summary.get("elapsed_seconds", None)
            if elapsed is not None:
                sum_elapsed += float(elapsed)
            n_llm += 1

            it = summary.get("input_tokens", None)
            ot = summary.get("output_tokens", None)
            tt = summary.get("total_tokens", None)

            if isinstance(it, int) and isinstance(ot, int) and isinstance(tt, int):
                sum_in += it
                sum_out += ot
                sum_total += tt
                n_tok += 1

        if args.max_cases and idx >= args.max_cases:
            print("### Reached --max-cases limit, stop.")
            break

    if n_llm == 0:
        print("[SUMMARY NOTE] No cases ran main.py (LLM). No average time/token to report.")
        return

    avg_elapsed = sum_elapsed / n_llm

    if n_tok > 0:
        avg_in = sum_in / n_tok
        avg_out = sum_out / n_tok
        avg_total = sum_total / n_tok
    else:
        avg_in = 0.0
        avg_out = 0.0
        avg_total = 0.0

    print("============== BATCH USAGE SUMMARY ==============")
    print(f"Total cases processed   : {n_llm}")
    print(f"  - Newly run (LLM)     : {n_llm - n_skipped}")
    print(f"  - Skipped (cached)    : {n_skipped}")
    print(f"Cases with token usage  : {n_tok}")
    print(f"Average elapsed seconds : {avg_elapsed:.4f}")
    print(f"Average input tokens    : {avg_in:.2f}")
    print(f"Average output tokens   : {avg_out:.2f}")
    print(f"Average total tokens    : {avg_total:.2f}")
    print("=================================================")

    batch_summary_path = OUTPUT_ROOT / "batch_usage_summary.json"
    batch_summary = {
        "dataset": CURRENT_DATASET,
        "llm_cases_time": n_llm,
        "llm_cases_with_tokens": n_tok,
        "newly_run": n_llm - n_skipped,
        "skipped_cached": n_skipped,
        "avg_elapsed_seconds": avg_elapsed,
        "avg_input_tokens": avg_in,
        "avg_output_tokens": avg_out,
        "avg_total_tokens": avg_total,
        "sum_elapsed_seconds": sum_elapsed,
        "sum_input_tokens": sum_in,
        "sum_output_tokens": sum_out,
        "sum_total_tokens": sum_total,
    }
    batch_summary_path.parent.mkdir(parents=True, exist_ok=True)
    batch_summary_path.write_text(
        json.dumps(batch_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] Batch summary written to: {batch_summary_path}")


if __name__ == "__main__":
    main()



