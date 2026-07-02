#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -f ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

# User-requested output root.
OUTPUT_ROOT="${OUTPUT_ROOT:-/Users/kausar/Documents/research/flash-fusion/flashfusion/results/july26}"
RUN_TAG="${RUN_TAG:-run_$(date +%Y%m%d_%H%M%S)}"
AGG_ROOT="${OUTPUT_ROOT}/_aggregate/${RUN_TAG}"
SMOKE_ROOT="${OUTPUT_ROOT}/_smoke/${RUN_TAG}"

MODEL="${MODEL:-meta-llama/llama-3.3-70b-instruct}"
RUNS="${RUNS:-1}"
QUERIES="${QUERIES:-all}"
MAX_LATENCY="${MAX_LATENCY:-30.0}"
BASELINES="${BASELINES:-FLASH_FUSION,HARGPT_PAPER,REACT_ONLY,AUTOIOT_PAPER}"

# Smoke-test controls (recommended before overnight run).
SMOKE_TEST="${SMOKE_TEST:-1}"
SMOKE_QUERIES="${SMOKE_QUERIES:-1,5,9}"
SMOKE_MAX_LATENCY="${SMOKE_MAX_LATENCY:-20.0}"
SMOKE_ABORT_ON_FAIL="${SMOKE_ABORT_ON_FAIL:-1}"

# Canonical data paths (no chat/data fallback).
WISDM_DATA="${WISDM_DATA:-data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt}"
MIT_ECG_DATA="${MIT_ECG_DATA:-data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt}"
BUS_DATA="${BUS_DATA:-data/bus/bus_data.csv}"

GT_WISDM="${GT_WISDM:-flashfusion/eval/ground_truth/ground_truth_wisdm.json}"
GT_MIT_ECG="${GT_MIT_ECG:-flashfusion/eval/ground_truth/ground_truth_mit_ecg.json}"
GT_BUS="${GT_BUS:-flashfusion/eval/ground_truth/ground_truth_bus.json}"

DATASETS=("wisdm" "mit_ecg" "bus")

IFS=',' read -r -a BASELINE_LIST <<< "${BASELINES}"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*"; }

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "[ERROR] Required file not found: ${path}" >&2
    exit 1
  fi
}

dataset_data_path() {
  local ds="$1"
  case "${ds}" in
    wisdm) echo "${WISDM_DATA}" ;;
    mit_ecg) echo "${MIT_ECG_DATA}" ;;
    bus) echo "${BUS_DATA}" ;;
    *) return 1 ;;
  esac
}

dataset_gt_path() {
  local ds="$1"
  case "${ds}" in
    wisdm) echo "${GT_WISDM}" ;;
    mit_ecg) echo "${GT_MIT_ECG}" ;;
    bus) echo "${GT_BUS}" ;;
    *) return 1 ;;
  esac
}

add_query_args() {
  local q="$1"
  if [[ "${q}" == "all" ]]; then
    return 0
  fi
  printf -- " --query-ids"
  IFS=',' read -r -a qids <<< "${q}"
  local qid
  for qid in "${qids[@]}"; do
    qid="$(echo "${qid}" | xargs)"
    [[ -n "${qid}" ]] && printf -- " %s" "${qid}"
  done
}

run_stage_latency_export() {
  local baseline="$1"
  local ds="$2"
  local target_dir="$3"
  local data_path="$4"
  local tmp_stage
  tmp_stage="$(mktemp -d)"

  local qargs
  qargs="$(add_query_args "${QUERIES}")"

  local cmd
  cmd="\"${PYTHON}\" -u flashfusion/miniexp/latencystages.py --mode semantic_single --baseline \"${baseline}\" --datasets \"${ds}\" --model \"${MODEL}\" --output-dir \"${tmp_stage}\" --data-path-wisdm \"${data_path}\" --data-path-mit-ecg \"${data_path}\" --data-path-bus \"${data_path}\"${qargs}"
  eval "${cmd}" >"${target_dir}/stage_semantic.log" 2>&1

  local baseline_lower
  baseline_lower="$(echo "${baseline}" | tr '[:upper:]' '[:lower:]')"
  local src_sem="${tmp_stage}/semantic_single/${baseline_lower}"
  if [[ -d "${src_sem}" ]]; then
    cp -f "${src_sem}"/* "${target_dir}/" 2>/dev/null || true
  fi

  if [[ "${baseline}" == "FLASH_FUSION" ]]; then
    local cmd_ff
    cmd_ff="\"${PYTHON}\" -u flashfusion/miniexp/latencystages.py --mode ff_only --datasets \"${ds}\" --model \"${MODEL}\" --output-dir \"${tmp_stage}\" --data-path-wisdm \"${data_path}\" --data-path-mit-ecg \"${data_path}\" --data-path-bus \"${data_path}\"${qargs}"
    eval "${cmd_ff}" >"${target_dir}/stage_ff_native.log" 2>&1
    local src_ff="${tmp_stage}/flash_fusion_native"
    if [[ -d "${src_ff}" ]]; then
      cp -f "${src_ff}"/* "${target_dir}/" 2>/dev/null || true
    fi
  fi

  rm -rf "${tmp_stage}"
}

run_smoke_tests() {
  mkdir -p "${SMOKE_ROOT}"
  local smoke_csv="${SMOKE_ROOT}/smoke_status.csv"
  echo "baseline,dataset,status,reason,rows,error_rows,log_file" > "${smoke_csv}"

  local baseline ds data_path gt_path smoke_out smoke_log
  for baseline in "${BASELINE_LIST[@]}"; do
    for ds in "${DATASETS[@]}"; do
      data_path="$(dataset_data_path "${ds}")"
      gt_path="$(dataset_gt_path "${ds}")"
      smoke_out="${SMOKE_ROOT}/${baseline}/${ds}"
      smoke_log="${SMOKE_ROOT}/${baseline}_${ds}.log"
      mkdir -p "${smoke_out}"

      log "[SMOKE] baseline=${baseline} dataset=${ds}"
      if ! "${PYTHON}" -u -m flashfusion.eval.benchmark \
        --dataset "${ds}" \
        --data "${data_path}" \
        --ground-truth "${gt_path}" \
        --baselines "${baseline}" \
        --queries "${SMOKE_QUERIES}" \
        --runs 1 \
        --model "${MODEL}" \
        --max-query-latency "${SMOKE_MAX_LATENCY}" \
        --ground-truth-measurement llm \
        --output "${smoke_out}" \
        >"${smoke_log}" 2>&1; then
        echo "${baseline},${ds},fail,benchmark_command_failed,0,0,${smoke_log}" >> "${smoke_csv}"
        continue
      fi

      local status_line
      status_line="$("${PYTHON}" - "${smoke_out}" <<'PYEOF'
import json
import sys
from pathlib import Path

import pandas as pd

out_dir = Path(sys.argv[1])
metrics = out_dir / "metrics.csv"
raw = out_dir / "raw_results.jsonl"

if not metrics.exists():
    print("fail,missing_metrics,0,0")
    raise SystemExit(0)

df = pd.read_csv(metrics)
if df.empty:
    print("fail,empty_metrics,0,0")
    raise SystemExit(0)

rows = int(len(df))
processed = bool(((df.get("executed", False) == True) | (df.get("rejected", False) == True)).any())
if not processed:
    print(f"fail,no_executed_or_rejected,{rows},0")
    raise SystemExit(0)

err_rows = 0
if raw.exists():
    with raw.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ans = str(obj.get("answer", ""))
            if ans.startswith("[ERROR"):
                err_rows += 1

if err_rows >= rows:
    print(f"fail,all_rows_error,{rows},{err_rows}")
else:
    print(f"pass,ok,{rows},{err_rows}")
PYEOF
)"
      IFS=',' read -r smoke_status smoke_reason smoke_rows smoke_err <<< "${status_line}"
      echo "${baseline},${ds},${smoke_status},${smoke_reason},${smoke_rows},${smoke_err},${smoke_log}" >> "${smoke_csv}"
    done
  done

  log "[SMOKE] Wrote status: ${smoke_csv}"
  local fail_count
  fail_count="$(( $(grep -c ',fail,' "${smoke_csv}" || true) ))"
  if [[ "${fail_count}" -gt 0 ]]; then
    log "[SMOKE] FAILURES=${fail_count}"
    if [[ "${SMOKE_ABORT_ON_FAIL}" == "1" ]]; then
      echo "[ERROR] Smoke test failed. Aborting full run. See ${smoke_csv}" >&2
      exit 1
    fi
  else
    log "[SMOKE] All baseline×dataset checks passed"
  fi
}

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
  echo "[ERROR] Missing API key. Set OPENROUTER_API_KEY or GROQ_API_KEY." >&2
  exit 1
fi

if [[ -z "${TAVILY_API_KEY:-}" ]]; then
  echo "[ERROR] Missing TAVILY_API_KEY (required by AUTOIOT_PAPER)." >&2
  exit 1
fi

require_file "${WISDM_DATA}"
require_file "${MIT_ECG_DATA}"
require_file "${BUS_DATA}"
require_file "${GT_WISDM}"
require_file "${GT_MIT_ECG}"
require_file "${GT_BUS}"

mkdir -p "${OUTPUT_ROOT}" "${AGG_ROOT}"

log "Starting July26 4-baseline run"
log "Repo root     : ${REPO_ROOT}"
log "Python        : ${PYTHON}"
log "Output root   : ${OUTPUT_ROOT}"
log "Run tag       : ${RUN_TAG}"
log "Model         : ${MODEL}"
log "Baselines     : ${BASELINE_LIST[*]}"
log "Datasets      : ${DATASETS[*]}"
log "Queries       : ${QUERIES}"
log "Runs          : ${RUNS}"
log "Max latency   : ${MAX_LATENCY}s"
log "Smoke test    : ${SMOKE_TEST} (queries=${SMOKE_QUERIES}, max_latency=${SMOKE_MAX_LATENCY}s)"

if [[ "${SMOKE_TEST}" == "1" ]]; then
  run_smoke_tests
fi

for baseline in "${BASELINE_LIST[@]}"; do
  for ds in "${DATASETS[@]}"; do
    DATA_PATH="$(dataset_data_path "${ds}")"
    GT_PATH="$(dataset_gt_path "${ds}")"

    TARGET_DIR="${OUTPUT_ROOT}/${baseline}/${ds}/${RUN_TAG}"
    mkdir -p "${TARGET_DIR}"

    log "[Benchmark] baseline=${baseline} dataset=${ds} -> ${TARGET_DIR}"
    "${PYTHON}" -u -m flashfusion.eval.benchmark \
      --dataset "${ds}" \
      --data "${DATA_PATH}" \
      --ground-truth "${GT_PATH}" \
      --baselines "${baseline}" \
      --queries "${QUERIES}" \
      --runs "${RUNS}" \
      --model "${MODEL}" \
      --max-query-latency "${MAX_LATENCY}" \
      --ground-truth-measurement llm \
      --output "${TARGET_DIR}" \
      2>&1 | tee "${TARGET_DIR}/benchmark.log"

    log "[Visualize] baseline=${baseline} dataset=${ds}"
    "${PYTHON}" -m flashfusion.eval.visualize_comparison \
      --metrics "${TARGET_DIR}/metrics.csv" \
      --dataset "${ds}" \
      --accuracy-column gt_score \
      --title "${baseline} (${ds})" \
      --output "${TARGET_DIR}" \
      2>&1 | tee "${TARGET_DIR}/visualize.log"

    log "[Stage latency] baseline=${baseline} dataset=${ds}"
    run_stage_latency_export "${baseline}" "${ds}" "${TARGET_DIR}" "${DATA_PATH}"
  done
done

log "[Summary] Building cross-dataset tables"
"${PYTHON}" - "${OUTPUT_ROOT}" "${RUN_TAG}" "${AGG_ROOT}" <<'PYEOF'
import sys
from pathlib import Path

import pandas as pd

from flashfusion.eval.queries import get_queries

output_root = Path(sys.argv[1])
run_tag = sys.argv[2]
out_root = Path(sys.argv[3])
out_root.mkdir(parents=True, exist_ok=True)

rows = []
for baseline_dir in output_root.iterdir():
    if not baseline_dir.is_dir():
        continue
    if baseline_dir.name.startswith("_"):
        continue
    baseline = baseline_dir.name
    for ds in ("wisdm", "mit_ecg", "bus"):
        metrics_path = baseline_dir / ds / run_tag / "metrics.csv"
        if not metrics_path.exists():
            continue
        df = pd.read_csv(metrics_path)
        if df.empty:
            continue

        qmap = {int(q["id"]): str(q.get("complexity", "")) for q in get_queries(ds)}

        def to_query_type(c: str) -> str:
            x = (c or "").strip().lower()
            if x == "direct":
                return "direct"
            if x in {"intermediate", "reasoning"}:
                return "reasoning"
            return "oos"

        df["dataset"] = ds
        df["baseline"] = baseline
        df["query_id"] = df["query_id"].astype(int)
        df["complexity"] = df["query_id"].map(qmap).fillna("")
        df["query_type"] = df["complexity"].map(to_query_type)
        df["total_tokens"] = df["input_tokens"].astype(float) + df["output_tokens"].astype(float)
        rows.append(df)

if not rows:
    raise SystemExit("No metrics.csv files found to summarize.")

combined = pd.concat(rows, ignore_index=True)
combined.to_csv(out_root / "query_metrics_all_datasets.csv", index=False)

agg_cols = {
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
    .agg(**agg_cols)
    .sort_values(["dataset", "query_type", "baseline"])
)
by_dataset_query_type.to_csv(out_root / "summary_by_dataset_query_type.csv", index=False)

# Balanced across datasets: average per-dataset means for each query type/baseline.
balanced = (
    by_dataset_query_type.groupby(["query_type", "baseline"], as_index=False)
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
    .sort_values(["query_type", "baseline"])
)
balanced.to_csv(out_root / "summary_balanced_by_query_type.csv", index=False)

by_dataset_overall = (
    combined.groupby(["dataset", "baseline"], as_index=False)
    .agg(**agg_cols)
    .sort_values(["dataset", "baseline"])
)
by_dataset_overall.to_csv(out_root / "summary_by_dataset_overall.csv", index=False)

overall_balanced = (
    by_dataset_overall.groupby(["baseline"], as_index=False)
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
    .sort_values(["baseline"])
)
overall_balanced.to_csv(out_root / "summary_balanced_overall.csv", index=False)

print(f"Wrote: {out_root / 'query_metrics_all_datasets.csv'}")
print(f"Wrote: {out_root / 'summary_by_dataset_query_type.csv'}")
print(f"Wrote: {out_root / 'summary_balanced_by_query_type.csv'}")
print(f"Wrote: {out_root / 'summary_by_dataset_overall.csv'}")
print(f"Wrote: {out_root / 'summary_balanced_overall.csv'}")
PYEOF

log "Complete"
log "Per-run outputs: ${OUTPUT_ROOT}/<baseline>/<dataset>/${RUN_TAG}/"
log "Cross-dataset summary tables: ${AGG_ROOT}"
if [[ "${SMOKE_TEST}" == "1" ]]; then
  log "Smoke report: ${SMOKE_ROOT}/smoke_status.csv"
fi
