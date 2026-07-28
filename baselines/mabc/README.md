# mABC - Multi-Agent Based Root Cause Analysis

A multi-agent system for root cause analysis (RCA) in distributed microservices systems. This framework uses LLM-based agents to analyze trace data, metrics, and topology information to identify the root cause of service anomalies.

## Table of Contents

- [Overview](#overview)
- [Directory Structure](#directory-structure)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
  - [Paths and environment variables](#paths-and-environment-variables)
- [Supported Datasets](#supported-datasets)
- [Running Benchmark Tests](#running-benchmark-tests)
  - [RE2-OB (Online Boutique)](#re2-ob-online-boutique)
  - [RE2-TT (TrainTicket)](#re2-tt-trainticket)
- [Evaluation Metrics](#evaluation-metrics)
- [Token Usage Statistics](#token-usage-statistics)
- [Output Files](#output-files)

## Overview

mABC employs multiple specialized agents working together to perform root cause analysis:

- **ProcessScheduler**: Orchestrates the overall RCA workflow
- **DataDetective**: Queries and analyzes endpoint metrics
- **DependencyExplorer**: Explores service dependencies via trace data
- **ProbabilityOracle**: Assesses fault probability based on metrics
- **FaultMapper**: Maps detected anomalies to potential root causes
- **AlertReceiver**: Processes incoming alerts
- **SolutionEngineer**: Generates final root cause analysis report

## Directory Structure

```
mABC/
├── main/
│   └── main.py              # Main entry point for single case RCA
├── agents/
│   ├── base/
│   │   ├── profile.py       # Agent profiles and configurations
│   │   └── run.py           # ReAct and ThreeHotCot execution logic
│   └── tools/               # Agent-specific tools
├── handle/
│   ├── metric_collect.py    # Metric data collection
│   └── trace_collect.py     # Trace data collection
├── utils/
│   ├── llm.py               # LLM API interface
│   └── logger.py            # Logging utilities
├── batch_rcaeval.py         # Batch processor for RE2-OB dataset
├── batch_rcaeval_tt.py      # Batch processor for RE2-TT dataset
├── config_paths.py          # Path helpers and env-based defaults (no hardcoded machine paths)
├── eval_re2_ob.py           # Evaluation script for RE2-OB
├── eval_generic.py          # Generic evaluation script
├── settings.py              # API keys and model configuration
└── output/                  # Output directory for results
```

## Prerequisites

1. **Python 3.10+**
2. **Required packages**:
   ```bash
   pip install pandas numpy openai tqdm matplotlib
   ```
3. **OpenAI API Key**: Configure in `settings.py`

## Configuration

### Paths and environment variables

The code does **not** embed machine-specific paths. Resolve data locations in one of these ways:

| Variable / mechanism | Used by | Meaning |
|----------------------|---------|---------|
| **`MABC_BENCHMARK_DATA_ROOT`** | `batch_rcaeval.py`, `batch_rcaeval_tt.py`, | Absolute path to **your** unpacked RE2 (any location). **Default when unset:** `<mABC>/data` (i.e. a `data` folder next to `main/`, `agents/`, … inside the mABC tree). No assumption about a sibling project or vendor-specific folder names. |
| **CLI overrides** | See table below | Per-run path overrides without editing code. |

Central definitions live in **`config_paths.py`** (`MABC_ROOT`, path helpers).

**CLI overrides (no env needed):**

| Script | Flag | Purpose |
|--------|------|---------|
| `batch_rcaeval.py` | `--benchmark-data-root DIR` | Same as setting `MABC_BENCHMARK_DATA_ROOT` for that run. |
| `batch_rcaeval_tt.py` | `--data-root DIR` | Override RE2-TT dataset root only. |

**Layout under the default benchmark root (`<mABC>/data`):**

- RE2-OB / RE2-TT: `RE2/RE2-OB/...`, `RE2/RE2-TT/RE2-TT/...`

Unpack or symlink benchmarks to match the above; paths need not mirror any particular upstream repository name.


### API Configuration (`settings.py`)

```python
OPENAI_API_KEY = "your-api-key-here"
OPENAI_MODEL = "gpt-4.1-mini"  # or "gpt-4", "gpt-3.5-turbo"
OPENAI_MAX_RETRIES = 10
OPENAI_RETRY_SLEEP = 30
```

### Root Cause Whitelist (`main/main.py`)

Before running tests, configure the `ROOTCAUSE_WHITELIST` in `main/main.py` to match your target dataset:

**For RE2-OB (Online Boutique):**
```python
ROOTCAUSE_WHITELIST = [
    "adservice", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "frontend", "paymentservice", "productcatalogservice",
    "recommendationservice", "shippingservice",
]
```

**For RE2-TT (TrainTicket):**
```python
ROOTCAUSE_WHITELIST = [
    "ts-auth-service", "ts-order-service", "ts-travel-service",
    "ts-seat-service", "ts-route-service", # ... (see main.py for full list)
]
```


## Supported Datasets

| Dataset | Description | Has inject_time | Time Window |
|---------|-------------|-----------------|-------------|
| RE2-OB | Online Boutique microservices | Yes | Based on inject_time |
| RE2-TT | TrainTicket microservices | Yes | Based on inject_time |

## Running Benchmark Tests

### RE2-OB (Online Boutique)

1. **Configure whitelist** in `main/main.py` for RE2-OB services

2. **Prepare data** (first run - builds cache files):
   ```bash
   cd mABC
   python batch_rcaeval.py --dataset RE2-OB --prepare-only
   ```

3. **Run RCA with LLM** (second run):
   ```bash
   python batch_rcaeval.py --dataset RE2-OB
   ```

4. **Evaluate results**:
   ```bash
   python eval_re2_ob.py
   ```

**Options:**
- `--benchmark-data-root DIR`: Override `MABC_BENCHMARK_DATA_ROOT` for this run (parent of `RE2/`, `dataset3/`, …)
- `--prepare-only`: Only build cache files, don't run LLM
- `--force-rebuild`: Force rebuild all cache files
- `--max-cases N`: Process at most N cases
- `--skip-completed`: Skip already completed cases (for resuming)
- `--model-tag TAG`: Add tag to output directory (e.g., `gpt4.1mini`)

### RE2-TT (TrainTicket)

1. **Configure whitelist** in `main/main.py` for RE2-TT services

2. **Prepare and run**:
   ```bash
   python batch_rcaeval_tt.py --prepare-only
   python batch_rcaeval_tt.py
   ```

3. **Evaluate results**:
   ```bash
   python eval_generic.py --output-dir output/RE2-TT
   ```

## Evaluation Metrics

After running the RCA pipeline, evaluation scripts compute the following metrics:

| Metric | Description |
|--------|-------------|
| **acc@1** | Accuracy if true root cause is in top-1 prediction |
| **acc@2** | Accuracy if true root cause is in top-2 predictions |
| **acc@3** | Accuracy if true root cause is in top-3 predictions |
| **acc@4** | Accuracy if true root cause is in top-4 predictions |
| **acc@5** | Accuracy if true root cause is in top-5 predictions |
| **avg@3** | Average of acc@1, acc@2, acc@3 |
| **avg@5** | Average of acc@1 through acc@5 |
| **MRR** | Mean Reciprocal Rank |

**Example evaluation output:**
```
============== EVALUATION SUMMARY ==============
  Total cases: 41
  MRR:   0.5234
  acc@1: 0.3415
  acc@2: 0.6098
  acc@3: 0.7805
  acc@4: 0.8780
  acc@5: 0.9268
  avg@3: 0.5773
  avg@5: 0.7073
```

## Token Usage Statistics

Token usage is tracked per case and summarized at batch level.

### Per-Case Files

Each case generates `llm_usage.jsonl` and `llm_usage_summary.json`:

```json
{
  "calls": 15,
  "input_tokens": 12500,
  "output_tokens": 2300,
  "total_tokens": 14800,
  "elapsed_seconds": 45.23
}
```

### Batch Summary

After batch completion, `batch_usage_summary.json` is generated:

```json
{
  "dataset": "RE2-OB",
  "llm_cases_time": 36,
  "avg_elapsed_seconds": 42.5,
  "avg_total_tokens": 15200,
  "sum_total_tokens": 547200
}
```

### Viewing Token Statistics

```bash
# View batch summary
cat output/RE2-OB/batch_usage_summary.json

# View specific case usage
cat output/RE2-OB/checkoutservice_cpu/1/llm_usage_summary.json
```

## Output Files

For each processed case, the following files are generated:

| File | Description |
|------|-------------|
| `label.json` | Ground truth label (alerting endpoint and time) |
| `endpoint_stats.json` | Per-endpoint per-minute metrics |
| `endpoint_maps.json` | Service dependency topology |
| `main_stdout.txt` | Full LLM conversation and analysis |
| `main_stderr.txt` | Error output (if any) |
| `llm_usage.jsonl` | Raw LLM API usage logs |
| `llm_usage_summary.json` | Aggregated token usage |

### Evaluation Output

| File | Description |
|------|-------------|
| `eval_summary.json` | Per-case results with hit@k scores |
| `eval_summary_dataset3.json` | Dataset3-specific evaluation |
| `batch_usage_summary.json` | Batch-level token statistics |

## Troubleshooting

### Common Issues

1. **"ROOTCAUSE_WHITELIST" mismatch**: Ensure the whitelist in `main/main.py` matches your target dataset.

2. **Missing cache files**: Run with `--prepare-only` first to build cache, then run again without it.

3. **API rate limits**: Adjust `OPENAI_MAX_RETRIES` and `OPENAI_RETRY_SLEEP` in `settings.py`.

4. **Resume interrupted batch**: Use `--skip-completed` flag to skip already processed cases.

5. **Force rebuild cache**: Use `--force-rebuild` to regenerate all cache files.

## Complete Workflow Example

```bash
# Step 1: Configure settings.py with your API key
# Step 2: Configure ROOTCAUSE_WHITELIST in main/main.py for your dataset

# Step 3: Prepare data (first run)
python batch_rcaeval.py --dataset RE2-OB --prepare-only

# Step 4: Run RCA (second run)
python batch_rcaeval.py --dataset RE2-OB

# Step 5: Evaluate results
python eval_re2_ob.py

# Step 6: View token statistics
cat output/RE2-OB/batch_usage_summary.json

# Step 7: View evaluation metrics
cat output/RE2-OB/eval_summary.json
```
