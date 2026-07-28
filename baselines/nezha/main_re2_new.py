#!/usr/bin/env python3
"""Run the RE2 benchmark on the prepared Nezha-style case layout."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from drain3 import TemplateMiner
from drain3.file_persistence import FilePersistence
from drain3.template_miner_config import TemplateMinerConfig

from alarm import determine_alarm, get_netwrok_metric
from data_integrate import build_alarm_lookup, generate_event_graph, get_events_within_trace
from log_parsing import from_id_to_template
from main_re2 import LOG_DIR, build_logger, canonicalize_dataset_name, compute_metrics
from pattern_miner import get_pattern_support
from pattern_ranker import abnormal_pattern_ranker, get_event_depth_pod


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PATTERN_SUPPORT = 5
DEFAULT_PATTERN_SCORE = 0.67
NOISY_MODULE_LOGGERS = [
    "alarm",
    "data_integrate",
    "pattern_miner",
    "pattern_ranker",
]


class TemplateMiningCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.events: List[Dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        marker = " Log Parings Result: "
        if marker not in message:
            return

        service_name, payload = message.split(marker, 1)
        event: Dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "service": service_name.strip(),
            "message": message,
        }

        try:
            event["result"] = json.loads(payload)
        except json.JSONDecodeError:
            event["raw_result"] = payload

        self.events.append(event)


@dataclass(frozen=True)
class PreparedDatasetConfig:
    canonical_name: str
    description: str
    namespace: str
    env_var: str


PREPARED_DATASET_CONFIG: Dict[str, PreparedDatasetConfig] = {
    "RE2-OB": PreparedDatasetConfig(
        canonical_name="RE2-OB",
        description="OnlineBoutique prepared case layout",
        namespace="hipster",
        env_var="NEZHA_RE2_OB_PATH",
    ),
    "RE2-TT": PreparedDatasetConfig(
        canonical_name="RE2-TT",
        description="TrainTicket prepared case layout",
        namespace="ts",
        env_var="NEZHA_RE2_TT_PATH",
    ),
}


@dataclass
class PreparedCase:
    case_id: str
    case_dir: Path
    construct_data_path: Path
    rca_data_path: Path
    root_cause_service: str
    fault_type: str
    normal_time_hint: Optional[str]
    abnormal_time_hint: Optional[str]
    metadata: Dict[str, object]


def load_template_miner(namespace: str, logger: logging.Logger) -> TemplateMiner:
    config_path = SCRIPT_DIR / "log_template" / f"drain3_{namespace}.ini"
    source_bin = SCRIPT_DIR / "log_template" / f"{namespace}.bin"

    if not config_path.exists():
        raise FileNotFoundError(f"Missing drain3 config file: {config_path}")
    if not source_bin.exists():
        raise FileNotFoundError(f"Missing drain3 state file: {source_bin}")

    runtime_dir = LOG_DIR / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    runtime_bin = runtime_dir / f"{namespace}_{int(time.time())}_{source_bin.name}"
    shutil.copy2(source_bin, runtime_bin)

    config = TemplateMinerConfig()
    config.load(str(config_path))
    config.profiling_enabled = False

    logger.info("Loaded drain3 config from %s", config_path)
    logger.info("Using runtime drain3 state copy at %s", runtime_bin)

    persistence = FilePersistence(str(runtime_bin))
    return TemplateMiner(persistence, config=config)


def quiet_module_loggers() -> None:
    for logger_name in NOISY_MODULE_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    log_parsing_logger = logging.getLogger("log_parsing")
    log_parsing_logger.setLevel(logging.INFO)
    log_parsing_logger.propagate = False
    for handler in list(log_parsing_logger.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            log_parsing_logger.removeHandler(handler)


def resolve_prepared_root(dataset_name: str, cli_path: Optional[str]) -> Path:
    config = PREPARED_DATASET_CONFIG[dataset_name]
    path_value = cli_path or os.environ.get(config.env_var)
    if not path_value:
        raise ValueError(
            f"No prepared dataset path provided. Pass --data-path or set {config.env_var}."
        )

    root = Path(path_value).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Prepared dataset path does not exist: {root}")
    return root


def normalize_service_name(name: str) -> str:
    return (name or "").strip().lower()


def pod_to_service_name(pod_name: str) -> str:
    if not pod_name:
        return "unknown"
    service = pod_name.rsplit("-", 1)[0]
    service = service.rsplit("-", 1)[0]
    return service or pod_name


def is_prepared_case_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_data_dirs = (path / "construct_data").exists() and (path / "rca_data").exists()
    has_metadata = (path / "case_metadata.json").exists() or (path / "conversion_result.json").exists()
    return has_data_dirs and has_metadata


def load_case_metadata(case_dir: Path) -> Dict[str, object]:
    metadata_path = case_dir / "case_metadata.json"
    if not metadata_path.exists():
        metadata_path = case_dir / "conversion_result.json"
    with metadata_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_metadata_time(value: object) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass

    for time_format in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, time_format)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue

    return None


def discover_prepared_cases(prepared_root: Path) -> List[PreparedCase]:
    cases: List[PreparedCase] = []

    for item in sorted(prepared_root.iterdir()):
        if not item.is_dir():
            continue

        if is_prepared_case_dir(item):
            cases.append(build_prepared_case(item, prepared_root))
            continue

        for sub_item in sorted(item.iterdir()):
            if is_prepared_case_dir(sub_item):
                cases.append(build_prepared_case(sub_item, prepared_root))

    return cases


def build_prepared_case(case_dir: Path, prepared_root: Path) -> PreparedCase:
    metadata = load_case_metadata(case_dir)
    time_windows = metadata.get("time_windows", {})

    construct_data_path = case_dir / "construct_data"
    if not construct_data_path.exists() and "construct_data_path" in metadata:
        construct_data_path = Path(str(metadata["construct_data_path"]))

    rca_data_path = case_dir / "rca_data"
    if not rca_data_path.exists() and "rca_data_path" in metadata:
        rca_data_path = Path(str(metadata["rca_data_path"]))

    case_id = str(metadata.get("case_id") or metadata.get("case_name") or case_dir.relative_to(prepared_root))
    root_cause_service = str(
        metadata.get("root_cause_service") or metadata.get("ground_truth_service") or "unknown"
    )
    fault_type = str(metadata.get("fault_type") or metadata.get("inject_type") or "unknown")
    normal_time_hint = normalize_metadata_time(
        metadata.get("normal_time") or time_windows.get("normal_start_utc")
    )
    abnormal_time_hint = normalize_metadata_time(
        metadata.get("abnormal_time") or time_windows.get("abnormal_start_utc")
    )

    return PreparedCase(
        case_id=case_id,
        case_dir=case_dir,
        construct_data_path=construct_data_path,
        rca_data_path=rca_data_path,
        root_cause_service=root_cause_service,
        fault_type=fault_type,
        normal_time_hint=normal_time_hint,
        abnormal_time_hint=abnormal_time_hint,
        metadata=metadata,
    )


def collect_phase_file_sets(phase_root: Path) -> List[Dict[str, object]]:
    phase_entries: Dict[Tuple[str, str], Dict[str, object]] = {}

    date_dirs = sorted([item for item in phase_root.iterdir() if item.is_dir()])
    if not date_dirs:
        raise FileNotFoundError(f"No date directory found under {phase_root}")

    for date_dir in date_dirs:
        for kind, suffix in (("trace_file", "_trace.csv"), ("traceid_file", "_traceid.csv"), ("log_file", "_log.csv")):
            sub_dir_name = kind.replace("_file", "")
            sub_dir = date_dir / sub_dir_name
            if not sub_dir.exists():
                continue

            for file_path in sorted(sub_dir.glob(f"*{suffix}")):
                time_key = file_path.stem[: -len(suffix.replace(".csv", ""))]
                entry_key = (date_dir.name, time_key)
                entry = phase_entries.setdefault(
                    entry_key,
                    {
                        "date_dir": date_dir,
                        "metric_dir": date_dir / "metric",
                        "time_key": time_key,
                        "phase_time_str": f"{date_dir.name} {time_key.replace('_', ':')}",
                    },
                )
                entry[kind] = file_path

    complete_entries: List[Dict[str, object]] = []
    for entry in phase_entries.values():
        if "trace_file" in entry:
            entry["phase_datetime"] = datetime.strptime(
                entry["phase_time_str"], "%Y-%m-%d %H:%M"
            )
            complete_entries.append(entry)

    if not complete_entries:
        raise FileNotFoundError(f"Incomplete prepared phase under {phase_root}")

    return sorted(complete_entries, key=lambda item: item["phase_datetime"])


def locate_phase_files(phase_root: Path, preferred_time: Optional[str] = None) -> Dict[str, object]:
    phase_entries = collect_phase_file_sets(phase_root)
    if preferred_time:
        for entry in phase_entries:
            if entry["phase_time_str"] == preferred_time:
                return entry

        preferred_dt = normalize_metadata_time(preferred_time)
        if preferred_dt:
            target_dt = datetime.strptime(preferred_dt, "%Y-%m-%d %H:%M")
            return min(
                phase_entries,
                key=lambda item: abs((item["phase_datetime"] - target_dt).total_seconds()),
            )

    return phase_entries[-1]


def build_alarm_list_for_phase(metric_dir: Path, trace_file: Path, namespace: str) -> List[Dict[str, object]]:
    trace_df = pd.read_csv(
        trace_file,
        usecols=["SpanID", "ParentID", "PodName", "EndTimeUnixNano"],
        engine="c",
    )
    span_parent_lookup: Dict[str, Tuple[str, float]] = {}
    for span_id, _, pod_name, end_time in trace_df.itertuples(index=False, name=None):
        if span_id not in span_parent_lookup:
            span_parent_lookup[span_id] = (str(pod_name), float(end_time))

    network_latency_by_pod: Dict[str, List[float]] = {}
    for _, parent_id, pod_name, end_time in trace_df.itertuples(index=False, name=None):
        pod_name = str(pod_name)
        if "front" in pod_name.lower():
            continue
        parent_info = span_parent_lookup.get(parent_id)
        if not parent_info:
            continue
        parent_pod_name, parent_end_time = parent_info
        if parent_pod_name == pod_name:
            continue
        latency_ms = (float(parent_end_time) - float(end_time)) / 1000000.0
        network_latency_by_pod.setdefault(pod_name, []).append(latency_ms)

    network_metric_lookup: Dict[str, float] = {}
    for pod_name, latency_list in network_latency_by_pod.items():
        if len(latency_list) > 2:
            network_metric_lookup[pod_name] = float(np.percentile(latency_list, 90))
        else:
            network_metric_lookup[pod_name] = 10.0

    alarm_list: List[Dict[str, object]] = []

    for metric_file in sorted(metric_dir.glob("*_metric.csv")):
        metric_df = pd.read_csv(metric_file)
        if metric_df.empty or "PodName" not in metric_df.columns:
            continue

        pod_name = str(metric_df["PodName"].iloc[0])
        alarm_items: List[Dict[str, object]] = []

        for metric_type in ["CpuUsageRate(%)", "MemoryUsageRate(%)"]:
            if metric_type not in metric_df.columns:
                continue
            metric_values = pd.to_numeric(metric_df[metric_type], errors="coerce").fillna(0)
            metric_value = float(metric_values.max())
            if determine_alarm(pod_name, metric_type, metric_value, std_num=6, ns=namespace):
                alarm_items.append({"metric_type": metric_type, "alarm_flag": True})

        network_p90 = network_metric_lookup.get(pod_name, 10.0)
        if determine_alarm(pod_name, "NetworkP90(ms)", float(network_p90), std_num=6, ns=namespace):
            alarm_items.append({"metric_type": "NetworkP90(ms)", "alarm_flag": True})

        if alarm_items:
            alarm_list.append({"pod": pod_name, "alarm": alarm_items})

    return alarm_list


def integrate_phase_serial(
    trace_file: Path,
    trace_id_file: Optional[Path],
    log_file: Optional[Path],
    alarm_list: List[Dict[str, object]],
    namespace: str,
    log_template_miner: TemplateMiner,
) -> List[object]:
    trace_reader = pd.read_csv(
        trace_file,
        usecols=[
            "TraceID",
            "SpanID",
            "ParentID",
            "PodName",
            "StartTimeUnixNano",
            "EndTimeUnixNano",
            "OperationName",
        ],
        engine="c",
    )
    trace_lookup: Dict[str, List[Tuple[object, object, object, object, object, object]]] = {}
    for trace_row in trace_reader.itertuples(index=False, name=None):
        trace_id, span_id, parent_id, pod_name, start_time, end_time, operation_name = trace_row
        if trace_id not in trace_lookup:
            trace_lookup[trace_id] = []
        trace_lookup[trace_id].append(
            (span_id, parent_id, pod_name, start_time, end_time, operation_name)
        )

    if trace_id_file and trace_id_file.exists():
        trace_id_reader = pd.read_csv(trace_id_file, index_col=False, header=None, engine="c")
        trace_ids = trace_id_reader[0].tolist()
    else:
        trace_ids = list(trace_lookup.keys())

    if log_file and log_file.exists():
        log_reader = pd.read_csv(
            log_file,
            usecols=["TimeUnixNano", "SpanID", "Log"],
            engine="c",
        )
        log_lookup: Dict[str, List[Tuple[object, object]]] = {}
        for log_row in log_reader.itertuples(index=False, name=None):
            timestamp, span_id, log_message = log_row
            if span_id not in log_lookup:
                log_lookup[span_id] = []
            log_lookup[span_id].append((timestamp, log_message))
    else:
        log_lookup = {}

    alarm_lookup = build_alarm_lookup(alarm_list)
    event_graphs: List[object] = []
    for trace_id in trace_ids:
        trace = get_events_within_trace(
            trace_lookup,
            log_lookup,
            trace_id,
            alarm_lookup,
            namespace,
            log_template_miner,
        )
        if trace is None:
            continue
        event_graph = generate_event_graph(trace, log_template_miner)
        event_graph.get_support()
        event_graphs.append(event_graph)

    return event_graphs


def build_pattern_support_for_phase(
    phase_root: Path,
    namespace: str,
    log_template_miner: TemplateMiner,
    preferred_time: Optional[str] = None,
) -> Tuple[Dict[str, int], List[object], List[Dict[str, object]], Dict[str, Path]]:
    phase_files = locate_phase_files(phase_root, preferred_time=preferred_time)
    alarm_list = build_alarm_list_for_phase(
        metric_dir=phase_files["metric_dir"],
        trace_file=phase_files["trace_file"],
        namespace=namespace,
    )
    event_graphs = integrate_phase_serial(
        trace_file=phase_files["trace_file"],
        trace_id_file=phase_files.get("traceid_file"),
        log_file=phase_files.get("log_file"),
        alarm_list=alarm_list,
        namespace=namespace,
        log_template_miner=log_template_miner,
    )
    pattern_support = get_pattern_support(event_graphs)
    return pattern_support, event_graphs, alarm_list, phase_files


def rank_case_patterns(
    normal_pattern_dict: Dict[str, int],
    normal_event_graphs: List[object],
    abnormal_pattern_dict: Dict[str, int],
    abnormal_alarm_list: List[Dict[str, object]],
    log_template_miner: TemplateMiner,
    min_pattern_support: int = DEFAULT_PATTERN_SUPPORT,
    min_score: float = DEFAULT_PATTERN_SCORE,
) -> Tuple[List[Dict[str, object]], List[str]]:
    template_cache: Dict[int, str] = {}

    def get_template_text(event_id: int) -> str:
        if event_id not in template_cache:
            template_cache[event_id] = from_id_to_template(event_id, log_template_miner)
        return template_cache[event_id]

    abnormal_pattern_score = abnormal_pattern_ranker(
        normal_pattern_dict,
        abnormal_pattern_dict,
        min_score=min_score,
    )

    score_dict: Dict[str, float] = {}
    for key in normal_pattern_dict.keys():
        if normal_pattern_dict[key] > min_pattern_support:
            if key not in score_dict:
                score_dict[key] = 0.0
            if key in abnormal_pattern_dict:
                score_dict[key] = 1.0 * normal_pattern_dict[key] / (
                    abnormal_pattern_dict[key] + normal_pattern_dict[key]
                )
            else:
                score_dict[key] = 1.0

    move_list = set()
    for key, value in score_dict.items():
        if float(value) < min_score:
            move_list.add(key)
    for item in move_list:
        score_dict.pop(item)

    score_keys = list(score_dict.keys())
    max_score_by_target: Dict[int, float] = {}
    for key, value in score_dict.items():
        target_event = int(key.split("_")[1])
        if target_event not in max_score_by_target or value > max_score_by_target[target_event]:
            max_score_by_target[target_event] = value

    move_list = set()
    for key in score_keys:
        source_event = int(key.split("_")[0])
        target_event = int(key.split("_")[1])
        target_template = get_template_text(target_event)
        if "Cpu" not in target_template and "Network" not in target_template and "Memory" not in target_template:
            if score_dict[key] <= max_score_by_target.get(source_event, -1.0):
                move_list.add(key)
    for item in move_list:
        score_dict.pop(item, None)

    result_list: List[Dict[str, object]] = []
    deepth_dict: Dict[str, int] = {}
    abnormal_alarm_by_pod = {str(item["pod"]): item for item in abnormal_alarm_list}
    depth_lookup: Dict[int, Tuple[int, str]] = {}
    source_events = {int(key.split("_")[0]) for key in score_dict.keys()}
    for source_event in source_events:
        max_depth = 0
        event_pod = ""
        for graph in normal_event_graphs:
            depth_info = graph.event_depth_lookup.get(source_event)
            if depth_info is None:
                continue
            deepth, pod = depth_info
            if deepth > max_depth:
                max_depth = deepth
                event_pod = pod
        depth_lookup[source_event] = (max_depth, event_pod)

    for key, value in score_dict.items():
        source_event = int(key.split("_")[0])
        deepth, pod = depth_lookup.get(source_event, (0, ""))
        if pod not in deepth_dict:
            deepth_dict[pod] = deepth
        elif deepth_dict[pod] < deepth:
            deepth_dict[pod] = deepth

        if pod == "":
            pod = "unknown-pod"
            deepth = 1

        abnormal_alarm = abnormal_alarm_by_pod.get(pod)
        if abnormal_alarm:
            result_list.append(
                {
                    "events": key,
                    "score": value,
                    "deepth": deepth,
                    "pod": pod,
                    "resource": abnormal_alarm["alarm"][0]["metric_type"],
                }
            )
        else:
            result_list.append(
                {
                    "events": key,
                    "score": value,
                    "deepth": deepth,
                    "pod": pod,
                }
            )

    move_indexes = set()
    for abnormal_alarm in abnormal_alarm_list:
        pod_name = abnormal_alarm["pod"]
        if pod_name not in deepth_dict:
            continue
        max_depth = deepth_dict[pod_name]
        keep_seen = False
        for index, item in enumerate(result_list):
            if "resource" not in item:
                continue
            if item["pod"] == pod_name and item["resource"] == abnormal_alarm["alarm"][0]["metric_type"]:
                if max_depth > item["deepth"]:
                    move_indexes.add(index)
                elif max_depth == item["deepth"] and keep_seen:
                    move_indexes.add(index)
                else:
                    keep_seen = True

    for index in sorted(move_indexes, reverse=True):
        result_list.pop(index)

    result_list = sorted(
        result_list,
        key=lambda item: (item["score"], item["deepth"]),
        reverse=True,
    )

    return result_list, abnormal_pattern_score


def collapse_pattern_results_to_services(pattern_results: List[Dict[str, object]]) -> List[Dict[str, object]]:
    service_best: Dict[str, Dict[str, object]] = {}
    current_rank = 0
    previous_key: Optional[Tuple[float, int]] = None

    for item in pattern_results:
        ranking_key = (float(item["score"]), int(item["deepth"]))
        if previous_key is None:
            current_rank = 1
        elif ranking_key != previous_key:
            current_rank += 1

        service = pod_to_service_name(str(item["pod"]))
        candidate = {
            "service": service,
            "rank": current_rank,
            "score": round(float(item["score"]), 6),
            "deepth": int(item["deepth"]),
            "pod": item["pod"],
            "resource": item.get("resource"),
            "events": item["events"],
        }

        if service not in service_best or current_rank < int(service_best[service]["rank"]):
            service_best[service] = candidate

        previous_key = ranking_key

    return sorted(
        service_best.values(),
        key=lambda item: (int(item["rank"]), -float(item["score"]), item["service"]),
    )


def describe_pattern_item(item: Dict[str, object], log_template_miner: TemplateMiner) -> Dict[str, object]:
    source_id, target_id = str(item["events"]).split("_")
    return {
        "service": item["service"],
        "rank": item["rank"],
        "score": item["score"],
        "deepth": item["deepth"],
        "pod": item["pod"],
        "resource": item.get("resource"),
        "source_template": from_id_to_template(int(source_id), log_template_miner),
        "target_template": from_id_to_template(int(target_id), log_template_miner),
    }


def summarize_template_events(events: List[Dict[str, object]]) -> Dict[str, object]:
    change_type_counts: Dict[str, int] = {}
    service_counts: Dict[str, int] = {}

    for event in events:
        result = event.get("result", {})
        if isinstance(result, dict):
            change_type = str(result.get("change_type", "unknown"))
        else:
            change_type = "unknown"
        service = str(event.get("service", "unknown"))
        change_type_counts[change_type] = change_type_counts.get(change_type, 0) + 1
        service_counts[service] = service_counts.get(service, 0) + 1

    return {
        "event_count": len(events),
        "change_type_counts": change_type_counts,
        "service_counts": service_counts,
    }


class PreparedRE2Benchmark:
    def __init__(
        self,
        dataset_name: str,
        prepared_root: Path,
        logger: logging.Logger,
        min_pattern_support: int = DEFAULT_PATTERN_SUPPORT,
        min_pattern_score: float = DEFAULT_PATTERN_SCORE,
    ) -> None:
        self.dataset_name = canonicalize_dataset_name(dataset_name)
        self.config = PREPARED_DATASET_CONFIG[self.dataset_name]
        self.prepared_root = prepared_root
        self.logger = logger
        self.min_pattern_support = min_pattern_support
        self.min_pattern_score = min_pattern_score
        self.log_template_miner = load_template_miner(self.config.namespace, logger)
        self.log_parsing_logger = logging.getLogger("log_parsing")

    def analyze_case(self, prepared_case: PreparedCase) -> Dict[str, object]:
        started_at = time.time()
        self.logger.info("=" * 72)
        self.logger.info("Analyzing prepared case %s", prepared_case.case_id)
        template_capture = TemplateMiningCaptureHandler()
        self.log_parsing_logger.addHandler(template_capture)
        try:
            normal_patterns, normal_graphs, normal_alarms, normal_phase = build_pattern_support_for_phase(
                prepared_case.construct_data_path,
                self.config.namespace,
                self.log_template_miner,
                preferred_time=prepared_case.normal_time_hint,
            )
            abnormal_patterns, abnormal_graphs, abnormal_alarms, abnormal_phase = build_pattern_support_for_phase(
                prepared_case.rca_data_path,
                self.config.namespace,
                self.log_template_miner,
                preferred_time=prepared_case.abnormal_time_hint,
            )

            pattern_results, abnormal_pattern_score = rank_case_patterns(
                normal_pattern_dict=normal_patterns,
                normal_event_graphs=normal_graphs,
                abnormal_pattern_dict=abnormal_patterns,
                abnormal_alarm_list=abnormal_alarms,
                log_template_miner=self.log_template_miner,
                min_pattern_support=self.min_pattern_support,
                min_score=self.min_pattern_score,
            )
            service_results = collapse_pattern_results_to_services(pattern_results)

            normalized_root = normalize_service_name(prepared_case.root_cause_service)
            rank = 0
            for item in service_results:
                normalized_service = normalize_service_name(str(item["service"]))
                if (
                    normalized_service == normalized_root
                    or normalized_root in normalized_service
                    or normalized_service in normalized_root
                ):
                    rank = int(item["rank"])
                    break
        finally:
            self.log_parsing_logger.removeHandler(template_capture)

        running_time = time.time() - started_at
        self.logger.info("Root-cause service: %s", prepared_case.root_cause_service)
        self.logger.info("Fault type: %s", prepared_case.fault_type)
        self.logger.info("Rank: %s", rank or "not found")
        self.logger.info("Case runtime: %.4f seconds", running_time)

        return {
            "case_id": prepared_case.case_id,
            "root_cause_service": prepared_case.root_cause_service,
            "fault_type": prepared_case.fault_type,
            "rank": rank,
            "running_time_seconds": round(running_time, 4),
            "normal_phase_time": normal_phase["phase_time_str"],
            "abnormal_phase_time": abnormal_phase["phase_time_str"],
            "normal_alarm_count": len(normal_alarms),
            "abnormal_alarm_count": len(abnormal_alarms),
            "normal_pattern_count": len(normal_patterns),
            "abnormal_pattern_count": len(abnormal_patterns),
            "top_services": [
                describe_pattern_item(item, self.log_template_miner) for item in service_results[:10]
            ],
            "top_patterns": pattern_results[:10],
            "abnormal_pattern_score": abnormal_pattern_score[:10],
            "template_mining_summary": summarize_template_events(template_capture.events),
            "template_mining_events": template_capture.events,
        }

    def run(self, case_limit: Optional[int] = None) -> Dict[str, object]:
        started_at = time.time()
        prepared_cases = discover_prepared_cases(self.prepared_root)
        self.logger.info("Discovered %d prepared cases under %s", len(prepared_cases), self.prepared_root)

        case_results: List[Dict[str, object]] = []
        ranks: List[int] = []

        for index, prepared_case in enumerate(prepared_cases):
            if case_limit is not None and index >= case_limit:
                break

            try:
                case_result = self.analyze_case(prepared_case)
            except Exception as exc:  # pragma: no cover - diagnostic path
                self.logger.exception("Failed to analyze %s: %s", prepared_case.case_id, exc)
                continue

            case_results.append(case_result)
            ranks.append(int(case_result["rank"]))

        total_running_time = time.time() - started_at
        case_times = [float(item["running_time_seconds"]) for item in case_results]
        metrics = compute_metrics(ranks)

        timing = {
            "total_running_time_seconds": round(total_running_time, 4),
            "average_case_running_time_seconds": round(float(np.mean(case_times)), 4) if case_times else 0.0,
            "min_case_running_time_seconds": round(float(np.min(case_times)), 4) if case_times else 0.0,
            "max_case_running_time_seconds": round(float(np.max(case_times)), 4) if case_times else 0.0,
            "std_case_running_time_seconds": round(float(np.std(case_times)), 4) if case_times else 0.0,
            "per_case": [
                {"case_id": item["case_id"], "running_time_seconds": item["running_time_seconds"]}
                for item in case_results
            ],
        }

        return {
            "dataset": self.dataset_name,
            "dataset_description": self.config.description,
            "namespace": self.config.namespace,
            "prepared_root": str(self.prepared_root),
            "total_cases": len(case_results),
            "metrics": metrics,
            "ranks": ranks,
            "timing": timing,
            "cases": case_results,
        }


def default_results_path(dataset_name: str) -> Path:
    return LOG_DIR / f"{dataset_name.lower()}_prepared_evaluation_results.json"


def write_case_artifact_files(results: Dict[str, object], results_file: Path) -> None:
    artifact_root = results_file.parent / f"{results_file.stem}_cases"
    artifact_root.mkdir(parents=True, exist_ok=True)

    for case_result in results.get("cases", []):
        template_events = case_result.pop("template_mining_events", [])
        safe_case_id = str(case_result["case_id"]).replace("/", "__").replace("\\", "__")
        artifact_path = artifact_root / f"{safe_case_id}.json"
        artifact_payload = {
            "case_id": case_result["case_id"],
            "root_cause_service": case_result["root_cause_service"],
            "fault_type": case_result["fault_type"],
            "normal_phase_time": case_result["normal_phase_time"],
            "abnormal_phase_time": case_result["abnormal_phase_time"],
            "template_mining_summary": case_result.get("template_mining_summary", {}),
            "template_mining_events": template_events,
        }
        with artifact_path.open("w", encoding="utf-8") as handle:
            json.dump(artifact_payload, handle, indent=2, ensure_ascii=False)

        case_result["template_mining_file"] = str(artifact_path)


def print_summary(results: Dict[str, object], results_file: Path) -> None:
    print("=" * 72)
    print(f"Dataset: {results['dataset']} ({results['dataset_description']})")
    print(f"Prepared root: {results['prepared_root']}")
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
        description="Run the RE2 benchmark on the prepared Nezha-style case layout."
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset name. Supported values: re2-ob, re2-tt.",
    )
    parser.add_argument(
        "--data-path",
        default=None,
        help="Path to the prepared dataset root. This may also be provided via an environment variable.",
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
        help="Optional JSON output path. Default: log/<dataset>_prepared_evaluation_results.json",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional log file path. Default: log/<dataset>_prepared_benchmark_<timestamp>.log",
    )
    return parser.parse_args()


def main() -> Dict[str, object]:
    args = parse_args()
    dataset_name = canonicalize_dataset_name(args.dataset)
    prepared_root = resolve_prepared_root(dataset_name, args.data_path)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = Path(args.log_file).expanduser() if args.log_file else LOG_DIR / (
        f"{dataset_name.lower()}_prepared_benchmark_{timestamp}.log"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = build_logger(log_file)
    quiet_module_loggers()

    logger.info("Dataset: %s", dataset_name)
    logger.info("Prepared root: %s", prepared_root)

    benchmark = PreparedRE2Benchmark(
        dataset_name=dataset_name,
        prepared_root=prepared_root,
        logger=logger,
    )
    results = benchmark.run(case_limit=args.case_limit)

    results_file = Path(args.results_file).expanduser() if args.results_file else default_results_path(dataset_name)
    results_file.parent.mkdir(parents=True, exist_ok=True)
    write_case_artifact_files(results, results_file)
    with results_file.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)

    logger.info("Saved results to %s", results_file)
    print_summary(results, results_file)
    return results


if __name__ == "__main__":
    main()
