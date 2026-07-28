#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compute_mrr.py - Compute evaluation metrics from eval_summary.json

Metrics computed:
    - MRR: Mean Reciprocal Rank = sum(1/rank) / n
    - acc@1: Accuracy at Top-1
    - acc@2: Accuracy at Top-2
    - acc@3: Accuracy at Top-3
    - acc@5: Accuracy at Top-5
    - avg@3: (acc@1 + acc@2 + acc@3) / 3
    - avg@5: (acc@1 + acc@2 + acc@3 + acc@4 + acc@5) / 5

Usage:
    python compute_mrr.py
"""

import json
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = THIS_DIR / "output"

DATASETS = ["RE2-OB", "RE2-TT"]


def get_rank(true_service: str, top5: list) -> int:
    """
    Get the rank (1-indexed) of true_service in top5 predictions.
    Returns 0 if not found.
    """
    true_service_lower = true_service.strip().lower()
    
    for i, pred in enumerate(top5):
        pred_service = pred.strip().lower()
        if "/" in pred_service:
            pred_service = pred_service.split("/", 1)[0]
        
        if pred_service == true_service_lower:
            return i + 1  # 1-indexed rank
    
    return 0  # Not found


def compute_metrics(ranks: list) -> dict:
    """
    Compute MRR, acc@1, acc@2, acc@3, acc@5, avg@3, avg@5 from a list of ranks.
    
    Args:
        ranks: List of ranks (1-based, 0=not found)
    
    Returns:
        Dict with all metrics
    """
    n = len(ranks)
    if n == 0:
        return {
            "MRR": 0, "acc@1": 0, "acc@2": 0, "acc@3": 0, "acc@4": 0, "acc@5": 0,
            "avg@3": 0, "avg@5": 0
        }
    
    # MRR = sum(1/rank) / n
    mrr = sum(1.0 / r if r > 0 else 0 for r in ranks) / n
    
    # acc@k = proportion of cases where rank <= k
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


def compute_metrics_for_dataset(dataset_name: str) -> dict | None:
    """
    Compute all metrics for a single dataset.
    """
    eval_file = OUTPUT_ROOT / dataset_name / "eval_summary.json"
    
    if not eval_file.exists():
        print(f"[WARN] {eval_file} not found, skipping {dataset_name}")
        return None
    
    with eval_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    
    results = data.get("results", [])
    if not results:
        print(f"[WARN] No results in {eval_file}")
        return None
    
    # Compute ranks for each case
    ranks = []
    for r in results:
        true_service = r.get("true_service", "")
        top5 = r.get("top5", [])
        
        if not top5:
            ranks.append(0)  # Not found
        else:
            rank = get_rank(true_service, top5)
            ranks.append(rank)
    
    # Compute metrics
    metrics = compute_metrics(ranks)
    metrics["dataset"] = dataset_name
    metrics["total_cases"] = len(ranks)
    metrics["ranks"] = ranks  # Keep for combined calculation
    
    # Rank distribution
    rank_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, "not_found": 0}
    for r in ranks:
        if r == 0:
            rank_dist["not_found"] += 1
        elif r in rank_dist:
            rank_dist[r] += 1
    metrics["rank_distribution"] = rank_dist
    
    return metrics


def main():
    print("=" * 70)
    print("  Evaluation Metrics Computation")
    print("  (MRR, acc@1, acc@2, acc@3, acc@5, avg@3, avg@5)")
    print("=" * 70)
    print()
    
    all_results = []
    combined_ranks = []
    
    for dataset in DATASETS:
        result = compute_metrics_for_dataset(dataset)
        if result:
            all_results.append(result)
            combined_ranks.extend(result["ranks"])
            
            print(f"Dataset: {dataset}")
            print(f"  Total cases: {result['total_cases']}")
            print(f"  MRR:   {result['MRR']:.4f}")
            print(f"  acc@1: {result['acc@1']:.4f}")
            print(f"  acc@2: {result['acc@2']:.4f}")
            print(f"  acc@3: {result['acc@3']:.4f}")
            print(f"  acc@4: {result['acc@4']:.4f}")
            print(f"  acc@5: {result['acc@5']:.4f}")
            print(f"  avg@3: {result['avg@3']:.4f}")
            print(f"  avg@5: {result['avg@5']:.4f}")
            print(f"  Rank distribution: {result['rank_distribution']}")
            print()
    
    if not all_results:
        print("[ERROR] No datasets found with eval_summary.json")
        return
    
    # Combined metrics across all datasets
    combined_metrics = compute_metrics(combined_ranks)
    combined_n = len(combined_ranks)
    
    print("=" * 70)
    print("  COMBINED RESULTS (All Datasets)")
    print("=" * 70)
    print(f"  Total cases: {combined_n}")
    print(f"  MRR:   {combined_metrics['MRR']:.4f}")
    print(f"  acc@1: {combined_metrics['acc@1']:.4f}")
    print(f"  acc@2: {combined_metrics['acc@2']:.4f}")
    print(f"  acc@3: {combined_metrics['acc@3']:.4f}")
    print(f"  acc@4: {combined_metrics['acc@4']:.4f}")
    print(f"  acc@5: {combined_metrics['acc@5']:.4f}")
    print(f"  avg@3: {combined_metrics['avg@3']:.4f}")
    print(f"  avg@5: {combined_metrics['avg@5']:.4f}")
    print("=" * 70)
    
    # Save results (remove ranks list for cleaner output)
    for r in all_results:
        del r["ranks"]
    
    output_file = OUTPUT_ROOT / "mrr_summary.json"
    summary = {
        "datasets": all_results,
        "combined": {
            "total_cases": combined_n,
            **combined_metrics
        }
    }
    if "ranks" in summary["combined"]:
        del summary["combined"]["ranks"]
    
    output_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n[OK] Results saved to: {output_file}")


if __name__ == "__main__":
    main()
