#!/usr/bin/env python3

from __future__ import annotations

"""Generate primary accuracy figures from July26 baseline results.

Figures produced:
1) Accuracy versus baselines across datasets
2) Accuracy versus baselines across query types
"""

"""
How to invoke each comparison:

ReAct OOS abstention (before = run without the new prompt, after = run with):

```
cd flashfusion/viz && python llamas.py \
  --react-oos-after-root ../results/react_oos_after \
  --before-after-baselines REACT_ONLY \
  --before-after-query-types "Out-of-Scope" \
  --before-after-title "Out-of-Scope Performance" \
  --output-dir ../results/figures/react_oos
```

Flash-Fusion S1/S2 SLM (before = 70B everywhere, after = 8B on S1/S2):

```
python llamas.py \
  --before-dir ../../results/ff_70b/bus \
  --after-dir  ../../results/ff_8b_s12/bus \
  --before-after-baselines FLASH_FUSION \
  --before-after-title "Flash-Fusion: S1/S2 with Llama-3.1-8B vs 70B"
```

"""

import argparse
from pathlib import Path
import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from measure import (
    BASELINE_COLORS,
    BASELINE_HATCHES,
    BASELINE_ORDER,
    DATASET_LABELS,
    DATASET_ORDER,
    QUERY_TYPE_ORDER,
    aggregate_accuracy_before_after,
    aggregate_accuracy_by_dataset,
    aggregate_accuracy_by_query_type,
    display_baseline,
    load_all_metrics,
    load_ffpaper_flash_fusion,
    load_ffpaper_metrics,
    load_metrics_from_dir,
)

TOP3_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
DATASET_FIG_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER", "HARGPT_PAPER", "LLMSENSE_PAPER"]

RC = {
    "font.family": "DejaVu Sans",
    "font.size": 13.5,
    "axes.labelsize": 13.5,
    "axes.labelweight": "bold",
    "xtick.labelsize": 13.0,
    "ytick.labelsize": 13.0,
    "legend.fontsize": 12.5,
    "legend.title_fontsize": 12.5,
    "axes.facecolor": "#ffffff",
    "figure.facecolor": "#ffffff",
}


def _clean_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(1.0)
    ax.spines["bottom"].set_linewidth(1.0)


def _bars_with_error_labels(
    ax,
    xpos: list[float],
    means: list[float],
    stds: list[float],
    width: float,
    baseline: str,
    label_shift: float = 0.0,
):
    means_arr = np.asarray(means, dtype=float)
    stds_arr = np.asarray(stds, dtype=float)
    upper = np.maximum(0.0, np.minimum(stds_arr, 100.0 - means_arr))
    lower = np.maximum(0.0, np.minimum(stds_arr, means_arr))
    bounded_yerr = np.vstack([lower, upper])

    bars = ax.bar(
        xpos,
        means,
        width,
        label=display_baseline(baseline),
        color=BASELINE_COLORS.get(baseline, "#999999"),
        edgecolor="#333333",
        linewidth=0.9,
        yerr=bounded_yerr,
        error_kw={"elinewidth": 1.2, "capsize": 4, "ecolor": "#222222"},
        hatch=BASELINE_HATCHES.get(baseline),
    )
    for bar, val in zip(bars, means):
        if val <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + max(abs(val) * 0.02, 0.4) + label_shift,
            f"{val:.0f}%",
            ha="center",
            va="bottom",
            fontsize=9.0,
            fontweight="bold",
        )


def plot_accuracy_across_datasets(summary: pd.DataFrame, out_path: Path) -> None:
    plt.rcParams.update(RC)

    x_labels = DATASET_ORDER
    baselines = DATASET_FIG_BASELINES
    x = list(range(len(x_labels)))
    width = 0.8 / max(len(baselines), 1)

    fig, ax = plt.subplots(figsize=(7.1, 3.8))
    for i, baseline in enumerate(baselines):
        bdf = summary[summary["baseline"] == baseline]
        means: list[float] = []
        stds: list[float] = []
        for dataset in x_labels:
            row = bdf[bdf["dataset"] == dataset]
            if row.empty:
                means.append(0.0)
                stds.append(0.0)
            else:
                means.append(float(row["mean"].iloc[0]))
                stds.append(float(row["std"].iloc[0]))

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        _bars_with_error_labels(ax, xpos, means, stds, width, baseline, label_shift=1.2 * (i % 2))

    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in x_labels])
    ax.set_xlabel("Dataset")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 110)
    ax.yaxis.grid(linestyle="--", alpha=0.35, linewidth=1.0)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.20), frameon=False, columnspacing=0.9, handletextpad=0.5)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_accuracy_across_query_types(summary: pd.DataFrame, out_path: Path) -> None:
    plt.rcParams.update(RC)

    x_labels = QUERY_TYPE_ORDER
    baselines = TOP3_BASELINES
    x = list(range(len(x_labels)))
    width = 0.8 / max(len(baselines), 1)

    fig, ax = plt.subplots(figsize=(7.1, 3.8))
    for i, baseline in enumerate(baselines):
        bdf = summary[summary["baseline"] == baseline]
        means: list[float] = []
        stds: list[float] = []
        for query_type in x_labels:
            row = bdf[bdf["query_type"] == query_type]
            if row.empty:
                means.append(0.0)
                stds.append(0.0)
            else:
                means.append(float(row["mean"].iloc[0]))
                stds.append(float(row["std"].iloc[0]))

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        _bars_with_error_labels(ax, xpos, means, stds, width, baseline, label_shift=1.0 * (i % 2))

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Query Type")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 110)
    ax.yaxis.grid(linestyle="--", alpha=0.35, linewidth=1.0)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.20), frameon=False)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_before_after(
    summary: pd.DataFrame,
    out_path: Path,
    *,
    group_col: str = "query_type",
    baselines: list[str] | None = None,
    title: str = "",
    x_label: str = "",
) -> None:
    """Grouped before/after bar chart for a single metric comparison.

    summary must come from ``aggregate_accuracy_before_after`` and contain
    columns: label ('before'/'after'), baseline, <group_col>, mean, std.
    """
    plt.rcParams.update(RC)

    if baselines is None:
        baselines = list(summary["baseline"].dropna().unique())
    x_labels = list(summary[group_col].dropna().unique())
    x = list(range(len(x_labels)))

    # Two label variants; baselines side-by-side within each (label, group) slot.
    slot_count = 2 * len(baselines)  # before + after for each baseline
    width = 0.8 / max(slot_count, 1)

    # Color: before = full baseline colour, after = lighter (~40% lighter via alpha)
    BEFORE_ALPHA = 1.0
    AFTER_ALPHA = 0.45

    fig, ax = plt.subplots(figsize=(max(7.1, len(x_labels) * 1.5 + 1), 3.8))

    handles: list = []
    handle_labels: list[str] = []

    slot = 0
    for baseline in baselines:
        for li, label in enumerate(("before", "after")):
            bdf = summary[(summary["baseline"] == baseline) & (summary["label"] == label)]
            means: list[float] = []
            stds: list[float] = []
            for g in x_labels:
                row = bdf[bdf[group_col] == g]
                means.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)
                stds.append(float(row["std"].iloc[0]) if not row.empty else 0.0)

            xpos = [p - 0.4 + (slot + 0.5) * width for p in x]
            color = BASELINE_COLORS.get(baseline, "#999999")
            alpha = BEFORE_ALPHA if label == "before" else AFTER_ALPHA
            edge = "#333333" if label == "before" else "#666666"
            lw = 0.9 if label == "before" else 0.6
            hatch = BASELINE_HATCHES.get(baseline) if label == "after" else None

            means_arr = np.asarray(means, dtype=float)
            stds_arr = np.asarray(stds, dtype=float)
            upper = np.maximum(0.0, np.minimum(stds_arr, 100.0 - means_arr))
            lower = np.maximum(0.0, np.minimum(stds_arr, means_arr))
            bounded_yerr = np.vstack([lower, upper])

            bars = ax.bar(
                xpos, means, width,
                color=color,
                alpha=alpha,
                edgecolor=edge,
                linewidth=lw,
                hatch=hatch,
                yerr=bounded_yerr,
                error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#444444"},
            )
            # One proxy per (baseline, label) pair for legend
            proxy = plt.Rectangle(
                (0, 0), 1, 1,
                facecolor=color,
                alpha=alpha,
                edgecolor=edge,
                linewidth=lw,
                hatch=hatch,
            )
            lbl = f"{display_baseline(baseline)} ({label})"
            if lbl not in handle_labels:
                handles.append(proxy)
                handle_labels.append(lbl)

            # Value labels above bars
            for bar, val in zip(bars, means):
                if val <= 0:
                    continue
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    bar.get_height() + 0.8,
                    f"{val:.0f}%",
                    ha="center", va="bottom",
                    fontsize=7.5, fontweight="bold",
                    color="#333333",
                )
            slot += 1

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel(x_label or group_col.replace("_", " ").title())
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 118)
    if title:
        ax.set_title(title, fontsize=13.0, pad=6)
    ax.yaxis.grid(linestyle="--", alpha=0.35, linewidth=1.0)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ncol = min(len(handle_labels), 4)
    ax.legend(
        handles, handle_labels,
        ncol=ncol,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.22),
        frameon=False,
        fontsize=10.5,
        columnspacing=0.8,
        handletextpad=0.4,
    )
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.32)
    fig.tight_layout(rect=(0.0, 0.06, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _load_oos_after_all_datasets(react_oos_after_root: Path) -> pd.DataFrame:
    """Load React-after OOS results from react_oos_after/{bus,mit_ecg,wisdm}/metrics.csv.

    benchmark metrics.csv has no dataset column, so the dataset is injected from
    the directory name after loading.
    """
    # map canonical dataset name -> subdirectory name used by benchmark --output
    dataset_dirs = {"bus": "bus", "wisdm": "wisdm", "ecg": "mit_ecg"}
    parts: list[pd.DataFrame] = []
    for dataset, dir_name in dataset_dirs.items():
        d = react_oos_after_root / dir_name
        if not d.exists():
            continue
        df = load_metrics_from_dir(
            d,
            label="after",
            baselines=["REACT_ONLY"],
            query_type_filter=["Out-of-Scope"],
        )
        df["dataset"] = dataset
        df["dataset"] = pd.Categorical(df["dataset"], categories=list(DATASET_ORDER), ordered=True)
        parts.append(df)
    if not parts:
        raise FileNotFoundError(f"No react_oos_after results found under {react_oos_after_root}")
    return pd.concat(parts, ignore_index=True)


def _aggregate_oos_by_dataset(df: pd.DataFrame, series_name: str) -> pd.DataFrame:
    """Filter to Out-of-Scope queries, group by dataset, return mean/std summary.

    Uses two-stage aggregation (per-run average first, then across runs) to
    match aggregate_accuracy_by_dataset and avoid inflated std from binary
    per-query scores.
    """
    oos = df[df["query_type"] == "Out-of-Scope"].copy()
    # Stage 1: average OOS accuracy per (dataset, run_id)
    per_run = (
        oos.groupby(["dataset", "run_id"], as_index=False, observed=True)
        .agg(accuracy_percent=("accuracy_percent", "mean"))
    )
    # Stage 2: mean/std across run-level averages
    agg = (
        per_run.groupby("dataset", as_index=False, observed=True)
        .agg(
            mean=("accuracy_percent", "mean"),
            std=("accuracy_percent", "std"),
            n=("accuracy_percent", "count"),
        )
    )
    agg["std"] = agg["std"].fillna(0.0)
    agg["series"] = series_name
    return agg


# Visual attributes for the three OOS comparison series
_OOS_SERIES = ["FLASH_FUSION", "REACT_ONLY_AFTER", "REACT_ONLY_BEFORE"]
_OOS_SERIES_LABELS = {
    "REACT_ONLY_BEFORE": "ReAct (before)",
    "REACT_ONLY_AFTER": "ReAct (after)",
    "FLASH_FUSION": "Flash-Fusion",
}
_OOS_SERIES_COLORS = {
    "REACT_ONLY_BEFORE": BASELINE_COLORS["REACT_ONLY"],
    "REACT_ONLY_AFTER": BASELINE_COLORS["REACT_ONLY"],
    "FLASH_FUSION": BASELINE_COLORS["FLASH_FUSION"],
}
_OOS_SERIES_ALPHA = {"REACT_ONLY_BEFORE": 1.0, "REACT_ONLY_AFTER": 0.45, "FLASH_FUSION": 1.0}
_OOS_SERIES_HATCH = {"REACT_ONLY_BEFORE": None, "REACT_ONLY_AFTER": "//", "FLASH_FUSION": None}


def plot_oos_abstention_across_datasets(summary: pd.DataFrame, out_path: Path) -> None:
    """Bar chart: OOS query accuracy for ReAct-before, ReAct-after, Flash-Fusion × dataset.

    summary has columns: series, dataset, mean, std
    """
    plt.rcParams.update(RC)

    x_labels = DATASET_ORDER
    x = list(range(len(x_labels)))
    width = 0.8 / len(_OOS_SERIES)

    fig, ax = plt.subplots(figsize=(7.1, 3.8))

    handles: list = []
    handle_labels: list[str] = []

    for i, series in enumerate(_OOS_SERIES):
        sdf = summary[summary["series"] == series]
        means: list[float] = []
        stds: list[float] = []
        for dataset in x_labels:
            row = sdf[sdf["dataset"] == dataset]
            means.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)
            stds.append(float(row["std"].iloc[0]) if not row.empty else 0.0)

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        color = _OOS_SERIES_COLORS[series]
        alpha = _OOS_SERIES_ALPHA[series]
        hatch = _OOS_SERIES_HATCH[series]

        means_arr = np.asarray(means, dtype=float)
        stds_arr = np.asarray(stds, dtype=float)
        lower_endpoint = np.clip(means_arr - stds_arr, 0.05, 100.0)
        upper_endpoint = np.clip(means_arr + stds_arr, 0.0, 100.0)
        bounded_yerr = np.vstack([
            means_arr - lower_endpoint,
            upper_endpoint - means_arr,
        ])

        bars = ax.bar(
            xpos, means, width,
            color=color,
            alpha=alpha,
            edgecolor="#333333",
            linewidth=0.9,
            hatch=hatch,
            yerr=bounded_yerr,
            error_kw={"elinewidth": 1.2, "capsize": 4, "ecolor": "#222222"},
        )
        for bar, val in zip(bars, means):
            if val <= 0:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + max(abs(val) * 0.02, 0.4) + 1.0 * (i % 2),
                f"{val:.0f}%",
                ha="center", va="bottom",
                fontsize=9.0, fontweight="bold",
            )
        proxy = plt.Rectangle(
            (0, 0), 1, 1,
            facecolor=color, alpha=alpha,
            edgecolor="#333333", linewidth=0.9, hatch=hatch,
        )
        handles.append(proxy)
        handle_labels.append(_OOS_SERIES_LABELS[series])

    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in x_labels])
    ax.set_xlabel("Dataset")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 100)
    ax.yaxis.grid(linestyle="--", alpha=0.35, linewidth=1.0)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ax.legend(
        handles, handle_labels,
        ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.20),
        frameon=False, columnspacing=0.9, handletextpad=0.5,
    )
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate July26 primary accuracy figures.")
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--results-root",
        default=str(script_dir.parent / "results" / "july26"),
        help="Root folder containing baseline result folders for July26.",
    )
    parser.add_argument(
        "--run-dir",
        default="july26_full",
        help="Per-dataset run folder name under each baseline/dataset.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(script_dir / "results" / "primary_visualizations"),
        help="Output folder for primary figures.",
    )
    parser.add_argument(
        "--llmsense-root",
        default=None,
        help="Alternate root directory for LLMSense results if missing from primary root.",
    )
    parser.add_argument(
        "--hargpt-root",
        default=None,
        help="Alternate root directory for HARGPT results if missing from primary root.",
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--ffpaper-data-root",
        default=str(script_dir / "performance_ffpaper" / "data" / "run_all_remaining_20260528_215519"),
        help="performance_ffpaper run root used as last-resort fallback (dataset/benchmark/metrics.csv layout).",
    )
    parser.add_argument(
        "--ffpaper-ecg-ff-root",
        default=str(script_dir / "performance_ffpaper" / "data" / "run_ecg_ff_20260529_115359"),
        help="performance_ffpaper ECG Flash-Fusion dedicated run root.",
    )
    # --- Before/after comparison args ---
    parser.add_argument(
        "--before-dir",
        default=None,
        help=(
            "Benchmark output directory for the 'before' condition of a before/after comparison "
            "(e.g. ReAct without OOS abstention, or Flash-Fusion with 70B on all stages). "
            "Expects a metrics.csv or run_*/metrics.csv inside this directory."
        ),
    )
    parser.add_argument(
        "--after-dir",
        default=None,
        help=(
            "Benchmark output directory for the 'after' condition of a before/after comparison "
            "(e.g. ReAct with OOS abstention, or Flash-Fusion with 8B on S1/S2)."
        ),
    )
    parser.add_argument(
        "--before-after-baselines",
        default=None,
        help=(
            "Comma-separated baselines to include in the before/after figure. "
            "Defaults to REACT_ONLY for OOS comparisons and FLASH_FUSION for SLM comparisons. "
            "Example: REACT_ONLY or FLASH_FUSION,REACT_ONLY"
        ),
    )
    parser.add_argument(
        "--before-after-query-types",
        default=None,
        help=(
            "Comma-separated query types to include in the before/after figure. "
            "Defaults to all types. Example: \"Out-of-Scope\" or \"Direct,Reasoning,Out-of-Scope\""
        ),
    )
    parser.add_argument(
        "--before-after-title",
        default="",
        help="Optional title for the before/after figure.",
    )
    parser.add_argument(
        "--react-oos-after-root",
        default=str(script_dir.parent / "results" / "react_oos_after"),
        help=(
            "Root folder containing per-dataset react_oos_after benchmark outputs "
            "(expects subdirs bus/, wisdm/, mit_ecg/ each with a metrics.csv). "
            "Used to generate the OOS abstention cross-dataset figure."
        ),
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    results_root = Path(args.results_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build fallback roots dict for baselines with alternate sources
    fallback_roots = {}
    if args.llmsense_root:
        fallback_roots["LLMSENSE_PAPER"] = Path(args.llmsense_root).resolve()
    if args.hargpt_root:
        fallback_roots["HARGPT_PAPER"] = Path(args.hargpt_root).resolve()

    ffpaper_run_root = Path(args.ffpaper_data_root).resolve() if args.ffpaper_data_root else None
    ffpaper_ecg_ff_root = Path(args.ffpaper_ecg_ff_root).resolve() if args.ffpaper_ecg_ff_root else None

    df = load_all_metrics(
        results_root=results_root,
        run_dir=args.run_dir,
        fallback_roots=fallback_roots,
        ffpaper_run_root=ffpaper_run_root,
    )

    def _apply_override(df: pd.DataFrame, override_df: pd.DataFrame, baseline: str, dataset: str | None = None) -> pd.DataFrame:
        """Replace rows for (baseline[, dataset]) with override_df rows."""
        if dataset is not None:
            mask = (df["baseline"] == baseline) & (df["dataset"] == dataset)
        else:
            mask = df["baseline"] == baseline
        df = pd.concat([df[~mask], override_df], ignore_index=True)
        df["baseline"] = pd.Categorical(df["baseline"], categories=list(BASELINE_ORDER), ordered=True)
        df["dataset"] = pd.Categorical(df["dataset"], categories=list(DATASET_ORDER), ordered=True)
        df["query_type"] = pd.Categorical(df["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)
        return df

    # Override Flash-Fusion Direct queries (query_id 1-4) only with ffpaper sources.
    # Reasoning (5-8) and Out-of-Scope (9-12) remain sourced from july26.
    if ffpaper_run_root is not None and ffpaper_ecg_ff_root is not None:
        ff_override = load_ffpaper_flash_fusion(ffpaper_run_root, ffpaper_ecg_ff_root)
        ff_direct = ff_override[ff_override["query_id"].isin([1, 2, 3, 4])].copy()
        direct_mask = (df["baseline"] == "FLASH_FUSION") & (df["query_id"].isin([1, 2, 3, 4]))
        df = pd.concat([df[~direct_mask], ff_direct], ignore_index=True)
        df["baseline"] = pd.Categorical(df["baseline"], categories=list(BASELINE_ORDER), ordered=True)
        df["dataset"] = pd.Categorical(df["dataset"], categories=list(DATASET_ORDER), ordered=True)
        df["query_type"] = pd.Categorical(df["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)

    # Override LLMSense WISDM: july26 run produced all-zero scores; ffpaper run has real results.
    if ffpaper_run_root is not None:
        llmsense_wisdm = load_ffpaper_metrics(ffpaper_run_root, "LLMSENSE_PAPER", "wisdm")
        if llmsense_wisdm is not None and not llmsense_wisdm.empty:
            df = _apply_override(df, llmsense_wisdm, "LLMSENSE_PAPER", "wisdm")

    by_dataset = aggregate_accuracy_by_dataset(df)
    by_query_type = aggregate_accuracy_by_query_type(df)

    fig1 = output_dir / "accuracy_vs_baselines_across_datasets.png"
    fig2 = output_dir / "accuracy_vs_baselines_across_query_types.png"
    plot_accuracy_across_datasets(by_dataset, fig1)
    plot_accuracy_across_query_types(by_query_type, fig2)

    by_dataset.to_csv(output_dir / "accuracy_vs_baselines_across_datasets_summary.csv", index=False)
    by_query_type.to_csv(output_dir / "accuracy_vs_baselines_across_query_types_summary.csv", index=False)

    print(f"Wrote {fig1}")
    print(f"Wrote {fig2}")

    # --- OOS abstention cross-dataset figure ---
    react_oos_after_root = Path(args.react_oos_after_root).resolve()
    if react_oos_after_root.exists():
        try:
            react_after_oos = _load_oos_after_all_datasets(react_oos_after_root)
            react_before_oos = df[
                (df["baseline"] == "REACT_ONLY") & (df["query_type"] == "Out-of-Scope")
            ].copy()
            ff_oos = df[
                (df["baseline"] == "FLASH_FUSION") & (df["query_type"] == "Out-of-Scope")
            ].copy()

            oos_summary = pd.concat(
                [
                    _aggregate_oos_by_dataset(react_before_oos, "REACT_ONLY_BEFORE"),
                    _aggregate_oos_by_dataset(react_after_oos, "REACT_ONLY_AFTER"),
                    _aggregate_oos_by_dataset(ff_oos, "FLASH_FUSION"),
                ],
                ignore_index=True,
            )
            fig3 = output_dir / "oos_abstention_across_datasets.png"
            plot_oos_abstention_across_datasets(oos_summary, fig3)
            oos_summary.to_csv(
                output_dir / "oos_abstention_across_datasets_summary.csv", index=False
            )
            print(f"Wrote {fig3}")
        except FileNotFoundError as exc:
            print(f"[WARN] Skipping OOS abstention figure: {exc}")
    else:
        print(f"[INFO] --react-oos-after-root {react_oos_after_root} not found; skipping OOS figure.")

    # --- Optional before/after comparison figure ---
    if args.before_dir and args.after_dir:
        before_dir = Path(args.before_dir).resolve()
        after_dir = Path(args.after_dir).resolve()
        ba_baselines = (
            [b.strip().upper() for b in args.before_after_baselines.split(",") if b.strip()]
            if args.before_after_baselines
            else None
        )
        ba_query_types = (
            [qt.strip() for qt in args.before_after_query_types.split(",") if qt.strip()]
            if args.before_after_query_types
            else None
        )
        before_df = load_metrics_from_dir(
            before_dir, label="before",
            baselines=ba_baselines or list(BASELINE_ORDER),
            query_type_filter=ba_query_types,
        )
        after_df = load_metrics_from_dir(
            after_dir, label="after",
            baselines=ba_baselines or list(BASELINE_ORDER),
            query_type_filter=ba_query_types,
        )
        ba_summary = aggregate_accuracy_before_after(
            before_df, after_df,
            baselines=ba_baselines,
        )
        fig3 = output_dir / "before_after_comparison.png"
        plot_before_after(
            ba_summary,
            fig3,
            title=args.before_after_title or "Before / After Comparison",
            x_label="Query Type",
        )
        ba_summary.to_csv(output_dir / "before_after_comparison_summary.csv", index=False)
        print(f"Wrote {fig3}")


if __name__ == "__main__":
    main()
