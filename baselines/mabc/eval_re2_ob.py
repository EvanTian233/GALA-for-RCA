from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple, Optional


THIS_DIR = Path(__file__).resolve().parent

OUTPUT_ROOT = THIS_DIR / "output" / "RE2-OB"

STDOUT_FILENAME = "main_stdout.txt"



def service_root(service_name: str) -> str:

    return service_name.split("_")[0]


def extract_service_from_endpoint(ep: str) -> str:

    ep = ep.strip()

    for meth in ("GET:", "POST:", "PUT:", "DELETE:", "PATCH:"):
        if ep.startswith(meth):
            ep = ep.split(":", 1)[1].strip()
            break

    if "/" in ep:
        return ep.split("/", 1)[0]
    return ep


def extract_final_answer_block(stdout: str) -> Optional[str]:

    matches = list(re.finditer(r"Final Answer:\s*", stdout))
    if not matches:
        return None
    last = matches[-1]
    return stdout[last.end():]


def parse_top5_from_block(block: str) -> List[str]:

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

    m = re.search(r"Root\s*Cause\s*Endpoint:\s*([^\n\r]+)", block)
    if not m:
        return None
    return m.group(1).strip()


def parse_top5_from_stdout(stdout: str) -> List[str]:

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


def eval_case(true_service: str, top5_endpoints: List[str]) -> Tuple[int, int, int]:

    pred_services = [extract_service_from_endpoint(ep) for ep in top5_endpoints]
    hit1 = int(true_service in pred_services[:1])
    hit3 = int(true_service in pred_services[:3])
    hit5 = int(true_service in pred_services[:5])
    return hit1, hit3, hit5


def iter_case_stdout_files():

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
    print(f"output root is: {OUTPUT_ROOT}")
    if not OUTPUT_ROOT.exists():
        print("[ERROR] OUTPUT_ROOT missing.")
        return

    results = []

    for service_dir_name, case_id, stdout_path in iter_case_stdout_files():
        true_srv = service_root(service_dir_name)  # e.g. checkoutservice_cpu -> checkoutservice
        text = stdout_path.read_text(encoding="utf-8", errors="ignore")

        top5 = parse_top5_from_stdout(text)

        if not top5:
            print(f"[WARN] {stdout_path} has no root cause endpoint, hit@k written as 0.")
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

    valid = [r for r in results if r["top5"]]
    n = len(valid)
    if n == 0:
        print("[ERROR] no case has root cause，cannot count avg@k。")
        return

    hit1_total = sum(r["hit1"] for r in valid)
    hit3_total = sum(r["hit3"] for r in valid)
    hit5_total = sum(r["hit5"] for r in valid)
    avg1 = hit1_total / n
    avg3 = hit3_total / n
    avg5 = hit5_total / n

    print("============== EVAL SUMMARY ==============")
    print(f" case number: {n}")
    print(f"avg@1 = {avg1:.4f}   (hit@1 total={hit1_total})")
    print(f"avg@3 = {avg3:.4f}   (hit@3 total={hit3_total})")
    print(f"avg@5 = {avg5:.4f}   (hit@5 total={hit5_total})")
    print("==========================================")

    summary_path = OUTPUT_ROOT / "eval_summary.json"
    summary_path.write_text(
        json.dumps(
            {"results": valid, "avg1": avg1, "avg3": avg3, "avg5": avg5},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[OK] : {summary_path}")


if __name__ == "__main__":
    main()