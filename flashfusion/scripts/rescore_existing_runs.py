"""Re-run LLM ground-truth scoring for existing benchmark artifacts.

The baseline inference in ``raw_results.jsonl`` is reused. Eligible rows are
rejudged, judge artifacts are replaced, and only ``gt_score`` and ``gt_method``
are refreshed in the existing ``metrics.csv``.

Typical use:
    python -m flashfusion.scripts.rescore_existing_runs \
        --datasets bus wisdm mit_ecg --runs run_2 run_3
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS, DEFAULT_GROUND_TRUTH_PATHS
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.ground_truth_llm_judge import run_llm_ground_truth_judge


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = (
    PROJECT_ROOT
    / "flashfusion"
    / "results"
    / "ff_and_react_qwen"
    / "FLASH_FUSION_CACHE"
)


def _load_raw_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected a JSON object at {path}:{line_number}")
            row["_source_file"] = str(path)
            rows.append(row)
    return rows


def _eligible_for_llm_judge(row: dict[str, Any]) -> bool:
    return not (bool(row.get("rejected", False)) and not bool(row.get("executed", False)))


def _update_metrics(
    metrics_path: Path,
    judgments_df: pd.DataFrame,
    ground_truth_by_id: dict[int, Any],
) -> pd.DataFrame:
    metrics_df = pd.read_csv(metrics_path)
    judgment_by_key = {
        (str(row["baseline"]), int(row["query_id"])): float(row["llm_score"])
        for row in judgments_df.to_dict(orient="records")
    }

    for index, row in metrics_df.iterrows():
        key = (str(row["baseline"]), int(row["query_id"]))
        score = judgment_by_key.get(key)
        if score is not None:
            metrics_df.at[index, "gt_score"] = score
            metrics_df.at[index, "gt_method"] = "llm_judge_score"
            continue

        ground_truth = ground_truth_by_id.get(key[1])
        if (
            bool(row.get("rejected", False))
            and not bool(row.get("executed", False))
            and ground_truth is not None
        ):
            if ground_truth.expected_rejection:
                metrics_df.at[index, "gt_score"] = 1.0
                metrics_df.at[index, "gt_method"] = "guardrail_skip_expected_rejection"
            else:
                metrics_df.at[index, "gt_score"] = 0.0
                metrics_df.at[index, "gt_method"] = "guardrail_skip_unexpected_rejection"
            continue

        raise ValueError(
            "No LLM judgment produced for eligible metrics row "
            f"baseline={key[0]!r}, query_id={key[1]} in {metrics_path}"
        )

    temporary_path = metrics_path.with_suffix(".csv.tmp")
    metrics_df.to_csv(temporary_path, index=False)
    temporary_path.replace(metrics_path)
    return metrics_df


def rescore_run(
    *,
    run_dir: Path,
    dataset: str,
    model_name: str,
    api_key: str,
) -> None:
    raw_results_path = run_dir / "raw_results.jsonl"
    metrics_path = run_dir / "metrics.csv"
    if not raw_results_path.is_file() or not metrics_path.is_file():
        raise FileNotFoundError(f"Missing raw_results.jsonl or metrics.csv in {run_dir}")

    ground_truth_path = PROJECT_ROOT / DEFAULT_GROUND_TRUTH_PATHS[dataset]
    data_path = PROJECT_ROOT / DEFAULT_DATA_PATHS[dataset]
    ground_truth_by_id = load_ground_truth(str(ground_truth_path))
    raw_rows = _load_raw_rows(raw_results_path)
    eligible_rows = [row for row in raw_rows if _eligible_for_llm_judge(row)]
    if not eligible_rows:
        raise ValueError(f"No rows eligible for LLM judging in {raw_results_path}")

    print(
        f"[{dataset}/{run_dir.name}] judging {len(eligible_rows)}/{len(raw_rows)} rows",
        flush=True,
    )
    judgments_df, _, _ = run_llm_ground_truth_judge(
        rows=eligible_rows,
        ground_truth_by_id=ground_truth_by_id,
        output_dir=str(run_dir / "ground_truth_llm_judge"),
        model_name=model_name,
        api_key=api_key,
        data_path=str(data_path),
        dataset=dataset,
    )
    if len(judgments_df) != len(eligible_rows):
        raise ValueError(
            f"Expected {len(eligible_rows)} judgments, received {len(judgments_df)} "
            f"for {dataset}/{run_dir.name}"
        )

    metrics_df = _update_metrics(metrics_path, judgments_df, ground_truth_by_id)
    missing_count = int((metrics_df["gt_method"] == "llm_judge_score_missing").sum())
    if missing_count:
        raise ValueError(
            f"Rescoring left {missing_count} llm_judge_score_missing rows in {metrics_path}"
        )
    print(
        f"[{dataset}/{run_dir.name}] wrote {len(judgments_df)} judgments; "
        "llm_judge_score_missing=0",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=tuple(DEFAULT_DATA_PATHS),
        default=["bus", "wisdm", "mit_ecg"],
    )
    parser.add_argument("--runs", nargs="+", default=["run_2", "run_3"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("Set OPENROUTER_API_KEY or GROQ_API_KEY before rescoring")

    results_root = args.results_root.resolve()
    for dataset in args.datasets:
        for run_name in args.runs:
            rescore_run(
                run_dir=results_root / dataset / run_name,
                dataset=dataset,
                model_name=args.model,
                api_key=api_key,
            )


if __name__ == "__main__":
    main()