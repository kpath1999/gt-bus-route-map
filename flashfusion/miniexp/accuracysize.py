"""Mini-experiment: dataset size vs. Flash-Fusion accuracy across datasets.

This runner evaluates FLASH_FUSION on Q1-Q12 for each requested dataset and
dataset-size fraction. Ground truth is loaded from manually curated files:

  flashfusion/eval/ground_truth/by_fraction/<dataset>/<fraction_tag>.json

where fraction_tag is one of 20, 40, 60, 80, 100 by default.

Data policy:
- Never read from chat/data
- Defaults resolve only under data/
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

# Fix module path discovery: locate repo root and add to sys.path
_script_dir = Path(__file__).resolve().parent  # .../flashfusion/miniexp
_repo_root = _script_dir.parents[1]  # .../flash-fusion (contains flashfusion/)
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import matplotlib.pyplot as plt
import pandas as pd

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.build_groundtruth.ground_truth_builder import build_ground_truth as _build_gt_entries
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.ground_truth_llm_judge import run_llm_ground_truth_judge
from flashfusion.eval.metrics import aggregate_metrics
from flashfusion.eval.queries import SUPPORTED_DATASETS, get_queries
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult

DEFAULT_DATA_PATHS: dict[str, str] = {
    "wisdm": "data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt",
    "mit_ecg": "data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt",
    "bus": "data/bus/bus_data.csv",
}

DEFAULT_FRACTIONS = [0.2, 0.4, 0.6, 0.8, 1.0]
DEFAULT_GT_ROOT = "flashfusion/eval/ground_truth/by_fraction"
DEFAULT_OUTPUT_DIR = "flashfusion/miniexp/results"


def _is_forbidden_chat_data_path(path: str) -> bool:
    normalized = os.path.normpath(path).replace("\\", "/")
    if normalized == "chat/data" or normalized.startswith("chat/data/"):
        return True
    return "/chat/data/" in normalized


def _fraction_to_tag(fraction: float) -> str:
    pct = int(round(fraction * 100))
    if abs((pct / 100.0) - fraction) > 1e-9:
        raise ValueError(f"Fraction {fraction} is not representable as an integer percent")
    if pct <= 0 or pct > 100:
        raise ValueError(f"Fraction percent must be in (0, 100], got {pct}")
    return str(pct)


def _resolve_dataset_file(dataset: str, data_path: str | None) -> Path:
    raw = data_path or DEFAULT_DATA_PATHS[dataset]
    if _is_forbidden_chat_data_path(raw):
        raise ValueError(
            f"Path {raw!r} is under chat/data, which is not allowed. Use data/ paths only."
        )
    p = Path(raw)
    # If a relative path is provided (e.g. "data/AutoIOT_dataset/..."),
    # resolve it against the repository root so scripts run from any CWD.
    if not p.is_absolute():
        # Walk upward from this file to find a directory that contains a top-level
        # `data/` directory. Limit depth to avoid infinite loops.
        cur = Path(__file__).resolve().parent
        repo_root_candidate: Path | None = None
        for _ in range(6):
            if (cur / "data").exists():
                repo_root_candidate = cur
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        if repo_root_candidate is None:
            repo_root_candidate = globals().get("_repo_root", Path(__file__).resolve().parents[1])
        p = (repo_root_candidate / p).resolve()
    if p.is_dir():
        if dataset == "wisdm":
            p = p / "WISDM_ar_v1.1_raw.txt"
        elif dataset == "mit_ecg":
            p = p / "MIT_arrythmia_v1.txt"
        elif dataset == "bus":
            p = p / "bus_data.csv"
    if not p.exists():
        raise FileNotFoundError(f"Dataset path not found for {dataset}: {p}")

    # Enforce policy that datasets come from data/ only.
    normalized = p.as_posix()
    if "/data/" not in normalized and not normalized.startswith("data/"):
        raise ValueError(f"Dataset path must be under data/: {p}")
    return p


def _resolve_gt_path(gt_root: str, dataset: str, fraction: float) -> Path:
    tag = _fraction_to_tag(fraction)
    return Path(gt_root) / dataset / f"{tag}.json"


def _prefix_sample(df: pd.DataFrame, fraction: float) -> pd.DataFrame:
    if fraction <= 0 or fraction > 1.0:
        raise ValueError(f"Fraction must be in (0, 1], got {fraction}")
    if fraction >= 1.0:
        return df.copy()
    rows = max(1, int(len(df) * fraction))
    return df.head(rows).copy()


def _run_single_query(
    dataset: str,
    query_id: int,
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
        # Keep the experiment progressing while recording the failure row.
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


def _rows_for_judge(results: list[RunResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in results:
        # Keep benchmark parity: out-of-scope guardrail rejections are scored deterministically.
        if r.rejected and not r.executed:
            continue
        row = dataclasses.asdict(r)
        row["_source_file"] = "[in_memory_accuracysize_run]"
        rows.append(row)
    return rows


def _validate_ground_truth_matrix(
    datasets: list[str],
    fractions: list[float],
    gt_root: str,
    query_ids: list[int],
) -> None:
    for dataset in datasets:
        expected_query_text = {q["id"]: q["text"] for q in get_queries(dataset)}
        for fraction in fractions:
            gt_path = _resolve_gt_path(gt_root, dataset, fraction)
            if not gt_path.exists():
                raise FileNotFoundError(f"Missing manual ground-truth file: {gt_path}")
            gt = load_ground_truth(str(gt_path))
            missing = [qid for qid in query_ids if qid not in gt]
            if missing:
                raise ValueError(f"Ground truth missing query IDs for {gt_path}: {missing}")
            for qid in query_ids:
                if gt[qid].query_text != expected_query_text[qid]:
                    raise ValueError(
                        f"Ground-truth query_text mismatch in {gt_path} for query {qid}."
                    )


def _count_lines(path: Path) -> int:
    """Fast line count without reading the whole file into memory."""
    count = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            count += chunk.count(b"\n")
    return count


def generate_ground_truth(
    datasets: list[str],
    fractions: list[float],
    gt_root: str,
    data_paths: dict[str, str] | None = None,
) -> None:
    """Build and write deterministic ground-truth JSON files for every dataset/fraction pair."""
    data_paths = data_paths or {}
    for dataset in datasets:
        data_file = _resolve_dataset_file(dataset, data_paths.get(dataset))

        # For very large text-based datasets (e.g. MIT ECG at 26M lines) loading
        # the entire file is extremely slow. Instead count lines cheaply, then load
        # only as many rows as the largest requested fraction requires.
        max_fraction = max(fractions)
        max_rows: int | None = None
        if dataset == "mit_ecg":
            print(f"Counting lines in {data_file} (large file) …", flush=True)
            total_lines = _count_lines(data_file)
            max_rows = max(1, int(total_lines * max_fraction))
            print(f"  {total_lines:,} lines → loading up to {max_rows:,} rows", flush=True)

        df_full = load_dataset_by_name(str(data_file), dataset, max_rows=max_rows)
        if df_full.empty:
            raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")
        for fraction in fractions:
            rows = max(1, int(len(df_full) * (fraction / max_fraction)))
            df = df_full.head(rows).copy()
            entries = _build_gt_entries(df, dataset)
            out = Path(gt_root) / dataset / f"{_fraction_to_tag(fraction)}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=True, indent=2)
            print(f"Wrote {len(entries)} entries → {out}")


def run_experiment(
    datasets: list[str],
    fractions: list[float],
    gt_root: str,
    output_dir: str,
    model: str,
    query_ids: list[int] | None = None,
    data_paths: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")

    data_paths = data_paths or {}
    os.makedirs(output_dir, exist_ok=True)

    all_query_metrics: list[pd.DataFrame] = []
    all_summary_rows: list[dict[str, Any]] = []

    for dataset in datasets:
        queries = get_queries(dataset)
        ds_query_ids = [q["id"] for q in queries]
        if query_ids is None:
            active_query_ids = ds_query_ids
        else:
            invalid = [qid for qid in query_ids if qid not in ds_query_ids]
            if invalid:
                raise ValueError(f"Invalid query IDs for dataset {dataset}: {invalid}")
            active_query_ids = query_ids

        data_file = _resolve_dataset_file(dataset, data_paths.get(dataset))
        df_full = load_dataset_by_name(str(data_file), dataset)
        if df_full.empty:
            raise ValueError(f"Dataset loaded empty for {dataset}: {data_file}")

        for fraction in fractions:
            fraction_tag = _fraction_to_tag(fraction)
            sampled_df = _prefix_sample(df_full, fraction)
            gt_path = _resolve_gt_path(gt_root, dataset, fraction)
            ground_truth_by_id = load_ground_truth(str(gt_path))

            run_results: list[RunResult] = []
            for qid in active_query_ids:
                query_text = queries[qid - 1]["text"]
                print(
                    f"[{dataset} {fraction_tag}%] Q{qid}: {query_text[:70]}...",
                    flush=True,
                )
                rr = _run_single_query(
                    dataset=dataset,
                    query_id=qid,
                    query_text=query_text,
                    sampled_df=sampled_df,
                    model=model,
                    api_key=api_key,
                )
                run_results.append(rr)

            rows = _rows_for_judge(run_results)
            judge_out = os.path.join(output_dir, dataset, fraction_tag, "ground_truth_llm_judge")
            if rows:
                judgments_df, _, _ = run_llm_ground_truth_judge(
                    rows=rows,
                    ground_truth_by_id=ground_truth_by_id,
                    output_dir=judge_out,
                    model_name=model,
                    api_key=api_key,
                    data_path=None,
                    dataset=dataset,
                )
            else:
                judgments_df = pd.DataFrame()

            metrics_df = aggregate_metrics(
                run_results,
                llm_judgments_df=judgments_df,
                ground_truth_by_id=ground_truth_by_id,
                query_defs=queries,
            )
            metrics_df["dataset"] = dataset
            metrics_df["fraction"] = fraction
            metrics_df["fraction_tag"] = fraction_tag
            metrics_df["sample_rows"] = len(sampled_df)
            metrics_df["full_rows"] = len(df_full)

            os.makedirs(os.path.join(output_dir, dataset, fraction_tag), exist_ok=True)
            metrics_df.to_csv(
                os.path.join(output_dir, dataset, fraction_tag, "metrics.csv"),
                index=False,
            )
            all_query_metrics.append(metrics_df)

            summary = {
                "dataset": dataset,
                "fraction": fraction,
                "fraction_tag": fraction_tag,
                "sample_rows": len(sampled_df),
                "full_rows": len(df_full),
                "avg_accuracy": float(metrics_df["gt_score"].mean()) if not metrics_df.empty else 0.0,
                "avg_latency_s": float(metrics_df["latency_s"].mean()) if not metrics_df.empty else 0.0,
                "avg_cost_usd": float(metrics_df["cost_usd"].mean()) if not metrics_df.empty else 0.0,
                "execution_rate": float(metrics_df["executed"].mean()) if not metrics_df.empty else 0.0,
                "query_count": int(len(metrics_df)),
            }
            all_summary_rows.append(summary)

    if all_query_metrics:
        query_metrics_df = pd.concat(all_query_metrics, ignore_index=True)
    else:
        query_metrics_df = pd.DataFrame()
    summary_df = pd.DataFrame(all_summary_rows).sort_values(["dataset", "fraction"]).reset_index(drop=True)

    query_metrics_df.to_csv(os.path.join(output_dir, "accuracy_vs_size_query_metrics.csv"), index=False)
    summary_df.to_csv(os.path.join(output_dir, "accuracy_vs_size_summary.csv"), index=False)

    with open(os.path.join(output_dir, "accuracy_vs_size_query_metrics.jsonl"), "w", encoding="utf-8") as fh:
        for row in query_metrics_df.to_dict(orient="records"):
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    return query_metrics_df, summary_df


def plot_results(summary_df: pd.DataFrame, output_dir: str) -> None:
    if summary_df.empty:
        print("No summary rows to plot.")
        return

    plt.figure(figsize=(10, 6))
    for dataset in sorted(summary_df["dataset"].unique()):
        ds = summary_df[summary_df["dataset"] == dataset].sort_values("fraction")
        plt.plot(ds["fraction"] * 100, ds["avg_accuracy"], marker="o", label=dataset)

    plt.xlabel("Dataset Size (%)")
    plt.ylabel("Average Query Accuracy")
    plt.title("Flash-Fusion Accuracy vs Dataset Size")
    plt.ylim(0, 1.05)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "accuracy_vs_size.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved plot to {out_path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dataset-size vs query-accuracy experiment for Flash-Fusion.",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(SUPPORTED_DATASETS),
        choices=list(SUPPORTED_DATASETS),
        help="Datasets to evaluate (default: wisdm mit_ecg bus)",
    )
    parser.add_argument(
        "--fractions",
        nargs="+",
        type=float,
        default=DEFAULT_FRACTIONS,
        help="Dataset-size fractions (default: 0.2 0.4 0.6 0.8 1.0)",
    )
    parser.add_argument(
        "--query-ids",
        nargs="+",
        type=int,
        default=None,
        help="Optional query IDs subset. Default is all queries for each dataset.",
    )
    parser.add_argument(
        "--gt-root",
        default=DEFAULT_GT_ROOT,
        help="Manual ground-truth root dir (default: flashfusion/eval/ground_truth/by_fraction)",
    )
    parser.add_argument(
        "--wisdm-data",
        default=None,
        help="Optional WISDM file or directory under data/AutoIOT_dataset/IMU/",
    )
    parser.add_argument(
        "--mit-ecg-data",
        default=None,
        help="Optional ECG file or directory under data/AutoIOT_dataset/ECG.0/",
    )
    parser.add_argument(
        "--bus-data",
        default=None,
        help="Optional bus file or directory under data/bus/",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate the three-line accuracy plot",
    )
    parser.add_argument(
        "--dry-run-check-gt",
        action="store_true",
        help="Only validate data paths and manual ground-truth matrix, then exit",
    )
    parser.add_argument(
        "--build-gt",
        action="store_true",
        help=(
            "Generate deterministic ground-truth JSON files for every requested "
            "dataset × fraction and write them under --gt-root, then exit. "
            "Existing files are overwritten."
        ),
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    fractions = sorted({float(f) for f in args.fractions})
    datasets = list(dict.fromkeys(args.datasets))
    data_paths = {
        "wisdm": args.wisdm_data,
        "mit_ecg": args.mit_ecg_data,
        "bus": args.bus_data,
    }

    # --build-gt: generate ground-truth files and exit (no LLM calls needed).
    if args.build_gt:
        generate_ground_truth(
            datasets=datasets,
            fractions=fractions,
            gt_root=args.gt_root,
            data_paths={k: v for k, v in data_paths.items() if v is not None},
        )
        print("Ground-truth generation complete.")
        return

    # Validate /data policy and resolvable files before any LLM call.
    for dataset in datasets:
        resolved = _resolve_dataset_file(dataset, data_paths.get(dataset))
        print(f"Resolved {dataset} data: {resolved}")

    # Validate all manual ground-truth files and query alignment.
    active_query_ids = args.query_ids
    if active_query_ids is None:
        # Validate full coverage by default (Q1-Q12 for each dataset profile).
        for dataset in datasets:
            ds_qids = [q["id"] for q in get_queries(dataset)]
            _validate_ground_truth_matrix([dataset], fractions, args.gt_root, ds_qids)
    else:
        _validate_ground_truth_matrix(datasets, fractions, args.gt_root, active_query_ids)

    if args.dry_run_check_gt:
        print("Dry-run validation complete: dataset paths and ground-truth matrix are valid.")
        return

    query_metrics_df, summary_df = run_experiment(
        datasets=datasets,
        fractions=fractions,
        gt_root=args.gt_root,
        output_dir=args.output_dir,
        model=args.model,
        query_ids=active_query_ids,
        data_paths=data_paths,
    )

    print(f"Saved per-query metrics rows: {len(query_metrics_df)}")
    print(f"Saved summary rows: {len(summary_df)}")
    print(f"Summary CSV: {os.path.join(args.output_dir, 'accuracy_vs_size_summary.csv')}")

    if args.plot:
        plot_results(summary_df, args.output_dir)


if __name__ == "__main__":
    main()
