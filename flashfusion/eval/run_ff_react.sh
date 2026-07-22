#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
fi

PYTHON="${PYTHON:-python}"
BASELINES="REACT_ONLY,FLASH_FUSION"
# BASELINES="FLASH_FUSION"
MODEL="${MODEL:-meta-llama/llama-3.3-70b-instruct}"
STAGE12_MODEL="${STAGE12_MODEL:-meta-llama/llama-3.1-8b-instruct}"
RUNS="${RUNS:-3}"
# RUNS="${RUNS:-1}"
OUTPUT_ROOT="${OUTPUT_ROOT:-flashfusion/results/ff_newlook_with_react}"
# OUTPUT_ROOT="${OUTPUT_ROOT:-flashfusion/results/trial_ff}"

run_dataset() {
    local dataset="$1"
    local data_path="$2"
    local ground_truth="$3"

    "${PYTHON}" -m flashfusion.eval.benchmark \
        --dataset "${dataset}" \
        --data "${data_path}" \
        --baselines "${BASELINES}" \
        --queries all \
        --runs "${RUNS}" \
        --model "${MODEL}" \
        --stage12-model "${STAGE12_MODEL}" \
        --ground-truth "${ground_truth}" \
        --output "${OUTPUT_ROOT}/${dataset}"
}

run_dataset \
    wisdm \
    data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt \
    flashfusion/eval/ground_truth/ground_truth_wisdm.json

run_dataset \
    bus \
    data/bus/bus_data_enriched_behavior.csv \
    flashfusion/eval/ground_truth/ground_truth_bus.json

run_dataset \
    mit_ecg \
    data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt \
    flashfusion/eval/ground_truth/ground_truth_mit_ecg.json

echo "Completed benchmark runs. Results are under ${OUTPUT_ROOT}/"