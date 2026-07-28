# Baselines

This directory holds the third-party baseline frameworks used in the GALA evaluation:

- `baselines/RCAEval`
- `baselines/mabc`
- `baselines/openrca`
- `baselines/nezha`

Each subproject keeps its own README, requirements file, and license. Refer to those for
framework-specific options and environment variables.

Paths below assume the RCAEval RE2 datasets have been downloaded to `data/RCAEval/data` (see
the main [README](../README.md)).

## 1) RCAEval baselines

RCAEval ships its own runner and evaluation utilities under `baselines/RCAEval`. Follow
[baselines/RCAEval/README.md](RCAEval/README.md) for installation and for running the
metric-based, trace-based, and multi-source baselines (BARO, CIRCA, RCD, TraceRCA,
PC/LiNGAM/Granger-PageRank, and others) on RE2-OB and RE2-TT.

## 2) mABC

Run from `baselines/mabc`:

```bash
cd baselines/mabc

# RE2-OB
python batch_rcaeval.py --dataset RE2-OB --prepare-only
python batch_rcaeval.py --dataset RE2-OB

# RE2-TT
python batch_rcaeval_tt.py --prepare-only
python batch_rcaeval_tt.py
```

## 3) OpenRCA

Run from `baselines/openrca`:

```bash
# Configure provider and RE2 paths first (see baselines/openrca/README.md), then run:
python rca/run_agent_standard.py --dataset RE2-OB --start_idx 0 --end_idx 89
python rca/run_agent_standard.py --dataset RE2-TT --start_idx 0 --end_idx 89

# Aggregate stats
python calc_stats.py RE2-OB
python calc_stats.py RE2-TT
```

## 4) Nezha

Run from `baselines/nezha`:

```bash
# Prepare RE2-OB / RE2-TT case layout
python convert_re2_to_nezha.py --dataset re2-ob --data-path ../../data/RCAEval/data/RE2/RE2-OB --output-root ./prepared/re2-ob --window 3
python convert_re2_to_nezha.py --dataset re2-tt --data-path ../../data/RCAEval/data/RE2/RE2-TT --output-root ./prepared/re2-tt --window 3

# Run benchmark
python main_re2_new.py --dataset re2-ob --data-path ./prepared/re2-ob --results-file ./results/re2-ob_evaluation_results.json
python main_re2_new.py --dataset re2-tt --data-path ./prepared/re2-tt --results-file ./results/re2-tt_evaluation_results.json
```

## 5) Flow-of-action

**Not distributed in this repository.** Please contact the original authors of Flow-of-action
to obtain the implementation.
