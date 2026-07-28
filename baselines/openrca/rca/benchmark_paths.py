from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PATH_CONFIG_FILE = PROJECT_ROOT / "rca" / "benchmark_paths.yaml"

PLACEHOLDER_PREFIXES = (
    "the/path/to/",
    "your/path/to/",
    "<set-me>",
)

DATASET_ENV_KEYS = {
    "RE2-OB": "RE2_OB_ROOT",
    "RE2-TT": "RE2_TT_ROOT",
}

SCHEMA_PLACEHOLDERS = {
    "RE2-OB": "__RE2_OB_ROOT__",
    "RE2-TT": "__RE2_TT_ROOT__",
}

INSTRUCTION_PLACEHOLDERS = {
    "Dataset3": "__DATASET3_ROOT__",
}


def _is_placeholder(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if text == "":
        return True
    return any(text.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES)


def _load_path_config() -> Dict[str, str]:
    if not PATH_CONFIG_FILE.exists():
        return {}

    with open(PATH_CONFIG_FILE, "r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    dataset_roots = raw.get("DATASET_ROOTS", {})
    if not isinstance(dataset_roots, dict):
        return {}

    config: Dict[str, str] = {}
    for key, value in dataset_roots.items():
        if not _is_placeholder(value):
            config[str(key)] = str(value).strip()
    return config


def _resolve_external_dataset_root(dataset: str) -> Path:
    path_config = _load_path_config()
    env_key = DATASET_ENV_KEYS[dataset]
    raw_value = os.environ.get(env_key) or path_config.get(dataset)

    if _is_placeholder(raw_value):
        raise ValueError(
            f"Dataset root for {dataset} is not configured. "
            f"Set environment variable {env_key} or update {PATH_CONFIG_FILE}."
        )

    root = Path(str(raw_value)).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"Configured dataset root does not exist: {root}")
    return root.resolve()


def resolve_dataset_root(dataset: str) -> Path:
    if dataset in DATASET_ENV_KEYS:
        return _resolve_external_dataset_root(dataset)
    return (PROJECT_ROOT / "dataset" / dataset).resolve()


def resolve_query_file(dataset: str) -> Path:
    return PROJECT_ROOT / "dataset" / dataset / "query.csv"


def resolve_record_file(dataset: str) -> Path:
    return PROJECT_ROOT / "dataset" / dataset / "record.csv"


def resolve_result_dir(dataset: str) -> Path:
    return PROJECT_ROOT / "test" / "result" / dataset


def resolve_monitor_dir(dataset: str) -> Path:
    return PROJECT_ROOT / "test" / "monitor" / dataset


def apply_instruction_placeholders(instruction: str, dataset: str) -> str:
    dataset_root = resolve_dataset_root(dataset).as_posix()
    placeholder = INSTRUCTION_PLACEHOLDERS.get(dataset)
    if not placeholder:
        return instruction
    return instruction.replace(placeholder, dataset_root)


def apply_schema_placeholders(schema: str, dataset: str) -> str:
    dataset_root = resolve_dataset_root(dataset).as_posix()
    placeholder = SCHEMA_PLACEHOLDERS.get(dataset)
    if not placeholder:
        return schema
    return schema.replace(placeholder, dataset_root)
