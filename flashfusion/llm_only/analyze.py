"""
flashfusion.llm_only.analyze — grade trials with the GT judge, aggregate to summary.

Reads `results/trials.csv` (written by experiment.py), runs the existing
`judge_rows_with_llm` against the WISDM/ECG ground truth JSONs, and emits
`results/summary.csv` aggregated by (baseline, dataset).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.ground_truth_llm_judge import judge_rows_with_llm
from flashfusion.eval.queries import DATASET_MIT_ECG, DATASET_WISDM


RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRIALS_CSV = RESULTS_DIR / "trials.csv"
JUDGMENTS_CSV = RESULTS_DIR / "judgments.csv"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"

REPO_ROOT = Path(__file__).resolve().parents[2]
GT_WISDM = REPO_ROOT / "flashfusion/eval/ground_truth.json"
GT_ECG = REPO_ROOT / "flashfusion/eval/ground_truth_mit_ecg.json"


def _grade_dataset(
    trials: pd.DataFrame, dataset_name: str, gt_path: Path, judge_key: str,
    model: str, api_key: str,
) -> pd.DataFrame:
    """Grade all trials in the given dataset against its ground-truth JSON."""
    subset = trials[trials["dataset"] == dataset_name].copy()
    if subset.empty:
        return pd.DataFrame()

    if not gt_path.exists():
        print(f"!! No GT for {dataset_name} at {gt_path}; skipping grading.")
        return pd.DataFrame()

    gt_by_id = load_ground_truth(str(gt_path))

    rows: list[dict] = []
    for _, r in subset.iterrows():
        rows.append({
            "baseline": r["baseline"],
            "query_id": int(r["question_id"]),
            "query": r["question"],
            "answer": r["answer"],
            "executed": True,           # LLM-Only always "executes" the call
            "rejected": False,
            # bus/wisdm/ecg trials don't expose final_code; FF trials don't
            # propagate it here either — judge falls back to answer text only.
            "final_code": "",
            "_source_file": f"llm_only_study::{dataset_name}",
            "_trial_index": int(r.name),
        })

    judged = judge_rows_with_llm(
        rows=rows,
        ground_truth_by_id=gt_by_id,
        dataset=judge_key,
        model_name=model,
        api_key=api_key,
    )
    # Re-attach trial index so we can merge back
    judged = judged.reset_index(drop=True)
    return judged


def grade_all_trials(model: str, api_key: str) -> pd.DataFrame:
    if not TRIALS_CSV.exists():
        sys.exit(f"No trials at {TRIALS_CSV}. Run experiment.py first.")
    trials = pd.read_csv(TRIALS_CSV)

    judged_frames: list[pd.DataFrame] = []
    for ds_name, gt_path, judge_key in [
        ("wisdm", GT_WISDM, DATASET_WISDM),
        ("ecg",   GT_ECG,   DATASET_MIT_ECG),
    ]:
        df = _grade_dataset(trials, ds_name, gt_path, judge_key, model, api_key)
        if not df.empty:
            df["dataset"] = ds_name
            judged_frames.append(df)

    if not judged_frames:
        print("No trials graded.")
        return pd.DataFrame()

    judged = pd.concat(judged_frames, ignore_index=True)
    judged.to_csv(JUDGMENTS_CSV, index=False)
    print(f"Wrote {len(judged)} judgments to {JUDGMENTS_CSV}")
    return judged


def build_summary() -> pd.DataFrame:
    """Aggregate per-trial metrics + judgments into a per-(baseline, dataset) summary."""
    if not TRIALS_CSV.exists():
        sys.exit(f"No trials at {TRIALS_CSV}.")
    trials = pd.read_csv(TRIALS_CSV)

    score_lookup: dict[tuple[str, str, int], float] = {}
    if JUDGMENTS_CSV.exists():
        j = pd.read_csv(JUDGMENTS_CSV)
        for _, r in j.iterrows():
            score_lookup[(r["baseline"], r["dataset"], int(r["query_id"]))] = float(
                r["llm_score"]
            )

    def _score(row: pd.Series) -> float | None:
        key = (row["baseline"], row["dataset"], int(row["question_id"]))
        return score_lookup.get(key, None)

    trials["accuracy_score"] = trials.apply(_score, axis=1)
    trials.to_csv(TRIALS_CSV, index=False)  # write back with scores

    agg = (
        trials.groupby(["baseline", "dataset"])
        .agg(
            n_trials=("rep", "count"),
            accuracy_mean=("accuracy_score", "mean"),
            accuracy_std=("accuracy_score", "std"),
            latency_mean=("latency_s", "mean"),
            latency_std=("latency_s", "std"),
            input_tokens_mean=("prompt_tokens", "mean"),
            input_tokens_std=("prompt_tokens", "std"),
            output_tokens_mean=("completion_tokens", "mean"),
            cost_mean=("cost_usd", "mean"),
            cost_std=("cost_usd", "std"),
            truncation_mean=("truncation_pct", "mean"),
        )
        .reset_index()
    )
    agg.to_csv(SUMMARY_CSV, index=False)
    return agg


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade trials + write summary.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--skip-grading", action="store_true",
        help="reuse existing judgments.csv; only rebuild summary",
    )
    args = parser.parse_args()

    if not args.skip_grading:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            sys.exit("Error: GROQ_API_KEY environment variable not set.")
        grade_all_trials(args.model, api_key)

    summary = build_summary()
    print("\n=== Summary ===")
    print(summary.to_string(index=False))
    print(f"\nWrote summary to {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
