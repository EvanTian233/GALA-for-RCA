# OpenRCA Benchmark Runner

This repository is organized for reviewer-facing benchmark execution. The primary entrypoints are:

- `python rca/run_agent_standard.py ...` to run RCA-Agent on a dataset
- `python calc_stats.py <dataset>` to aggregate token usage and ranking metrics
- `python scripts/prepare_dataset3.py ...` only if you need to rebuild the anonymized `Dataset3` files from raw source data

## 1. Environment Setup

OpenRCA requires Python `>=3.10`.

```bash
pip install -r requirements.txt
```

## 2. Manual Configuration

### 2.1 LLM API settings

Edit `rca/api_config.yaml` and set:

- `SOURCE`: provider name such as `OpenAI`, `AI`, `Google`, or `Anthropic`
- `MODEL`: model id
- `API_KEY`: your real API key
- `API_BASE`: only needed for OpenAI-compatible third-party endpoints

The file intentionally ships with placeholders. Do not leave `API_KEY` as `the/api/key`.

### 2.2 External dataset roots

Edit `rca/benchmark_paths.yaml` and replace the placeholders with your real local paths:

```yaml
DATASET_ROOTS:
  RE2-OB: "the/path/to/RE2-OB"
  RE2-TT: "the/path/to/RE2-TT/RE2-TT"
```

You can also provide the same values with environment variables:

```bash
RE2_OB_ROOT=the/path/to/RE2-OB
RE2_TT_ROOT=the/path/to/RE2-TT/RE2-TT
```

### 2.3 Dataset locations used by this repository

- `Bank` is the OpenRCA dataset already stored under `dataset/Bank`
- `Dataset3` is the anonymized dataset already stored under `dataset/Dataset3`
- `RE2-OB` and `RE2-TT` use the external roots configured in `rca/benchmark_paths.yaml`

`Dataset3/query.csv` intentionally contains the placeholder `__DATASET3_ROOT__`. The runner replaces it automatically at runtime, so reviewers do not need to edit that file.

## 3. Canonical Run Commands

Run from the repository root:

### OpenRCA (Bank, 136 cases)

```bash
python rca/run_agent_standard.py --dataset Bank --start_idx 0 --end_idx 135
```

### Dataset3 (41 cases)

```bash
python rca/run_agent_standard.py --dataset Dataset3 --start_idx 0 --end_idx 40
```

### RE2-OB (90 cases)

```bash
python rca/run_agent_standard.py --dataset RE2-OB --start_idx 0 --end_idx 89
```

### RE2-TT (90 cases)

```bash
python rca/run_agent_standard.py --dataset RE2-TT --start_idx 0 --end_idx 89
```

### Optional runtime arguments

```bash
--timeout 600
--controller_max_step 5
--controller_max_turn 5
--sample_num 1
--tag rca
```

## 4. Output Files

For each dataset run, the main outputs are:

- Per-case predictions: `test/result/<dataset>/agent-<tag>-<model>.csv`
- Per-run metric summary: `test/result/<dataset>/summary-agent-<tag>-<model>.csv`
- Execution logs, trajectories, prompts: `test/monitor/<dataset>/agent-<tag>-<model>/<timestamp>/`

The per-case result CSV includes:

- token columns: `input_tokens`, `output_tokens`, `total_tokens`
- runtime column: `runtime_sec`
- ranking columns: `component_acc@1`, `component_acc@2`, `component_acc@3`, `component_acc@4`, `component_acc@5`, `component_mrr`, `component_avg@3`, `component_avg@5`

## 5. Generate Final Benchmark Statistics

After each dataset run, aggregate the final statistics with:

```bash
python calc_stats.py Bank
python calc_stats.py Dataset3
python calc_stats.py RE2-OB
python calc_stats.py RE2-TT
```

If a dataset directory contains multiple `agent-*.csv` files, `calc_stats.py` uses the newest one by default. You can also specify an explicit CSV:

```bash
python calc_stats.py Dataset3 --csv test/result/Dataset3/agent-rca-gpt-4.1-mini.csv
```

The aggregated file is always written to:

- `test/result/<dataset>/statistics-summary.csv`

`statistics-summary.csv` contains:

- average and total token usage
- average and total runtime
- `component_acc@1` through `component_acc@5`
- `component_mrr`
- `component_avg@3`
- `component_avg@5`
- score distribution counts

## 6. Full End-to-End Workflow

```bash
cd /the/path/to/OpenRCA

# 1. Run OpenRCA (Bank)
python rca/run_agent_standard.py --dataset Bank --start_idx 0 --end_idx 135

# 2. Run Dataset3
python rca/run_agent_standard.py --dataset Dataset3 --start_idx 0 --end_idx 40

# 3. Run RE2-OB
python rca/run_agent_standard.py --dataset RE2-OB --start_idx 0 --end_idx 89

# 4. Run RE2-TT
python rca/run_agent_standard.py --dataset RE2-TT --start_idx 0 --end_idx 89

# 5. Aggregate final statistics
python calc_stats.py Bank
python calc_stats.py Dataset3
python calc_stats.py RE2-OB
python calc_stats.py RE2-TT
```

After step 5, the final reviewer-facing outputs are the four `statistics-summary.csv` files under `test/result/`.

## 7. Rebuild Dataset3 (Optional)

Only use this if you need to reconstruct the anonymized `Dataset3` benchmark files from a raw labeled source directory.

```bash
python scripts/prepare_dataset3.py --source-root the/path/to/dataset3/dataset --overwrite
```

Verification only:

```bash
python scripts/prepare_dataset3.py --verify-only
```

This script regenerates:

- `dataset/Dataset3/query.csv`
- `dataset/Dataset3/record.csv`
- `dataset/Dataset3/case_mapping.csv`
- `dataset/Dataset3/data/`

## 8. Auxiliary Utilities

These scripts are not required for the main benchmark flow, but remain available:

- `python rca/eval_result.py --csv the/path/to/agent-result.csv`
- `python rca/time_eval.py --csv the/path/to/agent-result.csv`
- `python re2/extract_re2_ob_inject_times.py --root the/path/to/RE2-OB`
- `python re2/extract_re2_tt_inject_times.py --root the/path/to/RE2-TT/RE2-TT`
