#!/usr/bin/env bash
set -euo pipefail

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="${PYTHON:-python3}"
else
    PYTHON="${PYTHON:-python}"
fi

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
    echo "[ERROR] Missing API key. Set OPENROUTER_API_KEY or GROQ_API_KEY." >&2
    exit 1
fi

DATASET="wisdm"
RUN_TAG="${RUN_TAG:-$(date '+%Y%m%d_%H%M%S')}"
OUT_BASE="${REPO_ROOT}/flashfusion/results/ff_and_react_qwen"
FF_OUT="${FF_OUT:-${OUT_BASE}/FLASH_FUSION/${DATASET}}"
CACHE_OUT="${CACHE_OUT:-${OUT_BASE}/FLASH_FUSION_CACHE/${DATASET}}"
CACHE_PATH="${CACHE_PATH:-${REPO_ROOT}/flashfusion/eval/cache/cache_registry.json}"
SEMANTIC_CACHE_PATH="${SEMANTIC_CACHE_PATH:-}"
FF_EVAL_MODULE="${FF_EVAL_MODULE:-flashfusion.eval.run_benchmark}"
FF_EVAL_EXTRA_ARGS="${FF_EVAL_EXTRA_ARGS:-}"
CACHE_EVAL_EXTRA_ARGS="${CACHE_EVAL_EXTRA_ARGS:-}"

mkdir -p "${FF_OUT}" "${CACHE_OUT}"

run_eval() {
    local baseline="$1"
    local out_dir="$2"
    shift 2
    log "Running ${baseline} on ${DATASET} -> ${out_dir}"
    "${PYTHON}" -m "${FF_EVAL_MODULE}" \
        --dataset "${DATASET}" \
        --baseline "${baseline}" \
        --output-dir "${out_dir}" \
        --run-tag "${RUN_TAG}" \
        "$@" \
        ${FF_EVAL_EXTRA_ARGS} \
        2>&1 | tee "${out_dir}/run_${RUN_TAG}.log"
}

log "Repo root: ${REPO_ROOT}"
log "Python: ${PYTHON}"
log "WISDM-only rerun. FF=${FF_OUT} CACHE=${CACHE_OUT}"

run_eval "FLASH_FUSION" "${FF_OUT}"

cache_args=(--cache-path "${CACHE_PATH}")
if [[ -n "${SEMANTIC_CACHE_PATH}" ]]; then
    cache_args+=(--semantic-cache-path "${SEMANTIC_CACHE_PATH}")
fi

# shellcheck disable=SC2068
run_eval "FLASH_FUSION_CACHE" "${CACHE_OUT}" ${cache_args[@]} ${CACHE_EVAL_EXTRA_ARGS}

log "Done. Compare misses via deterministic_fallback_reason in ${CACHE_OUT}/run_*/raw_results.jsonl"