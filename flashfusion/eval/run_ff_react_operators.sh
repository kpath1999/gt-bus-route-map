#!/usr/bin/env bash
# =============================================================================
# run_ff_react_operators.sh
#
# Run Flash-Fusion and ReAct across all 16 queries, three datasets, and three
# repeated runs.
#
# Output layout:
#   flashfusion/results/ff_react_operators/
#     FLASH_FUSION/<dataset>/
#     REACT_ONLY/<dataset>/
#     _aggregate/
#
# Default workload:
#   2 baselines x 3 datasets x 16 queries x 3 runs = 288 attempts.
#
# Usage:
#   chmod +x flashfusion/eval/run_ff_react_operators.sh
#   ./flashfusion/eval/run_ff_react_operators.sh
#
# Useful overrides:
#   RUNS=3 ./flashfusion/eval/run_ff_react_operators.sh
#   BASELINES=REACT_ONLY RUNS=3 ./flashfusion/eval/run_ff_react_operators.sh
#   DATASETS=mit_ecg BASELINES=REACT_ONLY RUNS=1 ./flashfusion/eval/run_ff_react_operators.sh
#   REACT_NO_ABSTENTION=1 ./flashfusion/eval/run_ff_react_operators.sh
# =============================================================================

# run this when you're back home: BASELINES=REACT_ONLY DATASETS=mit_ecg,bus ./flashfusion/eval/run_ff_react_operators.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.venv/bin/activate"
fi

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export TRANSFORMERS_NO_ADVISORY_WARNINGS="${TRANSFORMERS_NO_ADVISORY_WARNINGS:-1}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="${PYTHON:-python3}"
else
    PYTHON="${PYTHON:-python}"
fi

# ── Experiment configuration ─────────────────────────────────────────────────
OUTPUT_ROOT="${OUTPUT_ROOT:-flashfusion/results/ff_react_operators}"
# RUN_TAG="${RUN_TAG:-run_$(date +%Y%m%d_%H%M%S)}"
MODEL="${MODEL:-meta-llama/llama-3.3-70b-instruct}"

BASELINES="${BASELINES:-FLASH_FUSION,REACT_ONLY}"
DATASETS="${DATASETS:-wisdm,mit_ecg,bus}"
QUERIES="${QUERIES:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}"
RUNS="${RUNS:-3}"

# ReAct policy:
#   0: include the abstention clause.
#   1: omit the abstention clause.
REACT_NO_ABSTENTION="${REACT_NO_ABSTENTION:-0}"

REACT_ABSTENTION_CLAUSE="${REACT_ABSTENTION_CLAUSE:-Before writing code, determine whether the question can be answered using only the listed columns. If required information is not derivable from those columns, do not write Python code. Return exactly: REJECT: <one-sentence reason tied to missing columns or unavailable future or external data>. Otherwise, return only executable Python code and assign the final answer to result.}"

# Optional. Preserve the backend selected by the caller or react_only.py.
AGENT_BACKEND="${FLASHFUSION_AGENT_BACKEND:-safe}"

# Set MAX_LATENCY to override both baseline-specific limits.
MAX_LATENCY="${MAX_LATENCY:-}"
MAX_LATENCY_FLASH_FUSION="${MAX_LATENCY_FLASH_FUSION:-60.0}"
MAX_LATENCY_REACT_ONLY="${MAX_LATENCY_REACT_ONLY:-60.0}"

GROUND_TRUTH_MEASUREMENT="${GROUND_TRUTH_MEASUREMENT:-llm}"

WISDM_DATA="${WISDM_DATA:-data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt}"
MIT_ECG_DATA="${MIT_ECG_DATA:-data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt}"
BUS_DATA="${BUS_DATA:-data/bus/bus_data_enriched_behavior.csv}"

GT_WISDM="${GT_WISDM:-flashfusion/eval/ground_truth/ground_truth_wisdm.json}"
GT_MIT_ECG="${GT_MIT_ECG:-flashfusion/eval/ground_truth/ground_truth_mit_ecg.json}"
GT_BUS="${GT_BUS:-flashfusion/eval/ground_truth/ground_truth_bus.json}"

IFS=',' read -r -a BASELINE_LIST <<< "${BASELINES}"
IFS=',' read -r -a DATASET_LIST <<< "${DATASETS}"

ts() {
    date "+%Y-%m-%d %H:%M:%S"
}

log() {
    echo "[$(ts)] $*"
}

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

require_file() {
    [[ -f "$1" ]] || die "Required file not found: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

dataset_data_path() {
    case "$1" in
        wisdm)   echo "${WISDM_DATA}" ;;
        mit_ecg) echo "${MIT_ECG_DATA}" ;;
        bus)     echo "${BUS_DATA}" ;;
        *) die "Unknown dataset: $1" ;;
    esac
}

dataset_gt_path() {
    case "$1" in
        wisdm)   echo "${GT_WISDM}" ;;
        mit_ecg) echo "${GT_MIT_ECG}" ;;
        bus)     echo "${GT_BUS}" ;;
        *) die "Unknown dataset: $1" ;;
    esac
}

baseline_max_latency() {
    local baseline="$1"

    if [[ -n "${MAX_LATENCY}" ]]; then
        echo "${MAX_LATENCY}"
        return
    fi

    case "${baseline}" in
        FLASH_FUSION) echo "${MAX_LATENCY_FLASH_FUSION}" ;;
        REACT_ONLY)   echo "${MAX_LATENCY_REACT_ONLY}" ;;
        *) die "No latency configuration for unsupported baseline: ${baseline}" ;;
    esac
}

validate_baseline() {
    case "$1" in
        FLASH_FUSION|REACT_ONLY) ;;
        *) die "Unsupported baseline: $1. Allowed: FLASH_FUSION, REACT_ONLY" ;;
    esac
}

run_benchmark() {
    local baseline="$1"
    local dataset="$2"
    local data_path="$3"
    local ground_truth="$4"
    local target_dir="$5"
    local max_latency="$6"

    local log_path="${target_dir}/benchmark.log"

    if [[ "${baseline}" == "REACT_ONLY" ]]; then
        log "[ReAct config] backend=${AGENT_BACKEND} no_abstention=${REACT_NO_ABSTENTION}"

        FLASHFUSION_AGENT_BACKEND="${AGENT_BACKEND}" \
        REACT_NO_ABSTENTION="${REACT_NO_ABSTENTION}" \
        REACT_ABSTENTION_CLAUSE="${REACT_ABSTENTION_CLAUSE}" \
        "${PYTHON}" -u -m flashfusion.eval.benchmark \
            --dataset "${dataset}" \
            --data "${data_path}" \
            --ground-truth "${ground_truth}" \
            --baselines "${baseline}" \
            --queries "${QUERIES}" \
            --runs "${RUNS}" \
            --model "${MODEL}" \
            --max-query-latency "${max_latency}" \
            --ground-truth-measurement "${GROUND_TRUTH_MEASUREMENT}" \
            --output "${target_dir}" \
            2>&1 | tee "${log_path}"
    else
        "${PYTHON}" -u -m flashfusion.eval.benchmark \
            --dataset "${dataset}" \
            --data "${data_path}" \
            --ground-truth "${ground_truth}" \
            --baselines "${baseline}" \
            --queries "${QUERIES}" \
            --runs "${RUNS}" \
            --model "${MODEL}" \
            --max-query-latency "${max_latency}" \
            --ground-truth-measurement "${GROUND_TRUTH_MEASUREMENT}" \
            --output "${target_dir}" \
            2>&1 | tee "${log_path}"
    fi
}

# ── Validate preconditions ────────────────────────────────────────────────────
require_command "${PYTHON}"

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
    die "Missing API key. Set OPENROUTER_API_KEY or GROQ_API_KEY."
fi

[[ "${RUNS}" =~ ^[1-9][0-9]*$ ]] || die "RUNS must be a positive integer; got: ${RUNS}"

if [[ "${REACT_NO_ABSTENTION}" != "0" && "${REACT_NO_ABSTENTION}" != "1" ]]; then
    die "REACT_NO_ABSTENTION must be 0 or 1; got: ${REACT_NO_ABSTENTION}"
fi

for baseline in "${BASELINE_LIST[@]}"; do
    validate_baseline "${baseline}"
done

for ds in "${DATASET_LIST[@]}"; do
    require_file "$(dataset_data_path "${ds}")"
    require_file "$(dataset_gt_path "${ds}")"
done

mkdir -p "${OUTPUT_ROOT}"

# ── Run benchmark matrix ──────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " Flash-Fusion + ReAct Operator Benchmark"
echo " Timestamp   : $(date '+%Y-%m-%d %H:%M:%S')"
# echo " Run tag     : ${RUN_TAG}"
echo " Baselines   : ${BASELINES}"
echo " Datasets    : ${DATASETS}"
echo " Queries     : ${QUERIES}"
echo " Repetitions : ${RUNS}"
echo " Output root : ${OUTPUT_ROOT}"
echo " ReAct abstention enabled: $([[ "${REACT_NO_ABSTENTION}" == "0" ]] && echo yes || echo no)"
echo "================================================================"
echo ""

for baseline in "${BASELINE_LIST[@]}"; do
    for ds in "${DATASET_LIST[@]}"; do
        DATA_PATH="$(dataset_data_path "${ds}")"
        GT_PATH="$(dataset_gt_path "${ds}")"
        TARGET_DIR="${OUTPUT_ROOT}/${baseline}/${ds}"
        QUERY_MAX_LATENCY="$(baseline_max_latency "${baseline}")"

        mkdir -p "${TARGET_DIR}"

        log "[Benchmark] baseline=${baseline} dataset=${ds} queries=${QUERIES} runs=${RUNS}"
        log "[Output] ${TARGET_DIR}"

        run_benchmark \
            "${baseline}" \
            "${ds}" \
            "${DATA_PATH}" \
            "${GT_PATH}" \
            "${TARGET_DIR}" \
            "${QUERY_MAX_LATENCY}"
    done
done

# ── Aggregate all baseline x dataset outputs ──────────────────────────────────
AGG_ROOT="${OUTPUT_ROOT}/_aggregate"
mkdir -p "${AGG_ROOT}"

log "[Summary] Building aggregate tables -> ${AGG_ROOT}"

"${PYTHON}" - "${OUTPUT_ROOT}" "${AGG_ROOT}" <<'PYEOF'
import sys
from pathlib import Path

import pandas as pd
from flashfusion.eval.queries import get_queries

output_root = Path(sys.argv[1])
# run_tag = sys.argv[2]
aggregate_root = Path(sys.argv[3])
aggregate_root.mkdir(parents=True, exist_ok=True)

expected_baselines = ("FLASH_FUSION", "REACT_ONLY")
expected_datasets = ("wisdm", "mit_ecg", "bus")


def query_type(complexity: str) -> str:
    value = (complexity or "").strip().lower()
    if value == "direct":
        return "direct"
    if value in {"intermediate", "reasoning"}:
        return "reasoning"
    return "oos"


def numeric_column(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df.columns:
        return pd.Series(0.0, index=df.index, dtype=float)
    return pd.to_numeric(df[name], errors="coerce").fillna(0.0)


def boolean_column(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return df[name].fillna(False).astype(bool)


rows = []

for baseline in expected_baselines:
    for dataset in expected_datasets:
        metrics_path = output_root / baseline / dataset / "metrics.csv"

        if not metrics_path.exists():
            print(f"[WARN] Missing metrics file: {metrics_path}")
            continue

        metrics = pd.read_csv(metrics_path)

        if metrics.empty:
            print(f"[WARN] Empty metrics file: {metrics_path}")
            continue

        if "query_id" not in metrics.columns:
            print(f"[WARN] No query_id column; skipping: {metrics_path}")
            continue

        complexity_by_query = {
            int(query["id"]): str(query.get("complexity", ""))
            for query in get_queries(dataset)
        }

        metrics["baseline"] = baseline
        metrics["dataset"] = dataset
        metrics["query_id"] = pd.to_numeric(
            metrics["query_id"],
            errors="coerce",
        )

        metrics = metrics.dropna(subset=["query_id"]).copy()
        metrics["query_id"] = metrics["query_id"].astype(int)
        metrics["complexity"] = metrics["query_id"].map(complexity_by_query).fillna("")
        metrics["query_type"] = metrics["complexity"].map(query_type)

        metrics["gt_score"] = numeric_column(metrics, "gt_score")
        metrics["latency_s"] = numeric_column(metrics, "latency_s")
        metrics["input_tokens"] = numeric_column(metrics, "input_tokens")
        metrics["output_tokens"] = numeric_column(metrics, "output_tokens")
        metrics["cost_usd"] = numeric_column(metrics, "cost_usd")
        metrics["executed"] = boolean_column(metrics, "executed")
        metrics["rejected"] = boolean_column(metrics, "rejected")
        metrics["total_tokens"] = (
            metrics["input_tokens"] + metrics["output_tokens"]
        )

        rows.append(metrics)

if not rows:
    print("No metrics.csv files found; aggregate tables were not created.")
    raise SystemExit(0)

combined = pd.concat(rows, ignore_index=True)
combined.to_csv(
    aggregate_root / "query_metrics_all_datasets.csv",
    index=False,
)

aggregation = {
    "accuracy": ("gt_score", "mean"),
    "latency_s": ("latency_s", "mean"),
    "input_tokens": ("input_tokens", "mean"),
    "output_tokens": ("output_tokens", "mean"),
    "total_tokens": ("total_tokens", "mean"),
    "cost_usd": ("cost_usd", "mean"),
    "executed_rate": ("executed", "mean"),
    "rejected_rate": ("rejected", "mean"),
    "n": ("query_id", "count"),
}

by_dataset_query_type = (
    combined.groupby(["dataset", "query_type", "baseline"], as_index=False)
    .agg(**aggregation)
    .sort_values(["dataset", "query_type", "baseline"])
)
by_dataset_query_type.to_csv(
    aggregate_root / "summary_by_dataset_query_type.csv",
    index=False,
)

by_dataset = (
    combined.groupby(["dataset", "baseline"], as_index=False)
    .agg(**aggregation)
    .sort_values(["dataset", "baseline"])
)
by_dataset.to_csv(
    aggregate_root / "summary_by_dataset_overall.csv",
    index=False,
)

balanced_overall = (
    by_dataset.groupby("baseline", as_index=False)
    .agg(
        accuracy=("accuracy", "mean"),
        latency_s=("latency_s", "mean"),
        input_tokens=("input_tokens", "mean"),
        output_tokens=("output_tokens", "mean"),
        total_tokens=("total_tokens", "mean"),
        cost_usd=("cost_usd", "mean"),
        executed_rate=("executed_rate", "mean"),
        rejected_rate=("rejected_rate", "mean"),
    )
    .sort_values("baseline")
)
balanced_overall.to_csv(
    aggregate_root / "summary_balanced_overall.csv",
    index=False,
)

for name in (
    "query_metrics_all_datasets.csv",
    "summary_by_dataset_query_type.csv",
    "summary_by_dataset_overall.csv",
    "summary_balanced_overall.csv",
):
    print(f"Wrote {aggregate_root / name}")
PYEOF

echo ""
echo "================================================================"
echo " Complete"
echo ""
echo " Per-dataset results:"
echo "   ${OUTPUT_ROOT}/<BASELINE>/<dataset>/"
echo ""
echo " Aggregate tables:"
echo "   ${AGG_ROOT}/"
echo "================================================================"