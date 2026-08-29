#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="${PYTHON:-python3}"
else
    PYTHON="${PYTHON:-python}"
fi

OUTPUT_ROOT="${OUTPUT_ROOT:-results/hybridcachevsfuzzy/fuzzy}"
mkdir -p "${OUTPUT_ROOT}"

for dataset in bus wisdm mit_ecg; do
    echo "Running fuzzy cache matching for ${dataset}"
    "${PYTHON}" -m flashfusion.eval.benchmark_hybrid_cache \
        --dataset "${dataset}" \
        --mode fuzzy \
        --output "${OUTPUT_ROOT}/${dataset}_match_benchmark.json" \
        --output-csv "${OUTPUT_ROOT}/${dataset}_match_rows.csv" \
        --no-warmup
done