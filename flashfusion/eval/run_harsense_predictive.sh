#!/usr/bin/env bash
# run_harsense_predictive.sh
# Run predictive queries (13-16) for HARGPT_PAPER and LLMSENSE_PAPER
# across bus, wisdm, and mit_ecg datasets, then merge the predictive
# results with the existing July26 direct/reasoning/OOS results.
#
# Results:
#   new predictive runs:  flashfusion/results/harsense_predictive/<dataset>/
#   combined results:     flashfusion/results/harsense_combined/<baseline>/<dataset>/july26_full/

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
PREDICTIVE_ROOT="${PREDICTIVE_ROOT:-flashfusion/results/harsense_predictive}"
JULY26_ROOT="${JULY26_ROOT:-flashfusion/results/july26}"
COMBINED_ROOT="${COMBINED_ROOT:-flashfusion/results/harsense_combined}"
RUN_DIR="${RUN_DIR:-july26_full}"

# run_predictive <dataset> <data_path> <ground_truth>
run_predictive() {
    local dataset="$1"
    local data_path="$2"
    local ground_truth="$3"

    echo ""
    echo "=== Predictive queries 13-16: ${dataset} ==="
    "${PYTHON}" -m flashfusion.eval.benchmark \
        --dataset "${dataset}" \
        --data "${data_path}" \
        --baselines HARGPT_PAPER,LLMSENSE_PAPER \
        --queries 13,14,15,16 \
        --runs "${RUNS}" \
        --model "${MODEL}" \
        --ground-truth "${ground_truth}" \
        --output "${PREDICTIVE_ROOT}/${dataset}"
}

run_predictive bus \
    data/bus/bus_data_enriched_behavior.csv \
    flashfusion/eval/ground_truth/ground_truth_bus.json

run_predictive wisdm \
    data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt \
    flashfusion/eval/ground_truth/ground_truth_wisdm.json

run_predictive mit_ecg \
    data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt \
    flashfusion/eval/ground_truth/ground_truth_mit_ecg.json

echo ""
echo "=== Merging predictive results with July26 results ==="
"${PYTHON}" - <<'PYEOF'
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path.cwd())).resolve()
PREDICTIVE_ROOT = Path(os.environ.get("PREDICTIVE_ROOT", REPO_ROOT / "flashfusion" / "results" / "harsense_predictive"))
JULY26_ROOT = Path(os.environ.get("JULY26_ROOT", REPO_ROOT / "flashfusion" / "results" / "july26"))
COMBINED_ROOT = Path(os.environ.get("COMBINED_ROOT", REPO_ROOT / "flashfusion" / "results" / "harsense_combined"))
RUN_DIR = os.environ.get("RUN_DIR", "july26_full")

DATASETS = {
    "bus": "bus",
    "wisdm": "wisdm",
    "mit_ecg": "mit_ecg",
}
BASELINES = ["HARGPT_PAPER", "LLMSENSE_PAPER"]


def merge_metrics(old_path: Path, new_path: Path, out_path: Path, baseline: str) -> None:
    """Concatenate old July26 metrics with new predictive metrics for one baseline."""
    old_df = pd.read_csv(old_path)
    new_df = pd.read_csv(new_path)

    # The new file contains both HARGPT_PAPER and LLMSENSE_PAPER rows.
    new_df = new_df[new_df["baseline"] == baseline].copy()

    # Normalize column order: use union so missing optional columns are filled with NaN.
    cols = list(old_df.columns)
    for c in new_df.columns:
        if c not in cols:
            cols.append(c)
    old_df = old_df.reindex(columns=cols)
    new_df = new_df.reindex(columns=cols)

    combined = pd.concat([old_df, new_df], ignore_index=True)
    combined = combined.sort_values(["run_id", "query_id"], kind="mergesort").reset_index(drop=True)
    combined.to_csv(out_path, index=False)


def merge_raw_results(old_path: Path, new_path: Path, out_path: Path, baseline: str) -> None:
    """Concatenate old July26 raw_results.jsonl with new predictive rows for one baseline."""
    rows: list[dict] = []
    for path in (old_path, new_path):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))

    rows = [r for r in rows if r.get("baseline") == baseline]
    rows.sort(key=lambda r: (r.get("run_id", 0), r.get("query_id", 0)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


for baseline in BASELINES:
    for dataset_dir in DATASETS.values():
        old_dir = JULY26_ROOT / baseline / dataset_dir / RUN_DIR
        new_metrics = PREDICTIVE_ROOT / dataset_dir / "metrics.csv"
        new_raw = PREDICTIVE_ROOT / dataset_dir / "raw_results.jsonl"

        if not old_dir.exists():
            print(f"[WARN] Missing July26 directory: {old_dir}; skipping {baseline}/{dataset_dir}")
            continue
        if not new_metrics.exists():
            print(f"[WARN] Missing predictive metrics: {new_metrics}; skipping {baseline}/{dataset_dir}")
            continue

        out_dir = COMBINED_ROOT / baseline / dataset_dir / RUN_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        merge_metrics(
            old_dir / "metrics.csv",
            new_metrics,
            out_dir / "metrics.csv",
            baseline,
        )
        print(f"[OK] Wrote combined metrics: {out_dir / 'metrics.csv'}")

        if new_raw.exists() and (old_dir / "raw_results.jsonl").exists():
            merge_raw_results(
                old_dir / "raw_results.jsonl",
                new_raw,
                out_dir / "raw_results.jsonl",
                baseline,
            )
            print(f"[OK] Wrote combined raw results: {out_dir / 'raw_results.jsonl'}")

print(f"\nDone. Combined results are in: {COMBINED_ROOT}")
PYEOF

echo ""
echo "Done. Predictive results: ${PREDICTIVE_ROOT}"
echo "Done. Combined results:    ${COMBINED_ROOT}"
