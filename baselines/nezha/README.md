# Nezha RE2 Benchmark

This repository contains the Nezha codebase together with a cleaned RE2 benchmark workflow for:

- `RE2-OB` (OnlineBoutique)
- `RE2-TT` (TrainTicket)

The recommended workflow is:

1. prepare each RE2 dataset into a benchmark-ready Nezha-style case layout
2. run the benchmark on the prepared case layout
3. read the ranking metrics and per-case timing results from the output JSON

This ordering keeps the workflow easy to inspect, reproduce, and present.

## What You Need To Set Manually

Before running the workflow, decide these paths on your machine:

- `RE2_OB_PATH`: the raw `RE2-OB` dataset root
- `RE2_TT_PATH`: the raw `RE2-TT` dataset root
- `PREPARED_OB_ROOT`: where you want the prepared `RE2-OB` case layout to be written
- `PREPARED_TT_ROOT`: where you want the prepared `RE2-TT` case layout to be written
- `RESULTS_DIR`: where you want benchmark result JSON files to be written

Use placeholder-style paths such as:

- `the/path/to/RE2-OB`
- `the/path/to/RE2-TT`
- `the/path/to/prepared/re2-ob`
- `the/path/to/prepared/re2-tt`
- `the/path/to/results`

Optional benchmark parameters:

- `WINDOW_MINUTES`: abnormal window size before and after `inject_time`. Default: `3`
- `CASE_LIMIT`: optional limit for smoke tests. Omit it for the full benchmark

## Requirements

- Python 3.8+ recommended
- `pip`

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Bundled Drain3 State

The repository already includes the Drain3 state and config files used by the RE2 benchmark:

- `log_template/hipster.bin`
- `log_template/drain3_hipster.ini`
- `log_template/ts.bin`
- `log_template/drain3_ts.ini`

During evaluation, the benchmark automatically selects:

- `hipster.bin` for `RE2-OB`
- `ts.bin` for `RE2-TT`

A runtime copy of the selected `.bin` file is used during execution, so the checked-in Drain3 state is not overwritten.

## End-to-End Workflow

## Step 1: Prepare A Benchmark-Ready Case Layout

Start by preparing each RE2 dataset into a Nezha-style case layout.

At a high level, the preparation step:

1. discovers case folders with the shape `{service}_{fault_type}/{subcase}`
2. reads `inject_time.txt`, `logs.csv`, `traces.csv`, and `simple_metrics.csv`
3. computes:
   - a normal window from dataset start to one minute before the abnormal window
   - an abnormal window from `inject_time - WINDOW_MINUTES` to `inject_time + WINDOW_MINUTES`
4. slices logs and traces into normal-phase and abnormal-phase data
5. converts logs, traces, and metrics into a Nezha-style per-case layout
6. writes case-level metadata and a dataset-level index

This step is useful for:

- verifying case discovery
- checking window slicing
- inspecting converted log and trace tables
- checking service-to-pod naming consistency
- archiving a case-organized intermediate representation for reports or demos

### Input Case Shape

Each raw case is expected to have the structure:

```text
{service}_{fault_type}/{subcase}
  inject_time.txt
  logs.csv
  traces.csv
  simple_metrics.csv
```

### Prepared Output Layout

For each prepared case, the output layout is:

```text
the/path/to/prepared/re2-ob/
  checkoutservice_cpu/
    1/
      construct_data/
        YYYY-MM-DD/
          log/
          trace/
          traceid/
          metric/
      rca_data/
        YYYY-MM-DD/
          log/
          trace/
          traceid/
          metric/
      case_metadata.json
  ...
  index.json
```

Where:

- `construct_data/` stores the normal-phase slice
- `rca_data/` stores the abnormal-phase slice
- `case_metadata.json` stores the case identity, time windows, counts, and pod mapping
- `index.json` stores the summary of all prepared cases under the output root

### Prepare `RE2-OB`

```bash
python convert_re2_to_nezha.py \
  --dataset re2-ob \
  --data-path the/path/to/RE2-OB \
  --output-root the/path/to/prepared/re2-ob \
  --window 3
```

### Prepare `RE2-TT`

```bash
python convert_re2_to_nezha.py \
  --dataset re2-tt \
  --data-path the/path/to/RE2-TT \
  --output-root the/path/to/prepared/re2-tt \
  --window 3
```

The preparation command writes one folder per case and a root-level `index.json` summary.

### Notes On Log-To-Trace Matching

The preparation step uses a heuristic to associate logs with traces because the raw RE2 logs do not include a direct `SpanID` field.

The matching flow is:

1. read `container_name` from `logs.csv`
2. filter `traces.csv` rows whose `serviceName` matches that service
3. compare the log timestamp with candidate trace start times
4. assign the closest `spanID` to that log row in the prepared output

## Step 2: Run The Benchmark

After the case layout has been prepared, run the benchmark on the prepared dataset root.

### Run `RE2-OB`

```bash
python main_re2_new.py \
  --dataset re2-ob \
  --data-path the/path/to/prepared/re2-ob \
  --results-file the/path/to/results/re2-ob_evaluation_results.json
```

### Run `RE2-TT`

```bash
python main_re2_new.py \
  --dataset re2-tt \
  --data-path the/path/to/prepared/re2-tt \
  --results-file the/path/to/results/re2-tt_evaluation_results.json
```

Notes:

- Add `--case-limit N` for a quick smoke test

## Optional: Use Environment Variables Instead Of `--data-path`

The benchmark also supports dataset-root environment variables:

- `NEZHA_RE2_OB_PATH`
- `NEZHA_RE2_TT_PATH`

When using this option, point the variables to the prepared dataset roots.

Example:

```powershell
$env:NEZHA_RE2_OB_PATH = "the/path/to/prepared/re2-ob"
python main_re2_new.py --dataset re2-ob --results-file the/path/to/results/re2-ob_evaluation_results.json
```

```powershell
$env:NEZHA_RE2_TT_PATH = "the/path/to/prepared/re2-tt"
python main_re2_new.py --dataset re2-tt --results-file the/path/to/results/re2-tt_evaluation_results.json
```

## What The Benchmark Produces

Each run computes the following ranking metrics:

- `MRR`
- `acc@1`
- `acc@2`
- `acc@3`
- `acc@4`
- `acc@5`
- `avg@3`
- `avg@5`

It also records timing data:

- total runtime
- average runtime per case
- minimum case runtime
- maximum case runtime
- runtime standard deviation
- runtime for every individual case

## Output Files

Each benchmark run produces:

- a JSON summary file, for example `the/path/to/results/re2-ob_evaluation_results.json`
- a log file under `log/`, unless overridden with `--log-file`

The JSON file includes:

- `metrics`
- `ranks`
- `timing`
- `cases`

The `timing.per_case` section is the easiest place to inspect every case runtime and verify the reported average runtime.

## Benchmark Logic

The evaluation flow is:

1. load all prepared cases from the dataset root
2. read the prepared normal-phase and abnormal-phase data for each case
3. extract event patterns from traces and logs
4. compare normal and abnormal pattern supports
5. rank suspicious services
6. compute final ranking metrics from the root-cause service ranks
7. record per-case runtime and aggregate timing statistics

## Useful Commands

Show CLI help:

```bash
python main_re2_new.py --help
python convert_re2_to_nezha.py --help
```

Run a quick smoke test on the first 5 cases:

```bash
python main_re2_new.py \
  --dataset re2-ob \
  --data-path the/path/to/prepared/re2-ob \
  --case-limit 5 \
  --results-file the/path/to/results/re2-ob_smoke.json
```

## Repository Scope Note

Legacy Nezha workflow files remain in the repository. For the RE2 benchmark, follow the two-step workflow documented above: prepare the dataset first, then run evaluation on the prepared case layout.
