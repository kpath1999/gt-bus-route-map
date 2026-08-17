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
- Flash-Fusion: flashfusion/results/ff_and_react_qwen/FLASH_FUSION
- ReAct: flashfusion/results/ff_and_react_qwen/REACT_ONLY
- AutoIOT: flashfusion/results/with_slm_predictive
- HARGPT: flashfusion/results/july26/HARGPT_PAPER
- LLMSENSE: flashfusion/results/july26/LLMSENSE_PAPER
"""

"""
Run to consider:

cd /Users/kausar/Documents/research/flash-fusion/flashfusion/viz
python llamas.py --flash-fusion-root flashfusion/results/ff_and_react_qwen/FLASH_FUSION --react-root flashfusion/results/ff_and_react_qwen/REACT_ONLY --output-dir ../../results/primary_visualizations
python latencystages.py --flash-fusion-root flashfusion/results/ff_and_react_qwen/FLASH_FUSION --react-root flashfusion/results/ff_and_react_qwen/REACT_ONLY --output-dir ../../results/primary_visualizations
python queryaccuracy.py --results-root flashfusion/results/with_slm_predictive --output ../../results/primary_visualizations/accuracy_by_dataset_query_type_summary.csv
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
    CACHE_BASELINE,
    CACHE_BASELINE_VARIANTS,
    DATASET_LABELS,
    DATASET_ORDER,
    QUERY_TYPE_ORDER,
    aggregate_accuracy_by_dataset,
    aggregate_accuracy_by_query_type,
    aggregate_cost_by_dataset,
    aggregate_cost_by_query_type,
    display_baseline,
    expand_baselines,
    split_cache_baseline_rows,
)

from typing import Any

TOP3_BASELINES = ["FLASH_FUSION", CACHE_BASELINE, "REACT_ONLY"]
COST_QUERY_TYPE_BASELINES = [
    "FLASH_FUSION",
    CACHE_BASELINE,
    *CACHE_BASELINE_VARIANTS,
    "REACT_ONLY",
]
DATASET_FIG_BASELINES = TOP3_BASELINES
FULL_BASELINES = [
    "FLASH_FUSION",
    CACHE_BASELINE,
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

    out["query_type"] = out["query_type"].map(
        _query_type_from_complexity
    )

    if baselines is not None:
        out = out[out["baseline"].isin(baselines)].copy()

    if query_types is not None:
        out = out[out["query_type"].isin(query_types)].copy()

    out["baseline"] = pd.Categorical(
        out["baseline"],
        categories=list(BASELINE_ORDER),
        ordered=True,
    )
    out["dataset"] = pd.Categorical(
        out["dataset"],
        categories=list(DATASET_ORDER),
        ordered=True,
    )
    out["query_type"] = pd.Categorical(
        out["query_type"],
        categories=list(QUERY_TYPE_ORDER),
        ordered=True,
    )

    if out["query_type"].isna().any():
        bad_rows = out.loc[
            out["query_type"].isna(),
            ["baseline", "dataset", "query_id"],
        ]
        raise ValueError(
            "Unknown query_type values were converted to NaN. "
            f"Example rows:\n{bad_rows.head(10).to_string(index=False)}"
        )

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


def _cost_bars_with_error_labels(
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
    lower = np.minimum(stds_arr, means_arr)
    upper = stds_arr
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
    for bar, val, err in zip(bars, means, upper):
        if val <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + err + max(abs(val) * 0.02, 0.05) + label_shift,
            f"{val:.2f}",
            ha="center",
            va="bottom",
            fontsize=9.0,
            fontweight="bold",
        )


def plot_cost_across_datasets(
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
    peak = 0.0
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
        _cost_bars_with_error_labels(ax, xpos, means, stds, width, baseline, label_shift=0.02 * (i % 2))
        peak = max(peak, max((m + s for m, s in zip(means, stds)), default=0.0))

    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in x_labels])
    ax.set_xlabel("Dataset")
    ax.set_ylabel(r"Cost ($\times 10^{-5}$ USD)")
    ax.set_ylim(0, peak * 1.2 if peak > 0 else 1.0)
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


def plot_cost_across_query_types(
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
    peak = 0.0
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
        _cost_bars_with_error_labels(ax, xpos, means, stds, width, baseline, label_shift=0.02 * (i % 2))
        peak = max(peak, max((m + s for m, s in zip(means, stds)), default=0.0))

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels)
    ax.set_xlabel("Query Type")
    ax.set_ylabel(r"Cost ($\times 10^{-5}$ USD)")
    ax.set_ylim(0, peak * 1.2 if peak > 0 else 1.0)
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
        ncol=min(3, max(1, len(baselines))),
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
        "flash_fusion_cache": "FF Cache",
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

def _resolve_user_path(raw_path: str | None, repo_root: Path) -> Path | None:
    """Resolve user-entered paths with repo-relative and cwd-relative semantics.

    Paths that are clearly project-root relative (for example
    ``flashfusion/results/...`` or ``results/...``) resolve under the repo root.
    Paths containing ``..`` or ``.`` are treated as relative to the current
    working directory so commands run from the viz folder still land in the
    repository's results directory instead of escaping above it.
    """
    if raw_path is None:
        return None

    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()

    # Resolve project-root-relative paths under the repo.
    if path.parts and path.parts[0] in {"flashfusion", "results"}:
        return (repo_root / path).resolve()

    # Preserve common CLI usage from the viz directory, e.g. ../../results/...
    return (Path.cwd() / path).resolve()


def _normalize_bool(series: pd.Series) -> pd.Series:
    """Normalize bool-like CSV values to pandas booleans."""
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)

    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map(
            {
                "true": True,
                "1": True,
                "yes": True,
                "y": True,
                "false": False,
                "0": False,
                "no": False,
                "n": False,
            }
        )
        .fillna(False)
        .astype(bool)
    )

def _query_type_from_complexity(value: object) -> str:
    """Normalize benchmark complexity/query-type labels for plotting."""
    text = str(value or "").strip().lower()

    normalized = (
        text.replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
    )
    normalized = " ".join(normalized.split())

    if normalized == "direct":
        return "Direct"

    if normalized in {"intermediate", "reasoning"}:
        return "Reasoning"

    if normalized in {
        "predictive",
        "prediction",
        "forecasting",
    }:
        return "Predictive"

    if normalized in {
        "oos",
        "out of scope",
        "outofscope",
        "unsupported",
    }:
        return "Out-of-Scope"

    # Explicitly surface unexpected labels instead of silently misclassifying.
    print(
        f"[WARN] Unrecognized query-type/complexity label "
        f"{value!r}; assigning Out-of-Scope."
    )
    return "Out-of-Scope"


def _query_type_from_id(query_id: int) -> str:
    """Fallback mapping for the current 16-query benchmark suite."""
    if 1 <= query_id <= 4:
        return "Direct"
    if 5 <= query_id <= 8:
        return "Reasoning"
    if 9 <= query_id <= 12:
        return "OOS"
    if 13 <= query_id <= 16:
        return "Predictive"
    return "OOS"


def _infer_dataset_from_metrics_path(metrics_path: Path) -> str | None:
    """Infer the canonical dataset code from a metrics.csv ancestor path.

    Supports directory names used by the benchmark, such as ``wisdm``,
    ``mit_ecg``, and ``bus``, as well as common display-name variants.
    """
    aliases = {
        "wisdm": "wisdm",
        "mit_ecg": "ecg",
        "bus": "bus",
    }

    for parent in metrics_path.parents:
        name = parent.name.strip().lower()
        if name in aliases:
            return aliases[name]

    return None

def _load_baseline_root(
    baseline: str,
    root: Path,
) -> pd.DataFrame:
    """Recursively load all metrics.csv files below one baseline result root.

    Supported examples:
      <root>/<dataset>/<run_tag>/metrics.csv
      <root>/<dataset>/metrics.csv
      <root>/<baseline>/<dataset>/<run_tag>/metrics.csv

    The user supplies one root per baseline in interactive mode. The function
    therefore does not require every baseline to share a run tag.
    """
    if not root.exists():
        raise FileNotFoundError(f"Results root does not exist: {root}")

    metric_paths = sorted(root.rglob("metrics.csv"))
    if not metric_paths:
        raise FileNotFoundError(f"No metrics.csv files found below: {root}")

    frames: list[pd.DataFrame] = []

    for metrics_path in metric_paths:
        dataset = _infer_dataset_from_metrics_path(metrics_path)

        if dataset is None:
            print(
                f"[WARN] Skipping {metrics_path}: "
                "could not infer dataset from its parent directories."
            )
            continue

        try:
            metrics = pd.read_csv(metrics_path)
        except Exception as exc:
            print(f"[WARN] Could not read {metrics_path}: {exc}")
            continue

        if metrics.empty:
            print(f"[WARN] Skipping empty metrics file: {metrics_path}")
            continue

        if "query_id" not in metrics.columns:
            print(f"[WARN] Skipping {metrics_path}: missing query_id column.")
            continue

        metrics = metrics.copy()
        metrics["baseline"] = baseline
        metrics["dataset"] = dataset
        metrics["source_metrics_path"] = str(metrics_path)
        metrics["source_run_dir"] = metrics_path.parent.name

        metrics["query_id"] = pd.to_numeric(
            metrics["query_id"],
            errors="coerce",
        )
        metrics = metrics.dropna(subset=["query_id"]).copy()
        metrics["query_id"] = metrics["query_id"].astype(int)

        if "run_id" not in metrics.columns:
            metrics["run_id"] = 1

        if "gt_score" not in metrics.columns:
            print(f"[WARN] Skipping {metrics_path}: missing gt_score column.")
            continue

        metrics["gt_score"] = pd.to_numeric(
            metrics["gt_score"],
            errors="coerce",
        ).fillna(0.0)

        if "accuracy_percent" not in metrics.columns:
            metrics["accuracy_percent"] = metrics["gt_score"] * 100.0
        else:
            metrics["accuracy_percent"] = pd.to_numeric(
                metrics["accuracy_percent"],
                errors="coerce",
            ).fillna(metrics["gt_score"] * 100.0)

        if "complexity" in metrics.columns:
            metrics["query_type"] = metrics["complexity"].map(
                _query_type_from_complexity
            )
        elif "query_type" not in metrics.columns:
            metrics["query_type"] = metrics["query_id"].map(
                _query_type_from_id
            )
        else:
            metrics["query_type"] = metrics["query_id"].map(
                _query_type_from_id
            )

        for column in (
            "latency_s",
            "input_tokens",
            "output_tokens",
            "cost_usd",
        ):
            if column not in metrics.columns:
                metrics[column] = np.nan
            else:
                metrics[column] = pd.to_numeric(
                    metrics[column],
                    errors="coerce",
                )

        for column in ("executed", "rejected"):
            if column not in metrics.columns:
                metrics[column] = False
            else:
                metrics[column] = _normalize_bool(metrics[column])

        frames.append(metrics)

    if not frames:
        raise ValueError(
            f"No valid benchmark metrics could be loaded below: {root}"
        )

    return split_cache_baseline_rows(pd.concat(frames, ignore_index=True))

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate July26 primary accuracy figures.")
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactively prompt for each baseline's results root instead of using flags.",
    )
    parser.add_argument(
        "--flash-fusion-root",
        default=None,
        help="Optional override root for FLASH_FUSION baseline data.",
    )
    parser.add_argument(
        "--flash-fusion-cache-root",
        default=None,
        help="Optional override root for FLASH_FUSION_CACHE baseline data.",
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
        "--output-dir",
        default=str(script_dir.parent.parent / "results" / "primary_visualizations"),
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
        "--cost-query-type-baseline-set",
        default=",".join(COST_QUERY_TYPE_BASELINES),
        help="Comma-separated baseline codes to include in the query-type cost figure.",
    )
    parser.add_argument(
        "--query-types",
        default=",".join(QUERY_TYPE_ORDER),
        help="Comma-separated query types to include in figures.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    if args.interactive:
        roots = _prompt_for_baseline_roots(
            {
                "flash_fusion": args.flash_fusion_root,
                "flash_fusion_cache": args.flash_fusion_cache_root,
                "react": args.react_root,
                "autoiot": args.autoiot_root,
                "hargpt": args.hargpt_root,
                "llmsense": args.llmsense_root,
            }
        )
        args.flash_fusion_root = roots["flash_fusion"]
        args.flash_fusion_cache_root = roots["flash_fusion_cache"]
        args.react_root = roots["react"]
        args.autoiot_root = roots["autoiot"]
        args.hargpt_root = roots["hargpt"]
        args.llmsense_root = roots["llmsense"]

    output_dir = _resolve_user_path(args.output_dir, repo_root)
    assert output_dir is not None
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_baselines = expand_baselines([
        baseline.strip().upper()
        for baseline in (
            _parse_csv_list(args.baseline_set) or list(FULL_BASELINES)
        )
    ])
    dataset_baselines = expand_baselines([
        baseline.strip().upper()
        for baseline in (
            _parse_csv_list(args.dataset_baseline_set) or list(FULL_BASELINES)
        )
    ])
    query_type_baselines = expand_baselines([
        baseline.strip().upper()
        for baseline in (
            _parse_csv_list(args.query_type_baseline_set) or list(TOP3_BASELINES)
        )
    ])
    cost_query_type_baselines = expand_baselines([
        baseline.strip().upper()
        for baseline in (
            _parse_csv_list(args.cost_query_type_baseline_set)
            or list(COST_QUERY_TYPE_BASELINES)
        )
    ])
    required_baselines = list(dict.fromkeys([
        *selected_baselines,
        *dataset_baselines,
        *query_type_baselines,
        *cost_query_type_baselines,
    ]))
    selected_query_types = (
        _parse_csv_list(args.query_types) or list(QUERY_TYPE_ORDER)
    )

    configured_roots = {
        "FLASH_FUSION": args.flash_fusion_root,
        "FLASH_FUSION_CACHE": args.flash_fusion_cache_root,
        "REACT_ONLY": args.react_root,
        "AUTOIOT_PAPER": args.autoiot_root,
        "HARGPT_PAPER": args.hargpt_root,
        "LLMSENSE_PAPER": args.llmsense_root,
    }

    frames: list[pd.DataFrame] = []
    loaded_sources: set[str] = set()

    for baseline in selected_baselines:
        source_baseline = CACHE_BASELINE if baseline in CACHE_BASELINE_VARIANTS else baseline
        if source_baseline in loaded_sources:
            continue
        loaded_sources.add(source_baseline)
        raw_root = configured_roots.get(source_baseline)

        if raw_root is None:
            print(f"[INFO] Skipping {baseline}: no results root provided.")
            continue

        root = _resolve_user_path(raw_root, repo_root)
        assert root is not None

        try:
            baseline_df = _load_baseline_root(source_baseline, root)
        except (FileNotFoundError, ValueError) as exc:
            print(f"[WARN] Could not load {baseline} from {root}: {exc}")
            continue

        print(
            f"[INFO] Loaded {len(baseline_df)} rows for {source_baseline} "
            f"from {root}"
        )
        frames.append(baseline_df)

    if not frames:
        raise SystemExit(
            "No baseline metrics were loaded. Check the entered roots and "
            "confirm each root contains one or more metrics.csv files."
        )

    df = pd.concat(frames, ignore_index=True)

    df["baseline"] = pd.Categorical(
        df["baseline"],
        categories=list(BASELINE_ORDER),
        ordered=True,
    )
    df["dataset"] = pd.Categorical(
        df["dataset"],
        categories=list(DATASET_ORDER),
        ordered=True,
    )
    df["query_type"] = pd.Categorical(
        df["query_type"],
        categories=list(QUERY_TYPE_ORDER),
        ordered=True,
    )

    print("\n[DEBUG] Query-type values before filtering:")
    print(
        df.groupby(["baseline", "query_type"], dropna=False)
        .size()
        .rename("rows")
        .reset_index()
        .to_string(index=False)
    )

    print(f"\n[DEBUG] QUERY_TYPE_ORDER: {list(QUERY_TYPE_ORDER)}")
    print(f"[DEBUG] Selected query types: {selected_query_types}")

    df = _filter_metrics(
        df,
        required_baselines,
        selected_query_types,
    )

    if df.empty:
        raise SystemExit(
            "Metrics were loaded, but no rows remain after baseline/query-type "
            "filtering. Check BASELINE_ORDER, QUERY_TYPE_ORDER, and the "
            "--baseline-set / --query-types arguments."
        )

    by_dataset = aggregate_accuracy_by_dataset(df)
    by_query_type = aggregate_accuracy_by_query_type(df)
    cost_by_dataset = aggregate_cost_by_dataset(df)
    cost_by_query_type = aggregate_cost_by_query_type(df)

    if by_dataset.empty:
        raise SystemExit(
            "No dataset summary rows were produced. Verify dataset codes match "
            f"DATASET_ORDER: {list(DATASET_ORDER)}"
        )

    if by_query_type.empty:
        raise SystemExit(
            "No query-type summary rows were produced. Verify query types match "
            f"QUERY_TYPE_ORDER: {list(QUERY_TYPE_ORDER)}"
        )

    fig1 = output_dir / "accuracy_vs_baselines_across_datasets.png"
    fig2 = output_dir / "accuracy_vs_baselines_across_query_types.png"
    fig3 = output_dir / "cost_vs_baselines_across_datasets.png"
    fig4 = output_dir / "cost_vs_baselines_across_query_types.png"

    plot_accuracy_across_datasets(
        by_dataset,
        fig1,
        baselines=dataset_baselines,
    )
    plot_accuracy_across_query_types(
        by_query_type,
        fig2,
        baselines=query_type_baselines,
        query_types=selected_query_types,
    )
    plot_cost_across_datasets(
        cost_by_dataset,
        fig3,
        baselines=dataset_baselines,
    )
    plot_cost_across_query_types(
        cost_by_query_type,
        fig4,
        baselines=cost_query_type_baselines,
        query_types=selected_query_types,
    )

    by_dataset.to_csv(
        output_dir / "accuracy_vs_baselines_across_datasets_summary.csv",
        index=False,
    )
    by_query_type.to_csv(
        output_dir / "accuracy_vs_baselines_across_query_types_summary.csv",
        index=False,
    )
    cost_by_dataset.to_csv(
        output_dir / "cost_vs_baselines_across_datasets_summary.csv",
        index=False,
    )
    cost_by_query_type.to_csv(
        output_dir / "cost_vs_baselines_across_query_types_summary.csv",
        index=False,
    )

    print(f"Wrote {fig1}")
    print(f"Wrote {fig2}")
    print(f"Wrote {fig3}")
    print(f"Wrote {fig4}")


if __name__ == "__main__":
    main()