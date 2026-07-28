#!/usr/bin/env python3
"""Convert raw RE2 cases into a Nezha-like per-case export for inspection."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from main_re2 import LOG_DIR, build_logger, canonicalize_dataset_name, resolve_data_path


class RE2ToNezhaConverter:
    """Convert RE2-OB or RE2-TT cases into Nezha-formatted files."""

    def __init__(
        self,
        dataset_name: str,
        data_path: Path,
        output_root: Path,
        abnormal_window_minutes: int,
        logger: logging.Logger,
    ) -> None:
        self.dataset_name = canonicalize_dataset_name(dataset_name)
        self.data_path = self._normalize_dataset_root(Path(data_path).resolve())
        self.output_root = Path(output_root).resolve()
        self.abnormal_window_minutes = abnormal_window_minutes
        self.logger = logger
        self.service_to_pod: Dict[str, str] = {}

    def _normalize_dataset_root(self, data_path: Path) -> Path:
        if self.dataset_name == "RE2-TT":
            nested = data_path / "RE2-TT"
            has_direct_fault_folders = any(
                item.is_dir() and "_" in item.name for item in data_path.iterdir()
            )
            if nested.exists() and nested.is_dir() and not has_direct_fault_folders:
                return nested.resolve()
        return data_path

    def discover_fault_cases(self) -> List[Dict[str, object]]:
        fault_cases: List[Dict[str, object]] = []
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
                required_files = [
                    sub_folder / "inject_time.txt",
                    sub_folder / "logs.csv",
                    sub_folder / "traces.csv",
                    sub_folder / "simple_metrics.csv",
                ]
                if all(required_file.exists() for required_file in required_files):
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
    def read_csv(csv_path: Path) -> pd.DataFrame:
        return pd.read_csv(csv_path)

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
            "normal_start_ts": int(normal_start.timestamp()),
            "normal_end_ts": int(normal_end.timestamp()),
            "abnormal_start_ts": int(abnormal_start.timestamp()),
            "abnormal_end_ts": int(abnormal_end.timestamp()),
        }

    def pod_name_for_service(self, service_name: str) -> str:
        if service_name not in self.service_to_pod:
            self.service_to_pod[service_name] = f"{service_name}-benchmark-00000"
        return self.service_to_pod[service_name]

    def find_closest_span_id(
        self,
        traces_df: pd.DataFrame,
        timestamp_nano: int,
        service_name: str,
    ) -> str:
        if traces_df.empty:
            return "unknown"

        if "serviceName" not in traces_df.columns:
            candidate_traces = traces_df
        else:
            service_series = traces_df["serviceName"].fillna("").astype(str)
            candidate_traces = traces_df[service_series.str.contains(service_name, case=False, regex=False)]
            if candidate_traces.empty:
                candidate_traces = traces_df

        timestamp_micro = timestamp_nano / 1000
        diffs = (candidate_traces["startTime"] - timestamp_micro).abs()
        if diffs.empty:
            return "unknown"

        closest_index = diffs.idxmin()
        return str(candidate_traces.loc[closest_index, "spanID"])

    @staticmethod
    def _sorted_trace_arrays(candidate_traces: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        if candidate_traces.empty:
            return np.array([], dtype=np.int64), np.array([], dtype=object)

        start_times = pd.to_numeric(
            candidate_traces["startTime"], errors="coerce"
        ).fillna(0).astype("int64").to_numpy()
        span_ids = candidate_traces["spanID"].fillna("unknown").astype(str).to_numpy()
        order = np.argsort(start_times, kind="mergesort")
        return start_times[order], span_ids[order]

    def _build_trace_lookup(self, traces_df: pd.DataFrame, service_names: List[str]) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
        lookup: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        fallback_arrays = self._sorted_trace_arrays(traces_df)

        if traces_df.empty or "serviceName" not in traces_df.columns:
            for service_name in service_names:
                lookup[service_name] = fallback_arrays
            return lookup

        service_series = traces_df["serviceName"].fillna("").astype(str).str.lower()

        for service_name in service_names:
            normalized_service = service_name.strip().lower()
            candidate_traces = traces_df[service_series == normalized_service]
            if candidate_traces.empty:
                candidate_traces = traces_df[
                    service_series.str.contains(normalized_service, case=False, regex=False)
                ]
            if candidate_traces.empty:
                candidate_traces = traces_df
            lookup[service_name] = self._sorted_trace_arrays(candidate_traces)

        return lookup

    @staticmethod
    def _assign_nearest_span_ids(
        timestamps_nano: np.ndarray,
        start_times_micro: np.ndarray,
        span_ids: np.ndarray,
    ) -> np.ndarray:
        if len(start_times_micro) == 0:
            return np.full(len(timestamps_nano), "unknown", dtype=object)

        timestamps_micro = timestamps_nano // 1000
        right_index = np.searchsorted(start_times_micro, timestamps_micro, side="left")
        right_index = np.clip(right_index, 0, len(start_times_micro) - 1)
        left_index = np.clip(right_index - 1, 0, len(start_times_micro) - 1)

        right_diff = np.abs(start_times_micro[right_index] - timestamps_micro)
        left_diff = np.abs(start_times_micro[left_index] - timestamps_micro)
        chosen_index = np.where(left_diff <= right_diff, left_index, right_index)
        return span_ids[chosen_index]

    def convert_logs(self, logs_df: pd.DataFrame, traces_df: pd.DataFrame) -> pd.DataFrame:
        if logs_df.empty:
            return pd.DataFrame(columns=["TimeUnixNano", "SpanID", "Log", "PodName"])

        work_logs = logs_df.reset_index(drop=True).copy()
        container_names = (
            work_logs["container_name"]
            if "container_name" in work_logs.columns
            else pd.Series("unknown", index=work_logs.index)
        )
        messages = (
            work_logs["message"]
            if "message" in work_logs.columns
            else pd.Series("", index=work_logs.index)
        )
        work_logs["container_name"] = container_names.fillna("unknown").astype(str)
        work_logs["message"] = messages.fillna("").astype(str)
        work_logs["timestamp"] = pd.to_numeric(
            work_logs["timestamp"], errors="coerce"
        ).fillna(0).astype("int64")

        unique_services = sorted(work_logs["container_name"].drop_duplicates().tolist())
        trace_lookup = self._build_trace_lookup(traces_df, unique_services)

        assigned_span_ids = np.full(len(work_logs), "unknown", dtype=object)
        for service_name, row_indexes in work_logs.groupby("container_name").groups.items():
            start_times_micro, span_ids = trace_lookup[service_name]
            positions = row_indexes.to_numpy(dtype=int)
            timestamps_nano = work_logs.loc[positions, "timestamp"].to_numpy(dtype=np.int64)
            assigned_span_ids[positions] = self._assign_nearest_span_ids(
                timestamps_nano=timestamps_nano,
                start_times_micro=start_times_micro,
                span_ids=span_ids,
            )

        iso_times = (
            pd.to_datetime(work_logs["timestamp"], unit="ns", utc=True)
            .astype(str)
            .str.replace(" ", "T", regex=False)
            .str.replace("+00:00", "Z", regex=False)
        )
        pod_names = [self.pod_name_for_service(service_name) for service_name in work_logs["container_name"]]
        log_json_values = [
            json.dumps({"log": f"{message}\n", "stream": "stdout", "time": iso_time})
            for message, iso_time in zip(work_logs["message"], iso_times)
        ]

        return pd.DataFrame(
            {
                "TimeUnixNano": work_logs["timestamp"].to_numpy(dtype=np.int64),
                "SpanID": assigned_span_ids,
                "Log": log_json_values,
                "PodName": pod_names,
            }
        )

    def convert_traces(self, traces_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        if traces_df.empty:
            empty_trace_df = pd.DataFrame(
                columns=[
                    "TraceID",
                    "SpanID",
                    "ParentID",
                    "PodName",
                    "StartTimeUnixNano",
                    "EndTimeUnixNano",
                    "OperationName",
                ]
            )
            empty_traceid_df = pd.DataFrame(columns=[0])
            return empty_trace_df, empty_traceid_df

        service_names = (
            traces_df["serviceName"]
            if "serviceName" in traces_df.columns
            else pd.Series("unknown", index=traces_df.index)
        ).fillna("unknown").astype(str)
        pod_names = [self.pod_name_for_service(service_name) for service_name in service_names]
        start_time_nano = (
            pd.to_numeric(traces_df["startTime"], errors="coerce").fillna(0).astype("int64") * 1000
        )
        duration_micro = pd.to_numeric(
            traces_df.get("duration", 0), errors="coerce"
        ).fillna(0).astype("int64")
        end_time_nano = start_time_nano + duration_micro * 1000
        parent_ids = (
            traces_df["parentSpanID"]
            if "parentSpanID" in traces_df.columns
            else pd.Series("root", index=traces_df.index)
        ).fillna("root").astype(str).replace("", "root")
        operation_names = (
            traces_df["operationName"]
            if "operationName" in traces_df.columns
            else pd.Series("unknown", index=traces_df.index)
        ).fillna("unknown").astype(str)

        trace_df = pd.DataFrame(
            {
                "TraceID": traces_df["traceID"].astype(str).to_numpy(),
                "SpanID": traces_df["spanID"].astype(str).to_numpy(),
                "ParentID": parent_ids.to_numpy(),
                "PodName": pod_names,
                "StartTimeUnixNano": start_time_nano.to_numpy(dtype=np.int64),
                "EndTimeUnixNano": end_time_nano.to_numpy(dtype=np.int64),
                "OperationName": operation_names.to_numpy(),
            }
        )
        traceid_df = pd.DataFrame(
            trace_df["TraceID"].drop_duplicates().to_numpy(),
            columns=[0],
        )
        return trace_df, traceid_df

    def convert_metrics(self, metrics_df: pd.DataFrame, time_range: Tuple[int, int]) -> Dict[str, pd.DataFrame]:
        start_ts, end_ts = time_range
        filtered = metrics_df[(metrics_df["time"] >= start_ts) & (metrics_df["time"] <= end_ts)]
        pod_metrics: Dict[str, pd.DataFrame] = {}

        service_names = sorted(
            {
                column[:-4]
                for column in filtered.columns
                if column.endswith("_cpu")
            }
        )

        for service_name in service_names:
            cpu_column = f"{service_name}_cpu"
            mem_column = f"{service_name}_mem"
            if cpu_column not in filtered.columns:
                continue

            pod_name = self.pod_name_for_service(service_name)
            time_values = (
                pd.to_datetime(filtered["time"], unit="s", utc=True)
                .dt.strftime("%Y-%m-%d %H:%M:%S")
            )
            cpu_values = pd.to_numeric(filtered[cpu_column], errors="coerce").fillna(0)
            if mem_column in filtered.columns:
                memory_values = (
                    pd.to_numeric(filtered[mem_column], errors="coerce").fillna(0) / (1024 ** 3) * 100
                )
            else:
                memory_values = np.zeros(len(filtered))

            pod_metrics[pod_name] = pd.DataFrame(
                {
                    "Time": time_values.to_numpy(),
                    "PodName": np.full(len(filtered), pod_name, dtype=object),
                    "CpuUsageRate(%)": cpu_values.to_numpy(),
                    "MemoryUsageRate(%)": np.asarray(memory_values),
                }
            )

        return pod_metrics

    def save_phase(self, phase_data: Dict[str, object], output_dir: Path) -> None:
        date_dir = output_dir / phase_data["date_str"]
        for sub_dir in ["log", "trace", "traceid", "metric"]:
            (date_dir / sub_dir).mkdir(parents=True, exist_ok=True)

        if not phase_data["logs"].empty:
            phase_data["logs"].to_csv(
                date_dir / "log" / f"{phase_data['time_key']}_log.csv",
                index=False,
            )

        if not phase_data["traces"].empty:
            phase_data["traces"].to_csv(
                date_dir / "trace" / f"{phase_data['time_key']}_trace.csv",
                index=False,
            )

        if not phase_data["traceid"].empty:
            phase_data["traceid"].to_csv(
                date_dir / "traceid" / f"{phase_data['time_key']}_traceid.csv",
                index=False,
                header=False,
            )

        for pod_name, metric_df in phase_data["metrics"].items():
            metric_df.to_csv(date_dir / "metric" / f"{pod_name}_metric.csv", index=False)

    def process_single_case(self, case_path: Path, fault_info: Dict[str, object]) -> Dict[str, object]:
        case_id = f"{fault_info['fault_folder']}/{case_path.name}"
        self.logger.info("Converting case %s", case_id)

        self.service_to_pod = {}

        logs_df = self.read_csv(case_path / "logs.csv")
        traces_df = self.read_csv(case_path / "traces.csv")
        metrics_df = self.read_csv(case_path / "simple_metrics.csv")

        logs_df["timestamp"] = pd.to_numeric(logs_df["timestamp"], errors="coerce").fillna(0).astype("int64")
        traces_df["startTime"] = pd.to_numeric(traces_df["startTime"], errors="coerce").fillna(0).astype("int64")
        traces_df["duration"] = pd.to_numeric(traces_df["duration"], errors="coerce").fillna(0).astype("int64")
        metrics_df["time"] = pd.to_numeric(metrics_df["time"], errors="coerce").fillna(0).astype("int64")

        inject_timestamp = self.read_inject_time(case_path)
        time_windows = self.get_time_windows(inject_timestamp, int(metrics_df["time"].min()))

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

        case_output = self.output_root / fault_info["fault_folder"] / case_path.name
        construct_output = case_output / "construct_data"
        rca_output = case_output / "rca_data"
        construct_output.mkdir(parents=True, exist_ok=True)
        rca_output.mkdir(parents=True, exist_ok=True)

        if not normal_logs.empty or not normal_traces.empty:
            normal_logs_converted = self.convert_logs(normal_logs, normal_traces)
            normal_traces_converted, normal_traceids = self.convert_traces(normal_traces)
            normal_metrics = self.convert_metrics(
                metrics_df,
                (time_windows["normal_start_ts"], time_windows["normal_end_ts"]),
            )
            self.save_phase(
                {
                    "logs": normal_logs_converted,
                    "traces": normal_traces_converted,
                    "traceid": normal_traceids,
                    "metrics": normal_metrics,
                    "date_str": time_windows["normal_start"].strftime("%Y-%m-%d"),
                    "time_key": time_windows["normal_start"].strftime("%H_%M"),
                },
                construct_output,
            )

        if not abnormal_logs.empty or not abnormal_traces.empty:
            abnormal_logs_converted = self.convert_logs(abnormal_logs, abnormal_traces)
            abnormal_traces_converted, abnormal_traceids = self.convert_traces(abnormal_traces)
            abnormal_metrics = self.convert_metrics(
                metrics_df,
                (time_windows["abnormal_start_ts"], time_windows["abnormal_end_ts"]),
            )
            self.save_phase(
                {
                    "logs": abnormal_logs_converted,
                    "traces": abnormal_traces_converted,
                    "traceid": abnormal_traceids,
                    "metrics": abnormal_metrics,
                    "date_str": time_windows["abnormal_start"].strftime("%Y-%m-%d"),
                    "time_key": time_windows["abnormal_start"].strftime("%H_%M"),
                },
                rca_output,
            )

        metadata = {
            "dataset": self.dataset_name,
            "case_id": case_id,
            "input_case_path": str(case_path),
            "output_case_path": str(case_output),
            "root_cause_service": fault_info["root_cause_service"],
            "fault_type": fault_info["fault_type"],
            "abnormal_window_minutes": self.abnormal_window_minutes,
            "time_windows": {
                "inject_time_utc": time_windows["inject_time"].isoformat(),
                "normal_start_utc": time_windows["normal_start"].isoformat(),
                "normal_end_utc": time_windows["normal_end"].isoformat(),
                "abnormal_start_utc": time_windows["abnormal_start"].isoformat(),
                "abnormal_end_utc": time_windows["abnormal_end"].isoformat(),
            },
            "pod_mapping": self.service_to_pod,
            "counts": {
                "normal_logs": int(len(normal_logs)),
                "abnormal_logs": int(len(abnormal_logs)),
                "normal_traces": int(len(normal_traces)),
                "abnormal_traces": int(len(abnormal_traces)),
            },
        }
        with (case_output / "case_metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)

        return metadata

    def convert_all(self, case_limit: Optional[int] = None) -> Dict[str, object]:
        converted_cases: List[Dict[str, object]] = []
        processed = 0

        for fault_case in self.discover_fault_cases():
            case_root = Path(str(fault_case["full_path"]))
            for sub_folder in fault_case["sub_folders"]:
                if case_limit is not None and processed >= case_limit:
                    break

                case_path = case_root / str(sub_folder)
                try:
                    metadata = self.process_single_case(case_path, fault_case)
                except Exception as exc:  # pragma: no cover - diagnostic path
                    self.logger.exception("Failed to convert %s: %s", case_path, exc)
                    continue

                converted_cases.append(metadata)
                processed += 1

            if case_limit is not None and processed >= case_limit:
                break

        summary = {
            "dataset": self.dataset_name,
            "data_path": str(self.data_path),
            "output_root": str(self.output_root),
            "abnormal_window_minutes": self.abnormal_window_minutes,
            "total_cases": len(converted_cases),
            "cases": converted_cases,
        }
        with (self.output_root / "index.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)

        self.logger.info("Converted %d cases into %s", len(converted_cases), self.output_root)
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert raw RE2 cases into Nezha-formatted folders."
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
        "--output-root",
        required=True,
        help="Directory where converted Nezha-formatted cases will be written.",
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
        help="Optional limit for the number of cases to convert.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional log file path. Default: log/convert_<dataset>_<timestamp>.log",
    )
    return parser.parse_args()


def main() -> Dict[str, object]:
    args = parse_args()
    dataset_name = canonicalize_dataset_name(args.dataset)
    data_path = resolve_data_path(dataset_name, args.data_path)
    output_root = Path(args.output_root).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = Path(args.log_file).expanduser() if args.log_file else LOG_DIR / (
        f"convert_{dataset_name.lower()}_{timestamp}.log"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = build_logger(log_file)

    logger.info("Dataset: %s", dataset_name)
    logger.info("Data path: %s", data_path)
    logger.info("Output root: %s", output_root)

    converter = RE2ToNezhaConverter(
        dataset_name=dataset_name,
        data_path=data_path,
        output_root=output_root,
        abnormal_window_minutes=args.window,
        logger=logger,
    )
    summary = converter.convert_all(case_limit=args.case_limit)

    print("=" * 72)
    print(f"Dataset: {summary['dataset']}")
    print(f"Converted cases: {summary['total_cases']}")
    print(f"Output root: {summary['output_root']}")
    print(f"Index file: {output_root / 'index.json'}")
    print("=" * 72)
    return summary


if __name__ == "__main__":
    main()
