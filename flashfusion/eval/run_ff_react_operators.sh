#!/usr/bin/env bash
# =============================================================================
# run_ff_react_operators.sh
#
# Run Flash-Fusion and ReAct across all 16 queries, three datasets, and three
# repeated runs. Results are stored under:
#
#   flashfusion/results/ff_react_operators/
#     FLASH_FUSION/<dataset>/<RUN_TAG>/
#     REACT_ONLY/<dataset>/<RUN_TAG>/
#
# Default workload:
#   2 baselines x 3 datasets x 16 queries x 3 runs = 288 benchmark attempts.
#
# Usage:
#   chmod +x run_ff_react_operators.sh
#   ./run_ff_react_operators.sh
#
# Optional overrides:
#   RUN_TAG=ff_react_ops_july30 RUNS=3 ./run_ff_react_operators.sh
#   QUERIES=1,2,3 RUNS=1 ./run_ff_react_operators.sh
#   MAX_LATENCY=120 ./run_ff_react_operators.sh
# =============================================================================

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
else
    PYTHON="${PYTHON:-python3}"
fi

# ── Experiment configuration ─────────────────────────────────────────────────
OUTPUT_ROOT="${OUTPUT_ROOT:-flashfusion/results/ff_react_operators}"
RUN_TAG="${RUN_TAG:-run_$(date +%Y%m%d_%H%M%S)}"
MODEL="${MODEL:-meta-llama/llama-3.3-70b-instruct}"

BASELINES="${BASELINES:-FLASH_FUSION,REACT_ONLY}"
DATASETS="${DATASETS:-wisdm,mit_ecg,bus}"
QUERIES="${QUERIES:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16}"
RUNS="${RUNS:-3}"

# Set MAX_LATENCY to override both baseline-specific limits.
MAX_LATENCY="${MAX_LATENCY:-}"
MAX_LATENCY_FLASH_FUSION="${MAX_LATENCY_FLASH_FUSION:-60.0}"
MAX_LATENCY_REACT_ONLY="${MAX_LATENCY_REACT_ONLY:-60.0}"

# The consolidated benchmark uses LLM-based ground-truth measurement.
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

require_file() {
    if [[ ! -f "$1" ]]; then
        echo "[ERROR] Required file not found: $1" >&2
        exit 1
    fi
}

dataset_data_path() {
    case "$1" in
        wisdm)   echo "${WISDM_DATA}" ;;
        mit_ecg) echo "${MIT_ECG_DATA}" ;;
        bus)     echo "${BUS_DATA}" ;;
        *)
            echo "[ERROR] Unknown dataset: $1" >&2
            return 1
            ;;
    esac
}

dataset_gt_path() {
    case "$1" in
        wisdm)   echo "${GT_WISDM}" ;;
        mit_ecg) echo "${GT_MIT_ECG}" ;;
        bus)     echo "${GT_BUS}" ;;
        *)
            echo "[ERROR] Unknown dataset: $1" >&2
            return 1
            ;;
    esac
}

baseline_max_latency() {
    local baseline="$1"

    if [[ -n "${MAX_LATENCY}" ]]; then
        echo "${MAX_LATENCY}"
        return
    fi

    local varname="MAX_LATENCY_${baseline}"
    echo "${!varname:-60.0}"
}

# ── Validate preconditions ────────────────────────────────────────────────────
if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
    echo "[ERROR] Missing API key. Set OPENROUTER_API_KEY or GROQ_API_KEY." >&2
    exit 1
fi

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
echo " Run tag     : ${RUN_TAG}"
echo " Baselines   : ${BASELINES}"
echo " Datasets    : ${DATASETS}"
echo " Queries     : ${QUERIES}"
echo " Repetitions : ${RUNS}"
echo " Output root : ${OUTPUT_ROOT}"
echo "================================================================"
echo ""

for baseline in "${BASELINE_LIST[@]}"; do
    for ds in "${DATASET_LIST[@]}"; do
        DATA_PATH="$(dataset_data_path "${ds}")"
        GT_PATH="$(dataset_gt_path "${ds}")"
        TARGET_DIR="${OUTPUT_ROOT}/${baseline}/${ds}/${RUN_TAG}"
        QUERY_MAX_LATENCY="$(baseline_max_latency "${baseline}")"

        mkdir -p "${TARGET_DIR}"

        log "[Benchmark] baseline=${baseline} dataset=${ds} queries=${QUERIES} runs=${RUNS}"
        log "[Output] ${TARGET_DIR}"

        "${PYTHON}" -u -m flashfusion.eval.benchmark \
            --dataset "${ds}" \
            --data "${DATA_PATH}" \
            --ground-truth "${GT_PATH}" \
            --baselines "${baseline}" \
            --queries "${QUERIES}" \
            --runs "${RUNS}" \
            --model "${MODEL}" \
            --max-query-latency "${QUERY_MAX_LATENCY}" \
            --ground-truth-measurement "${GROUND_TRUTH_MEASUREMENT}" \
            --output "${TARGET_DIR}" \
            2>&1 | tee "${TARGET_DIR}/benchmark.log"

        log "[Visualize] baseline=${baseline} dataset=${ds}"

        "${PYTHON}" -m flashfusion.eval.visualize_comparison \
            --metrics "${TARGET_DIR}/metrics.csv" \
            --dataset "${ds}" \
            --accuracy-column gt_score \
            --title "${baseline} (${ds})" \
            --output "${TARGET_DIR}" \
            2>&1 | tee "${TARGET_DIR}/visualize.log" || true
    done
done

# ── Aggregate all baseline x dataset outputs ──────────────────────────────────
AGG_ROOT="${OUTPUT_ROOT}/_aggregate/${RUN_TAG}"
mkdir -p "${AGG_ROOT}"

log "[Summary] Building aggregate tables -> ${AGG_ROOT}"

"${PYTHON}" - "${OUTPUT_ROOT}" "${RUN_TAG}" "${AGG_ROOT}" <<'PYEOF'
import sys
from pathlib import Path

import pandas as pd
from flashfusion.eval.queries import get_queries

output_root = Path(sys.argv[1])
run_tag = sys.argv[2]
aggregate_root = Path(sys.argv[3])
aggregate_root.mkdir(parents=True, exist_ok=True)


def query_type(complexity: str) -> str:
    value = (complexity or "").strip().lower()
    if value == "direct":
        return "direct"
    if value in {"intermediate", "reasoning"}:
        return "reasoning"
    return "oos"


rows = []

for baseline_dir in sorted(output_root.iterdir()):
    if not baseline_dir.is_dir() or baseline_dir.name.startswith("_"):
        continue

    baseline = baseline_dir.name

    for dataset in ("wisdm", "mit_ecg", "bus"):
        metrics_path = baseline_dir / dataset / run_tag / "metrics.csv"

        if not metrics_path.exists():
            continue

        metrics = pd.read_csv(metrics_path)

        if metrics.empty:
            continue

        complexity_by_query = {
            int(query["id"]): str(query.get("complexity", ""))
            for query in get_queries(dataset)
        }

        metrics["baseline"] = baseline
        metrics["dataset"] = dataset
        metrics["query_id"] = metrics["query_id"].astype(int)
        metrics["complexity"] = metrics["query_id"].map(complexity_by_query).fillna("")
        metrics["query_type"] = metrics["complexity"].map(query_type)

        for column in ("input_tokens", "output_tokens", "cost_usd", "latency_s"):
            if column in metrics.columns:
                metrics[column] = pd.to_numeric(metrics[column], errors="coerce")

        metrics["total_tokens"] = (
            metrics.get("input_tokens", 0).fillna(0)
            + metrics.get("output_tokens", 0).fillna(0)
        )

        rows.append(metrics)

if not rows:
    print("No metrics.csv files found; aggregate tables were not created.")
    raise SystemExit(0)

combined = pd.concat(rows, ignore_index=True)
combined.to_csv(aggregate_root / "query_metrics_all_datasets.csv", index=False)

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

print(f"Wrote {aggregate_root / 'query_metrics_all_datasets.csv'}")
print(f"Wrote {aggregate_root / 'summary_by_dataset_query_type.csv'}")
print(f"Wrote {aggregate_root / 'summary_by_dataset_overall.csv'}")
print(f"Wrote {aggregate_root / 'summary_balanced_overall.csv'}")
PYEOF

echo ""
echo "================================================================"
echo " Complete"
echo ""
echo " Per-dataset results:"
echo "   ${OUTPUT_ROOT}/<BASELINE>/<dataset>/${RUN_TAG}/"
echo ""
echo " Aggregate tables:"
echo "   ${AGG_ROOT}/"
echo "================================================================"