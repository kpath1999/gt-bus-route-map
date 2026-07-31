#!/usr/bin/env python3

from __future__ import annotations

"""Generate primary accuracy figures from baseline results.

Figures produced:
1) Accuracy versus baselines across datasets (FF, ReAct, AutoIOT)
2) Accuracy versus baselines across query types

```
cd flashfusion/viz && python llamas.py --output-dir results/primary_visualizations
```

Paths (currently) -- metrics.csv would provide all the details:
- Flash-Fusion: flashfusion/results/ff_react_operators/FLASH_FUSION
- ReAct: flashfusion/results/ff_react_operators/REACT_ONLY
- AutoIOT: flashfusion/results/with_slm_predictive
- HARGPT: flashfusion/results/july26/HARGPT_PAPER
- LLMSENSE: flashfusion/results/july26/LLMSENSE_PAPER
"""

import os

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
    load_ffpaper_metrics,
)

TOP3_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
DATASET_FIG_BASELINES = TOP3_BASELINES
FULL_BASELINES = [
    "FLASH_FUSION",
    "REACT_ONLY",
    "AUTOIOT_PAPER",
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


def _prompt_for_root(baseline_label: str, current_default: str | None) -> str | None:
    """Prompt the user for a baseline's results root, defaulting to current_default.

    Pressing Enter keeps current_default (which may be None). Entering "-" or
    "none" explicitly clears an existing default (sets it to None).
    """
    shown_default = current_default if current_default is not None else "none"
    raw = input(f"  {baseline_label} root [{shown_default}]: ").strip()
    if not raw:
        return current_default
    if raw.lower() in {"-", "none"}:
        return None
    return raw


def _prompt_for_baseline_roots(defaults: dict[str, str | None]) -> dict[str, str | None]:
    """Interactively collect a results root for each baseline.

    defaults maps baseline key (e.g. "flash_fusion") to its current default
    root (or None). Returns a dict of the same shape with user-entered values.
    """
    labels = {
        "flash_fusion": "Flash-Fusion",
        "react": "ReAct",
        "autoiot": "AutoIOT",
        "hargpt": "HARGPT",
        "llmsense": "LLMSense",
    }
    print("\nEnter the results root for each baseline (press Enter to keep the default,")
    print("or type '-' / 'none' to clear it):\n")
    roots: dict[str, str | None] = {}
    for key, label in labels.items():
        roots[key] = _prompt_for_root(label, defaults.get(key))
    return roots


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate July26 primary accuracy figures.")
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactively prompt for each baseline's results root instead of using flags.",
    )
    parser.add_argument(
        "--results-root",
        default=str(script_dir.parent / "results" / "ff_react_operators"),
        help="Root folder containing dataset-level metrics.csv files.",
    )
    # this run tag is causing issues; each baseline has a different one. can we do away with it
    # parser.add_argument(
    #     "--run-tag",
    #     required=True,
    #     help="Benchmark run tag, e.g., run_20260730_131331.",
    # )
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

    if args.interactive:
        roots = _prompt_for_baseline_roots(
            {
                "flash_fusion": args.flash_fusion_root,
                "react": args.react_root,
                "autoiot": args.autoiot_root,
                "hargpt": args.hargpt_root,
                "llmsense": args.llmsense_root,
            }
        )
        args.flash_fusion_root = roots["flash_fusion"]
        args.react_root = roots["react"]
        args.autoiot_root = roots["autoiot"]
        args.hargpt_root = roots["hargpt"]
        args.llmsense_root = roots["llmsense"]

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

    # Load from the primary root (ff_newlook_with_react) and fall back to
    # baseline-specific roots for baselines that are not present there.
    df = load_all_metrics(
        results_root=results_root,
        run_dir=args.run_dir,
        fallback_roots=fallback_roots,
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

    # Override LLMSense WISDM: july26 run produced all-zero scores; ffpaper run has real results.
    script_dir = Path(__file__).resolve().parent
    llmsense_wisdm = os.path.join(script_dir.parent, "july26", "LLMSENSE_PAPER", "wisdm")
    if llmsense_wisdm is not None and not llmsense_wisdm.empty:
        df = _apply_override(df, llmsense_wisdm, "LLMSENSE_PAPER", "wisdm")

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

if __name__ == "__main__":
    main()
