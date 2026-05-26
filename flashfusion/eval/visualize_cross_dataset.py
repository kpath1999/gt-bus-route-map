"""visualize_cross_dataset.py — Balanced cross-dataset aggregation and charts.

Aggregates metrics from multiple per-dataset benchmark runs and produces a
single set of charts that show average performance by query type across all
datasets.

Averaging strategy (balanced):
    For each (dataset, baseline, query_type), compute the mean metric value.
    Then average those means across datasets.
    This prevents larger datasets from dominating the aggregate.

CLI usage (called from run_benchmark.sh after all datasets finish):
    python -m flashfusion.eval.visualize_cross_dataset \\
        --wisdm-metrics   path/to/wisdm/benchmark/metrics.csv \\
        --ecg-metrics     path/to/ecg/benchmark/metrics.csv \\
        --bus-metrics     path/to/bus/benchmark/metrics.csv \\
        --output          path/to/visuals_all/
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from flashfusion.eval.queries import (
    DATASET_BUS,
    DATASET_MIT_ECG,
    DATASET_WISDM,
    get_queries,
)
from flashfusion.eval.visualize_comparison import (
    BASELINE_COLORS,
    BASELINE_LABELS,
    BASELINE_ORDER,
    QUERY_TYPE_LABELS,
    QUERY_TYPE_ORDER,
    _configure_plot_style,
    _format_for_table,
    _format_for_table_with_variation,
    _format_value_label,
    _import_matplotlib,
    _ordered_baselines_from,
    _save_metric_table_markdown,
)

DATASET_LABEL = {
    DATASET_WISDM: "WISDM (IMU)",
    DATASET_MIT_ECG: "MIT-ECG",
    DATASET_BUS: "Bus Telemetry",
}

METRICS: list[dict[str, Any]] = [
    {
        "key": "accuracy_percent",
        "ylabel": "LLM Verdict Accuracy (%)",
        "title": "Accuracy by Query Type — All Datasets",
        "filename": "cross_accuracy_by_query_type.png",
        "table_base": "cross_accuracy_by_query_type",
        "format_kind": "percent",
        "ylim": (0.0, 110.0),
    },
    {
        "key": "latency_s",
        "ylabel": "Latency (s)",
        "title": "Latency by Query Type — All Datasets",
        "filename": "cross_latency_by_query_type.png",
        "table_base": "cross_latency_by_query_type",
        "format_kind": "float2",
        "ylim": None,
    },
    {
        "key": "input_tokens",
        "ylabel": "Input Tokens",
        "title": "Input Tokens by Query Type — All Datasets",
        "filename": "cross_input_tokens_by_query_type.png",
        "table_base": "cross_input_tokens_by_query_type",
        "format_kind": "int",
        "ylim": None,
    },
    {
        "key": "output_tokens",
        "ylabel": "Output Tokens",
        "title": "Output Tokens by Query Type — All Datasets",
        "filename": "cross_output_tokens_by_query_type.png",
        "table_base": "cross_output_tokens_by_query_type",
        "format_kind": "int",
        "ylim": None,
    },
    {
        "key": "cost_usd",
        "ylabel": "Cost (USD)",
        "title": "Cost by Query Type — All Datasets",
        "filename": "cross_cost_by_query_type.png",
        "table_base": "cross_cost_by_query_type",
        "format_kind": "usd",
        "ylim": None,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_and_tag(path: str, dataset: str) -> pd.DataFrame:
    """Load metrics.csv and attach query_type + dataset columns."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Metrics file not found: {path}")

    df = pd.read_csv(p)

    for col in ("input_tokens", "output_tokens"):
        if col not in df.columns:
            df[col] = 0

    query_defs = get_queries(dataset)
    complexity_by_id = {int(q["id"]): str(q["complexity"]) for q in query_defs}
    df["query_id"] = pd.to_numeric(df["query_id"], errors="coerce").fillna(0).astype(int)
    df["query_type"] = df["query_id"].map(complexity_by_id).map(QUERY_TYPE_LABELS)

    unknown = df[df["query_type"].isna()]["query_id"].unique().tolist()
    if unknown:
        raise ValueError(
            f"Unknown query_ids {unknown} not in {dataset} query bank."
        )

    df["dataset"] = dataset
    return df


def _balanced_aggregate(frames: list[pd.DataFrame], accuracy_col: str) -> pd.DataFrame:
    """Compute a balanced cross-dataset mean by query_type and baseline.

    Strategy:
        1. For each dataset, compute mean metric per (baseline, query_type).
        2. Average those per-dataset means across datasets.
        3. Also compute std across datasets as an uncertainty indicator.
    """
    per_dataset: list[pd.DataFrame] = []
    for df in frames:
        agg = (
            df.groupby(["dataset", "baseline", "query_type"], as_index=False)
            .agg(
                accuracy_raw=(accuracy_col, "mean"),
                latency_s=("latency_s", "mean"),
                input_tokens=("input_tokens", "mean"),
                output_tokens=("output_tokens", "mean"),
                cost_usd=("cost_usd", "mean"),
            )
        )
        per_dataset.append(agg)

    stacked = pd.concat(per_dataset, ignore_index=True)

    summary = (
        stacked.groupby(["baseline", "query_type"], as_index=False)
        .agg(
            accuracy_raw=("accuracy_raw", "mean"),
            accuracy_raw_std=("accuracy_raw", "std"),
            latency_s=("latency_s", "mean"),
            latency_s_std=("latency_s", "std"),
            input_tokens=("input_tokens", "mean"),
            input_tokens_std=("input_tokens", "std"),
            output_tokens=("output_tokens", "mean"),
            output_tokens_std=("output_tokens", "std"),
            cost_usd=("cost_usd", "mean"),
            cost_usd_std=("cost_usd", "std"),
            dataset_count=("dataset", "nunique"),
        )
    )

    for col in [
        "accuracy_raw_std", "latency_s_std", "input_tokens_std",
        "output_tokens_std", "cost_usd_std",
    ]:
        summary[col] = summary[col].fillna(0.0)

    summary["accuracy_percent"] = summary["accuracy_raw"] * 100.0
    summary["accuracy_percent_std"] = summary["accuracy_raw_std"] * 100.0

    # Reindex to the canonical (baseline × query_type) grid with NaNs for missing combos
    ordered_baselines = _ordered_baselines_from(summary["baseline"])
    full_index = pd.MultiIndex.from_product(
        [ordered_baselines, QUERY_TYPE_ORDER], names=["baseline", "query_type"]
    )
    summary = summary.set_index(["baseline", "query_type"]).reindex(full_index).reset_index()
    return summary


def _balanced_overall_accuracy(frames: list[pd.DataFrame], accuracy_col: str) -> pd.DataFrame:
    """Overall accuracy per baseline, balanced across datasets."""
    per_dataset: list[pd.DataFrame] = []
    for df in frames:
        agg = (
            df.groupby(["dataset", "baseline"], as_index=False)
            .agg(accuracy_raw=(accuracy_col, "mean"))
        )
        per_dataset.append(agg)

    stacked = pd.concat(per_dataset, ignore_index=True)
    overall = (
        stacked.groupby("baseline", as_index=False)
        .agg(
            accuracy_raw=("accuracy_raw", "mean"),
            accuracy_raw_std=("accuracy_raw", "std"),
        )
    )
    overall["accuracy_raw_std"] = overall["accuracy_raw_std"].fillna(0.0)
    overall["accuracy_percent"] = overall["accuracy_raw"] * 100.0
    overall["accuracy_percent_std"] = overall["accuracy_raw_std"] * 100.0
    return overall


def _per_dataset_summary(frames: list[pd.DataFrame], accuracy_col: str) -> pd.DataFrame:
    """Per-dataset × baseline × query_type breakdown for transparency CSV."""
    per_dataset: list[pd.DataFrame] = []
    for df in frames:
        agg = (
            df.groupby(["dataset", "baseline", "query_type"], as_index=False)
            .agg(
                accuracy_raw=(accuracy_col, "mean"),
                latency_s=("latency_s", "mean"),
                cost_usd=("cost_usd", "mean"),
                input_tokens=("input_tokens", "mean"),
                output_tokens=("output_tokens", "mean"),
                n_queries=("query_id", "nunique"),
            )
        )
        agg["accuracy_percent"] = agg["accuracy_raw"] * 100.0
        agg["dataset_label"] = agg["dataset"].map(DATASET_LABEL)
        per_dataset.append(agg)

    return pd.concat(per_dataset, ignore_index=True).sort_values(
        ["dataset", "baseline", "query_type"]
    )


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _metric_table_from_summary(summary: pd.DataFrame, metric_key: str) -> pd.DataFrame:
    table = summary.pivot(index="baseline", columns="query_type", values=metric_key)
    table = table.reindex(index=_ordered_baselines_from(pd.Series(table.index)))
    table = table.reindex(columns=QUERY_TYPE_ORDER)
    table.index = [BASELINE_LABELS.get(b, str(b)) for b in table.index]
    table.index.name = "Baseline"
    return table


def _plot_cross_metric(
    summary: pd.DataFrame,
    metric_key: str,
    metric_std_key: str,
    overall_df: pd.DataFrame | None,
    output_png: Path,
    title: str,
    ylabel: str,
    format_kind: str,
    ylim: tuple[float, float] | None,
    dataset_count: int,
) -> None:
    plt = _import_matplotlib()
    _configure_plot_style(plt)

    include_overall = overall_df is not None
    query_labels = list(QUERY_TYPE_ORDER)
    if include_overall:
        query_labels.append("All")
    x = np.arange(len(query_labels))
    width = 0.24

    fig, ax = plt.subplots(figsize=(13.5, 7.5))

    baselines = _ordered_baselines_from(summary["baseline"])
    for idx, baseline in enumerate(baselines):
        base_df = summary[summary["baseline"] == baseline].set_index("query_type")
        vals: list[float] = [
            float(base_df[metric_key].get(qt, np.nan)) for qt in QUERY_TYPE_ORDER
        ]
        errs = np.array(
            [float(base_df[metric_std_key].get(qt, 0.0)) for qt in QUERY_TYPE_ORDER],
            dtype=float,
        )
        errs = np.nan_to_num(errs, nan=0.0)

        if include_overall:
            ov_row = overall_df[overall_df["baseline"] == baseline]
            ov_val = float(ov_row[metric_key].iloc[0]) if not ov_row.empty else np.nan
            ov_std = (
                float(ov_row[metric_std_key].iloc[0])
                if not ov_row.empty and metric_std_key in ov_row.columns
                else 0.0
            )
            vals.append(ov_val)
            errs = np.append(errs, float(ov_std))

        centers = x + (idx - (len(baselines) - 1) / 2) * width
        bars = ax.bar(
            centers,
            vals,
            width=width,
            yerr=errs,
            capsize=4,
            error_kw={"ecolor": "#111111", "elinewidth": 1.2, "capthick": 1.2},
            label=BASELINE_LABELS.get(baseline, baseline),
            color=BASELINE_COLORS.get(baseline, "#999999"),
            edgecolor="#1f1f1f",
            linewidth=0.8,
        )
        for bar, v in zip(bars, vals):
            if pd.isna(v):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                float(v) + max(0.01 * abs(float(v)), 0.3 if format_kind == "percent" else 0.005),
                _format_value_label(float(v), format_kind),
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
            )

    ax.set_title(title + f"\n(balanced mean across {dataset_count} datasets)", pad=16)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Query Type", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(query_labels)
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(
        title="Baseline",
        loc="upper left",
        bbox_to_anchor=(1.01, 1),
        borderaxespad=0,
        frameon=True,
        fontsize=14,
        title_fontsize=14,
    )
    fig.subplots_adjust(left=0.09, right=0.84, bottom=0.12, top=0.86)
    fig.savefig(output_png, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cross_dataset_visualization(
    dataset_metrics: dict[str, str],
    output_dir: str,
    accuracy_col: str = "gt_score",
    title_prefix: str = "Flash-Fusion",
) -> list[Path]:
    """Load per-dataset metrics, aggregate, and write charts + tables.

    Args:
        dataset_metrics: Mapping of dataset name → path to metrics.csv.
                         Example: {"wisdm": "...", "mit_ecg": "...", "bus": "..."}
        output_dir:      Directory where output files will be written.
        accuracy_col:    Column used as the accuracy metric (default: gt_score).
        title_prefix:    Prefix for chart titles.

    Returns:
        List of written output Paths.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Load and tag each dataset
    frames: list[pd.DataFrame] = []
    loaded_datasets: list[str] = []
    for ds_name, metrics_path in dataset_metrics.items():
        if not Path(metrics_path).exists():
            print(f"  Warning: {metrics_path} not found; skipping {ds_name}.")
            continue
        df = _load_and_tag(metrics_path, ds_name)
        frames.append(df)
        loaded_datasets.append(ds_name)

    if not frames:
        print("  Warning: no metrics files loaded; skipping cross-dataset visualization.")
        return []

    dataset_count = len(frames)
    summary = _balanced_aggregate(frames, accuracy_col)
    overall = _balanced_overall_accuracy(frames, accuracy_col)
    per_dataset_df = _per_dataset_summary(frames, accuracy_col)

    output_paths: list[Path] = []

    # Transparency CSV: per-dataset breakdown
    breakdown_csv = out / "cross_per_dataset_breakdown.csv"
    per_dataset_df.to_csv(breakdown_csv, index=False)
    output_paths.append(breakdown_csv)
    print(f"  Wrote {breakdown_csv}")

    # Balanced aggregate summary CSV
    agg_csv = out / "cross_aggregate_summary.csv"
    summary.to_csv(agg_csv, index=False)
    output_paths.append(agg_csv)
    print(f"  Wrote {agg_csv}")

    # Charts and tables per metric
    for spec in METRICS:
        metric_key = str(spec["key"])
        metric_std_key = f"{metric_key}_std"
        overall_key_present = metric_key in overall.columns

        chart_path = out / str(spec["filename"])
        _plot_cross_metric(
            summary=summary,
            metric_key=metric_key,
            metric_std_key=metric_std_key,
            overall_df=overall if (metric_key == "accuracy_percent" and overall_key_present) else None,
            output_png=chart_path,
            title=f"{title_prefix} — {spec['title']}",
            ylabel=str(spec["ylabel"]),
            format_kind=str(spec["format_kind"]),
            ylim=spec["ylim"],
            dataset_count=dataset_count,
        )
        output_paths.append(chart_path)
        print(f"  Wrote {chart_path}")

        table = _metric_table_from_summary(summary, metric_key)
        std_table = (
            _metric_table_from_summary(summary, metric_std_key)
            if metric_std_key in summary.columns
            else None
        )

        csv_path = out / f"{spec['table_base']}.csv"
        md_path = out / f"{spec['table_base']}.md"
        table.to_csv(csv_path)
        _save_metric_table_markdown(
            table,
            md_path,
            heading=str(spec["title"]),
            format_kind=str(spec["format_kind"]),
            std_df=std_table,
        )
        output_paths.extend([csv_path, md_path])
        print(f"  Wrote {csv_path}")
        print(f"  Wrote {md_path}")

    return output_paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-dataset balanced visualization. Aggregates per-dataset metrics.csv "
            "files and produces charts showing query-type performance across all datasets."
        )
    )
    parser.add_argument(
        "--wisdm-metrics",
        default=None,
        help="Path to WISDM benchmark/metrics.csv",
    )
    parser.add_argument(
        "--ecg-metrics",
        default=None,
        help="Path to MIT-ECG benchmark/metrics.csv",
    )
    parser.add_argument(
        "--bus-metrics",
        default=None,
        help="Path to Bus benchmark/metrics.csv",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/eval_results/runs/latest/visuals_all",
        help="Output directory for cross-dataset charts and tables",
    )
    parser.add_argument(
        "--accuracy-column",
        default="gt_score",
        help="Accuracy column in metrics.csv (default: gt_score)",
    )
    parser.add_argument(
        "--title-prefix",
        default="Flash-Fusion",
        help="Chart title prefix",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dataset_metrics: dict[str, str] = {}
    if args.wisdm_metrics:
        dataset_metrics[DATASET_WISDM] = args.wisdm_metrics
    if args.ecg_metrics:
        dataset_metrics[DATASET_MIT_ECG] = args.ecg_metrics
    if args.bus_metrics:
        dataset_metrics[DATASET_BUS] = args.bus_metrics

    if not dataset_metrics:
        parser.error("Provide at least one of --wisdm-metrics, --ecg-metrics, --bus-metrics")

    paths = run_cross_dataset_visualization(
        dataset_metrics=dataset_metrics,
        output_dir=args.output,
        accuracy_col=args.accuracy_column,
        title_prefix=args.title_prefix,
    )
    print(f"\nCross-dataset visualization: {len(paths)} files written to {args.output}")


if __name__ == "__main__":
    main()
