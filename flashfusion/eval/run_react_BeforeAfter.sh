#!/usr/bin/env bash
# run_react_BeforeAfter.sh
# Run ReAct OOS queries (9-12) with and without the abstention clause,
# then generate the OOS abstention figure via llamas.py.
#
# Results:
#   before (no abstention):  flashfusion/results/react_before/<dataset>/
#   after  (with abstention): flashfusion/results/react_after/<dataset>/
#   figure: flashfusion/viz/results/primary_visualizations/oos_abstention_across_datasets.png

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
fi

PYTHON="${PYTHON:-python}"
MODEL="${MODEL:-meta-llama/llama-3.3-70b-instruct}"
RUNS="${RUNS:-3}"
BEFORE_ROOT="${BEFORE_ROOT:-flashfusion/results/react_before}"
AFTER_ROOT="${AFTER_ROOT:-flashfusion/results/react_after}"
AGENT_BACKEND="${FLASHFUSION_AGENT_BACKEND:-safe}"
REACT_ABSTENTION_CLAUSE="${REACT_ABSTENTION_CLAUSE:-Before writing code, determine whether the question can be answered using only the listed columns. If required information is not derivable from those columns, do not write Python code. Return exactly: REJECT: <one-sentence reason tied to missing columns or unavailable future or external data>. Otherwise, return only executable Python code and assign the final answer to result.}"

if [[ "${AGENT_BACKEND}" != "safe" ]]; then
    echo "[WARN] FLASHFUSION_AGENT_BACKEND=${AGENT_BACKEND} overrides safe backend; this prompt-only before/after experiment may be invalid."
fi

# run_oos <no_abstention:0|1> <output_root> <dataset> <data_path> <ground_truth>
run_oos() {
    local no_abstention="$1"
    local output_root="$2"
    local dataset="$3"
    local data_path="$4"
    local ground_truth="$5"

    local abstention_clause=""
    if [[ "${no_abstention}" == "0" ]]; then
        abstention_clause="${REACT_ABSTENTION_CLAUSE}"
    fi

    FLASHFUSION_AGENT_BACKEND="${AGENT_BACKEND}" REACT_NO_ABSTENTION="${no_abstention}" REACT_ABSTENTION_CLAUSE="${abstention_clause}" "${PYTHON}" -m flashfusion.eval.benchmark \
        --dataset "${dataset}" \
        --data "${data_path}" \
        --baselines REACT_ONLY \
        --queries 9,10,11,12 \
        --runs "${RUNS}" \
        --model "${MODEL}" \
        --ground-truth "${ground_truth}" \
        --output "${output_root}/${dataset}"
}

echo "=== ReAct BEFORE (no abstention clause) — OOS queries 9-12 ==="
run_oos 1 "${BEFORE_ROOT}" wisdm \
    data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt \
    flashfusion/eval/ground_truth/ground_truth_wisdm.json

run_oos 1 "${BEFORE_ROOT}" bus \
    data/bus/bus_data_enriched_behavior.csv \
    flashfusion/eval/ground_truth/ground_truth_bus.json

run_oos 1 "${BEFORE_ROOT}" mit_ecg \
    data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt \
    flashfusion/eval/ground_truth/ground_truth_mit_ecg.json

echo "=== ReAct AFTER (with abstention clause) — OOS queries 9-12 ==="
run_oos 0 "${AFTER_ROOT}" wisdm \
    data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt \
    flashfusion/eval/ground_truth/ground_truth_wisdm.json

run_oos 0 "${AFTER_ROOT}" bus \
    data/bus/bus_data_enriched_behavior.csv \
    flashfusion/eval/ground_truth/ground_truth_bus.json

run_oos 0 "${AFTER_ROOT}" mit_ecg \
    data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt \
    flashfusion/eval/ground_truth/ground_truth_mit_ecg.json

echo "=== Generating OOS abstention figure ==="
cd "${REPO_ROOT}/flashfusion/viz"
"${PYTHON}" llamas.py \
    --react-before-root "../../results/react_before" \
    --react-after-root  "../../results/react_after" \
    --output-dir        "results/primary_visualizations"

echo "Done. Figure: flashfusion/viz/results/primary_visualizations/oos_abstention_across_datasets.png"