from __future__ import annotations

"""Mini-experiment: dataset size vs. Flash-Fusion accuracy across datasets.

This runner evaluates FLASH_FUSION on Q1-Q12 for each requested dataset and
dataset-size fraction. Ground truth is loaded from manually curated files:

  flashfusion/eval/ground_truth/by_fraction/<dataset>/<fraction_tag>.json

where fraction_tag is one of 20, 40, 60, 80, 100 by default.

Data policy:
- Never read from chat/data
- Defaults resolve only under data/
"""

"""
python -m flashfusion.viz.miniexp.accuracysize \
  --datasets wisdm mit_ecg bus \
  --fractions 0.2 0.4 0.6 0.8 1.0 \
  --baselines FLASH_FUSION AUTOIOT_PAPER REACT_ONLY \
  --output-dir flashfusion/miniexp/results/full_run \
  --plot

python -m flashfusion.viz.miniexp.accuracysize \
--datasets wisdm mit_ecg bus \
--fractions 0.2 0.4 0.6 0.8 1.0 \
--baselines FLASH_FUSION REACT_ONLY \
--output-dir flashfusion/viz/miniexp/results_size/ \
--plot
"""

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any

# Fix module path discovery: locate repo root and add to sys.path
_script_dir = Path(__file__).resolve().parent  # .../flashfusion/viz/miniexp
_repo_root = _script_dir.parents[2]  # .../flash-fusion (contains flashfusion/)
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
DEFAULT_BASELINES = ["FLASH_FUSION"]

# ---------------------------------------------------------------------------
# Visualization constants
# ---------------------------------------------------------------------------
_STAGE_KEYS: tuple[str, ...] = ("guardrail", "typed_exec", "agent")
_STAGE_LABELS: dict[str, str] = {
    "guardrail": "Guardrail", "typed_exec": "Typed Exec", "agent": "Agent",
}
_STAGE_COLORS: dict[str, str] = {
    "guardrail": "#d62728", "typed_exec": "#f28e2b", "agent": "#9467bd",
}
_DATASET_COLORS: dict[str, str] = {
    "wisdm": "#2f6ad9", "mit_ecg": "#f28e2b", "bus": "#2c8c4a",
}
_QUERY_TYPE_ORDER: tuple[str, ...] = ("direct", "reasoning", "oos")
_QUERY_TYPE_DISPLAY: dict[str, str] = {
    "direct": "Direct", "reasoning": "Reasoning", "oos": "Out-of-Scope",
}
_LINESTYLES: tuple[str, ...] = ("-", "--", ":", "-.")
_PLOT_RCPARAMS: dict[str, Any] = {
    "font.family": "DejaVu Sans",
    "figure.facecolor": "#ffffff",
    "axes.facecolor": "#ffffff",
    "axes.edgecolor": "#222222",
    "axes.linewidth": 1.8,
    "axes.titlesize": 22,
    "axes.titleweight": "bold",
    "axes.labelsize": 20,
    "axes.labelweight": "bold",
    "xtick.labelsize": 18,
    "ytick.labelsize": 18,
    "legend.fontsize": 24,
    "legend.title_fontsize": 24,
    "grid.alpha": 0.55,
    "grid.color": "#cccccc",
    "grid.linewidth": 1.0,
}


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
        # Use the module-level _repo_root (already resolved above) so we always
        # anchor relative paths against the true repository root, not a nested
        # data/ subdirectory that may exist elsewhere in the tree.
        repo_root_candidate: Path = globals().get("_repo_root", Path(__file__).resolve().parents[2])
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
    baseline: str,
) -> RunResult:
    client = LLMClient(model_name=model, api_key=api_key)
    runner = BaselineRunner(mode=baseline, df=sampled_df, client=client)
    try:
        result = runner.run(query_text)
        result.query_id = int(query_id)
        return result
    except Exception as exc:
        # Keep the experiment progressing while recording the failure row.
        r = RunResult(
            baseline=baseline,
            model=model,
            query=query_text,
            query_id=int(query_id),
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


def _get_next_run_number(base_output_dir: str) -> int:
    """Find the next available run number in base_output_dir.
    
    Scans for existing run_1/, run_2/, etc., and returns the next available number.
    If no runs exist, returns 1.
    """
    base = Path(base_output_dir)
    if not base.exists():
        return 1
    
    existing_runs = []
    for item in base.iterdir():
        if item.is_dir() and item.name.startswith("run_"):
            try:
                run_num = int(item.name.split("_")[1])
                existing_runs.append(run_num)
            except (ValueError, IndexError):
                pass
    
    if not existing_runs:
        return 1
    return max(existing_runs) + 1


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
    baselines: list[str],
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
            for baseline in baselines:
                run_results: list[RunResult] = []
                for qid in active_query_ids:
                    query_text = queries[qid - 1]["text"]
                    print(
                        f"[{baseline} | {dataset} {fraction_tag}%] Q{qid}: {query_text[:70]}...",
                        flush=True,
                    )
                    rr = _run_single_query(
                        dataset=dataset,
                        query_id=qid,
                        query_text=query_text,
                        sampled_df=sampled_df,
                        model=model,
                        api_key=api_key,
                        baseline=baseline,
                    )
                    run_results.append(rr)

                rows = _rows_for_judge(run_results)
                baseline_dir = os.path.join(output_dir, baseline)
                judge_out = os.path.join(
                    baseline_dir,
                    dataset,
                    fraction_tag,
                    "ground_truth_llm_judge",
                )
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
                metrics_df["baseline"] = baseline
                metrics_df["dataset"] = dataset
                metrics_df["fraction"] = fraction
                metrics_df["fraction_tag"] = fraction_tag
                metrics_df["sample_rows"] = len(sampled_df)
                metrics_df["full_rows"] = len(df_full)

                os.makedirs(os.path.join(baseline_dir, dataset, fraction_tag), exist_ok=True)
                metrics_df.to_csv(
                    os.path.join(baseline_dir, dataset, fraction_tag, "metrics.csv"),
                    index=False,
                )
                all_query_metrics.append(metrics_df)

                summary = {
                    "baseline": baseline,
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
    summary_df = (
        pd.DataFrame(all_summary_rows)
        .sort_values(["baseline", "dataset", "fraction"])
        .reset_index(drop=True)
    )

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
    has_multi_baseline = "baseline" in summary_df.columns and summary_df["baseline"].nunique() > 1
    if has_multi_baseline:
        baseline_order = sorted(summary_df["baseline"].unique())
        linestyle_by_baseline = {
            b: _LINESTYLES[idx % len(_LINESTYLES)] for idx, b in enumerate(baseline_order)
        }
        grouped = summary_df.groupby(["baseline", "dataset"], as_index=False)
        for (baseline, dataset), ds in grouped:
            ds = ds.sort_values("fraction")
            plt.plot(
                ds["fraction"] * 100,
                ds["avg_accuracy"],
                marker="o",
                linestyle=linestyle_by_baseline[baseline],
                color=_DATASET_COLORS.get(dataset),
                label=f"{baseline} | {dataset}",
            )
    else:
        for dataset in sorted(summary_df["dataset"].unique()):
            ds = summary_df[summary_df["dataset"] == dataset].sort_values("fraction")
            plt.plot(ds["fraction"] * 100, ds["avg_accuracy"], marker="o", label=dataset)

    plt.xlabel("Dataset Size (%)")
    plt.ylabel("Average Query Accuracy")
    if has_multi_baseline:
        plt.title("Accuracy vs Dataset Size (Multi-baseline)")
    else:
        baseline_label = (
            str(summary_df["baseline"].iloc[0])
            if "baseline" in summary_df.columns and not summary_df.empty
            else "Flash-Fusion"
        )
        plt.title(f"{baseline_label} Accuracy vs Dataset Size")
    plt.ylim(0, 1.05)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "accuracy_vs_size.png")
    plt.savefig(out_path, dpi=300)
    plt.close()
    print(f"Saved plot to {out_path}")


# ---------------------------------------------------------------------------
# Helpers for JSONL-based plotting
# ---------------------------------------------------------------------------

def _normalize_query_type(complexity: str) -> str:
    raw = (complexity or "").strip().lower()
    if raw == "direct":
        return "direct"
    if raw == "intermediate":
        return "reasoning"
    if raw == "out_of_scope":
        return "oos"
    return "reasoning"


def _build_query_type_map() -> dict[tuple[str, int], str]:
    """Return {(dataset, query_id): query_type} for all supported datasets."""
    result: dict[tuple[str, int], str] = {}
    for dataset in SUPPORTED_DATASETS:
        for q in get_queries(dataset):
            result[(dataset, int(q["id"]))] = _normalize_query_type(str(q.get("complexity", "")))
    return result


def _load_jsonl(jsonl_path: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise ValueError(f"No rows found in {jsonl_path}")
    return pd.DataFrame(rows)


def plot_accuracy_vs_size_from_jsonl(jsonl_path: str, output_dir: str) -> None:
    """Plot (1): query accuracy vs dataset size across datasets, from JSONL results.
    
    Includes confidence bands (±1 std dev) around each line.
    """
    df = _load_jsonl(jsonl_path)
    group_cols = ["dataset", "fraction"]
    if "baseline" in df.columns:
        group_cols = ["baseline", "dataset", "fraction"]
    agg = (
        df.groupby(group_cols)["gt_score"]
        .agg(["mean", "std"])
        .reset_index()
        .rename(columns={"mean": "avg_accuracy", "std": "std_accuracy"})
        .sort_values(group_cols)
    )
    agg["std_accuracy"] = agg["std_accuracy"].fillna(0.0)

    plt.rcParams.update(_PLOT_RCPARAMS)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    has_multi_baseline = "baseline" in agg.columns and agg["baseline"].nunique() > 1

    if has_multi_baseline:
        baseline_order = sorted(agg["baseline"].unique())
        linestyle_by_baseline = {
            b: _LINESTYLES[idx % len(_LINESTYLES)] for idx, b in enumerate(baseline_order)
        }
        grouped = agg.groupby(["baseline", "dataset"])
        for (baseline, dataset), ds in grouped:
            ds = ds.sort_values("fraction")
            x = ds["fraction"] * 100
            y = ds["avg_accuracy"] * 100
            yerr = ds["std_accuracy"] * 100
            color = _DATASET_COLORS.get(dataset)

            ax.fill_between(
                x,
                y - yerr,
                y + yerr,
                alpha=0.15,
                color=color,
            )
            ax.plot(
                x,
                y,
                marker="o",
                linewidth=2.2,
                markersize=7,
                linestyle=linestyle_by_baseline[baseline],
                color=color,
                label=f"{baseline} | {dataset.upper().replace('_', '-')}",
            )
    else:
        for dataset in sorted(agg["dataset"].unique()):
            ds = agg[agg["dataset"] == dataset].sort_values("fraction")
            x = ds["fraction"] * 100
            y = ds["avg_accuracy"] * 100
            yerr = ds["std_accuracy"] * 100
            color = _DATASET_COLORS.get(dataset)

            # Plot confidence band (±std) as light semi-transparent fill
            ax.fill_between(
                x, y - yerr, y + yerr,
                alpha=0.2,
                color=color,
            )

            # Plot line and markers
            ax.plot(
                x, y,
                marker="o",
                linewidth=2.2,
                markersize=7,
                color=color,
                label=dataset.upper().replace("_", "-"),
            )

    ax.set_xlabel("Dataset Size (%)")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 105)
    ax.set_xticks([20, 40, 60, 80, 100])
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.legend(title="Dataset", loc="lower right")
    fig.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_png = os.path.join(output_dir, "accuracy_vs_size.png")
    out_pdf = os.path.join(output_dir, "accuracy_vs_size.pdf")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")


def plot_accuracy_vs_size_by_baseline(jsonl_path: str, output_dir: str) -> None:
    """Plot query accuracy vs dataset size, one line per baseline averaged across all datasets.

    Std dev is computed across datasets at each (baseline, fraction) point, giving a
    sense of how consistent each baseline is across different data domains.
    """
    df = _load_jsonl(jsonl_path)
    if "baseline" not in df.columns:
        print("No 'baseline' column in data; skipping baseline-comparison plot.")
        return

    # Average gt_score across queries per (baseline, dataset, fraction), then
    # compute mean ± std across datasets for each (baseline, fraction).
    per_dataset = (
        df.groupby(["baseline", "dataset", "fraction"])["gt_score"]
        .mean()
        .reset_index()
        .rename(columns={"gt_score": "dataset_avg"})
    )
    agg = (
        per_dataset.groupby(["baseline", "fraction"])["dataset_avg"]
        .agg(["mean", "std"])
        .reset_index()
        .rename(columns={"mean": "avg_accuracy", "std": "std_accuracy"})
    )
    agg["std_accuracy"] = agg["std_accuracy"].fillna(0.0)

    # Colour, linestyle, and display name per baseline
    _BASELINE_DISPLAY: dict[str, str] = {
        "FLASH_FUSION": "Flash-Fusion",
        "REACT_ONLY": "ReAct",
    }
    _BASELINE_COLORS_FIXED: dict[str, str] = {
        "FLASH_FUSION": "#2f8f57",
        "REACT_ONLY": "#7fba00",
    }
    fallback_colors = ["#2f6ad9", "#f28e2b", "#d62728", "#9467bd"]
    baseline_order = sorted(agg["baseline"].unique())
    color_by_baseline = {
        b: _BASELINE_COLORS_FIXED.get(b, fallback_colors[i % len(fallback_colors)])
        for i, b in enumerate(baseline_order)
    }
    linestyle_by_baseline = {b: _LINESTYLES[i % len(_LINESTYLES)] for i, b in enumerate(baseline_order)}

    plt.rcParams.update(_PLOT_RCPARAMS)
    fig, ax = plt.subplots(figsize=(11, 6.5))

    for baseline in baseline_order:
        ds = agg[agg["baseline"] == baseline].sort_values("fraction")
        x = ds["fraction"] * 100
        y = ds["avg_accuracy"] * 100
        yerr = ds["std_accuracy"] * 100
        color = color_by_baseline[baseline]

        ax.fill_between(x, y - yerr, y + yerr, alpha=0.15, color=color)
        ax.plot(
            x, y,
            marker="o",
            linewidth=2.2,
            markersize=7,
            linestyle=linestyle_by_baseline[baseline],
            color=color,
            label=_BASELINE_DISPLAY.get(baseline, baseline.replace("_", "-")),
        )

    ax.set_xlabel("Dataset Size (%)")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 105)
    ax.set_xticks([20, 40, 60, 80, 100])
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.legend(title="Baseline", loc="lower right")
    fig.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_png = os.path.join(output_dir, "accuracy_vs_size_by_baseline.png")
    out_pdf = os.path.join(output_dir, "accuracy_vs_size_by_baseline.pdf")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")


def plot_latency_stages_from_jsonl(jsonl_path: str, output_dir: str) -> None:
    """Plot (2): Flash-Fusion stage latency swimlanes, averaged by query type at 100% fraction."""
    df = _load_jsonl(jsonl_path)

    df_full = df[df["fraction"] >= 1.0 - 1e-9].copy()
    if df_full.empty:
        print("No 100% fraction rows found; skipping latency-stages plot.")
        return

    stage_cols = [f"{s}_latency_s" for s in _STAGE_KEYS]
    for col in stage_cols:
        if col not in df_full.columns:
            df_full[col] = 0.0

    # Keep only rows where stage decomposition exists to avoid mixing in non-staged baselines.
    stage_totals = df_full[stage_cols].fillna(0.0).sum(axis=1)
    df_full = df_full[stage_totals > 0]
    if df_full.empty:
        print("No rows with stage-level latency found; skipping latency-stages plot.")
        return

    qt_map = _build_query_type_map()
    df_full["query_type"] = df_full.apply(
        lambda r: qt_map.get((r["dataset"], int(r["query_id"])), "reasoning"), axis=1
    )
    agg = df_full.groupby("query_type")[stage_cols].mean().reset_index()

    # Ensure all query types are represented
    for qt in _QUERY_TYPE_ORDER:
        if qt not in agg["query_type"].values:
            agg = pd.concat(
                [agg, pd.DataFrame([{"query_type": qt, **{c: 0.0 for c in stage_cols}}])],
                ignore_index=True,
            )

    agg["query_type"] = pd.Categorical(agg["query_type"], categories=list(_QUERY_TYPE_ORDER), ordered=True)
    agg = agg.sort_values("query_type").reset_index(drop=True)

    plt.rcParams.update({
        **_PLOT_RCPARAMS,
        "axes.titlesize": 17,
        "axes.labelsize": 16,
        "xtick.labelsize": 22.5,
        "ytick.labelsize": 19,
        "legend.fontsize": 19,
        "legend.title_fontsize": 17,
    })

    labels = [_QUERY_TYPE_DISPLAY.get(str(q), str(q)) for q in agg["query_type"].tolist()]
    y_pos = list(range(len(labels)))
    left = [0.0] * len(labels)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    for stage in _STAGE_KEYS:
        values = agg[f"{stage}_latency_s"].astype(float).tolist()
        ax.barh(
            y_pos,
            values,
            left=left,
            color=_STAGE_COLORS[stage],
            edgecolor="white",
            linewidth=0.8,
            label=_STAGE_LABELS[stage],
            height=0.56,
        )
        left = [l + v for l, v in zip(left, values)]

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Latency (s)")
    # ax.set_ylabel("Query Type")
    ax.grid(axis="x", alpha=0.25)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", title=None, ncol=2)
    fig.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_png = os.path.join(output_dir, "latency_by_stages.png")
    out_pdf = os.path.join(output_dir, "latency_by_stages.pdf")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")


def _load_jsonl_from_runs(base_output_dir: str) -> pd.DataFrame:
    """Load and combine JSONL data from all run_N/ subdirectories.
    
    Adds a 'run_id' column to track which run each row came from.
    """
    all_dfs: list[pd.DataFrame] = []
    base = Path(base_output_dir)
    
    # Scan for run_N directories
    run_dirs = sorted(
        [d for d in base.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda d: int(d.name.split("_")[1]) if d.name.split("_")[1].isdigit() else 0,
    )
    
    if not run_dirs:
        raise FileNotFoundError(f"No run_N directories found in {base_output_dir}")
    
    for run_dir in run_dirs:
        candidate_paths = [run_dir / "accuracy_vs_size_query_metrics.jsonl"]
        for baseline_dir in sorted([d for d in run_dir.iterdir() if d.is_dir()]):
            candidate_paths.append(baseline_dir / "accuracy_vs_size_query_metrics.jsonl")

        for jsonl_path in candidate_paths:
            if jsonl_path.exists():
                df = _load_jsonl(str(jsonl_path))
                df["run_id"] = run_dir.name  # Add run identifier
                if "baseline" not in df.columns and jsonl_path.parent != run_dir:
                    df["baseline"] = jsonl_path.parent.name
                all_dfs.append(df)
                print(f"Loaded {len(df)} rows from {jsonl_path}")
    
    if not all_dfs:
        raise FileNotFoundError(
            f"No accuracy_vs_size_query_metrics.jsonl files found in run_N/ directories"
        )
    
    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"Combined {len(combined)} total rows from {len(all_dfs)} runs")
    return combined


def plot_accuracy_across_runs(base_output_dir: str, output_dir: str) -> None:
    """Plot accuracy across runs: one line per dataset, with ±std confidence bands."""
    df = _load_jsonl_from_runs(base_output_dir)
    
    # First: compute average gt_score per (run, dataset, fraction)
    run_group_cols = ["run_id", "dataset", "fraction"]
    if "baseline" in df.columns:
        run_group_cols = ["run_id", "baseline", "dataset", "fraction"]
    run_averages = (
        df.groupby(run_group_cols)["gt_score"]
        .mean()
        .reset_index()
        .rename(columns={"gt_score": "run_avg_accuracy"})
    )
    
    # Second: compute mean and std across runs for each (dataset, fraction)
    agg_group_cols = ["dataset", "fraction"]
    if "baseline" in run_averages.columns:
        agg_group_cols = ["baseline", "dataset", "fraction"]
    agg = (
        run_averages.groupby(agg_group_cols)["run_avg_accuracy"]
        .agg(["mean", "std"])
        .reset_index()
        .rename(columns={"mean": "avg_accuracy", "std": "std_accuracy"})
        .sort_values(agg_group_cols)
    )
    agg["std_accuracy"] = agg["std_accuracy"].fillna(0.0)

    plt.rcParams.update(_PLOT_RCPARAMS)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    has_multi_baseline = "baseline" in agg.columns and agg["baseline"].nunique() > 1

    if has_multi_baseline:
        baseline_order = sorted(agg["baseline"].unique())
        linestyle_by_baseline = {
            b: _LINESTYLES[idx % len(_LINESTYLES)] for idx, b in enumerate(baseline_order)
        }
        grouped = agg.groupby(["baseline", "dataset"])
        for (baseline, dataset), ds in grouped:
            ds = ds.sort_values("fraction")
            x = ds["fraction"] * 100
            y = ds["avg_accuracy"] * 100
            yerr = ds["std_accuracy"] * 100
            color = _DATASET_COLORS.get(dataset)

            ax.fill_between(
                x, y - yerr, y + yerr,
                alpha=0.15,
                color=color,
            )
            ax.plot(
                x,
                y,
                marker="o",
                linewidth=2.2,
                markersize=7,
                linestyle=linestyle_by_baseline[baseline],
                color=color,
                label=f"{baseline} | {dataset.upper().replace('_', '-')}",
            )
    else:
        for dataset in sorted(agg["dataset"].unique()):
            ds = agg[agg["dataset"] == dataset].sort_values("fraction")
            x = ds["fraction"] * 100
            y = ds["avg_accuracy"] * 100
            yerr = ds["std_accuracy"] * 100
            color = _DATASET_COLORS.get(dataset)

            # Plot confidence band (±std) as light semi-transparent fill
            ax.fill_between(
                x, y - yerr, y + yerr,
                alpha=0.2,
                color=color,
            )

            # Plot line and markers
            ax.plot(
                x, y,
                marker="o",
                linewidth=2.2,
                markersize=7,
                color=color,
                label=dataset.upper().replace("_", "-"),
            )

    ax.set_xlabel("Dataset Size (%)")
    ax.set_ylabel("Query Accuracy (%) (N=3)")
    ax.set_ylim(0, 105)
    ax.set_xticks([20, 40, 60, 80, 100])
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.legend(title="Dataset", loc="lower right")
    fig.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_png = os.path.join(output_dir, "accuracy_across_runs.png")
    out_pdf = os.path.join(output_dir, "accuracy_across_runs.pdf")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")


def plot_latency_stages_across_runs(base_output_dir: str, output_dir: str) -> None:
    """Plot latency stages across runs: swimlanes by query type at 100% fraction."""
    df = _load_jsonl_from_runs(base_output_dir)

    df_full = df[df["fraction"] >= 1.0 - 1e-9].copy()
    if df_full.empty:
        print("No 100% fraction rows found; skipping latency-stages-across-runs plot.")
        return

    stage_cols = [f"{s}_latency_s" for s in _STAGE_KEYS]
    for col in stage_cols:
        if col not in df_full.columns:
            df_full[col] = 0.0

    stage_totals = df_full[stage_cols].fillna(0.0).sum(axis=1)
    df_full = df_full[stage_totals > 0]
    if df_full.empty:
        print("No rows with stage-level latency found; skipping latency-stages-across-runs plot.")
        return

    qt_map = _build_query_type_map()
    df_full["query_type"] = df_full.apply(
        lambda r: qt_map.get((r["dataset"], int(r["query_id"])), "reasoning"), axis=1
    )
    
    # First: compute average per (run, query_type) for each stage
    run_averages = df_full.groupby(["run_id", "query_type"])[stage_cols].mean().reset_index()
    
    # Second: compute mean across runs for each query_type
    agg = run_averages.groupby("query_type")[stage_cols].mean().reset_index()

    # Ensure all query types are represented
    for qt in _QUERY_TYPE_ORDER:
        if qt not in agg["query_type"].values:
            agg = pd.concat(
                [agg, pd.DataFrame([{"query_type": qt, **{c: 0.0 for c in stage_cols}}])],
                ignore_index=True,
            )

    agg["query_type"] = pd.Categorical(agg["query_type"], categories=list(_QUERY_TYPE_ORDER), ordered=True)
    agg = agg.sort_values("query_type").reset_index(drop=True)

    plt.rcParams.update({
        **_PLOT_RCPARAMS,
        "axes.titlesize": 17,
        "axes.labelsize": 18,
        "xtick.labelsize": 17.5,
        "ytick.labelsize": 19,
        "legend.fontsize": 17,
        "legend.title_fontsize": 17,
    })

    labels = [_QUERY_TYPE_DISPLAY.get(str(q), str(q)) for q in agg["query_type"].tolist()]
    y_pos = list(range(len(labels)))
    left = [0.0] * len(labels)

    fig, ax = plt.subplots(figsize=(11, 4.8))
    for stage in _STAGE_KEYS:
        values = agg[f"{stage}_latency_s"].astype(float).tolist()
        ax.barh(
            y_pos,
            values,
            left=left,
            color=_STAGE_COLORS[stage],
            edgecolor="white",
            linewidth=0.8,
            label=_STAGE_LABELS[stage],
            height=0.56,
        )
        left = [l + v for l, v in zip(left, values)]

    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s)")
    # ax.set_ylabel("Query Type")
    ax.grid(axis="x", alpha=0.25)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", title=None, ncol=2)
    fig.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    out_png = os.path.join(output_dir, "latency_by_stages_across_runs.png")
    out_pdf = os.path.join(output_dir, "latency_by_stages_across_runs.pdf")
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dataset-size vs query-accuracy experiment across one or more baselines.",
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
        "--baselines",
        nargs="+",
        default=DEFAULT_BASELINES,
        help=(
            "Baselines to evaluate. Supports space-separated names, comma-separated "
            "values, or 'all'. Default: FLASH_FUSION"
        ),
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
        "--run-number",
        type=int,
        default=None,
        help="Run number (default: auto-detect next available run_N). Results stored in output-dir/run_N/",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate the three-line accuracy plot",
    )
    parser.add_argument(
        "--jsonl-input",
        default=None,
        help=(
            "Path to existing accuracy_vs_size_query_metrics.jsonl to plot (skips experiment). "
            "Requires --plot to generate figures."
        ),
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help=(
            "Aggregate results across all run_N/ directories and generate aggregated plots. "
            "Results stored in output-dir/run_all/. "
        ),
    )
    parser.add_argument(
        "--baseline-comparison",
        default=None,
        metavar="JSONL_PATH",
        help=(
            "Path to a query-metrics JSONL (e.g. results_size/run_1/accuracy_vs_size_query_metrics.jsonl). "
            "Generates accuracy_vs_size_by_baseline.png/pdf comparing baselines averaged across datasets. "
            "If omitted but --run-all is set, the run_all JSONL is used automatically."
        ),
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


def _parse_baselines(values: list[str]) -> list[str]:
    parts: list[str] = []
    for value in values:
        for token in value.split(","):
            token = token.strip()
            if token:
                parts.append(token)

    if not parts:
        return list(DEFAULT_BASELINES)

    if any(p.lower() == "all" for p in parts):
        return list(BaselineRunner.MODES)

    allowed = set(BaselineRunner.MODES)
    ordered: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for p in parts:
        name = p.upper()
        if name not in allowed:
            invalid.append(p)
            continue
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    if invalid:
        raise ValueError(
            "Invalid baseline(s): "
            + ", ".join(invalid)
            + ". Valid options: "
            + ", ".join(BaselineRunner.MODES)
        )
    return ordered


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    baselines = _parse_baselines(args.baselines)

    # --run-all: aggregate results across all run_N/ directories
    if args.run_all:
        run_all_dir = os.path.join(args.output_dir, "run_all")
        plot_accuracy_across_runs(args.output_dir, run_all_dir)
        plot_latency_stages_across_runs(args.output_dir, run_all_dir)
        print(f"Aggregated results stored in {run_all_dir}")
        return

    # Determine run number and create run_N subdirectory
    if args.run_number is None:
        run_number = _get_next_run_number(args.output_dir)
    else:
        run_number = args.run_number
    
    actual_output_dir = os.path.join(args.output_dir, f"run_{run_number}")

    # --baseline-comparison: generate baseline-vs-baseline accuracy plot and exit.
    # Write output beside the JSONL (not into a new run_N subfolder).
    if args.baseline_comparison:
        comparison_out_dir = str(Path(args.baseline_comparison).parent)
        plot_accuracy_vs_size_by_baseline(args.baseline_comparison, comparison_out_dir)
        return

    # --jsonl-input: plot from pre-existing results and exit (no LLM calls needed).
    if args.jsonl_input:
        if args.plot:
            plot_accuracy_vs_size_from_jsonl(args.jsonl_input, actual_output_dir)
            plot_accuracy_vs_size_by_baseline(args.jsonl_input, actual_output_dir)
            plot_latency_stages_from_jsonl(args.jsonl_input, actual_output_dir)
        else:
            print(f"Loaded {args.jsonl_input}. Pass --plot to generate figures.")
        return

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
        output_dir=actual_output_dir,
        baselines=baselines,
        model=args.model,
        query_ids=active_query_ids,
        data_paths=data_paths,
    )

    print(f"Saved per-query metrics rows: {len(query_metrics_df)}")
    print(f"Saved summary rows: {len(summary_df)}")
    print(f"Summary CSV: {os.path.join(actual_output_dir, 'accuracy_vs_size_summary.csv')}")
    print(f"Run directory: {actual_output_dir}")
    print(f"Baselines: {', '.join(baselines)}")

    if args.plot:
        plot_results(summary_df, actual_output_dir)


if __name__ == "__main__":
    main()
