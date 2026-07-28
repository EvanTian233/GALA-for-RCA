import sys
import os
import re
import json
import ast
import argparse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, parent_dir)


def _normalize_str(x: Any) -> str:
    """Normalize to trimmed string; treat NaN-like as empty."""
    if x is None:
        return ""
    s = str(x).strip()
    # pandas/numpy NaN -> 'nan'
    if s.lower() == "nan":
        return ""
    return s


def _ensure_str_list(x: Any) -> List[str]:
    """Convert a JSON value into a list[str].

    - If x is already a list, stringifies each item.
    - If x is a string (or other scalar), wraps it as a single-item list.
    - If x is None, returns [].
    """
    if x is None:
        return []
    if isinstance(x, list):
        return [_normalize_str(i) for i in x if _normalize_str(i) != ""]
    s = _normalize_str(x)
    return [s] if s != "" else []


def _try_parse_listlike_str(x: Any) -> List[str]:
    """Like _ensure_str_list, but also tries to parse list-like strings such as:
      - '["a","b"]'
      - "['a','b']"
    """
    if x is None:
        return []
    if isinstance(x, list):
        return [_normalize_str(i) for i in x if _normalize_str(i) != ""]
    s = _normalize_str(x)
    if s == "":
        return []

    # try parse list-like literals/JSON
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("(") and s.endswith(")")):
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return [_normalize_str(i) for i in v if _normalize_str(i) != ""]
        except Exception:
            pass
        try:
            v = ast.literal_eval(s)
            if isinstance(v, (list, tuple)):
                return [_normalize_str(i) for i in v if _normalize_str(i) != ""]
        except Exception:
            pass

    # fallback: scalar
    return [s]


def _extract_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract and parse ONE JSON object from a free-form string.

    Returns:
        (obj, err)
        - obj: parsed dict if success else None
        - err: error message if failed else None
    """
    if text is None:
        return None, "prediction is None"
    raw = str(text).strip()
    if raw == "":
        return None, "empty prediction"

    # 1) Prefer fenced ```json ... ``` blocks (most likely from controller).
    fence = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    else:
        # 2) Fallback: take substring from first '{' to last '}'
        l = raw.find('{')
        r = raw.rfind('}')
        if l != -1 and r != -1 and r > l:
            raw = raw[l:r + 1]

    # 3) Try strict JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, None
        return None, "top-level JSON is not an object"
    except Exception:
        pass

    # 4) Try Python literal (tolerate single quotes / trailing commas)
    try:
        obj = ast.literal_eval(raw)
        if isinstance(obj, dict):
            return obj, None
        return None, "top-level literal is not an object"
    except Exception as e:
        return None, f"cannot parse JSON: {e}"


def _parse_prediction_top5(prediction: str) -> Tuple[List[Dict[str, List[str]]], Optional[str]]:
    """Parse prediction into a list of root-cause entries.

    Expected NEW format (recommended):
        {
          "1": {"root cause occurrence datetime": [..x5], "root cause component": [..x5], "root cause reason": [..x5]},
          "2": {...}
        }

    Also tolerates OLD format (scalar values) and converts to single-item lists.
    """
    obj, err = _extract_json_object(prediction)
    if obj is None:
        return [], err

    # Case A: numbered dict: {"1": {...}, "2": {...}}
    numeric_keys = [k for k in obj.keys() if isinstance(k, str) and k.strip().isdigit()]
    if numeric_keys:
        entries: List[Dict[str, List[str]]] = []
        for k in sorted(numeric_keys, key=lambda s: int(s.strip())):
            v = obj.get(k)
            if not isinstance(v, dict):
                v = {}
            entries.append({
                "root cause occurrence datetime": _ensure_str_list(v.get("root cause occurrence datetime")),
                "root cause component": _ensure_str_list(v.get("root cause component")),
                "root cause reason": _ensure_str_list(v.get("root cause reason")),
            })
        return entries, None

    # Case B: single object with the three keys directly
    if any(k in obj for k in ["root cause component", "root cause reason", "root cause occurrence datetime"]):
        entry = {
            "root cause occurrence datetime": _ensure_str_list(obj.get("root cause occurrence datetime")),
            "root cause component": _ensure_str_list(obj.get("root cause component")),
            "root cause reason": _ensure_str_list(obj.get("root cause reason")),
        }
        return [entry], None

    return [], "prediction JSON does not contain expected keys"


def _parse_scoring_points(scoring_points: str) -> List[Dict[str, Optional[str]]]:
    """Parse scoring_points string into ordered ground-truth root-cause entries."""
    component_pattern = r"The (?:\d+-th|only) predicted root cause component is ([^\n]+)"
    reason_pattern = r"The (?:\d+-th|only) predicted root cause reason is ([^\n]+)"
    time_pattern = r"The (?:\d+-th|only) root cause occurrence time is within 1 minutes \(i.e\., <=1min\) of ([^\n]+)"

    comps = re.findall(component_pattern, str(scoring_points))
    reas = re.findall(reason_pattern, str(scoring_points))
    times = re.findall(time_pattern, str(scoring_points))

    n = max(len(comps), len(reas), len(times), 0)
    gt: List[Dict[str, Optional[str]]] = []
    for i in range(n):
        gt.append({
            "root cause occurrence datetime": _normalize_str(times[i]) if i < len(times) else None,
            "root cause component": _normalize_str(comps[i]) if i < len(comps) else None,
            "root cause reason": _normalize_str(reas[i]) if i < len(reas) else None,
        })
    return gt


def _parse_groundtruth_row(groundtruth: Any) -> List[Dict[str, Optional[str]]]:
    """Parse record.csv row (or dict/str) into ordered GT root-cause entries.

    Supports:
      - pandas Series / dict-like with keys: component, datetime, reason
      - string containing lines like:
            component: Tomcat04
            datetime: 2021-03-07 16:29:00
            reason: network packet loss
      - list-like strings for any field, e.g. '["Tomcat04","MG02"]'
    """
    if groundtruth is None:
        return []

    comp_raw = None
    dt_raw = None
    reason_raw = None

    # dict-like / pd.Series-like
    if isinstance(groundtruth, dict) or hasattr(groundtruth, "get"):
        try:
            comp_raw = groundtruth.get("component", None)
            dt_raw = groundtruth.get("datetime", None)
            reason_raw = groundtruth.get("reason", None)
        except Exception:
            comp_raw = None
            dt_raw = None
            reason_raw = None

    # string fallback
    if (comp_raw is None and dt_raw is None and reason_raw is None) and isinstance(groundtruth, str):
        s = groundtruth
        m = re.search(r"component:\s*(.+)", s)
        if m:
            comp_raw = m.group(1).strip()
        m = re.search(r"datetime:\s*(.+)", s)
        if m:
            dt_raw = m.group(1).strip()
        m = re.search(r"reason:\s*(.+)", s)
        if m:
            reason_raw = m.group(1).strip()

    comps = _try_parse_listlike_str(comp_raw)
    dts = _try_parse_listlike_str(dt_raw)
    reasons = _try_parse_listlike_str(reason_raw)

    # if all empty -> no usable GT
    if not comps and not dts and not reasons:
        return []

    n = max(len(comps), len(dts), len(reasons), 1)

    def _broadcast(lst: List[str], n_: int) -> List[Optional[str]]:
        if not lst:
            return [None] * n_
        if len(lst) == 1 and n_ > 1:
            return [lst[0]] * n_
        if len(lst) >= n_:
            return [lst[i] for i in range(n_)]
        # pad with last element
        out = [lst[i] for i in range(len(lst))]
        out.extend([lst[-1]] * (n_ - len(lst)))
        return out

    comps_b = _broadcast(comps, n)
    dts_b = _broadcast(dts, n)
    reasons_b = _broadcast(reasons, n)

    gt_entries: List[Dict[str, Optional[str]]] = []
    for i in range(n):
        gt_entries.append({
            "root cause occurrence datetime": _normalize_str(dts_b[i]) if dts_b[i] is not None else None,
            "root cause component": _normalize_str(comps_b[i]) if comps_b[i] is not None else None,
            "root cause reason": _normalize_str(reasons_b[i]) if reasons_b[i] is not None else None,
        })
    return gt_entries


def _time_within_1min(gt_time: str, pred_time: str) -> bool:
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        t1 = datetime.strptime(gt_time, fmt)
        t2 = datetime.strptime(pred_time, fmt)
    except Exception:
        return False
    return abs((t1 - t2).total_seconds()) <= 60


def _hit_at_k(gt_val: Optional[str], pred_list: List[str], k: int, *, is_time: bool = False) -> int:
    if gt_val is None or _normalize_str(gt_val) == "":
        return 0
    topk = pred_list[: max(0, k)]
    if not topk:
        return 0
    if is_time:
        return 1 if any(_time_within_1min(_normalize_str(gt_val), _normalize_str(p)) for p in topk) else 0
    gt = _normalize_str(gt_val)
    return 1 if any(_normalize_str(p) == gt for p in topk) else 0


def _reciprocal_rank(gt_val: Optional[str], pred_list: List[str], *, is_time: bool = False) -> float:
    """Calculate Reciprocal Rank for a single prediction.
    
    RR = 1/rank if gt_val is found in pred_list, else 0.
    """
    if gt_val is None or _normalize_str(gt_val) == "":
        return 0.0
    if not pred_list:
        return 0.0
    
    gt = _normalize_str(gt_val)
    for rank, pred in enumerate(pred_list, start=1):
        if is_time:
            if _time_within_1min(gt, _normalize_str(pred)):
                return 1.0 / rank
        else:
            if _normalize_str(pred) == gt:
                return 1.0 / rank
    return 0.0


def _pair_score_at5(pred_entry: Dict[str, List[str]], gt_entry: Dict[str, Optional[str]]) -> int:
    """Pairwise matching score used for permutation alignment (0-3)."""
    s = 0
    if gt_entry.get("root cause component"):
        s += _hit_at_k(gt_entry["root cause component"], pred_entry.get("root cause component", []), 5)
    if gt_entry.get("root cause reason"):
        s += _hit_at_k(gt_entry["root cause reason"], pred_entry.get("root cause reason", []), 5)
    if gt_entry.get("root cause occurrence datetime"):
        s += _hit_at_k(gt_entry["root cause occurrence datetime"], pred_entry.get("root cause occurrence datetime", []), 5, is_time=True)
    return s


def _best_align_predictions(pred_entries: List[Dict[str, List[str]]], gt_entries: List[Dict[str, Optional[str]]]) -> List[Optional[Dict[str, List[str]]]]:
    """Align predicted root-cause entries to GT entries.

    - If counts match and small enough, use permutation search to be order-invariant (like original evaluate.py).
    - Otherwise, align by index (best-effort).

    Returns a list of length len(gt_entries), where each element is the matched pred entry (or None).
    """
    import itertools

    n_gt = len(gt_entries)
    n_pred = len(pred_entries)
    if n_gt == 0:
        return []

    # Order-invariant alignment when feasible.
    if n_gt == n_pred and n_gt <= 6:
        best_perm: Optional[Tuple[Dict[str, List[str]], ...]] = None
        best_score = -1
        for perm in itertools.permutations(pred_entries):
            score = 0
            for i in range(n_gt):
                score += _pair_score_at5(perm[i], gt_entries[i])
            if score > best_score:
                best_score = score
                best_perm = perm
        return list(best_perm) if best_perm is not None else [None] * n_gt

    # Fallback: positional alignment.
    aligned: List[Optional[Dict[str, List[str]]]] = []
    for i in range(n_gt):
        aligned.append(pred_entries[i] if i < n_pred else None)
    return aligned


def evaluate(prediction: str, scoring_points: str, groundtruth: Any = None) -> Dict[str, Any]:
    """Evaluate ONE prediction string with top-5 lists.

    IMPORTANT CHANGE:
      - If `groundtruth` (from record.csv row) is provided, we ALWAYS evaluate datetime/component/reason
        against that GT, regardless of what the question/scoring_points asks.
      - If `groundtruth` is not provided or cannot be parsed, fallback to scoring_points.

    Output metrics (per case):
        - datetime_acc@1/@3/@5
        - component_acc@1/@3/@5
        - reason_acc@1/@3/@5
        - avg@1/@3/@5  (mean of the three metrics; always numeric)

    Notes:
        - If multiple root causes exist, accuracy is averaged over all GT root causes.
        - Root causes are aligned in an order-invariant way when counts match and small enough.
    """
    gt_entries = _parse_groundtruth_row(groundtruth)
    if not gt_entries:
        gt_entries = _parse_scoring_points(scoring_points)

    pred_entries, parse_err = _parse_prediction_top5(prediction)
    aligned_preds = _best_align_predictions(pred_entries, gt_entries)

    # Denominators (how many GT labels exist for each attribute)
    gt_time_n = sum(1 for g in gt_entries if g.get("root cause occurrence datetime"))
    gt_comp_n = sum(1 for g in gt_entries if g.get("root cause component"))
    gt_reason_n = sum(1 for g in gt_entries if g.get("root cause reason"))

    def _acc(attr: str, k: int, *, is_time: bool = False) -> float:
        denom = gt_time_n if is_time else (gt_comp_n if attr == "root cause component" else gt_reason_n)
        # IMPORTANT CHANGE: return 0.0 instead of None to avoid empty cells in CSV
        if denom == 0:
            return 0.0
        hits = 0
        for i, g in enumerate(gt_entries):
            gt_val = g.get(attr)
            if not gt_val:
                continue
            p = aligned_preds[i] or {}
            pred_list = p.get(attr, []) if isinstance(p, dict) else []
            hits += _hit_at_k(gt_val, pred_list, k, is_time=is_time)
        return hits / denom

    # Compute acc@k for each attribute (always numeric)
    time_acc_1 = _acc("root cause occurrence datetime", 1, is_time=True)
    time_acc_3 = _acc("root cause occurrence datetime", 3, is_time=True)
    time_acc_5 = _acc("root cause occurrence datetime", 5, is_time=True)

    comp_acc_1 = _acc("root cause component", 1)
    comp_acc_2 = _acc("root cause component", 2)
    comp_acc_3 = _acc("root cause component", 3)
    comp_acc_4 = _acc("root cause component", 4)
    comp_acc_5 = _acc("root cause component", 5)

    reason_acc_1 = _acc("root cause reason", 1)
    reason_acc_3 = _acc("root cause reason", 3)
    reason_acc_5 = _acc("root cause reason", 5)

    # Compute MRR for component
    def _mrr(attr: str, *, is_time: bool = False) -> float:
        denom = gt_time_n if is_time else (gt_comp_n if attr == "root cause component" else gt_reason_n)
        if denom == 0:
            return 0.0
        rr_sum = 0.0
        for i, g in enumerate(gt_entries):
            gt_val = g.get(attr)
            if not gt_val:
                continue
            p = aligned_preds[i] or {}
            pred_list = p.get(attr, []) if isinstance(p, dict) else []
            rr_sum += _reciprocal_rank(gt_val, pred_list, is_time=is_time)
        return rr_sum / denom

    comp_mrr = _mrr("root cause component")

    # avg@3 = (acc@1 + acc@2 + acc@3) / 3
    # avg@5 = (acc@1 + acc@2 + acc@3 + acc@4 + acc@5) / 5
    comp_avg_3 = (comp_acc_1 + comp_acc_2 + comp_acc_3) / 3.0
    comp_avg_5 = (comp_acc_1 + comp_acc_2 + comp_acc_3 + comp_acc_4 + comp_acc_5) / 5.0

    return {
        "n_gt": len(gt_entries),
        "n_pred": len(pred_entries),
        "gt_time_n": gt_time_n,
        "gt_component_n": gt_comp_n,
        "gt_reason_n": gt_reason_n,
        "datetime_acc@1": time_acc_1,
        "datetime_acc@3": time_acc_3,
        "datetime_acc@5": time_acc_5,
        "component_acc@1": comp_acc_1,
        "component_acc@2": comp_acc_2,
        "component_acc@3": comp_acc_3,
        "component_acc@4": comp_acc_4,
        "component_acc@5": comp_acc_5,
        "component_mrr": comp_mrr,
        "component_avg@3": comp_avg_3,
        "component_avg@5": comp_avg_5,
        "reason_acc@1": reason_acc_1,
        "reason_acc@3": reason_acc_3,
        "reason_acc@5": reason_acc_5,
        "parse_error": parse_err,
    }


def file_evaluate(prediction_file: str, query_file: str, report_file: str):
    """Evaluate a prediction file against a dataset query file, save per-case metrics."""
    import pandas as pd

    pred_df = pd.read_csv(prediction_file)
    query_df = pd.read_csv(query_file)

    if len(pred_df) != len(query_df):
        raise ValueError("The length of prediction file and record file should be the same")

    rows = []
    for idx in range(len(pred_df)):
        prediction = pred_df.loc[idx, "prediction"]
        scoring_points = query_df.loc[idx, "scoring_points"]
        instruction = query_df.loc[idx, "instruction"]
        task_index = query_df.loc[idx, "task_index"]

        # Backward compatible: file_evaluate does not have record.csv row here, so groundtruth=None.
        metrics = evaluate(prediction, scoring_points, groundtruth=None)
        rows.append({
            "query": instruction,
            "answer": prediction,
            "groundtruth": scoring_points,
            "task_index": task_index,
            **metrics,
        })

    eval_df = pd.DataFrame(rows)

    if os.path.exists(report_file):
        eval_df.to_csv(report_file, mode="a", header=False, index=False)
    else:
        os.makedirs(os.path.dirname(report_file), exist_ok=True)
        eval_df.to_csv(report_file, index=False)


def report(report_file: str):
    """Print aggregated acc@k for datetime/component/reason and their avg@k."""
    import pandas as pd

    df = pd.read_csv(report_file)
    if len(df) == 0:
        print("Empty report.")
        return

    # difficulty: task_1-3 easy, task_4-6 middle, task_7 hard
    df["difficulty"] = df["task_index"].apply(
        lambda x: "easy" if int(str(x).split('_')[1]) <= 3 else "middle" if int(str(x).split('_')[1]) <= 6 else "hard"
    )

    def _safe_mean(series) -> float:
        s = pd.to_numeric(series, errors="coerce")
        if s.dropna().empty:
            return float("nan")
        return float(s.mean())

    def _weighted_mean(acc_col: str, denom_col: str) -> float:
        acc = pd.to_numeric(df[acc_col], errors="coerce")
        denom = pd.to_numeric(df[denom_col], errors="coerce")
        mask = (~acc.isna()) & (~denom.isna()) & (denom > 0)
        if mask.sum() == 0:
            return float("nan")
        return float((acc[mask] * denom[mask]).sum() / denom[mask].sum())

    metrics = [
        ("datetime", "gt_time_n"),
        ("component", "gt_component_n"),
        ("reason", "gt_reason_n"),
        ("avg", None),
    ]

    ks = [1, 3, 5]

    print("\n=== Overall (micro-avg for datetime/component/reason; macro-avg for avg@k) ===")
    for name, denom in metrics:
        for k in ks:
            col = f"{name}_acc@{k}" if name != "avg" else f"avg@{k}"
            if name == "avg":
                v = _safe_mean(df[col])
            else:
                v = _weighted_mean(col, denom)
            print(f"{col}: {v:.4f}" if v == v else f"{col}: N/A")

    print("\n=== By difficulty (same averaging rule as above) ===")
    for diff in ["easy", "middle", "hard"]:
        sdf = df[df["difficulty"] == diff].copy()
        if len(sdf) == 0:
            continue
        print(f"\n[{diff}] cases={len(sdf)}")
        for name, denom in metrics:
            for k in ks:
                col = f"{name}_acc@{k}" if name != "avg" else f"avg@{k}"
                if name == "avg":
                    v = _safe_mean(sdf[col])
                else:
                    acc = pd.to_numeric(sdf[col], errors="coerce")
                    den = pd.to_numeric(sdf[denom], errors="coerce")
                    mask = (~acc.isna()) & (~den.isna()) & (den > 0)
                    if mask.sum() == 0:
                        v = float("nan")
                    else:
                        v = float((acc[mask] * den[mask]).sum() / den[mask].sum())
                print(f"{col}: {v:.4f}" if v == v else f"{col}: N/A")


if __name__ == "__main__":
    """CLI:
    -p: list of prediction csv files (must contain column: prediction)
    -q: list of query.csv files (must contain columns: instruction, scoring_points, task_index)
    -r: output report csv
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", type=str, nargs="+", help="a list of prediction files to evaluate")
    parser.add_argument("-q", type=str, nargs="+", help="a list of query files to evaluate")
    parser.add_argument("-r", type=str, help="evaluation file to save")
    args = parser.parse_args()

    if len(args.p) != len(args.q):
        raise ValueError("The length of prediction files and query files should be the same")
    if os.path.exists(args.r):
        os.remove(args.r)

    for i in range(len(args.p)):
        try:
            file_evaluate(args.p[i], args.q[i], args.r)
        except Exception as e:
            print(f"Error when evaluating the file {args.p[i]}: {e}")
            continue

    report(args.r)
