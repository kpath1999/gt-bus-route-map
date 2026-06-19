#!/usr/bin/env bash
# run_eval_suite.sh — End-to-end evaluation suite for Flash-Fusion mini-experiments.
#
# Runs three primary evaluation experiments in sequence:
#   1. latencystages  — per-stage latency breakdown by query type (all 3 datasets)
#   2. modeltype      — Flash-Fusion accuracy across two smaller models (all 3 datasets)
#   3. accuracysize   — Flash-Fusion accuracy vs. dataset size fraction (all 3 datasets)
#
# Prerequisites:
#   - OPENROUTER_API_KEY set in the environment (or GROQ_API_KEY)
#   - Python virtualenv activated with flashfusion dependencies installed
#
# Usage:
#   cd <repo_root>          # must run from repo root
#   bash flashfusion/miniexp/run_eval_suite.sh
#
# Optional overrides (env vars):
#   SUITE_MODEL          Model for latencystages + accuracysize (default: meta-llama/llama-3.3-70b-instruct)
#   SUITE_JUDGE_MODEL    Model for modeltype judge            (default: meta-llama/llama-3.3-70b-instruct)
#   SUITE_SMALL_MODELS   Space-separated smaller models for modeltype
#                        (default: "meta-llama/llama-4-scout-17b-16e-instruct meta-llama/llama-3.1-8b-instruct")
#   SUITE_OUTPUT_ROOT    Root output directory               (default: flashfusion/miniexp/results)
#   SUITE_FRACTIONS      Space-separated fractions for accuracysize
#                        (default: "0.2 0.4 0.6 0.8 1.0")
#   SUITE_DATASETS       Space-separated dataset names       (default: "wisdm mit_ecg bus")
#   SUITE_QUERY_IDS      Optional space-separated query ID subset (default: all)
#   SUITE_SKIP_PREFLIGHT Set to "1" to skip per-experiment dry-run path checks
#   SUITE_DRY_RUN        Set to "1" to only run preflight checks without LLM calls

# remember
"""
tmux detach-client -s eval
tmux attach -t eval
tmux kill-session -t eval  # Terminate the session when finished
"""

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PYTHON="${PYTHON:-python3}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

SUITE_MODEL="${SUITE_MODEL:-meta-llama/llama-3.3-70b-instruct}"
SUITE_JUDGE_MODEL="${SUITE_JUDGE_MODEL:-meta-llama/llama-3.3-70b-instruct}"
SUITE_SMALL_MODELS="${SUITE_SMALL_MODELS:-meta-llama/llama-4-scout-17b-16e-instruct meta-llama/llama-3.1-8b-instruct}"
SUITE_OUTPUT_ROOT="${SUITE_OUTPUT_ROOT:-flashfusion/miniexp/results}"
SUITE_FRACTIONS="${SUITE_FRACTIONS:-0.2 0.4 0.6 0.8 1.0}"
SUITE_DATASETS="${SUITE_DATASETS:-wisdm mit_ecg bus}"
SUITE_QUERY_IDS="${SUITE_QUERY_IDS:-}"
SUITE_SKIP_PREFLIGHT="${SUITE_SKIP_PREFLIGHT:-0}"
SUITE_DRY_RUN="${SUITE_DRY_RUN:-0}"

LATENCY_OUT="${SUITE_OUTPUT_ROOT}/latencystages"
MODELTYPE_OUT="${SUITE_OUTPUT_ROOT}/modeltype"
ACCURACYSIZE_OUT="${SUITE_OUTPUT_ROOT}/accuracysize"
GT_ROOT="flashfusion/eval/ground_truth/by_fraction"

LOG_DIR="${SUITE_OUTPUT_ROOT}/logs"
SUITE_LOG="${LOG_DIR}/suite_$(date +%Y%m%d_%H%M%S).log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ts() { date "+%Y-%m-%d %H:%M:%S"; }

_log() {
    local msg="$*"
    echo "[$(_ts)] ${msg}"
    echo "[$(_ts)] ${msg}" >> "${SUITE_LOG}"
}

_header() {
    echo ""
    echo "============================================================"
    echo "  $*"
    echo "============================================================"
    echo ""
}

_check_api_key() {
    if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
        echo "[ERROR] Neither OPENROUTER_API_KEY nor GROQ_API_KEY is set."
        echo "        Export one of these before running the suite."
        exit 1
    fi
}

_elapsed() {
    local start_s=$1 end_s=$2
    local secs=$(( end_s - start_s ))
    printf "%dm%02ds" $(( secs / 60 )) $(( secs % 60 ))
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

cd "${REPO_ROOT}"
mkdir -p "${LOG_DIR}"

_header "Flash-Fusion Evaluation Suite"
_log "Repo root   : ${REPO_ROOT}"
_log "Datasets    : ${SUITE_DATASETS}"
_log "Main model  : ${SUITE_MODEL}"
_log "Judge model : ${SUITE_JUDGE_MODEL}"
_log "Small models: ${SUITE_SMALL_MODELS}"
_log "Output root : ${SUITE_OUTPUT_ROOT}"
_log "Suite log   : ${SUITE_LOG}"
[[ "${SUITE_DRY_RUN}" == "1" ]] && _log "*** DRY RUN MODE — no LLM calls will be made ***"
echo ""

if [[ "${SUITE_DRY_RUN}" != "1" ]]; then
    _check_api_key
fi

# Build dataset/query-id args shared across all three scripts
DATASET_ARGS=""
for ds in ${SUITE_DATASETS}; do DATASET_ARGS="${DATASET_ARGS} ${ds}"; done
DATASET_ARGS="--datasets${DATASET_ARGS}"

QUERY_ID_ARGS=""
if [[ -n "${SUITE_QUERY_IDS}" ]]; then
    QUERY_ID_ARGS="--query-ids ${SUITE_QUERY_IDS}"
fi

FRACTION_ARGS=""
for f in ${SUITE_FRACTIONS}; do FRACTION_ARGS="${FRACTION_ARGS} ${f}"; done
FRACTION_ARGS="--fractions${FRACTION_ARGS}"

SMALL_MODEL_ARGS=""
for m in ${SUITE_SMALL_MODELS}; do SMALL_MODEL_ARGS="${SMALL_MODEL_ARGS} ${m}"; done
SMALL_MODEL_ARGS="--models${SMALL_MODEL_ARGS}"

# ---------------------------------------------------------------------------
# Experiment 1: Latency stages
# ---------------------------------------------------------------------------

_header "Experiment 1/3 — Stage Latency (latencystages.py)"
_log "Output: ${LATENCY_OUT}"

if [[ "${SUITE_SKIP_PREFLIGHT}" != "1" ]]; then
    _log "Running preflight checks..."
    ${PYTHON} flashfusion/miniexp/latencystages.py \
        ${DATASET_ARGS} \
        --model "${SUITE_MODEL}" \
        --output-dir "${LATENCY_OUT}" \
        --dry-run-check-paths \
        2>&1 | tee -a "${SUITE_LOG}"
    _log "Preflight passed."
fi

if [[ "${SUITE_DRY_RUN}" == "1" ]]; then
    _log "[DRY RUN] Skipping latencystages LLM execution."
else
    _t0=$SECONDS
    _log "Starting latencystages run..."
    ${PYTHON} flashfusion/miniexp/latencystages.py \
        ${DATASET_ARGS} \
        ${QUERY_ID_ARGS} \
        --model "${SUITE_MODEL}" \
        --output-dir "${LATENCY_OUT}" \
        2>&1 | tee -a "${LOG_DIR}/latencystages.log"
    _t1=$SECONDS
    _log "latencystages complete in $(_elapsed ${_t0} ${_t1}). Outputs: ${LATENCY_OUT}"
fi

# ---------------------------------------------------------------------------
# Experiment 2: Model type comparison
# ---------------------------------------------------------------------------

_header "Experiment 2/3 — Model Type Comparison (modeltype.py)"
_log "Output: ${MODELTYPE_OUT}"
_log "Models under test: ${SUITE_SMALL_MODELS}"

if [[ "${SUITE_SKIP_PREFLIGHT}" != "1" ]]; then
    _log "Running preflight checks..."
    ${PYTHON} flashfusion/miniexp/modeltype.py \
        ${DATASET_ARGS} \
        ${SMALL_MODEL_ARGS} \
        --judge-model "${SUITE_JUDGE_MODEL}" \
        --output-dir "${MODELTYPE_OUT}" \
        --dry-run-check-paths \
        2>&1 | tee -a "${SUITE_LOG}"
    _log "Preflight passed."
fi

if [[ "${SUITE_DRY_RUN}" == "1" ]]; then
    _log "[DRY RUN] Skipping modeltype LLM execution."
else
    _t0=$SECONDS
    _log "Starting modeltype run..."
    ${PYTHON} flashfusion/miniexp/modeltype.py \
        ${DATASET_ARGS} \
        ${SMALL_MODEL_ARGS} \
        ${QUERY_ID_ARGS} \
        --judge-model "${SUITE_JUDGE_MODEL}" \
        --output-dir "${MODELTYPE_OUT}" \
        --plot \
        2>&1 | tee -a "${LOG_DIR}/modeltype.log"
    _t1=$SECONDS
    _log "modeltype complete in $(_elapsed ${_t0} ${_t1}). Outputs: ${MODELTYPE_OUT}"
fi

# ---------------------------------------------------------------------------
# Experiment 3: Accuracy vs. dataset size
# ---------------------------------------------------------------------------

_header "Experiment 3/3 — Accuracy vs. Dataset Size (accuracysize.py)"
_log "Output: ${ACCURACYSIZE_OUT}"
_log "Fractions: ${SUITE_FRACTIONS}"

# Build ground-truth files for every dataset × fraction before the main run.
# This step is idempotent and fast (no LLM calls); existing files are overwritten.
_log "Building ground-truth files under ${GT_ROOT}..."
${PYTHON} flashfusion/miniexp/accuracysize.py \
    ${DATASET_ARGS} \
    ${FRACTION_ARGS} \
    --gt-root "${GT_ROOT}" \
    --build-gt \
    2>&1 | tee -a "${LOG_DIR}/accuracysize_buildgt.log"
_log "Ground-truth build complete."

if [[ "${SUITE_SKIP_PREFLIGHT}" != "1" ]]; then
    _log "Running preflight checks..."
    ${PYTHON} flashfusion/miniexp/accuracysize.py \
        ${DATASET_ARGS} \
        ${FRACTION_ARGS} \
        --gt-root "${GT_ROOT}" \
        --model "${SUITE_MODEL}" \
        --output-dir "${ACCURACYSIZE_OUT}" \
        --dry-run-check-gt \
        2>&1 | tee -a "${SUITE_LOG}"
    _log "Preflight passed."
fi

if [[ "${SUITE_DRY_RUN}" == "1" ]]; then
    _log "[DRY RUN] Skipping accuracysize LLM execution."
else
    _t0=$SECONDS
    _log "Starting accuracysize run..."
    ${PYTHON} flashfusion/miniexp/accuracysize.py \
        ${DATASET_ARGS} \
        ${FRACTION_ARGS} \
        ${QUERY_ID_ARGS} \
        --model "${SUITE_MODEL}" \
        --gt-root "${GT_ROOT}" \
        --output-dir "${ACCURACYSIZE_OUT}" \
        --plot \
        2>&1 | tee -a "${LOG_DIR}/accuracysize.log"
    _t1=$SECONDS
    _log "accuracysize complete in $(_elapsed ${_t0} ${_t1}). Outputs: ${ACCURACYSIZE_OUT}"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_header "Suite complete"
_log "Output artifacts:"
_log "  Stage latency   : ${LATENCY_OUT}/"
_log "    latency_by_stages_query_metrics.csv"
_log "    latency_by_stages_summary.csv"
_log "    latency_by_stages_per_dataset_summary.csv"
_log "    latency_by_stages.png / .pdf"
_log "    latency_by_stages_raw_results.jsonl"
_log "  Model comparison: ${MODELTYPE_OUT}/"
_log "    metrics_vs_model_type.csv"
_log "    modeltype_summary.csv"
_log "    metrics_vs_model_type.png"
_log "    <dataset>/<model>/metrics.csv"
_log "    <dataset>/<model>/raw_results.jsonl"
_log "  Accuracy vs size: ${ACCURACYSIZE_OUT}/"
_log "    accuracy_vs_size_query_metrics.csv"
_log "    accuracy_vs_size_summary.csv"
_log "    accuracy_vs_size.png"
_log "  Suite log       : ${SUITE_LOG}"
_log "  Per-experiment logs: ${LOG_DIR}/"
