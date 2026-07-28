# RCAEval: A Benchmark for Root Cause Analysis of Microservice Systems

RCAEval is an open-source benchmark that offers three datasets (RE1, RE2, RE3) and an evaluation framework for root cause analysis (RCA) in microservice systems. It includes 15 reproducible baselines covering metric-based, trace-based, and multi-source RCA methods.

## Prerequisites

We recommend using machines equipped with at least 8 cores, 16GB RAM, and ~50GB available disk space with Ubuntu 22.04 or Ubuntu 20.04, and Python3.10 or above.

## Installation

Open your terminal and run the following commands

```bash
sudo apt update -y
sudo apt install -y build-essential \
  libxml2 libxml2-dev zlib1g-dev \
  python3-tk graphviz
```

Clone RCAEval from GitHub

```bash
git clone https://github.com/phamquiluan/RCAEval.git && cd RCAEval
```

Create virtual environment with Python 3.10+.

```bash
python3.10 -m venv env
. env/bin/activate
```

## How-to-use

### Data format

The telemetry data must be presented as `pandas.DataFrame`. We require the data to have a column named `time` that stores the timestep. A sample of valid data could be downloaded using the `download_data()` or `download_multi_source_data()` method that we will demonstrate shortly below.

## Available Datasets

RCAEval benchmark includes three datasets: RE1, RE2, and RE3, designed to comprehensively support benchmarking RCA in microservice systems. Together, our three datasets feature 735 failure cases collected from three microservice systems (Online Boutique, Sock Shop, and Train Ticket) and including 11 fault types. Each failure case also includes annotated root cause service and root cause indicator (e.g., specific metric or log indicating the root cause). The statistics of the datasets are presented in the Table below.

|   Dataset   |   Systems  |   Fault Types            |   Cases  |   Metrics  |   Logs (millions)  |   Traces (millions)  |
|-------------|------------|--------------------------|----------|------------|--------------------|----------------------|
|   RE1       |   3        |   3 Resource, 2 Network  |   375    |   49-212   |   N/A              |   N/A                |
|   RE2       |   3        |   4 Resource, 2 Network  |   270    |   77-376   |   8.6-26.9         |   39.6-76.7          |
|   RE3       |   3        |   5 Code-level           |   90     |   68-322   |   1.7-2.7          |   4.5-4.7            |

Our datasets and their description are publicly available in Zenodo repository with the following information:
- Dataset DOI: https://doi.org/10.5281/zenodo.14590730
- Dataset URL: [https://zenodo.org/records/14590730](https://zenodo.org/records/14590730)

We also provide utility functions to download our datasets using Python. The downloaded datasets will be available at directory `data`.

```python
from RCAEval.utility import (
    download_re1_dataset,
    download_re2_dataset,
    download_re3_dataset,
)

download_re1_dataset()
download_re2_dataset()
download_re3_dataset()
```
<details>
<summary>Expected output after running the above code (it takes half an hour to download and extract the datasets. )</summary>

```
$ python test.py
Downloading RE1.zip..: 100%|█████████████████████| 390M/390M [01:02<00:00, 6.22MiB/s]
Downloading RE2.zip..: 100%|███████████████████| 4.21G/4.21G [11:23<00:00, 6.17MiB/s]
Downloading RE3.zip..: 100%|█████████████████████| 534M/534M [01:29<00:00, 5.97MiB/s]
```
</details>


## Available Baselines 

RCAEval stores all the RCA methods in the `e2e` module (implemented in `RCAEval.e2e`). There are 15 RCA baselines available: RUN, CausalRCA, CIRCA, RCD, MicroCause, EasyRCA, MSCRED, BARO, 𝜖-Diagnosis, TraceRCA, MicroRank, PDiagnose, Multi-source BARO, Multi-source RCD, Multi-source CIRCA.

## Reproducibility

### RCAEval Benchmark Paper

We provide a script named `main.py` to assist in reproducing the results from [our RCAEval paper](https://arxiv.org/pdf/2412.17015). This script can be executed using Python with the following syntax: 

```
python main.py [-h] [--dataset DATASET] [--method METHOD]
```

The available options and their descriptions are as follows:

```
options:
  -h, --help            Show this help message and exit
  --dataset DATASET     Choose a dataset. Valid options:
                        [re2-ob, re2-ss, re2-tt, etc.]
  --method METHOD       Choose a method (`causalrca`, `microcause`, `e_diagnosis`, `baro`, `rcd`, `circa`, etc.)
```

For example, in Table 6, BARO achieves Avg@5 of 0.72, 0.99, 1, 0.83, 0.64, and 0.8 for CPU, MEM, DISK, SOCKET, DELAY, LOSS, and AVERAGE on the Train Ticket dataset. To reproduce these results, you can run the following commands:

```bash
python  main.py --method baro --dataset re2-tt
```

The expected output should be exactly as presented in the paper (it takes less than 1 minute to run the code)

```
$ python  main.py --method baro --dataset re2-tt --length 20
100%|███████████████████████████████████████████████████████████████████████████████████████████████████████████████████████████| 90/90 [00:45<00:00,  1.98it/s]
--- Evaluation results ---
Avg@5-CPU:   0.72
Avg@5-MEM:   0.99
Avg@5-DISK:  1.0
Avg@5-SOCKET: 0.83
Avg@5-DELAY: 0.63
Avg@5-LOSS:  0.64
---
Avg speed: 0.51
```

We can replace the baro method with other methods (e.g., circa) and substitute re2-tt with other datasets to replicate the corresponding results shown in Table 6. This reproduction process is also integrated into our Continuous Integration (CI) setup. For more details, refer to the [.circleci/config.yml](.circleci/config.yml) file.

## Licensing

This repository includes code from various sources with different licenses. We have included their corresponding LICENSE into the [LICENSES](LICENSES) directory:

- **BARO**: Licensed under the [MIT License](LICENSES/LICENSE-BARO). Original source: [BARO GitHub Repository](https://github.com/phamquiluan/baro/blob/main/LICENSE).
- **CausalRCA**: No License. Original source: [CausalRCA GitHub Repository](https://github.com/AXinx/CausalRCA_code).
- **CIRCA**: Licensed under the [BSD 3-Clause License](LICENSES/LICENSE-CIRCA). Original source: [CIRCA GitHub Repository](https://github.com/NetManAIOps/CIRCA/blob/master/LICENSE).
- **E-Diagnosis**: Licensed under the [BSD 3-Clause License](LICENSES/LICENSE-E-Diagnosis). Original source: [PyRCA GitHub Repository](https://github.com/salesforce/PyRCA/blob/main/LICENSE).
- **MicroCause**: Licensed under the [Apache License 2.0](LICENSES/LICENSE-MicroCause). Original source: [MicroCause GitHub Repository](https://github.com/PanYicheng/dycause_rca/blob/main/LICENSE).
- **RCD**: Licensed under the [MIT License](LICENSES/LICENSE-RCD). Original source: [RCD GitHub Repository](https://github.com/azamikram/rcd).
- **RUN**: No License. Original source: [RUN GitHub Repository](https://github.com/zmlin1998/RUN).

**For the code implemented by us and for our datasets, we distribute them under the [MIT LICENSE](LICENSE)**.

## Acknowledgments

We would like to express our sincere gratitude to the researchers and developers who created the baselines used in our study. Their work has been instrumental in making this project possible. We deeply appreciate the time, effort, and expertise that have gone into developing and maintaining these resources. This project would not have been feasible without their contributions.
