#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
eval_re2_tt.py - Evaluation script for RE2-TT (TrainTicket) dataset results

This script evaluates the root cause analysis results from batch_rcaeval_tt.py.
It calculates hit@1, hit@3, hit@5 metrics based on the LLM's predictions.

RE2-TT Service naming format:
  - Directory: ts-auth-service_cpu, ts-order-service_delay, etc.
  - True service: ts-auth-service, ts-order-service, etc. (extracted by split("_")[0])

Usage:
    python eval_re2_tt.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple, Optional


THIS_DIR = Path(__file__).resolve().parent

# RE2-TT output directory
OUTPUT_ROOT = THIS_DIR / "output" / "RE2-TT"

STDOUT_FILENAME = "main_stdout.txt"

# RE2-TT TrainTicket service list (for reference/validation)
TT_SERVICES = [
    "ts-auth-service", "ts-order-service", "ts-order-other-service",
    "ts-route-service", "ts-route-plan-service", "ts-train-service",
    "ts-travel-service", "ts-travel2-service", "ts-travel-plan-service",
    "ts-seat-service", "ts-station-service", "ts-basic-service",
    "ts-config-service", "ts-contacts-service", "ts-consign-service",
    "ts-consign-price-service", "ts-execute-service", "ts-preserve-service",
    "ts-preserve-other-service", "ts-price-service", "ts-security-service",
    "ts-ticketinfo-service", "ts-user-service", "ts-payment-service",
    "ts-rebook-service", "ts-cancel-service", "ts-food-service",
    "ts-food-map-service", "ts-assurance-service",
    "ts-admin-basic-info-service", "ts-admin-order-service",
    "ts-admin-route-service", "ts-admin-travel-service",
    "ts-admin-user-service", "ts-inside-payment-service",
    "ts-notification-service", "ts-verification-code-service",
    "ts-voucher-service", "ts-news-service", "ts-avatar-service",
    "ts-ticket-office-service", "ts-ui-dashboard",
]


def service_root(service_name: str) -> str:
    """
    Extract service name from directory name.
    
    RE2-TT format: ts-auth-service_cpu -> ts-auth-service
    Uses split("_")[0] to get the first part before underscore.
    
    Note: This works because service names use '-' (hyphen) not '_' (underscore).
    """
    return service_name.split("_")[0]


def extract_service_from_endpoint(ep: str) -> str:
    """
    Extract service name from endpoint string.
    
    Handles formats like:
      - "ts-auth-service"
      - "ts-auth-service/api/v1/xxx"
      - "GET: ts-auth-service/api/v1/xxx"
    """
    ep = ep.strip()

    # Remove HTTP method prefix if present
    for meth in ("GET:", "POST:", "PUT:", "DELETE:", "PATCH:"):
        if ep.startswith(meth):
            ep = ep.split(":", 1)[1].strip()
            break

    # Extract service name (part before first '/')
    if "/" in ep:
        return ep.split("/", 1)[0]
    return ep


def normalize_service_name(name: str) -> str:
    """
    Normalize service name for comparison.
    Handles case sensitivity and common variations.
    """
    return name.strip().lower()


def extract_final_answer_block(stdout: str) -> Optional[str]:
    """
    Extract the final answer block from LLM output.
    Returns the text after the last "Final Answer:" marker.
    """
    matches = list(re.finditer(r"Final Answer:\s*", stdout))
    if not matches:
        return None
    last = matches[-1]
    return stdout[last.end():]


def parse_top5_from_block(block: str) -> List[str]:
    """
    Parse Top-5 candidates from the answer block.
    
    Expected format:
    Top-5 Candidates (from most likely to least likely):
    1. Endpoint: ts-auth-service, Reason: ...
    2. Endpoint: ts-order-service, Reason: ...
    ...
    """
    lines = block.splitlines()
    top5: List[str] = []
    capture = False
    for line in lines:
        if "Top-5 Candidates" in line:
            capture = True
            continue
        if capture:
            # Match pattern: "1. Endpoint: ts-xxx-service, Reason: ..."
            m = re.match(r"\s*\d+\.\s*Endpoint:\s*([^,]+)", line)
            if m:
                ep = m.group(1).strip()
                top5.append(ep)
                if len(top5) >= 5:
                    break

    return top5


def parse_root_from_block(block: str) -> Optional[str]:
    """
    Parse the root cause endpoint from the answer block.
    
    Expected format:
    Root Cause Endpoint: ts-auth-service
    """
    m = re.search(r"Root\s*Cause\s*Endpoint:\s*([^\n\r,]+)", block)
    if not m:
        return None
    return m.group(1).strip()


def parse_top5_from_stdout(stdout: str) -> List[str]:
    """
    Parse Top-5 predictions from LLM stdout.
    Falls back to single root cause if Top-5 not found.
    """
    block = extract_final_answer_block(stdout)
    if block is None:
        return []

    top5 = parse_top5_from_block(block)
    if top5:
        return top5

    # Fallback: try to get at least the root cause endpoint
    root = parse_root_from_block(block)
    if root is not None:
        return [root]

    return []


def eval_case(true_service: str, top5_endpoints: List[str]) -> Tuple[int, int, int]:
    """
    Evaluate a single case.
    
    Returns (hit@1, hit@3, hit@5) where each is 1 if true_service is in top-k predictions.
    Uses normalized comparison (lowercase) for robustness.
    """
    true_service_norm = normalize_service_name(true_service)
    pred_services = [normalize_service_name(extract_service_from_endpoint(ep)) for ep in top5_endpoints]
    
    hit1 = int(true_service_norm in pred_services[:1])
    hit3 = int(true_service_norm in pred_services[:3])
    hit5 = int(true_service_norm in pred_services[:5])
    return hit1, hit3, hit5


def iter_case_stdout_files():
    """
    Iterate through all case stdout files in RE2-TT output directory.
    
    Yields (service_dir_name, case_id, stdout_path) tuples.
    """
    for service_dir in OUTPUT_ROOT.iterdir():
        if not service_dir.is_dir():
            continue
        for case_dir in service_dir.iterdir():
            if not case_dir.is_dir():
                continue
            stdout_path = case_dir / STDOUT_FILENAME
            if stdout_path.exists():
                yield service_dir.name, case_dir.name, stdout_path


def main():
    print("=" * 60)
    print("  RE2-TT (TrainTicket) Evaluation")
    print("=" * 60)
    print(f"Output root: {OUTPUT_ROOT}\n")
    
    if not OUTPUT_ROOT.exists():
        print("[ERROR] OUTPUT_ROOT does not exist. Please run batch_rcaeval_tt.py first.")
        return

    results = []

    for service_dir_name, case_id, stdout_path in iter_case_stdout_files():
        # Extract true service: ts-auth-service_cpu -> ts-auth-service
        true_srv = service_root(service_dir_name)
        text = stdout_path.read_text(encoding="utf-8", errors="ignore")

        top5 = parse_top5_from_stdout(text)

        if not top5:
            print(f"[WARN] {stdout_path} has no root cause endpoint, hit@k set to 0.")
            hit1, hit3, hit5 = 0, 0, 0
        else:
            hit1, hit3, hit5 = eval_case(true_srv, top5)

        print(f"Case: service_dir={service_dir_name}, case={case_id}")
        print(f"  True service: {true_srv}")
        print(f"  Top-5 endpoints: {top5}")
        print(f"  hit@1={hit1}, hit@3={hit3}, hit@5={hit5}\n")

        results.append(
            {
                "service_dir": service_dir_name,
                "case_id": case_id,
                "true_service": true_srv,
                "top5": top5,
                "hit1": hit1,
                "hit3": hit3,
                "hit5": hit5,
                "stdout_path": str(stdout_path),
            }
        )

    # Calculate statistics
    valid = [r for r in results if r["top5"]]
    n = len(valid)
    
    if n == 0:
        print("[ERROR] No cases have root cause predictions. Cannot calculate avg@k.")
        return

    hit1_total = sum(r["hit1"] for r in valid)
    hit3_total = sum(r["hit3"] for r in valid)
    hit5_total = sum(r["hit5"] for r in valid)
    avg1 = hit1_total / n
    avg3 = hit3_total / n
    avg5 = hit5_total / n

    print("=" * 60)
    print("  RE2-TT EVALUATION SUMMARY")
    print("=" * 60)
    print(f" Total cases evaluated: {len(results)}")
    print(f" Cases with predictions: {n}")
    print(f"")
    print(f" avg@1 = {avg1:.4f}   (hit@1 total: {hit1_total})")
    print(f" avg@3 = {avg3:.4f}   (hit@3 total: {hit3_total})")
    print(f" avg@5 = {avg5:.4f}   (hit@5 total: {hit5_total})")
    print("=" * 60)

    # Save detailed results
    summary_path = OUTPUT_ROOT / "eval_summary.json"
    summary_data = {
        "dataset": "RE2-TT",
        "total_cases": len(results),
        "valid_cases": n,
        "avg1": avg1,
        "avg3": avg3,
        "avg5": avg5,
        "hit1_total": hit1_total,
        "hit3_total": hit3_total,
        "hit5_total": hit5_total,
        "results": results,
    }
    summary_path.write_text(
        json.dumps(summary_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] Detailed results saved to: {summary_path}")


if __name__ == "__main__":
    main()
