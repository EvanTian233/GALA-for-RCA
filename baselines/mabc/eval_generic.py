#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
eval_generic.py - Generic evaluation script for any output directory

Computes metrics according to the specified formulas:
- MRR = sum(1/rank) / n
- acc@k = proportion where rank <= k
- avg@3 = (acc@1 + acc@2 + acc@3) / 3
- avg@5 = (acc@1 + acc@2 + acc@3 + acc@4 + acc@5) / 5

Usage:
    python eval_generic.py --output-dir output/RE2-TT-gpt-4.1-mini
    python eval_generic.py --output-dir output/RE2-OB-gpt4.1mini
"""

from __future__ import annotations

import json
import re
import argparse
from pathlib import Path
from typing import List, Tuple, Optional


THIS_DIR = Path(__file__).resolve().parent
STDOUT_FILENAME = "main_stdout.txt"


def parse_args():
    p = argparse.ArgumentParser(description="Evaluate RCA results from any output directory")
    p.add_argument("--output-dir", type=str, required=True,
                   help="Path to output directory (e.g., output/RE2-TT-gpt-4.1-mini)")
    return p.parse_args()


def service_root(service_name: str) -> str:
    """Extract service name from directory name (e.g., ts-auth-service_cpu -> ts-auth-service)"""
    return service_name.split("_")[0]


def extract_service_from_endpoint(ep: str) -> str:
    """Extract service name from endpoint string."""
    ep = ep.strip()
    
    for meth in ("GET:", "POST:", "PUT:", "DELETE:", "PATCH:"):
        if ep.startswith(meth):
            ep = ep.split(":", 1)[1].strip()
            break
    
    if "/" in ep:
        return ep.split("/", 1)[0]
    return ep


def normalize_service(name: str) -> str:
    """Normalize service name for comparison."""
    return name.strip().lower()


def extract_final_answer_block(stdout: str) -> Optional[str]:
    """Extract the final answer block from LLM output."""
    matches = list(re.finditer(r"Final Answer:\s*", stdout))
    if not matches:
        return None
    last = matches[-1]
    return stdout[last.end():]


def parse_top5_from_block(block: str) -> List[str]:
    """Parse Top-5 candidates from the answer block."""
    lines = block.splitlines()
    top5: List[str] = []
    capture = False
    for line in lines:
        if "Top-5 Candidates" in line:
            capture = True
            continue
        if capture:
            m = re.match(r"\s*\d+\.\s*Endpoint:\s*([^,]+)", line)
            if m:
                ep = m.group(1).strip()
                top5.append(ep)
                if len(top5) >= 5:
                    break
    return top5


def parse_root_from_block(block: str) -> Optional[str]:
    """Parse the root cause endpoint from the answer block."""
    m = re.search(r"Root\s*Cause\s*Endpoint:\s*([^\n\r,]+)", block)
    if not m:
        return None
    return m.group(1).strip()


def parse_top5_from_stdout(stdout: str) -> List[str]:
    """Parse Top-5 predictions from LLM stdout."""
    block = extract_final_answer_block(stdout)
    if block is None:
        return []
    
    top5 = parse_top5_from_block(block)
    if top5:
        return top5
    
    root = parse_root_from_block(block)
    if root is not None:
        return [root]
    
    return []


def get_rank(true_service: str, top5: List[str]) -> int:
    """
    Get the rank (1-indexed) of true_service in top5 predictions.
    Returns 0 if not found.
    """
    true_norm = normalize_service(true_service)
    
    for i, pred in enumerate(top5):
        pred_service = normalize_service(extract_service_from_endpoint(pred))
        if pred_service == true_norm:
            return i + 1
    
    return 0


def compute_metrics(ranks: List[int]) -> dict:
    """
    Compute MRR, acc@1, acc@2, acc@3, acc@5, avg@3, avg@5 from a list of ranks.
    
    Formulas:
    - MRR = sum(1/rank) / n
    - acc@k = proportion where 0 < rank <= k
    - avg@3 = (acc@1 + acc@2 + acc@3) / 3
    - avg@5 = (acc@1 + acc@2 + acc@3 + acc@4 + acc@5) / 5
    """
    n = len(ranks)
    if n == 0:
        return {
            "MRR": 0, "acc@1": 0, "acc@2": 0, "acc@3": 0, "acc@4": 0, "acc@5": 0,
            "avg@3": 0, "avg@5": 0
        }
    
    # MRR = sum(1/rank) / n
    mrr = sum(1.0 / r if r > 0 else 0 for r in ranks) / n
    
    # acc@k = proportion where 0 < rank <= k
    acc1 = sum(1 for r in ranks if 0 < r <= 1) / n
    acc2 = sum(1 for r in ranks if 0 < r <= 2) / n
    acc3 = sum(1 for r in ranks if 0 < r <= 3) / n
    acc4 = sum(1 for r in ranks if 0 < r <= 4) / n
    acc5 = sum(1 for r in ranks if 0 < r <= 5) / n
    
    # avg@3 = (acc@1 + acc@2 + acc@3) / 3
    avg3 = (acc1 + acc2 + acc3) / 3
    
    # avg@5 = (acc@1 + acc@2 + acc@3 + acc@4 + acc@5) / 5
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


def iter_case_stdout_files(output_root: Path):
    """Iterate through all case stdout files in output directory."""
    for service_dir in output_root.iterdir():
        if not service_dir.is_dir():
            continue
        for case_dir in service_dir.iterdir():
            if not case_dir.is_dir():
                continue
            stdout_path = case_dir / STDOUT_FILENAME
            if stdout_path.exists():
                yield service_dir.name, case_dir.name, stdout_path


def main():
    args = parse_args()
    
    # Resolve output directory
    output_dir = args.output_dir
    if not output_dir.startswith("/") and not output_dir[1:2] == ":":
        output_root = THIS_DIR / output_dir
    else:
        output_root = Path(output_dir)
    
    print("=" * 70)
    print("  Generic RCA Evaluation")
    print("=" * 70)
    print(f"Output directory: {output_root}")
    
    if not output_root.exists():
        print(f"[ERROR] Directory not found: {output_root}")
        return
    
    results = []
    ranks = []
    
    for service_dir_name, case_id, stdout_path in iter_case_stdout_files(output_root):
        true_srv = service_root(service_dir_name)
        text = stdout_path.read_text(encoding="utf-8", errors="ignore")
        
        top5 = parse_top5_from_stdout(text)
        rank = get_rank(true_srv, top5) if top5 else 0
        ranks.append(rank)
        
        # Compute individual hit metrics for backward compatibility
        hit1 = 1 if 0 < rank <= 1 else 0
        hit3 = 1 if 0 < rank <= 3 else 0
        hit5 = 1 if 0 < rank <= 5 else 0
        
        print(f"Case: {service_dir_name}/{case_id}")
        print(f"  True service: {true_srv}")
        print(f"  Top-5: {top5}")
        print(f"  Rank: {rank if rank > 0 else 'not found'}")
        print()
        
        results.append({
            "service_dir": service_dir_name,
            "case_id": case_id,
            "true_service": true_srv,
            "top5": top5,
            "rank": rank,
            "hit1": hit1,
            "hit3": hit3,
            "hit5": hit5,
            "stdout_path": str(stdout_path),
        })
    
    if not results:
        print("[ERROR] No cases found with main_stdout.txt")
        return
    
    # Compute aggregate metrics
    metrics = compute_metrics(ranks)
    
    # Rank distribution
    rank_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, "not_found": 0}
    for r in ranks:
        if r == 0:
            rank_dist["not_found"] += 1
        elif r in rank_dist:
            rank_dist[r] += 1
    
    # Print summary
    print("=" * 70)
    print("  EVALUATION SUMMARY")
    print("=" * 70)
    print(f"  Total cases: {len(results)}")
    print(f"  MRR:   {metrics['MRR']:.4f}")
    print(f"  acc@1: {metrics['acc@1']:.4f}")
    print(f"  acc@2: {metrics['acc@2']:.4f}")
    print(f"  acc@3: {metrics['acc@3']:.4f}")
    print(f"  acc@4: {metrics['acc@4']:.4f}")
    print(f"  acc@5: {metrics['acc@5']:.4f}")
    print(f"  avg@3: {metrics['avg@3']:.4f}")
    print(f"  avg@5: {metrics['avg@5']:.4f}")
    print(f"  Rank distribution: {rank_dist}")
    print("=" * 70)
    
    # Determine dataset name from directory
    dataset_name = output_root.name
    
    # Save results
    summary = {
        "dataset": dataset_name,
        "total_cases": len(results),
        "MRR": metrics["MRR"],
        "acc@1": metrics["acc@1"],
        "acc@2": metrics["acc@2"],
        "acc@3": metrics["acc@3"],
        "acc@4": metrics["acc@4"],
        "acc@5": metrics["acc@5"],
        "avg@3": metrics["avg@3"],
        "avg@5": metrics["avg@5"],
        "rank_distribution": rank_dist,
        "results": results,
    }
    
    summary_path = output_root / "eval_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[OK] Results saved to: {summary_path}")


if __name__ == "__main__":
    main()
