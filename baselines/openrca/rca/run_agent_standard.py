import argparse
import concurrent.futures
import importlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger
from nbformat import v4 as nbf

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from main.evaluate import evaluate
from rca.api_router import clear_active_case, configs, pop_case_token_usage, set_active_case
from rca.benchmark_paths import (
    apply_instruction_placeholders,
    apply_schema_placeholders,
    resolve_monitor_dir,
    resolve_query_file,
    resolve_record_file,
    resolve_result_dir,
)


PROMPT_MODULES = {
    "Telecom": "rca.baseline.rca_agent.prompt.basic_prompt_Telecom",
    "Bank": "rca.baseline.rca_agent.prompt.basic_prompt_Bank",
    "Market/cloudbed-1": "rca.baseline.rca_agent.prompt.basic_prompt_Market",
    "Market/cloudbed-2": "rca.baseline.rca_agent.prompt.basic_prompt_Market",
    "RE2-OB": "rca.baseline.rca_agent.prompt.basic_prompt_RE2_OB",
    "RE2-TT": "rca.baseline.rca_agent.prompt.basic_prompt_RE2_TT",
    "Dataset3": "rca.baseline.rca_agent.prompt.basic_prompt_Dataset3",
}

METRIC_COLS = [
    "component_acc@1",
    "component_acc@2",
    "component_acc@3",
    "component_acc@4",
    "component_acc@5",
    "component_mrr",
    "component_avg@3",
    "component_avg@5",
]


def _load_basic_prompt(dataset: str):
    module_name = PROMPT_MODULES.get(dataset)
    if not module_name:
        supported = ", ".join(sorted(PROMPT_MODULES))
        raise ValueError(f"Unsupported dataset: {dataset}. Supported datasets: {supported}")

    prompt_module = importlib.import_module(module_name)
    prompt_module.schema = apply_schema_placeholders(prompt_module.schema, dataset)
    return prompt_module


def _ensure_input_files(dataset: str):
    inst_file = resolve_query_file(dataset)
    gt_file = resolve_record_file(dataset)

    if not inst_file.exists():
        raise FileNotFoundError(
            f"Query file not found: {inst_file}\n"
            f"Expected file: dataset/{dataset}/query.csv"
        )
    if not gt_file.exists():
        raise FileNotFoundError(
            f"Record file not found: {gt_file}\n"
            f"Expected file: dataset/{dataset}/record.csv"
        )

    return inst_file, gt_file


def run_agent_with_timeout(agent_prompt, basic_prompt, instruction, case_logger, args, case_id):
    from rca.baseline.rca_agent.rca_agent import RCA_Agent

    set_active_case(case_id)
    try:
        agent = RCA_Agent(agent_prompt, basic_prompt)
        prediction, trajectory, prompt = agent.run(
            instruction,
            case_logger,
            max_step=args.controller_max_step,
            max_turn=args.controller_max_turn,
        )
        return prediction, trajectory, prompt
    finally:
        clear_active_case()


def main(args, uid, dataset):
    import rca.baseline.rca_agent.prompt.agent_prompt as agent_prompt

    basic_prompt = _load_basic_prompt(dataset)
    inst_file, gt_file = _ensure_input_files(dataset)

    model_name = configs["MODEL"].split("/")[-1]
    result_dir = resolve_result_dir(dataset)
    monitor_dir = resolve_monitor_dir(dataset)
    eval_file = result_dir / f"agent-{args.tag}-{model_name}.csv"
    unique_obs_path = monitor_dir / f"agent-{args.tag}-{model_name}" / uid

    instruct_data = pd.read_csv(inst_file)
    gt_data = pd.read_csv(gt_file)
    logger.info(f"Loaded query file: {inst_file} ({len(instruct_data)} rows)")
    logger.info(f"Loaded record file: {gt_file} ({len(gt_data)} rows)")

    for subdir in ("history", "trajectory", "prompt"):
        (unique_obs_path / subdir).mkdir(parents=True, exist_ok=True)

    result_dir.mkdir(parents=True, exist_ok=True)
    if not eval_file.exists():
        eval_df = pd.DataFrame(
            columns=[
                "row_id",
                "task_index",
                "instruction",
                "prediction",
                "groundtruth",
                "passed",
                "failed",
                "score",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "runtime_sec",
            ]
            + METRIC_COLS
        )
    else:
        eval_df = pd.read_csv(eval_file)

    for col in ["input_tokens", "output_tokens", "total_tokens", "runtime_sec"] + METRIC_COLS:
        if col not in eval_df.columns:
            eval_df[col] = pd.NA

    base_len = len(eval_df)

    scores = {"total": 0, "easy": 0, "middle": 0, "hard": 0}
    nums = {"total": 0, "easy": 0, "middle": 0, "hard": 0}

    logger.info(f"Using dataset: {dataset}")
    logger.info(f"Using model: {model_name}")

    for idx, row in instruct_data.iterrows():
        if idx < args.start_idx:
            continue
        if idx > args.end_idx:
            break

        instruction = apply_instruction_placeholders(row["instruction"], dataset)
        task_index = row["task_index"]
        scoring_points = row["scoring_points"]
        task_id = int(task_index.split("_")[1])
        best_comp_acc_1 = 0.0

        if task_id <= 3:
            catalog = "easy"
        elif task_id <= 6:
            catalog = "middle"
        else:
            catalog = "hard"

        temp_scores = scores.copy()
        temp_nums = nums.copy()

        for i in range(args.sample_num):
            case_uuid = f"{uid}_#{idx}-{i}"
            nb = nbf.new_notebook()
            nbfile = unique_obs_path / "trajectory" / f"{case_uuid}.ipynb"
            promptfile = unique_obs_path / "prompt" / f"{case_uuid}.json"
            logfile = unique_obs_path / "history" / f"{case_uuid}.log"

            logger.remove()
            logger.add(sys.stdout, colorize=True, enqueue=True, level="INFO")
            logger.add(str(logfile), colorize=True, enqueue=True, level="INFO")
            logger.debug("\n" + "#" * 80 + f"\n{case_uuid}: {task_index}\n" + "#" * 80)

            try:
                t0 = time.perf_counter()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        run_agent_with_timeout,
                        agent_prompt,
                        basic_prompt,
                        instruction,
                        logger,
                        args,
                        case_uuid,
                    )
                    prediction, trajectory, prompt = future.result(timeout=args.timeout)
                runtime_sec = time.perf_counter() - t0

                token_usage = pop_case_token_usage(case_uuid) or {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

                for step in trajectory:
                    code_cell = nbf.new_code_cell(step["code"])
                    result_cell = nbf.new_markdown_cell(f"```\n{step['result']}\n```")
                    nb.cells.append(code_cell)
                    nb.cells.append(result_cell)

                with open(nbfile, "w", encoding="utf-8") as file:
                    json.dump(nb, file, ensure_ascii=False, indent=4)
                logger.info(f"Trajectory has been saved to {nbfile}")

                with open(promptfile, "w", encoding="utf-8") as file:
                    json.dump({"messages": prompt}, file, ensure_ascii=False, indent=4)
                logger.info(f"Prompt has been saved to {promptfile}")

                row_dict = {
                    "row_id": idx,
                    "task_index": task_index,
                    "instruction": instruction,
                    "prediction": prediction,
                    "groundtruth": "\n".join(
                        f"{col}: {gt_data.iloc[idx][col]}"
                        for col in gt_data.columns
                        if col != "description"
                    ),
                    "passed": "N/A",
                    "failed": "N/A",
                    "score": pd.NA,
                    "input_tokens": token_usage.get("input_tokens", 0),
                    "output_tokens": token_usage.get("output_tokens", 0),
                    "total_tokens": token_usage.get("total_tokens", 0),
                    "runtime_sec": runtime_sec,
                }
                for col in METRIC_COLS:
                    row_dict[col] = pd.NA

                eval_df = pd.concat([eval_df, pd.DataFrame([row_dict])], ignore_index=True)
                eval_df.to_csv(eval_file, index=False)

                gt_row = gt_data.iloc[idx]
                metrics = evaluate(prediction, scoring_points, gt_row)

                logger.info(f"Prediction: {prediction}")
                logger.info(f"Scoring Points: {scoring_points}")

                last = eval_df.index[-1]

                passed_list = metrics.get("passed", None) if isinstance(metrics, dict) else None
                failed_list = metrics.get("failed", None) if isinstance(metrics, dict) else None
                if isinstance(passed_list, list):
                    eval_df.loc[last, "passed"] = "\n".join(map(str, passed_list))
                    logger.info(f"Passed Criteria: {passed_list}")
                if isinstance(failed_list, list):
                    eval_df.loc[last, "failed"] = "\n".join(map(str, failed_list))
                    logger.info(f"Failed Criteria: {failed_list}")

                if isinstance(metrics, dict):
                    for col in METRIC_COLS:
                        if col in metrics:
                            eval_df.loc[last, col] = metrics[col]

                comp_acc_1_val = 0.0
                if isinstance(metrics, dict) and metrics.get("component_acc@1") is not None:
                    try:
                        comp_acc_1_val = float(metrics["component_acc@1"])
                    except (TypeError, ValueError):
                        comp_acc_1_val = 0.0

                eval_df.loc[last, "score"] = comp_acc_1_val
                logger.info(f"Score (component_acc@1): {comp_acc_1_val}")

                best_comp_acc_1 = max(best_comp_acc_1, comp_acc_1_val)
                eval_df.to_csv(eval_file, index=False)

                temp_scores = scores.copy()
                temp_scores[catalog] += best_comp_acc_1
                temp_scores["total"] += best_comp_acc_1
                temp_nums = nums.copy()
                temp_nums[catalog] += 1
                temp_nums["total"] += 1

            except concurrent.futures.TimeoutError:
                logger.error(f"Loop {i} exceeded the time limit and was skipped")
                continue

        scores = temp_scores
        nums = temp_nums

    new_df = eval_df.iloc[base_len:].copy()
    for col in ["input_tokens", "output_tokens", "total_tokens", "runtime_sec"]:
        if col in new_df.columns:
            new_df[col] = pd.to_numeric(new_df[col], errors="coerce")
    new_df = new_df.dropna(subset=["input_tokens", "output_tokens", "total_tokens", "runtime_sec"])

    if len(new_df) > 0:
        avg_input = new_df["input_tokens"].mean()
        avg_output = new_df["output_tokens"].mean()
        avg_total = new_df["total_tokens"].mean()
        avg_runtime = new_df["runtime_sec"].mean()

        logger.info(f"Average input tokens: {avg_input}")
        logger.info(f"Average output tokens: {avg_output}")
        logger.info(f"Average total tokens: {avg_total}")
        logger.info(f"Average runtime (sec): {avg_runtime}")

        if "score" in new_df.columns:
            score_series = pd.to_numeric(new_df["score"], errors="coerce").dropna()
            if len(score_series) > 0:
                logger.info(f"Average score: {score_series.mean()}")
            else:
                logger.info("Average score: N/A (no numeric scores in this run)")
        else:
            logger.info("Average score: N/A (score column not found)")

        for col in METRIC_COLS:
            if col in new_df.columns:
                series = pd.to_numeric(new_df[col], errors="coerce").dropna()
                if len(series) > 0:
                    logger.info(f"Average {col}: {float(series.mean()):.4f}")
                else:
                    logger.info(f"Average {col}: N/A")

        summary_file = result_dir / f"summary-agent-{args.tag}-{model_name}.csv"
        summary_file.parent.mkdir(parents=True, exist_ok=True)

        def _get_mean(col_name):
            if col_name in new_df.columns:
                series = pd.to_numeric(new_df[col_name], errors="coerce").dropna()
                if len(series) > 0:
                    return float(series.mean())
            return None

        summary_row = pd.DataFrame(
            [
                {
                    "uid": uid,
                    "dataset": dataset,
                    "tag": args.tag,
                    "model": model_name,
                    "cases": int(len(new_df)),
                    "component_acc@1": _get_mean("component_acc@1"),
                    "component_acc@2": _get_mean("component_acc@2"),
                    "component_acc@3": _get_mean("component_acc@3"),
                    "component_acc@4": _get_mean("component_acc@4"),
                    "component_acc@5": _get_mean("component_acc@5"),
                    "component_mrr": _get_mean("component_mrr"),
                    "component_avg@3": _get_mean("component_avg@3"),
                    "component_avg@5": _get_mean("component_avg@5"),
                }
            ]
        )

        if summary_file.exists():
            summary_row.to_csv(summary_file, mode="a", header=False, index=False)
        else:
            summary_row.to_csv(summary_file, index=False)

        logger.info(f"Run summary has been saved to {summary_file}")
    else:
        logger.info("No completed cases were recorded for token/time averaging.")
        logger.info("Average score: N/A")
        logger.info("Average component_acc@1/3/5, MRR: N/A")


if __name__ == "__main__":
    uid = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str, default="Bank")
    parser.add_argument("--sample_num", type=int, default=1)
    parser.add_argument("--start_idx", type=int, default=0)
    parser.add_argument("--end_idx", type=int, default=150)
    parser.add_argument("--controller_max_step", type=int, default=5)
    parser.add_argument("--controller_max_turn", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--tag", type=str, default="rca")
    parser.add_argument("--auto", action="store_true")

    args = parser.parse_args()

    if args.auto:
        print(f"Auto mode is on. Model is fixed to {configs['MODEL']}")
        for dataset_name in ["Market/cloudbed-1", "Market/cloudbed-2", "Bank", "Telecom"]:
            main(args, uid, dataset_name)
    else:
        main(args, uid, args.dataset)
