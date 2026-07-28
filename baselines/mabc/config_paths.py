"""
Central path configuration for mABC (release / paper-friendly).

Do not commit machine-specific absolute paths. Set environment variables or pass CLI flags.
See README.md section "Paths and environment variables".
"""
from __future__ import annotations

import os
from pathlib import Path

# mABC code root (directory containing this file)
MABC_ROOT = Path(__file__).resolve().parent


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name, "").strip()
    if v:
        return Path(v).expanduser().resolve()
    return default


def benchmark_data_root() -> Path:
    """
    Root directory that contains RE2/, dataset3/, etc.

    Environment variable: MABC_BENCHMARK_DATA_ROOT — set this to **your** unpacked benchmark
    data (any path on disk; no assumed parent project name).

    Default when unset: ``<MABC_ROOT>/data`` (place datasets under the mABC checkout).

    Expected layout under that root (example):

        RE2/RE2-OB/...
        RE2/RE2-TT/RE2-TT/...
        dataset3/dataset3/dataset/...
    """
    return _env_path("MABC_BENCHMARK_DATA_ROOT", MABC_ROOT / "data")


def path_re2_ob() -> Path:
    return benchmark_data_root() / "RE2" / "RE2-OB"


def path_re2_tt() -> Path:
    return benchmark_data_root() / "RE2" / "RE2-TT" / "RE2-TT"


def path_dataset3() -> Path:
    return benchmark_data_root() / "dataset3" / "dataset3" / "dataset"


def get_dataset_paths(benchmark_root: Path | None = None) -> dict[str, Path]:
    """Paths for batch_rcaeval.py (RE2-OB / RE2-TT)."""
    root = benchmark_root if benchmark_root is not None else benchmark_data_root()
    return {
        "RE2-OB": root / "RE2" / "RE2-OB",
        "RE2-TT": root / "RE2" / "RE2-TT" / "RE2-TT",
    }


def resolve_openrca_bank_root(explicit: str | None = None) -> Path:
    """
    Directory that contains query.csv (and typically record.csv, telemetry/).
    Priority: explicit CLI path > OPENRCA_BANK_ROOT env.
    """
    if explicit and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    v = os.environ.get("OPENRCA_BANK_ROOT", "").strip()
    if v:
        return Path(v).expanduser().resolve()
    raise ValueError(
        "OpenRCA Bank root path is not set. Pass --bank-root, or set the environment variable "
        "OPENRCA_BANK_ROOT to the directory containing query.csv (see README.md)."
    )


def default_output_openrca_bank() -> Path:
    return MABC_ROOT / "output" / "OpenRCA-Bank"
