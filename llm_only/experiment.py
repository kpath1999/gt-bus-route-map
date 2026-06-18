"""
flashfusion.llm_only.experiment — sweep driver.

Cells: {bus, WISDM, ECG} × {LLM_ONLY, FLASH_FUSION} × 4 questions × N reps.
Each trial writes one row to results/trials.csv plus a JSON archive under
results/raw_responses/.

Datasets are treated as the *size axis* (bus=small, WISDM=medium, ECG=large).
LLM_ONLY truncates the prompt when the dataset exceeds the context budget;
FLASH_FUSION uses the existing staged pipeline at full data.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import sys
import time
from pathlib import Path

import pandas as pd

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.queries import MIT_ECG_QUERIES, WISDM_QUERIES
from llm_only.runner import (
    TrialResult,
    run_flash_fusion_trial,
    run_llm_only_trial,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRIALS_CSV = RESULTS_DIR / "trials.csv"
RAW_DIR = RESULTS_DIR / "raw_responses"

COST_CAP_USD = 5.0

# ---------------------------------------------------------------------------
# Datasets, loaders, and question subsets
# ---------------------------------------------------------------------------

BUS_DATA_PATH = REPO_ROOT / "chat/data/bus/bus_data.csv"
WISDM_DATA_PATH = REPO_ROOT / "chat/data/imu/WISDM_ar_v1.1_raw.txt"
ECG_DATA_PATH = REPO_ROOT / "data/Agent_dataset/ECG.0/MIT_arrythmia_v1.txt"


def _load_bus() -> pd.DataFrame:
    return pd.read_csv(BUS_DATA_PATH)


def _load_wisdm() -> pd.DataFrame:
    from flashfusion.pipeline.loader import load_wisdm
    return load_wisdm(str(WISDM_DATA_PATH))


def _load_ecg() -> pd.DataFrame:
    from flashfusion.pipeline.loader import load_mit_arrythmia
    return load_mit_arrythmia(str(ECG_DATA_PATH))


# Question subsets — 4 per dataset. We pick the executable (non out-of-scope)
# queries so accuracy is meaningful. Bus has no GT so accuracy stays null.
BUS_QUESTIONS = [
    # id, text — we synthesize 4 bus questions analogous in shape to WISDM Q1-Q8
    (1, "What is the maximum recorded accel_mean value in this trip?"),
    (2, "How many samples have accel_variance greater than 1.0?"),
    (3, "What is the average accel_mean across the entire trip?"),
    (4, "Identify the time window with the highest acceleration variability."),
]

WISDM_QUESTION_IDS = [1, 2, 3, 4]  # all "direct" complexity from WISDM_QUERIES
ECG_QUESTION_IDS = [1, 2, 3, 4]    # all "direct" from MIT_ECG_QUERIES


def _wisdm_questions() -> list[tuple[int, str]]:
    return [(q["id"], q["text"]) for q in WISDM_QUERIES if q["id"] in WISDM_QUESTION_IDS]


def _ecg_questions() -> list[tuple[int, str]]:
    return [(q["id"], q["text"]) for q in MIT_ECG_QUERIES if q["id"] in ECG_QUESTION_IDS]


DATASETS = {
    "bus":   {"loader": _load_bus,   "questions": BUS_QUESTIONS,         "has_gt": False},
    "wisdm": {"loader": _load_wisdm, "questions": _wisdm_questions(),    "has_gt": True},
    "ecg":   {"loader": _load_ecg,   "questions": _ecg_questions(),      "has_gt": True},
}


# ---------------------------------------------------------------------------
# CSV writing — one row per trial, appended atomically
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "baseline", "dataset", "n_rows", "question_id", "question", "rep",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "prompt_tokens_estimated", "latency_s", "cost_usd",
    "truncation_pct", "prompt_sha256", "answer", "api_error", "model",
]


def _append_trial(result: TrialResult) -> None:
    is_new = not TRIALS_CSV.exists()
    TRIALS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with TRIALS_CSV.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        row = dataclasses.asdict(result)
        # answers can contain newlines/commas — DictWriter handles quoting.
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def _print(msg: str) -> None:
    print(msg, flush=True)


def run_sweep(
    *,
    datasets: list[str],
    baselines: list[str],
    reps: int,
    model: str,
    api_key: str,
    phase1: bool,
) -> None:
    """Run the sweep across (dataset, baseline, question, rep) cells."""
    cumulative_cost = 0.0
    started = time.time()
    n_trials = 0

    for dataset_name in datasets:
        spec = DATASETS[dataset_name]
        _print(f"\n=== Loading dataset: {dataset_name} ===")
        df = spec["loader"]()
        _print(f"    rows={len(df):,}  cols={list(df.columns)}")

        questions = spec["questions"]
        if phase1:
            questions = questions[:1]

        for qid, qtext in questions:
            for rep in range(1, reps + 1):
                for baseline in baselines:
                    if cumulative_cost > COST_CAP_USD:
                        _print(
                            f"!! Cost cap ${COST_CAP_USD:.2f} reached "
                            f"(cumulative ${cumulative_cost:.4f}); aborting sweep."
                        )
                        return

                    label = (
                        f"[{baseline}] {dataset_name} Q{qid} rep{rep} "
                        f"(rows={len(df):,})"
                    )
                    _print(label)

                    t0 = time.time()
                    if baseline == "LLM_ONLY":
                        result = run_llm_only_trial(
                            dataset=dataset_name,
                            df=df,
                            n_rows=len(df),
                            question_id=qid,
                            question=qtext,
                            rep=rep,
                            model=model,
                            api_key=api_key,
                            raw_response_dir=RAW_DIR,
                        )
                    elif baseline == "FLASH_FUSION":
                        result = run_flash_fusion_trial(
                            dataset=dataset_name,
                            df=df,
                            question_id=qid,
                            question=qtext,
                            rep=rep,
                            model=model,
                            api_key=api_key,
                        )
                    else:
                        raise ValueError(f"Unknown baseline {baseline}")

                    elapsed = time.time() - t0
                    cumulative_cost += result.cost_usd
                    n_trials += 1
                    _print(
                        f"    → tokens={result.prompt_tokens:,}+{result.completion_tokens:,} "
                        f"trunc={result.truncation_pct*100:.1f}% "
                        f"lat={result.latency_s:.1f}s cost=${result.cost_usd:.4f} "
                        f"err={result.api_error or 'none'} (wall={elapsed:.1f}s)"
                    )
                    _append_trial(result)

    wall = time.time() - started
    _print(
        f"\n=== Sweep complete: {n_trials} trials, "
        f"cumulative cost ${cumulative_cost:.4f}, wall {wall:.0f}s ==="
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-Only vs Flash-Fusion sweep")
    parser.add_argument(
        "--phase", type=int, choices=[1, 2], default=2,
        help="phase 1 = dry-run (1 question, 1 rep, all datasets/baselines); "
             "phase 2 = full sweep",
    )
    parser.add_argument(
        "--datasets", default="bus,wisdm,ecg",
        help="comma-separated subset",
    )
    parser.add_argument(
        "--baselines", default="LLM_ONLY,FLASH_FUSION",
        help="comma-separated subset",
    )
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--reset", action="store_true",
        help="delete results/trials.csv before running",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("Error: set OPENROUTER_API_KEY (or GROQ_API_KEY for transition compatibility)")

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    baselines = [b.strip() for b in args.baselines.split(",") if b.strip()]
    reps = 1 if args.phase == 1 else args.reps

    if args.reset and TRIALS_CSV.exists():
        TRIALS_CSV.unlink()

    _print(
        f"Phase {args.phase}: datasets={datasets} baselines={baselines} "
        f"reps={reps} model={args.model} cap=${COST_CAP_USD:.2f}"
    )

    run_sweep(
        datasets=datasets,
        baselines=baselines,
        reps=reps,
        model=args.model,
        api_key=api_key,
        phase1=(args.phase == 1),
    )


if __name__ == "__main__":
    main()
