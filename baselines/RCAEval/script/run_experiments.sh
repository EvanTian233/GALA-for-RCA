# run_experiments.sh

DATASETS=("re1-ob" "re1-ss" "re1-tt" "re2-ob" "re2-ss" "re2-tt" "re3-ob" "re3-ss" "re3-tt")
METHODS=("causalrca" "baro" "pc_pagerank" "pc_randomwalk" "lingam_pagerank" "lingam_randomwalk" "granger_pagerank" "granger_randomwalk" "tracerca")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PARENT_DIR" || exit

for DATASET_NAME in "${DATASETS[@]}"; do
    for METHOD_NAME in "${METHODS[@]}"; do
        echo "Running experiment with method: ${METHOD_NAME} on dataset: ${DATASET_NAME}"
        python main.py --method "${METHOD_NAME}" --dataset "${DATASET_NAME}"
        # python main.py --method "${METHOD_NAME}" --dataset "${DATASET_NAME}" --gala
    done
done
