#!/usr/bin/env python3
"""Run the Nezha benchmark on the raw RE2-OB or RE2-TT datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig


SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "log"
LOG_DIR.mkdir(exist_ok=True)

DEFAULT_PATTERN_SUPPORT = 5
DEFAULT_PATTERN_SCORE = 0.67


@dataclass(frozen=True)
class DatasetConfig:
    canonical_name: str
    description: str
    drain3_namespace: str
    logs_have_cluster_id: bool
    env_var: str


DATASET_CONFIG: Dict[str, DatasetConfig] = {
    "RE2-OB": DatasetConfig(
        canonical_name="RE2-OB",
        description="OnlineBoutique",
        drain3_namespace="hipster",
        logs_have_cluster_id=True,
        env_var="NEZHA_RE2_OB_PATH",
    ),
    "RE2-TT": DatasetConfig(
        canonical_name="RE2-TT",
        description="TrainTicket",
        drain3_namespace="ts",
        logs_have_cluster_id=False,
        env_var="NEZHA_RE2_TT_PATH",
    ),
}


def canonicalize_dataset_name(dataset: str) -> str:
    normalized = dataset.strip().lower()
    mapping = {
        "re2-ob": "RE2-OB",
        "re2_ob": "RE2-OB",
        "re2ob": "RE2-OB",
        "re2-tt": "RE2-TT",
        "re2_tt": "RE2-TT",
        "re2tt": "RE2-TT",
    }
    if normalized in mapping:
        return mapping[normalized]
    normalized = dataset.strip().upper()
    if normalized in DATASET_CONFIG:
        return normalized
    raise ValueError(
        f"Unsupported dataset '{dataset}'. Choose one of: "
        f"{', '.join(sorted(DATASET_CONFIG))}."
    )


def compute_metrics(ranks: List[int]) -> Dict[str, float]:
    """Compute ranking metrics from 1-based ranks. Zero means not found."""
    if not ranks:
        return {
            "MRR": 0.0,
            "acc@1": 0.0,
            "acc@2": 0.0,
            "acc@3": 0.0,
            "acc@4": 0.0,
            "acc@5": 0.0,
            "avg@3": 0.0,
            "avg@5": 0.0,
        }

    total = len(ranks)
    mrr = sum(1.0 / rank if rank > 0 else 0.0 for rank in ranks) / total
    acc1 = sum(1 for rank in ranks if 0 < rank <= 1) / total
    acc2 = sum(1 for rank in ranks if 0 < rank <= 2) / total
    acc3 = sum(1 for rank in ranks if 0 < rank <= 3) / total
    acc4 = sum(1 for rank in ranks if 0 < rank <= 4) / total
    acc5 = sum(1 for rank in ranks if 0 < rank <= 5) / total
    avg3 = (acc1 + acc2 + acc3) / 3
    avg5 = (acc1 + acc2 + acc3 + acc4 + acc5) / 5

    return {
        "MRR": round(mrr, 4),
        "acc@1": round(acc1, 4),
        "acc@2": round(acc2, 4),
        "acc@3": round(acc3, 4),
        "acc@4": round(acc4, 4),
        "acc@5": round(acc5, 4),
        "avg@3": round(avg3, 4),
        "avg@5": round(avg5, 4),
    }


def build_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("nezha_re2")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s %(filename)s:%(lineno)d %(message)s"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def stable_event_id(service_name: str, operation_name: str, phase: str) -> int:
    raw_key = f"{service_name}|{operation_name}|{phase}"
    digest = hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


class RE2NezhaAnalyzer:
    """Evaluate raw RE2 cases with the Nezha pattern comparison logic."""

    def __init__(
        self,
        dataset_name: str,
        data_path: Path,
        abnormal_window_minutes: int,
        logger: logging.Logger,
        min_pattern_support: int = DEFAULT_PATTERN_SUPPORT,
        min_pattern_score: float = DEFAULT_PATTERN_SCORE,
    ) -> None:
        self.dataset_name = canonicalize_dataset_name(dataset_name)
        self.dataset = DATASET_CONFIG[self.dataset_name]
        self.data_path = self._normalize_dataset_root(Path(data_path).resolve())
        self.abnormal_window_minutes = abnormal_window_minutes
        self.min_pattern_support = min_pattern_support
        self.min_pattern_score = min_pattern_score
        self.logger = logger
        self.template_miner = self._init_template_miner()

    def _normalize_dataset_root(self, data_path: Path) -> Path:
        if self.dataset_name == "RE2-TT":
            nested = data_path / "RE2-TT"
            has_direct_fault_folders = any(
                item.is_dir() and "_" in item.name for item in data_path.iterdir()
            )
            if nested.exists() and nested.is_dir() and not has_direct_fault_folders:
                return nested.resolve()
        return data_path

    def _init_template_miner(self) -> TemplateMiner:
        config_path = SCRIPT_DIR / "log_template" / f"drain3_{self.dataset.drain3_namespace}.ini"
        source_bin = SCRIPT_DIR / "log_template" / f"{self.dataset.drain3_namespace}.bin"

        if not config_path.exists():
            raise FileNotFoundError(f"Missing drain3 config file: {config_path}")
        if not source_bin.exists():
            raise FileNotFoundError(f"Missing drain3 state file: {source_bin}")

        runtime_bin_dir = LOG_DIR / "runtime"
        runtime_bin_dir.mkdir(exist_ok=True)
        runtime_bin = runtime_bin_dir / (
            f"{self.dataset_name.lower()}_{int(time.time())}_{source_bin.name}"
        )
        shutil.copy2(source_bin, runtime_bin)

        config = TemplateMinerConfig()
        config.load(str(config_path))
        config.profiling_enabled = False

        self.logger.info("Loaded drain3 config from %s", config_path)
        self.logger.info("Using runtime drain3 state copy at %s", runtime_bin)

        persistence = FilePersistence(str(runtime_bin))
        return TemplateMiner(persistence, config=config)

    def discover_fault_cases(self) -> List[Dict[str, str]]:
        fault_cases: List[Dict[str, str]] = []
        for folder in sorted(self.data_path.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            parts = folder.name.rsplit("_", 1)
            if len(parts) != 2:
                continue

            root_cause_service, fault_type = parts
            sub_folders = []
            for sub_folder in sorted(folder.iterdir(), key=lambda item: item.name):
                if not sub_folder.is_dir() or not sub_folder.name.isdigit():
                    continue
                if (sub_folder / "inject_time.txt").exists():
                    sub_folders.append(sub_folder.name)

            if sub_folders:
                fault_cases.append(
                    {
                        "fault_folder": folder.name,
                        "root_cause_service": root_cause_service,
                        "fault_type": fault_type,
                        "full_path": str(folder),
                        "sub_folders": sub_folders,
                    }
                )

        self.logger.info("Discovered %d fault folders under %s", len(fault_cases), self.data_path)
        return fault_cases

    @staticmethod
    def read_inject_time(case_path: Path) -> int:
        return int((case_path / "inject_time.txt").read_text(encoding="utf-8").strip())

    def get_time_windows(self, inject_timestamp: int, data_start_timestamp: int) -> Dict[str, object]:
        inject_dt = datetime.fromtimestamp(inject_timestamp, tz=timezone.utc)
        data_start_dt = datetime.fromtimestamp(data_start_timestamp, tz=timezone.utc)

        abnormal_start = inject_dt - timedelta(minutes=self.abnormal_window_minutes)
        abnormal_end = inject_dt + timedelta(minutes=self.abnormal_window_minutes)
        normal_start = data_start_dt
        normal_end = abnormal_start - timedelta(minutes=1)

        return {
            "inject_time": inject_dt,
            "normal_start": normal_start,
            "normal_end": normal_end,
            "abnormal_start": abnormal_start,
            "abnormal_end": abnormal_end,
            "inject_timestamp": inject_timestamp,
            "normal_start_ts": int(normal_start.timestamp()),
            "normal_end_ts": int(normal_end.timestamp()),
            "abnormal_start_ts": int(abnormal_start.timestamp()),
            "abnormal_end_ts": int(abnormal_end.timestamp()),
        }

    @staticmethod
    def normalize_service_name(name: str) -> str:
        if not name:
            return "unknown"
        normalized = name.strip().lower()
        normalized = normalized.replace("ts-", "")
        normalized = normalized.replace("-service", "")
        normalized = normalized.replace("service", "")
        normalized = normalized.strip("-_ ")
        return normalized or "unknown"

    @staticmethod
    def read_csv(csv_path: Path) -> pd.DataFrame:
        return pd.read_csv(csv_path, engine="python")

    @staticmethod
    def coerce_cluster_id(cluster_id: object) -> Optional[int]:
        if cluster_id is None or (isinstance(cluster_id, float) and np.isnan(cluster_id)):
            return None
        text = str(cluster_id).strip()
        if not text or text.lower() == "nan":
            return None
        try:
            value = int(float(text))
        except ValueError:
            return None
        return value if value > 0 else None

    def parse_log_to_event_id(
        self,
        container_name: str,
        message: str,
        cluster_id: Optional[object] = None,
    ) -> int:
        if self.dataset.logs_have_cluster_id:
            parsed_cluster_id = self.coerce_cluster_id(cluster_id)
            if parsed_cluster_id is not None:
                return parsed_cluster_id

        clean_container = container_name or "unknown"
        if self.dataset_name == "RE2-TT":
            clean_container = clean_container.replace("ts-", "").replace("-service", "")

        log_message = f"{clean_container} {message or ''}".strip()
        result = self.template_miner.add_log_message(log_message)
        return int(result["cluster_id"])

    def extract_patterns_from_traces(
        self,
        traces_df: pd.DataFrame,
        logs_df: pd.DataFrame,
    ) -> Tuple[Dict[str, int], Dict[str, set]]:
        pattern_counts: Dict[str, int] = {}
        pattern_service_map: Dict[str, set] = {}

        if traces_df.empty:
            return pattern_counts, pattern_service_map

        for _, trace_group in traces_df.groupby("traceID", sort=False):
            sorted_traces = trace_group.sort_values("startTime")
            events: List[Dict[str, object]] = []

            for _, row in sorted_traces.iterrows():
                service = str(row.get("serviceName", "unknown"))
                operation = str(row.get("operationName", "unknown") or "unknown")
                start_time = int(row["startTime"])
                duration = int(row.get("duration", 0) or 0)
                end_time = start_time + duration

                normalized_service = self.normalize_service_name(service)
                start_event = stable_event_id(service, operation, "start")
                end_event = stable_event_id(service, operation, "end")

                events.append(
                    {
                        "event_id": start_event,
                        "timestamp": start_time,
                        "service": normalized_service,
                    }
                )
                events.append(
                    {
                        "event_id": end_event,
                        "timestamp": end_time,
                        "service": normalized_service,
                    }
                )

            if not logs_df.empty:
                trace_start_ns = int(sorted_traces["startTime"].min()) * 1000
                trace_end_ns = int(
                    (sorted_traces["startTime"] + sorted_traces["duration"].fillna(0)).max()
                ) * 1000
                relevant_logs = logs_df[
                    (logs_df["timestamp"] >= trace_start_ns)
                    & (logs_df["timestamp"] <= trace_end_ns)
                ]

                for _, log_row in relevant_logs.iterrows():
                    event_id = self.parse_log_to_event_id(
                        container_name=str(log_row.get("container_name", "unknown")),
                        message=str(log_row.get("message", "")),
                        cluster_id=log_row.get("cluster_id"),
                    )
                    events.append(
                        {
                            "event_id": event_id,
                            "timestamp": int(log_row["timestamp"] / 1000),
                            "service": self.normalize_service_name(
                                str(log_row.get("container_name", "unknown"))
                            ),
                        }
                    )

            events.sort(key=lambda item: int(item["timestamp"]))
            for left, right in zip(events, events[1:]):
                pair_key = f"{left['event_id']}_{right['event_id']}"
                pattern_counts[pair_key] = pattern_counts.get(pair_key, 0) + 1
                pattern_service_map.setdefault(pair_key, set()).update(
                    {str(left["service"]), str(right["service"])}
                )

        return pattern_counts, pattern_service_map

    def rank_patterns(
        self,
        normal_patterns: Dict[str, int],
        abnormal_patterns: Dict[str, int],
        normal_service_map: Dict[str, set],
        abnormal_service_map: Dict[str, set],
    ) -> List[Dict[str, object]]:
        all_service_map: Dict[str, set] = {}
        for pattern, services in normal_service_map.items():
            all_service_map[pattern] = set(services)
        for pattern, services in abnormal_service_map.items():
            all_service_map.setdefault(pattern, set()).update(services)

        pattern_scores: Dict[str, float] = {}

        for pattern, normal_count in normal_patterns.items():
            if normal_count <= self.min_pattern_support:
                continue
            abnormal_count = abnormal_patterns.get(pattern, 0)
            score = normal_count / (normal_count + abnormal_count) if abnormal_count else 1.0
            if score >= self.min_pattern_score:
                pattern_scores[pattern] = max(pattern_scores.get(pattern, 0.0), score)

        for pattern, abnormal_count in abnormal_patterns.items():
            if abnormal_count <= self.min_pattern_support:
                continue
            normal_count = normal_patterns.get(pattern, 0)
            score = abnormal_count / (normal_count + abnormal_count) if normal_count else 1.0
            if score >= self.min_pattern_score:
                pattern_scores[pattern] = max(pattern_scores.get(pattern, 0.0), score)

        service_scores: Dict[str, float] = {}
        service_patterns: Dict[str, List[Tuple[str, float]]] = {}

        for pattern, score in pattern_scores.items():
            for service in all_service_map.get(pattern, set()):
                service_scores[service] = service_scores.get(service, 0.0) + score
                service_patterns.setdefault(service, []).append((pattern, score))

        ranked_services = sorted(
            service_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )

        results: List[Dict[str, object]] = []
        for service, total_score in ranked_services:
            patterns = service_patterns.get(service, [])
            top_pattern, top_score = max(patterns, key=lambda item: item[1], default=("", 0.0))
            results.append(
                {
                    "service": service,
                    "score": round(total_score, 6),
                    "pattern_count": len(patterns),
                    "top_pattern": top_pattern,
                    "top_pattern_score": round(top_score, 6),
                }
            )

        return results

    def analyze_single_case(self, case_path: Path, fault_info: Dict[str, object]) -> Dict[str, object]:
        started_at = time.time()
        case_id = f"{fault_info['fault_folder']}/{case_path.name}"

        self.logger.info("=" * 72)
        self.logger.info("Analyzing case %s", case_id)
        self.logger.info("Root-cause service: %s", fault_info["root_cause_service"])
        self.logger.info("Fault type: %s", fault_info["fault_type"])

        logs_df = self.read_csv(case_path / "logs.csv")
        traces_df = self.read_csv(case_path / "traces.csv")
        metrics_df = self.read_csv(case_path / "simple_metrics.csv")

        logs_df["timestamp"] = pd.to_numeric(logs_df["timestamp"], errors="coerce").fillna(0).astype("int64")
        traces_df["startTime"] = pd.to_numeric(traces_df["startTime"], errors="coerce").fillna(0).astype("int64")
        traces_df["duration"] = pd.to_numeric(traces_df["duration"], errors="coerce").fillna(0).astype("int64")
        metrics_df["time"] = pd.to_numeric(metrics_df["time"], errors="coerce").fillna(0).astype("int64")

        inject_timestamp = self.read_inject_time(case_path)
        data_start_ts = int(metrics_df["time"].min())
        time_windows = self.get_time_windows(inject_timestamp, data_start_ts)

        self.logger.info(
            "Normal window: %s -> %s",
            time_windows["normal_start"],
            time_windows["normal_end"],
        )
        self.logger.info(
            "Abnormal window: %s -> %s",
            time_windows["abnormal_start"],
            time_windows["abnormal_end"],
        )

        normal_logs = logs_df[
            (logs_df["timestamp"] >= time_windows["normal_start_ts"] * 1_000_000_000)
            & (logs_df["timestamp"] <= time_windows["normal_end_ts"] * 1_000_000_000)
        ]
        abnormal_logs = logs_df[
            (logs_df["timestamp"] >= time_windows["abnormal_start_ts"] * 1_000_000_000)
            & (logs_df["timestamp"] <= time_windows["abnormal_end_ts"] * 1_000_000_000)
        ]
        normal_traces = traces_df[
            (traces_df["startTime"] >= time_windows["normal_start_ts"] * 1_000_000)
            & (traces_df["startTime"] <= time_windows["normal_end_ts"] * 1_000_000)
        ]
        abnormal_traces = traces_df[
            (traces_df["startTime"] >= time_windows["abnormal_start_ts"] * 1_000_000)
            & (traces_df["startTime"] <= time_windows["abnormal_end_ts"] * 1_000_000)
        ]

        normal_patterns, normal_service_map = self.extract_patterns_from_traces(normal_traces, normal_logs)
        abnormal_patterns, abnormal_service_map = self.extract_patterns_from_traces(
            abnormal_traces, abnormal_logs
        )
        ranked_results = self.rank_patterns(
            normal_patterns,
            abnormal_patterns,
            normal_service_map,
            abnormal_service_map,
        )

        normalized_root_cause = self.normalize_service_name(str(fault_info["root_cause_service"]))
        rank = 0
        for index, result in enumerate(ranked_results, start=1):
            candidate = str(result["service"])
            if (
                candidate == normalized_root_cause
                or candidate in normalized_root_cause
                or normalized_root_cause in candidate
            ):
                rank = index
                break

        running_time = time.time() - started_at
        self.logger.info("Rank for %s: %s", normalized_root_cause, rank or "not found")
        self.logger.info("Case runtime: %.2f seconds", running_time)

        return {
            "case_id": case_id,
            "fault_folder": fault_info["fault_folder"],
            "sub_case": case_path.name,
            "root_cause_service": fault_info["root_cause_service"],
            "normalized_root_cause_service": normalized_root_cause,
            "fault_type": fault_info["fault_type"],
            "rank": rank,
            "running_time_seconds": round(running_time, 4),
            "normal_trace_count": int(len(normal_traces)),
            "abnormal_trace_count": int(len(abnormal_traces)),
            "normal_log_count": int(len(normal_logs)),
            "abnormal_log_count": int(len(abnormal_logs)),
            "time_windows": {
                "inject_time_utc": time_windows["inject_time"].isoformat(),
                "normal_start_utc": time_windows["normal_start"].isoformat(),
                "normal_end_utc": time_windows["normal_end"].isoformat(),
                "abnormal_start_utc": time_windows["abnormal_start"].isoformat(),
                "abnormal_end_utc": time_windows["abnormal_end"].isoformat(),
            },
            "top_candidates": ranked_results[:10],
        }

    def run_evaluation(self, case_limit: Optional[int] = None) -> Dict[str, object]:
        started_at = time.time()
        case_results: List[Dict[str, object]] = []
        ranks: List[int] = []

        processed_cases = 0
        for fault_case in self.discover_fault_cases():
            case_root = Path(str(fault_case["full_path"]))
            for sub_folder in fault_case["sub_folders"]:
                if case_limit is not None and processed_cases >= case_limit:
                    break
                case_path = case_root / str(sub_folder)
                try:
                    case_result = self.analyze_single_case(case_path, fault_case)
                except Exception as exc:  # pragma: no cover - diagnostic path
                    self.logger.exception("Failed to analyze %s: %s", case_path, exc)
                    continue

                case_results.append(case_result)
                ranks.append(int(case_result["rank"]))
                processed_cases += 1

            if case_limit is not None and processed_cases >= case_limit:
                break

        total_running_time = time.time() - started_at
        case_times = [float(result["running_time_seconds"]) for result in case_results]
        metrics = compute_metrics(ranks)

        timing_summary = {
            "total_running_time_seconds": round(total_running_time, 4),
            "average_case_running_time_seconds": round(float(np.mean(case_times)), 4)
            if case_times
            else 0.0,
            "min_case_running_time_seconds": round(float(np.min(case_times)), 4) if case_times else 0.0,
            "max_case_running_time_seconds": round(float(np.max(case_times)), 4) if case_times else 0.0,
            "std_case_running_time_seconds": round(float(np.std(case_times)), 4) if case_times else 0.0,
            "per_case": [
                {
                    "case_id": result["case_id"],
                    "running_time_seconds": result["running_time_seconds"],
                }
                for result in case_results
            ],
        }

        self.logger.info("=" * 72)
        self.logger.info("Finished dataset %s (%s)", self.dataset_name, self.dataset.description)
        self.logger.info("Total cases: %d", len(case_results))
        for metric_name, metric_value in metrics.items():
            self.logger.info("%s = %.4f", metric_name, metric_value)
        self.logger.info(
            "Average case runtime = %.4f seconds",
            timing_summary["average_case_running_time_seconds"],
        )

        return {
            "dataset": self.dataset_name,
            "dataset_description": self.dataset.description,
            "data_path": str(self.data_path),
            "abnormal_window_minutes": self.abnormal_window_minutes,
            "min_pattern_support": self.min_pattern_support,
            "min_pattern_score": self.min_pattern_score,
            "total_cases": len(case_results),
            "metrics": metrics,
            "ranks": ranks,
            "timing": timing_summary,
            "cases": case_results,
        }


def resolve_data_path(dataset_name: str, cli_path: Optional[str]) -> Path:
    dataset = DATASET_CONFIG[dataset_name]
    path_value = cli_path or os.environ.get(dataset.env_var)
    if not path_value:
        raise ValueError(
            f"No dataset path provided. Pass --data-path or set {dataset.env_var}."
        )
    data_path = Path(path_value).expanduser()
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {data_path}")
    return data_path


def default_results_path(dataset_name: str) -> Path:
    return LOG_DIR / f"{dataset_name.lower()}_evaluation_results.json"


def print_summary(results: Dict[str, object], results_file: Path) -> None:
    print("=" * 72)
    print(f"Dataset: {results['dataset']} ({results['dataset_description']})")
    print(f"Cases: {results['total_cases']}")
    print("Metrics:")
    for metric_name, metric_value in results["metrics"].items():
        print(f"  {metric_name}: {metric_value:.4f}")
    print("Timing:")
    print(f"  Total runtime: {results['timing']['total_running_time_seconds']:.4f} s")
    print(
        "  Average case runtime: "
        f"{results['timing']['average_case_running_time_seconds']:.4f} s"
    )
    print(f"Results saved to: {results_file}")
    print("=" * 72)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Nezha benchmark on RE2-OB or RE2-TT."
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset name. Supported values: re2-ob, re2-tt.",
    )
    parser.add_argument(
        "--data-path",
        default=None,
        help="Path to the raw dataset root. This may also be provided via an environment variable.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=3,
        help="Abnormal window size in minutes before and after inject_time. Default: 3.",
    )
    parser.add_argument(
        "--case-limit",
        type=int,
        default=None,
        help="Optional limit for the number of cases to evaluate.",
    )
    parser.add_argument(
        "--results-file",
        default=None,
        help="Optional JSON output path. Default: log/<dataset>_evaluation_results.json",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional log file path. Default: log/<dataset>_benchmark_<timestamp>.log",
    )
    return parser.parse_args()


def main() -> Dict[str, object]:
    args = parse_args()
    dataset_name = canonicalize_dataset_name(args.dataset)
    data_path = resolve_data_path(dataset_name, args.data_path)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = Path(args.log_file).expanduser() if args.log_file else LOG_DIR / (
        f"{dataset_name.lower()}_benchmark_{timestamp}.log"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = build_logger(log_file)

    logger.info("Dataset: %s", dataset_name)
    logger.info("Data path: %s", data_path)
    logger.info("Window size: %d minutes", args.window)

    analyzer = RE2NezhaAnalyzer(
        dataset_name=dataset_name,
        data_path=data_path,
        abnormal_window_minutes=args.window,
        logger=logger,
    )
    results = analyzer.run_evaluation(case_limit=args.case_limit)

    results_file = Path(args.results_file).expanduser() if args.results_file else default_results_path(dataset_name)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with results_file.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)

    logger.info("Saved results to %s", results_file)
    print_summary(results, results_file)
    return results


if __name__ == "__main__":
    main()
