#!/usr/bin/env python3

from __future__ import annotations

"""Generate primary accuracy figures from baseline results.

Figures produced:
1) Accuracy versus baselines across datasets (FF, ReAct, AutoIOT)
2) Accuracy versus baselines across query types
3) OOS abstention accuracy across datasets (ReAct before/after + Flash-Fusion)

Default invocation (all data from ff_newlook_with_react; ReAct OOS and
ReAct-after abstention from react_after; ReAct-before from july26):

```
cd flashfusion/viz && python llamas.py --output-dir results/primary_visualizations
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
    aggregate_accuracy_by_dataset,
    aggregate_accuracy_by_query_type,
    display_baseline,
    load_all_metrics,
    load_ffpaper_flash_fusion,
    load_ffpaper_metrics,
    load_metrics_from_dir,
)

TOP3_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
DATASET_FIG_BASELINES = TOP3_BASELINES
FULL_BASELINES = [
    "FLASH_FUSION",
    "AUTOIOT_PAPER",
    "REACT_ONLY",
    "HARGPT_PAPER",
    "LLMSENSE_PAPER",
]

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


def _parse_csv_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or None


def _filter_metrics(
    df: pd.DataFrame,
    baselines: list[str] | None,
    query_types: list[str] | None,
) -> pd.DataFrame:
    out = df.copy()
    if baselines is not None:
        out = out[out["baseline"].isin(baselines)].copy()
    if query_types is not None:
        out = out[out["query_type"].isin(query_types)].copy()
    out["baseline"] = pd.Categorical(out["baseline"], categories=list(BASELINE_ORDER), ordered=True)
    out["dataset"] = pd.Categorical(out["dataset"], categories=list(DATASET_ORDER), ordered=True)
    out["query_type"] = pd.Categorical(out["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)
    return out


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


def plot_accuracy_across_datasets(
    summary: pd.DataFrame,
    out_path: Path,
    baselines: list[str] | None = None,
) -> None:
    plt.rcParams.update(RC)

    x_labels = DATASET_ORDER
    baselines = baselines or DATASET_FIG_BASELINES
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

    ax.legend(
        ncol=min(5, max(1, len(baselines))),
        loc="upper center",
        bbox_to_anchor=(0.5, -0.20),
        frameon=False,
        columnspacing=0.9,
        handletextpad=0.5,
    )
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_accuracy_across_query_types(
    summary: pd.DataFrame,
    out_path: Path,
    baselines: list[str] | None = None,
    query_types: list[str] | None = None,
) -> None:
    plt.rcParams.update(RC)

    if query_types is None:
        present_query_types = [
            str(value)
            for value in summary["query_type"].dropna().unique().tolist()
            if str(value) in QUERY_TYPE_ORDER
        ]
        x_labels = [qt for qt in QUERY_TYPE_ORDER if qt in present_query_types]
    else:
        x_labels = [qt for qt in QUERY_TYPE_ORDER if qt in query_types]
    baselines = baselines or TOP3_BASELINES
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

    ax.legend(
        ncol=min(3, max(1, len(baselines))),
        loc="upper center",
        bbox_to_anchor=(0.5, -0.20),
        frameon=False,
    )
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _load_react_before_after_root(
    root: Path,
    label: str,
    *,
    old_layout: bool,
    query_type_filter: list[str] | None = ["Out-of-Scope"],
) -> pd.DataFrame:
    """Load ReAct metrics from the July26 (old) or new-look (new) per-dataset layout.

    old_layout=True expects root/REACT_ONLY/<dataset>/july26_full/metrics.csv
    (the original July26 ReAct results, i.e. the "before" scope-check-prompt data).
    old_layout=False expects root/<dataset>/metrics.csv directly (the new-look
    per-dataset benchmark layout, e.g. results/ff_newlook_with_react, i.e. the
    "after" data).

    query_type_filter defaults to Out-of-Scope only (used by the OOS before/after
    figures); pass None to load all query types (used to override REACT_ONLY
    wholesale in the primary accuracy figures).
    """
    dataset_dirs = {"bus": "bus", "wisdm": "wisdm", "ecg": "mit_ecg"}
    parts: list[pd.DataFrame] = []
    for dataset, dir_name in dataset_dirs.items():
        metrics_root = root / "REACT_ONLY" / dir_name / "july26_full" if old_layout else root / dir_name
        if not metrics_root.exists():
            continue
        df = load_metrics_from_dir(
            metrics_root,
            label=label,
            baselines=["REACT_ONLY"],
            query_type_filter=query_type_filter,
        )
        df["dataset"] = dataset
        parts.append(df)
    if not parts:
        raise FileNotFoundError(f"No ReAct metrics found under {root}")
    out = pd.concat(parts, ignore_index=True)
    out["dataset"] = pd.Categorical(out["dataset"], categories=list(DATASET_ORDER), ordered=True)
    return out


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
        lower_endpoint = np.maximum(0.0, means_arr - stds_arr)
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
        default=str(script_dir.parent / "results" / "ff_newlook_with_react"),
        help="Root folder containing dataset-level metrics.csv files.",
    )
    parser.add_argument(
        "--flash-fusion-root",
        default=None,
        help="Optional override root for FLASH_FUSION baseline data.",
    )
    parser.add_argument(
        "--react-root",
        default=None,
        help="Optional override root for REACT_ONLY baseline data.",
    )
    parser.add_argument(
        "--autoiot-root",
        default=str(script_dir.parent / "results" / "with_slm_predictive"),
        help="Optional override root for AUTOIOT_PAPER baseline data.",
    )
    parser.add_argument(
        "--run-dir",
        default="ff_newlook_react",
        help="Per-dataset run folder name under each baseline/dataset.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(script_dir / "results" / "primary_visualizations"),
        help="Output folder for primary figures.",
    )
    parser.add_argument(
        "--llmsense-root",
        default=str(script_dir.parent / "results" / "july26"),
        help="Alternate root directory for LLMSense results if missing from primary root.",
    )
    parser.add_argument(
        "--hargpt-root",
        default=str(script_dir.parent / "results" / "july26"),
        help="Alternate root directory for HARGPT results if missing from primary root.",
    )
    parser.add_argument(
        "--ffpaper-data-root",
        default=None,
        help="performance_ffpaper run root used as last-resort fallback (dataset/benchmark/metrics.csv layout).",
    )
    parser.add_argument(
        "--ffpaper-ecg-ff-root",
        default=None,
        help="performance_ffpaper ECG Flash-Fusion dedicated run root.",
    )
    parser.add_argument(
        "--react-before-root",
        default=None,
        help=(
            "Flat-layout root for ReAct 'before' (no abstention) OOS results "
            "(root/<dataset>/metrics.csv). Produced by run_react_BeforeAfter.sh. "
            "When set, takes precedence over --react-july26-root for the OOS figure."
        ),
    )
    parser.add_argument(
        "--react-after-root",
        default=str(script_dir.parent / "results" / "react_after"),
        help=(
            "Flat-layout root for ReAct 'after' (with abstention) OOS results "
            "(root/<dataset>/metrics.csv). Used for both the OOS abstention figure "
            "and the Out-of-Scope portion of the primary accuracy figures."
        ),
    )
    parser.add_argument(
        "--react-july26-root",
        default=str(script_dir.parent / "results" / "july26"),
        help=(
            "Legacy July26 REACT_ONLY results root (root/REACT_ONLY/<dataset>/july26_full/ "
            "metrics.csv layout). Fallback 'before' source when --react-before-root is not set."
        ),
    )
    parser.add_argument(
        "--baseline-set",
        default=",".join(FULL_BASELINES),
        help="Comma-separated baseline codes to include in figures.",
    )
    parser.add_argument(
        "--dataset-baseline-set",
        default=",".join(FULL_BASELINES),
        help="Comma-separated baseline codes to include in the dataset accuracy figure.",
    )
    parser.add_argument(
        "--query-type-baseline-set",
        default=",".join(TOP3_BASELINES),
        help="Comma-separated baseline codes to include in the query-type accuracy figure.",
    )
    parser.add_argument(
        "--query-types",
        default=",".join(QUERY_TYPE_ORDER),
        help="Comma-separated query types to include in figures.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    results_root = Path(args.results_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_baselines = [
        b.strip().upper() for b in _parse_csv_list(args.baseline_set) or list(FULL_BASELINES)
    ]
    dataset_baselines = [
        b.strip().upper() for b in _parse_csv_list(args.dataset_baseline_set) or list(FULL_BASELINES)
    ]
    query_type_baselines = [
        b.strip().upper() for b in _parse_csv_list(args.query_type_baseline_set) or list(TOP3_BASELINES)
    ]
    selected_query_types = _parse_csv_list(args.query_types) or list(QUERY_TYPE_ORDER)

    # Build fallback roots dict for baselines with alternate sources.
    # For HARGPT/LLMSense the July26 nested layout is used as the default source
    # because these baselines are not present in the primary ff_newlook_with_react
    # flat layout.
    fallback_roots = {}
    if args.llmsense_root:
        fallback_roots["LLMSENSE_PAPER"] = Path(args.llmsense_root).resolve()
    if args.hargpt_root:
        fallback_roots["HARGPT_PAPER"] = Path(args.hargpt_root).resolve()

    ffpaper_run_root = Path(args.ffpaper_data_root).resolve() if args.ffpaper_data_root else None
    ffpaper_ecg_ff_root = Path(args.ffpaper_ecg_ff_root).resolve() if args.ffpaper_ecg_ff_root else None

    # Load from the primary root (ff_newlook_with_react) and fall back to
    # baseline-specific roots for baselines that are not present there.
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

    baseline_overrides = {
        "FLASH_FUSION": Path(args.flash_fusion_root).resolve() if args.flash_fusion_root else None,
        "REACT_ONLY": Path(args.react_root).resolve() if args.react_root else None,
        "AUTOIOT_PAPER": Path(args.autoiot_root).resolve() if args.autoiot_root else None,
    }
    for baseline_code, override_root in baseline_overrides.items():
        if override_root is None:
            continue
        try:
            override_df = load_all_metrics(
                results_root=override_root,
                baselines=[baseline_code],
                datasets=DATASET_ORDER,
                run_dir=args.run_dir,
            )
            df = _apply_override(df, override_df, baseline_code)
        except ValueError:
            print(f"[WARN] Could not load override data for {baseline_code} from {override_root}")

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

    # Ensure REACT_ONLY is loaded from the flat per-dataset layout in results_root
    # (root/<dataset>/metrics.csv), covering all query types across multiple runs.
    # This supersedes any nested run_dir layout that load_all_metrics may have found.
    if results_root.exists():
        try:
            react_newlook_df = _load_react_before_after_root(
                results_root, "after", old_layout=False, query_type_filter=None
            )
            df = _apply_override(df, react_newlook_df, "REACT_ONLY")
        except FileNotFoundError as exc:
            print(f"[WARN] Could not load REACT_ONLY from {results_root}: {exc}")

    # Override ReAct Out-of-Scope scores from the dedicated react_after source.
    # This affects both the primary accuracy figures and the OOS abstention figure.
    react_after_root_path = Path(args.react_after_root).resolve() if args.react_after_root else None
    if react_after_root_path is not None and react_after_root_path.exists():
        try:
            react_after_oos_df = _load_react_before_after_root(
                react_after_root_path, "after", old_layout=False, query_type_filter=["Out-of-Scope"]
            )
            react_after_oos_df = react_after_oos_df.drop(columns=["label"], errors="ignore")
            react_mask = (df["baseline"] == "REACT_ONLY") & (df["query_type"] == "Out-of-Scope")
            df = pd.concat([df[~react_mask], react_after_oos_df], ignore_index=True)
            df["baseline"] = pd.Categorical(df["baseline"], categories=list(BASELINE_ORDER), ordered=True)
            df["dataset"] = pd.Categorical(df["dataset"], categories=list(DATASET_ORDER), ordered=True)
            df["query_type"] = pd.Categorical(df["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)
        except FileNotFoundError as exc:
            print(f"[WARN] Could not load REACT_ONLY OOS from {react_after_root_path}: {exc}")

    # Inject zero-score Predictive rows for HARGPT and LLMSense so the query-type
    # accuracy figure shows all five baselines on the same x-axis. July26 runs for
    # these baselines did not include queries 13-16, so treat predictive accuracy
    # as 0% across all datasets and runs that are already present. Latency, cost,
    # and token columns are left as NaN so these rows do not contaminate other
    # measurements.
    for zero_pred_baseline in ("HARGPT_PAPER", "LLMSENSE_PAPER"):
        if zero_pred_baseline in selected_baselines:
            present = df[df["baseline"] == zero_pred_baseline]
            if not present.empty:
                run_ids = present["run_id"].unique() if "run_id" in present.columns else [1]
                zero_rows = []
                for dataset in DATASET_ORDER:
                    for run_id in run_ids:
                        for query_id in (13, 14, 15, 16):
                            zero_rows.append({
                                "baseline": zero_pred_baseline,
                                "dataset": dataset,
                                "run_id": run_id,
                                "query_id": query_id,
                                "gt_score": 0.0,
                                "accuracy_percent": 0.0,
                                "query_type": "Predictive",
                            })
                df = pd.concat([df, pd.DataFrame(zero_rows)], ignore_index=True)

    df = _filter_metrics(df, selected_baselines, selected_query_types)

    by_dataset = aggregate_accuracy_by_dataset(df)
    by_query_type = aggregate_accuracy_by_query_type(df)

    fig1 = output_dir / "accuracy_vs_baselines_across_datasets.png"
    fig2 = output_dir / "accuracy_vs_baselines_across_query_types.png"
    plot_accuracy_across_datasets(by_dataset, fig1, baselines=dataset_baselines)
    plot_accuracy_across_query_types(
        by_query_type,
        fig2,
        baselines=query_type_baselines,
        query_types=selected_query_types,
    )

    by_dataset.to_csv(output_dir / "accuracy_vs_baselines_across_datasets_summary.csv", index=False)
    by_query_type.to_csv(output_dir / "accuracy_vs_baselines_across_query_types_summary.csv", index=False)

    print(f"Wrote {fig1}")
    print(f"Wrote {fig2}")

    # --- OOS abstention cross-dataset figure ---
    # "before" source: --react-before-root (flat layout, new runs) takes priority;
    #   falls back to --react-july26-root (nested july26_full layout, legacy data).
    # "after" source: --react-after-root (flat layout, new runs) takes priority;
    #   falls back to the REACT_ONLY slice already in df (from --results-root).
    react_before_root_path = (
        Path(args.react_before_root).resolve() if args.react_before_root else None
    )
    react_july26_root = Path(args.react_july26_root).resolve()

    # Resolve the before source
    if react_before_root_path is not None and react_before_root_path.exists():
        _before_root, _before_old_layout = react_before_root_path, False
    elif react_july26_root.exists():
        _before_root, _before_old_layout = react_july26_root, True
    else:
        _before_root, _before_old_layout = None, False

    if _before_root is not None:
        try:
            react_before_oos = _load_react_before_after_root(
                _before_root, "before", old_layout=_before_old_layout
            )

            # Resolve the after source
            if args.react_after_root:
                react_after_root_path = Path(args.react_after_root).resolve()
                react_after_oos = _load_react_before_after_root(
                    react_after_root_path, "after", old_layout=False
                )
            else:
                react_after_oos = df[
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
        print("[INFO] No ReAct before-source found; skipping OOS abstention figure."
              " Set --react-before-root or --react-july26-root.")


if __name__ == "__main__":
    main()
