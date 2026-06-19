"""Mini-experiment: Flash-Fusion scores across model sizes and datasets.

Runs FLASH_FUSION for the selected datasets and models, then computes accuracy
using the benchmark-style ground-truth LLM judge.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

# Ensure `flashfusion` is importable when run from subdirectories.
_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from flashfusion.config import DEFAULT_MODEL, MODEL_RATE_PER_1M_TOKENS
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.ground_truth_llm_judge import run_llm_ground_truth_judge
from flashfusion.eval.metrics import aggregate_metrics
from flashfusion.eval.queries import SUPPORTED_DATASETS, get_queries
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult

DEFAULT_SMALL_MODELS: list[str] = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-3.1-8b-instruct",
]

DEFAULT_DATA_PATHS: dict[str, str] = {
    "wisdm": "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt",
    "mit_ecg": "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt",
    "bus": "data/bus/bus_data.csv",
}

DEFAULT_GT_PATHS: dict[str, str] = {
    "wisdm": "flashfusion/eval/ground_truth/ground_truth_wisdm.json",
    "mit_ecg": "flashfusion/eval/ground_truth/ground_truth_mit_ecg.json",
    "bus": "flashfusion/eval/ground_truth/ground_truth_bus.json",
}

DEFAULT_OUTPUT_DIR = "flashfusion/miniexp/results/modeltype"


def _is_forbidden_chat_data_path(path: str) -> bool:
    normalized = os.path.normpath(path).replace("\\", "/")
    if normalized == "chat/data" or normalized.startswith("chat/data/"):
        return True
    return "/chat/data/" in normalized


def _resolve_dataset_file(dataset: str, data_path: str | None) -> Path:
    raw = data_path or DEFAULT_DATA_PATHS[dataset]
    if _is_forbidden_chat_data_path(raw):
        raise ValueError(f"Path {raw!r} is under chat/data, which is not allowed.")

    p = Path(raw)
    if not p.is_absolute():
        p = (_repo_root / p).resolve()

    if p.is_dir():
        suffix = {
            "wisdm": "WISDM_ar_v1.1_raw.txt",
            "mit_ecg": "MIT_arrythmia_v1.txt",
            "bus": "bus_data.csv",
        }[dataset]
        p = p / suffix

    if not p.exists():
        raise FileNotFoundError(f"Dataset path not found for {dataset}: {p}")

    normalized = p.as_posix()
    if "/data/" not in normalized and not normalized.startswith("data/"):
        raise ValueError(f"Dataset path must be under data/: {p}")
    return p


def _resolve_ground_truth_file(dataset: str, gt_path: str | None) -> Path:
    raw = gt_path or DEFAULT_GT_PATHS[dataset]
    p = Path(raw)
    if not p.is_absolute():
        p = (_repo_root / p).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Ground-truth path not found for {dataset}: {p}")
    return p


def _safe_model_dirname(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


def _rows_for_judge(results: list[RunResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in results:
        if r.rejected and not r.executed:
            continue
        row = dataclasses.asdict(r)
        row["_source_file"] = "[in_memory_modeltype_run]"
        rows.append(row)
    return rows


def _run_single_query(
    query_text: str,
    sampled_df: pd.DataFrame,
    model: str,
    api_key: str,
) -> RunResult:
    client = LLMClient(model_name=model, api_key=api_key)
    runner = BaselineRunner(mode="FLASH_FUSION", df=sampled_df, client=client)
    try:
        return runner.run(query_text)
    except Exception as exc:
        r = RunResult(
            baseline="FLASH_FUSION",
            model=model,
            query=query_text,
            answer=f"[ERROR] {type(exc).__name__}: {exc}",
            executed=False,
            rejected=False,
        )
        r.input_tokens = client.total_input_tokens()
        r.output_tokens = client.total_output_tokens()
        r.cost_usd = client.total_cost_usd()
        return r


def _validate_models(models: list[str]) -> None:
    unknown = [m for m in models if m not in MODEL_RATE_PER_1M_TOKENS]
    if unknown:
        raise ValueError(
            "Unknown model(s): "
            f"{unknown}. Add rates in flashfusion/config.py or pass supported model IDs."
        )


def run_experiment(
    datasets: list[str],
    models: list[str],
    query_ids: list[int] | None,
    data_paths: dict[str, str | None],
    gt_paths: dict[str, str | None],
    output_dir: str,
    judge_model: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")

    os.makedirs(output_dir, exist_ok=True)
    all_metrics: list[pd.DataFrame] = []

    for dataset in datasets:
        data_file = _resolve_dataset_file(dataset, data_paths.get(dataset))
        gt_file = _resolve_ground_truth_file(dataset, gt_paths.get(dataset))
        gt_by_id = load_ground_truth(str(gt_file))
        query_defs = get_queries(dataset)
        valid_ids = {q["id"] for q in query_defs}
        query_map = {q["id"]: q["text"] for q in query_defs}

        if query_ids is None:
            active_ids = sorted(valid_ids)
        else:
            invalid = [qid for qid in query_ids if qid not in valid_ids]
            if invalid:
                raise ValueError(f"Invalid query IDs for {dataset}: {invalid}")
            active_ids = list(query_ids)

        print(f"[{dataset}] Loading dataset once: {data_file}", flush=True)
        df_full = load_dataset_by_name(str(data_file), dataset)
        if df_full.empty:
            raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")

        for model in models:
            print(f"[{dataset}] Model: {model}", flush=True)
            run_results: list[RunResult] = []

            for qid in active_ids:
                query_text = query_map[qid]
                print(f"[{dataset}] [{model}] Q{qid}: {query_text[:72]}...", flush=True)
                rr = _run_single_query(
                    query_text=query_text,
                    sampled_df=df_full,
                    model=model,
                    api_key=api_key,
                )
                run_results.append(rr)

            model_dir = os.path.join(output_dir, dataset, _safe_model_dirname(model))
            os.makedirs(model_dir, exist_ok=True)

            with open(os.path.join(model_dir, "raw_results.jsonl"), "w", encoding="utf-8") as fh:
                for r in run_results:
                    row = dataclasses.asdict(r)
                    row["dataset"] = dataset
                    fh.write(json.dumps(row, ensure_ascii=True) + "\n")

            rows = _rows_for_judge(run_results)
            judge_out = os.path.join(model_dir, "ground_truth_llm_judge")
            if rows:
                judgments_df, _, _ = run_llm_ground_truth_judge(
                    rows=rows,
                    ground_truth_by_id=gt_by_id,
                    output_dir=judge_out,
                    model_name=judge_model,
                    api_key=api_key,
                    data_path=str(data_file),
                    dataset=dataset,
                )
            else:
                judgments_df = pd.DataFrame()

            metrics_df = aggregate_metrics(
                run_results,
                llm_judgments_df=judgments_df,
                ground_truth_by_id=gt_by_id,
                query_defs=query_defs,
            )
            metrics_df["dataset"] = dataset
            metrics_df["model_under_test"] = model
            metrics_df["judge_model"] = judge_model
            metrics_df["data_path"] = str(data_file)
            metrics_df["ground_truth_path"] = str(gt_file)
            metrics_df.to_csv(os.path.join(model_dir, "metrics.csv"), index=False)
            all_metrics.append(metrics_df)

    if all_metrics:
        query_metrics_df = pd.concat(all_metrics, ignore_index=True)
    else:
        query_metrics_df = pd.DataFrame()

    summary_df = (
        query_metrics_df.groupby(["dataset", "model_under_test", "judge_model"], as_index=False)
        .agg(
            avg_accuracy=("gt_score", "mean"),
            avg_latency_s=("latency_s", "mean"),
            avg_cost_usd=("cost_usd", "mean"),
            execution_rate=("executed", "mean"),
            query_count=("query_id", "count"),
        )
        .sort_values(["dataset", "model_under_test"])
        .reset_index(drop=True)
    )

    query_metrics_df.to_csv(os.path.join(output_dir, "metrics_vs_model_type.csv"), index=False)
    summary_df.to_csv(os.path.join(output_dir, "modeltype_summary.csv"), index=False)

    with open(os.path.join(output_dir, "run_metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "datasets": datasets,
                "models": models,
                "judge_model": judge_model,
                "query_ids": query_ids,
            },
            fh,
            ensure_ascii=True,
            indent=2,
        )

    return query_metrics_df, summary_df


def plot_results(summary_df: pd.DataFrame, output_dir: str) -> None:
    if summary_df.empty:
        print("No summary data to plot.")
        return

    datasets = list(summary_df["dataset"].unique())
    models = list(summary_df["model_under_test"].unique())
    x = list(range(len(datasets)))
    width = 0.35 if len(models) <= 2 else (0.8 / max(len(models), 1))

    plt.figure(figsize=(10, 6))
    for idx, model in enumerate(models):
        subset = summary_df[summary_df["model_under_test"] == model]
        y = []
        for ds in datasets:
            hit = subset[subset["dataset"] == ds]
            y.append(float(hit["avg_accuracy"].iloc[0]) if not hit.empty else 0.0)
        offset = (idx - (len(models) - 1) / 2) * width
        plt.bar([v + offset for v in x], y, width=width, label=model)

    plt.xticks(x, datasets)
    plt.ylim(0, 1.05)
    plt.ylabel("Average Query Accuracy")
    plt.title("Flash-Fusion Accuracy by Dataset and Model")
    plt.grid(True, axis="y", alpha=0.3)
    plt.legend()
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "metrics_vs_model_type.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved plot to {out_path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Flash-Fusion model-size comparison with benchmark-style GT scoring.",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(SUPPORTED_DATASETS),
        choices=list(SUPPORTED_DATASETS),
        help="Datasets to evaluate (default: wisdm mit_ecg bus)",
    )
    parser.add_argument(
        "--query-ids",
        nargs="+",
        type=int,
        default=None,
        help="Optional query IDs subset. Default is all dataset queries (Q1-Q12).",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Models under test. Default is the two smaller models.",
    )
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_MODEL,
        help=(
            "Model used by the ground-truth LLM judge "
            f"(default: {DEFAULT_MODEL})"
        ),
    )
    parser.add_argument("--wisdm-data", default=None, help="Override WISDM data file/dir")
    parser.add_argument("--mit-ecg-data", default=None, help="Override MIT ECG data file/dir")
    parser.add_argument("--bus-data", default=None, help="Override bus data file/dir")
    parser.add_argument("--wisdm-gt", default=None, help="Override WISDM ground-truth JSON")
    parser.add_argument("--mit-ecg-gt", default=None, help="Override MIT ECG ground-truth JSON")
    parser.add_argument("--bus-gt", default=None, help="Override bus ground-truth JSON")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--plot", action="store_true", help="Generate summary plot")
    parser.add_argument(
        "--dry-run-check-paths",
        action="store_true",
        help="Validate models/paths/ground-truth files and exit",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    datasets = list(dict.fromkeys(args.datasets))
    models = args.models or list(DEFAULT_SMALL_MODELS)

    _validate_models(models)
    _validate_models([args.judge_model])

    data_paths: dict[str, str | None] = {
        "wisdm": args.wisdm_data,
        "mit_ecg": args.mit_ecg_data,
        "bus": args.bus_data,
    }
    gt_paths: dict[str, str | None] = {
        "wisdm": args.wisdm_gt,
        "mit_ecg": args.mit_ecg_gt,
        "bus": args.bus_gt,
    }

    if args.dry_run_check_paths:
        print("Preflight checks")
        print(f"  Models under test: {models}")
        print(f"  Judge model: {args.judge_model}")
        for dataset in datasets:
            ds_path = _resolve_dataset_file(dataset, data_paths.get(dataset))
            gt_path = _resolve_ground_truth_file(dataset, gt_paths.get(dataset))
            print(f"  {dataset}: data={ds_path}")
            print(f"  {dataset}: gt={gt_path}")
        print("Dry-run preflight complete.")
        return

    query_metrics_df, summary_df = run_experiment(
        datasets=datasets,
        models=models,
        query_ids=args.query_ids,
        data_paths=data_paths,
        gt_paths=gt_paths,
        output_dir=args.output_dir,
        judge_model=args.judge_model,
    )

    print(f"Saved per-query rows: {len(query_metrics_df)}")
    print(f"Saved summary rows: {len(summary_df)}")
    print(f"Summary CSV: {os.path.join(args.output_dir, 'modeltype_summary.csv')}")

    if args.plot:
        plot_results(summary_df, args.output_dir)


if __name__ == "__main__":
    main()