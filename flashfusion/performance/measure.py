#!/usr/bin/env python3
"""Generate benchmark visualizations/tables from dual-run Flash-Fusion data.

This script reads:
- run_all_remaining_20260528_215519 for bus, wisdm, and non-Flash ECG baselines
- run_ecg_ff_20260529_115359 for ECG Flash-Fusion

Outputs are organized under one root directory:
- bus/
- wisdm/
- ecg/
- all_datasets/

Per dataset (bus/wisdm/ecg):
- grouped bar charts with std-dev error bars
- CSV + Markdown tables for each metric

All datasets:
- balanced cross-dataset CSV + Markdown tables only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict


if sys.version_info >= (3, 14):
    raise SystemExit(
        "This script is not compatible with Python 3.14 in this repository (pandas import stalls). "
        "Run with system Python 3.11 (e.g., `python3 performance/measure.py ...`) or recreate .venv with Python 3.11/3.12."
    )

import pandas as pd


QUERY_TYPE_BY_ID = {
    1: "Direct",
    2: "Direct",
    3: "Direct",
    4: "Direct",
    5: "Reasoning",
    6: "Reasoning",
    7: "Reasoning",
    8: "Reasoning",
    9: "Out-of-Scope",
    10: "Out-of-Scope",
    11: "Out-of-Scope",
    12: "Out-of-Scope",
}

QUERY_TYPES = ["Direct", "Reasoning", "Out-of-Scope", "All"]
BASELINE_ORDER = ["FLASH_FUSION", "AUTOIOT_PAPER", "HARGPT_PAPER", "LLMSENSE_PAPER"]
BASELINE_LABELS = {
    "FLASH_FUSION": "Flash-Fusion",
    "AUTOIOT_PAPER": "AutoIOT",
    "AUTOIOT_ONLY": "ReAct",
    "HARGPT_PAPER": "HARGPT",
    "LLMSENSE_PAPER": "LLMSense",
}

BASELINE_COLORS = {
    "FLASH_FUSION": "#2f8f57",
    "AUTOIOT_PAPER": "#4c78a8",
    "AUTOIOT_ONLY": "#7fba00",
    "HARGPT_PAPER": "#f58518",
    "LLMSENSE_PAPER": "#b279a2",
}
BASELINE_HATCHES = {
    "AUTOIOT_ONLY": "//",
}
DATASET_LABELS = {
    "bus": "Bus",
    "wisdm": "WISDM",
    "ecg": "ECG",
    "all_datasets": "All Datasets",
}
DATASET_ORDER = ["bus", "wisdm", "ecg"]
COST_PLOT_SCALE = 1e5
COST_PLOT_YLABEL = r"Cost ($\times 10^{-5}$ USD)"


# ── Plot styling (borrowed from performance/llamas.py) ───────────────────────

RC = {
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.labelsize": 11,
    "axes.labelweight": "bold",
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "legend.title_fontsize": 10,
}


def _apply_llamas_rc(plt) -> None:
    plt.rcParams.update(RC)


def _clean_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _plot_value(metric_key: str, value: float) -> float:
    if pd.isna(value):
        return float("nan")
    if metric_key == "cost_usd":
        return value * COST_PLOT_SCALE
    return value


def _plot_label(metric_key: str, value: float, fmt: str) -> str:
    if metric_key == "cost_usd":
        return f"{_plot_value(metric_key, value):.2f}"
    return _format_value(value, fmt)


def _plot_ylabel(metric_key: str, default_ylabel: str) -> str:
    if metric_key == "cost_usd":
        return COST_PLOT_YLABEL
    return default_ylabel


@dataclass(frozen=True)
class MetricSpec:
    key: str
    ylabel: str
    title: str
    slug: str
    fmt: str


METRICS = [
    MetricSpec(
        key="accuracy_percent",
        ylabel="LLM Verdict Accuracy (%)",
        title="LLM Verdict Accuracy by Query Type",
        slug="accuracy_by_query_type",
        fmt="percent",
    ),
    MetricSpec(
        key="input_tokens",
        ylabel="Input Tokens",
        title="Input Tokens by Query Type",
        slug="input_tokens_by_query_type",
        fmt="int",
    ),
    MetricSpec(
        key="output_tokens",
        ylabel="Output Tokens",
        title="Output Tokens by Query Type",
        slug="output_tokens_by_query_type",
        fmt="int",
    ),
    MetricSpec(
        key="cost_usd",
        ylabel="Cost (USD)",
        title="Cost by Query Type",
        slug="cost_by_query_type",
        fmt="usd",
    ),
    MetricSpec(
        key="latency_s",
        ylabel="Latency (s)",
        title="Latency by Query Type",
        slug="latency_by_query_type",
        fmt="float2",
    ),
]


def _import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "matplotlib is required. Install with: pip install matplotlib"
        ) from exc
    return plt


def _normalize_baseline(value: object) -> str:
    return str(value).strip().upper()


def _display_baseline(code: str) -> str:
    return BASELINE_LABELS.get(code, code)


def _ordered_present_baselines(values: pd.Series) -> list[str]:
    present = {str(v) for v in values.dropna().astype(str).tolist()}
    ordered = [b for b in BASELINE_ORDER if b in present]
    extras = sorted([b for b in present if b not in BASELINE_ORDER])
    return ordered + extras


def _format_value(value: float, fmt: str) -> str:
    if pd.isna(value):
        return "N/A"
    if fmt == "percent":
        return f"{value:.1f}%"
    if fmt == "int":
        return f"{int(round(value))}"
    if fmt == "usd":
        return f"${value:.6f}"
    if fmt == "float2":
        return f"{value:.2f}"
    return f"{value:.4f}"


def _load_metrics_csv(path: Path, dataset_name: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing metrics file: {path}")

    df = pd.read_csv(path)
    required_cols = {
        "run_id",
        "baseline",
        "query_id",
        "gt_score",
        "latency_s",
        "cost_usd",
        "input_tokens",
        "output_tokens",
    }
    missing = sorted(required_cols - set(df.columns))
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")

    out = df[list(required_cols)].copy()
    out["baseline"] = out["baseline"].map(_normalize_baseline)
    out["query_id"] = pd.to_numeric(out["query_id"], errors="coerce").astype("Int64")
    if out["query_id"].isna().any():
        raise ValueError(f"{path} has non-numeric query_id values")

    unknown_query_ids = sorted(
        [int(q) for q in out[~out["query_id"].isin(QUERY_TYPE_BY_ID.keys())]["query_id"].unique()]
    )
    if unknown_query_ids:
        raise ValueError(
            f"{path} contains query_id values not in mapping: {unknown_query_ids}"
        )

    out["query_type"] = out["query_id"].map(QUERY_TYPE_BY_ID)
    out["accuracy_percent"] = pd.to_numeric(out["gt_score"], errors="coerce") * 100.0
    out["latency_s"] = pd.to_numeric(out["latency_s"], errors="coerce")
    out["cost_usd"] = pd.to_numeric(out["cost_usd"], errors="coerce")
    out["input_tokens"] = pd.to_numeric(out["input_tokens"], errors="coerce")
    out["output_tokens"] = pd.to_numeric(out["output_tokens"], errors="coerce")
    out["run_id"] = pd.to_numeric(out["run_id"], errors="coerce").astype("Int64")
    if out[["run_id", "accuracy_percent", "latency_s", "cost_usd", "input_tokens", "output_tokens"]].isna().any().any():
        raise ValueError(f"{path} contains invalid numeric values in required columns")

    out["dataset"] = dataset_name
    return out[
        [
            "dataset",
            "run_id",
            "baseline",
            "query_type",
            "accuracy_percent",
            "latency_s",
            "cost_usd",
            "input_tokens",
            "output_tokens",
        ]
    ]


def _aggregate_dataset(df: pd.DataFrame) -> pd.DataFrame:
    metric_keys = [m.key for m in METRICS]

    run_by_type = (
        df.groupby(["baseline", "run_id", "query_type"], as_index=False)[metric_keys]
        .mean(numeric_only=True)
        .copy()
    )
    run_all = (
        df.groupby(["baseline", "run_id"], as_index=False)[metric_keys]
        .mean(numeric_only=True)
        .copy()
    )
    run_all["query_type"] = "All"
    run_combined = pd.concat([run_by_type, run_all], ignore_index=True)

    long_df = run_combined.melt(
        id_vars=["baseline", "run_id", "query_type"],
        value_vars=metric_keys,
        var_name="metric",
        value_name="run_value",
    )

    summary = (
        long_df.groupby(["baseline", "query_type", "metric"], as_index=False)
        .agg(mean=("run_value", "mean"), std=("run_value", "std"), runs=("run_value", "count"))
        .copy()
    )
    summary["std"] = summary["std"].fillna(0.0)
    return summary


def _summary_to_wide(metric_summary: pd.DataFrame) -> pd.DataFrame:
    baselines = _ordered_present_baselines(metric_summary["baseline"])
    rows = []
    for baseline in baselines:
        row: Dict[str, object] = {"Baseline": _display_baseline(baseline)}
        bdf = metric_summary[metric_summary["baseline"] == baseline]
        for qt in QUERY_TYPES:
            qdf = bdf[bdf["query_type"] == qt]
            if qdf.empty:
                row[f"{qt}_mean"] = float("nan")
                row[f"{qt}_std"] = float("nan")
            else:
                row[f"{qt}_mean"] = float(qdf["mean"].iloc[0])
                row[f"{qt}_std"] = float(qdf["std"].iloc[0])
        rows.append(row)

    cols = ["Baseline"]
    for qt in QUERY_TYPES:
        cols.extend([f"{qt}_mean", f"{qt}_std"])
    return pd.DataFrame(rows, columns=cols)


def _write_markdown_table(df_wide: pd.DataFrame, out_md: Path, fmt: str, overwrite: bool) -> bool:
    if out_md.exists() and not overwrite:
        print(f"  Skip existing: {out_md}")
        return False

    headers = ["Baseline"] + QUERY_TYPES
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for _, row in df_wide.iterrows():
        vals = [str(row["Baseline"])]
        for qt in QUERY_TYPES:
            mean = row[f"{qt}_mean"]
            std = row[f"{qt}_std"]
            vals.append(f"{_format_value(mean, fmt)} +/- {_format_value(std, fmt)}")
        lines.append("| " + " | ".join(vals) + " |")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote {out_md}")
    return True


def _write_csv(df_wide: pd.DataFrame, out_csv: Path, overwrite: bool) -> bool:
    if out_csv.exists() and not overwrite:
        print(f"  Skip existing: {out_csv}")
        return False
    df_wide.to_csv(out_csv, index=False)
    print(f"  Wrote {out_csv}")
    return True


def _write_markdown_dataframe(
    df: pd.DataFrame,
    out_md: Path,
    overwrite: bool,
    column_formats: Dict[str, str] | None = None,
) -> bool:
    if out_md.exists() and not overwrite:
        print(f"  Skip existing: {out_md}")
        return False

    column_formats = column_formats or {}
    headers = [str(c) for c in df.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]

    for _, row in df.iterrows():
        vals = []
        for col in headers:
            value = row[col]
            if pd.isna(value):
                vals.append("N/A")
                continue
            fmt = column_formats.get(col)
            if fmt is not None:
                vals.append(_format_value(float(value), fmt))
            else:
                vals.append(str(value))
        lines.append("| " + " | ".join(vals) + " |")

    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote {out_md}")
    return True


def _plot_metric(metric_summary: pd.DataFrame, spec: MetricSpec, out_png: Path, title_prefix: str, overwrite: bool) -> bool:
    if out_png.exists() and not overwrite:
        print(f"  Skip existing: {out_png}")
        return False

    plt = _import_matplotlib()
    _apply_llamas_rc(plt)

    baselines = _ordered_present_baselines(metric_summary["baseline"])
    qtypes = QUERY_TYPES
    x = list(range(len(qtypes)))
    width = 0.8 / max(len(baselines), 1)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    for i, baseline in enumerate(baselines):
        bdf = metric_summary[metric_summary["baseline"] == baseline]
        means = []
        stds = []
        for qt in qtypes:
            qdf = bdf[bdf["query_type"] == qt]
            mean_val = float(qdf["mean"].iloc[0]) if not qdf.empty else 0.0
            std_val = float(qdf["std"].iloc[0]) if not qdf.empty else 0.0
            means.append(_plot_value(spec.key, mean_val))
            stds.append(_plot_value(spec.key, std_val))

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        color = BASELINE_COLORS.get(baseline, "#999999")
        bars = ax.bar(
            xpos,
            means,
            width,
            label=_display_baseline(baseline),
            color=color,
            edgecolor="#333333",
            linewidth=0.6,
            yerr=stds,
            error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#222222"},
            hatch=BASELINE_HATCHES.get(baseline),
        )

        for bar, val in zip(bars, means):
            raw_val = val / COST_PLOT_SCALE if spec.key == "cost_usd" else val
            if spec.fmt == "usd" and raw_val < 1e-4:
                continue
            if spec.fmt == "int" and val < 1:
                continue
            label = _plot_label(spec.key, raw_val, spec.fmt)
            y_offset = max(abs(val) * 0.03, 0.02 if spec.fmt == "usd" else 0.4)
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + y_offset,
                label,
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(qtypes)
    ax.set_xlabel("Query Type")
    ax.set_ylabel(_plot_ylabel(spec.key, spec.ylabel))
    ax.yaxis.grid(linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    _clean_axes(ax)
    if spec.key in {"cost_usd", "latency_s"}:
        ax.set_yscale("log")
    else:
        ax.set_ylim(bottom=0)

    if spec.key == "accuracy_percent":
        ax.set_ylim(0, 115)

    ncol = 3 if len(baselines) >= 3 else max(len(baselines), 1)
    ax.legend(
        ncol=ncol,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_png}")
    return True


def _aggregate_all_datasets(dataset_summaries: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for dataset_name, df in dataset_summaries.items():
        part = df.copy()
        part["dataset"] = dataset_name
        rows.append(part)

    combined = pd.concat(rows, ignore_index=True)
    agg = (
        combined.groupby(["baseline", "query_type", "metric"], as_index=False)
        .agg(
            mean=("mean", "mean"),
            std=("mean", "std"),
            dataset_count=("dataset", "nunique"),
        )
        .copy()
    )
    agg["std"] = agg["std"].fillna(0.0)
    return agg


def _metric_mean_std(
    summary: pd.DataFrame,
    baseline: str,
    metric: str,
    query_type: str = "All",
) -> tuple[float, float]:
    match = summary[
        (summary["baseline"] == baseline)
        & (summary["metric"] == metric)
        & (summary["query_type"] == query_type)
    ]
    if match.empty:
        return float("nan"), float("nan")
    return float(match["mean"].iloc[0]), float(match["std"].iloc[0])


def _build_cross_dataset_accuracy_frame(
    dataset_summaries: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for dataset_name in DATASET_ORDER:
        summary = dataset_summaries[dataset_name]
        for baseline in BASELINE_ORDER:
            mean, std = _metric_mean_std(summary, baseline, "accuracy_percent", "All")
            rows.append(
                {
                    "dataset": dataset_name,
                    "baseline": baseline,
                    "mean": mean,
                    "std": std,
                }
            )
    return pd.DataFrame(rows)


def _plot_accuracy_by_dataset(
    accuracy_df: pd.DataFrame,
    out_png: Path,
    overwrite: bool,
) -> bool:
    if out_png.exists() and not overwrite:
        print(f"  Skip existing: {out_png}")
        return False

    plt = _import_matplotlib()
    _apply_llamas_rc(plt)

    datasets = DATASET_ORDER
    baselines = BASELINE_ORDER
    x = list(range(len(datasets)))
    width = 0.8 / max(len(baselines), 1)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    for i, baseline in enumerate(baselines):
        bdf = accuracy_df[accuracy_df["baseline"] == baseline]
        means = []
        stds = []
        for dataset_name in datasets:
            ddf = bdf[bdf["dataset"] == dataset_name]
            if ddf.empty:
                means.append(0.0)
                stds.append(0.0)
            else:
                mean_val = ddf["mean"].iloc[0]
                std_val = ddf["std"].iloc[0]
                means.append(0.0 if pd.isna(mean_val) else float(mean_val))
                stds.append(0.0 if pd.isna(std_val) else float(std_val))

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        bars = ax.bar(
            xpos,
            means,
            width,
            label=_display_baseline(baseline),
            color=BASELINE_COLORS.get(baseline, "#999999"),
            edgecolor="#333333",
            linewidth=0.6,
            yerr=stds,
            error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#222222"},
            hatch=BASELINE_HATCHES.get(baseline),
        )
        for bar, val in zip(bars, means):
            if val <= 0:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + max(abs(val) * 0.03, 0.4),
                f"{val:.1f}%",
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in datasets])
    ax.set_xlabel("Dataset")
    ax.set_ylabel("LLM Verdict Accuracy (%)")
    ax.set_ylim(0, 115)
    ax.yaxis.grid(linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ncol = 3 if len(baselines) >= 3 else max(len(baselines), 1)
    ax.legend(
        ncol=ncol,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_png}")
    return True


def _safe_reduction_percent(reference: float, new_value: float) -> float:
    if pd.isna(reference) or pd.isna(new_value) or abs(reference) < 1e-12:
        return float("nan")
    return ((reference - new_value) / reference) * 100.0


def _build_dataset_baseline_metric_table(
    dataset_summaries: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for dataset_name in DATASET_ORDER:
        summary = dataset_summaries[dataset_name]
        row: Dict[str, object] = {"Dataset": DATASET_LABELS[dataset_name]}

        flash_tokens, _ = _metric_mean_std(summary, "FLASH_FUSION", "input_tokens", "All")
        flash_cost, _ = _metric_mean_std(summary, "FLASH_FUSION", "cost_usd", "All")

        for baseline in BASELINE_ORDER:
            label = _display_baseline(baseline)
            tokens, _ = _metric_mean_std(summary, baseline, "input_tokens", "All")
            cost, _ = _metric_mean_std(summary, baseline, "cost_usd", "All")
            row[f"{label}_tokens"] = tokens
            row[f"{label}_cost_usd"] = cost

            if baseline == "FLASH_FUSION":
                row[f"{label}_token_reduction_vs_baseline_pct"] = 0.0
                row[f"{label}_cost_reduction_vs_baseline_pct"] = 0.0
            else:
                row[f"{label}_token_reduction_vs_baseline_pct"] = _safe_reduction_percent(tokens, flash_tokens)
                row[f"{label}_cost_reduction_vs_baseline_pct"] = _safe_reduction_percent(cost, flash_cost)

        rows.append(row)

    return pd.DataFrame(rows)


def _build_react_dataset_frames(run_react_root: Path) -> Dict[str, pd.DataFrame]:
    bus = _load_metrics_csv(run_react_root / "bus" / "benchmark" / "metrics.csv", "bus")
    wisdm = _load_metrics_csv(run_react_root / "wisdm" / "benchmark" / "metrics.csv", "wisdm")
    ecg = _load_metrics_csv(run_react_root / "mit_ecg" / "benchmark" / "metrics.csv", "ecg")
    return {"bus": bus, "wisdm": wisdm, "ecg": ecg}


def _build_react_vs_llmsense_table(
    react_summaries: Dict[str, pd.DataFrame],
    paper_summaries: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for dataset_name in DATASET_ORDER:
        react_summary = react_summaries[dataset_name]
        paper_summary = paper_summaries[dataset_name]

        react_tokens, _ = _metric_mean_std(react_summary, "AUTOIOT_ONLY", "input_tokens", "All")
        react_cost, _ = _metric_mean_std(react_summary, "AUTOIOT_ONLY", "cost_usd", "All")
        llmsense_tokens, _ = _metric_mean_std(paper_summary, "LLMSENSE_PAPER", "input_tokens", "All")
        llmsense_cost, _ = _metric_mean_std(paper_summary, "LLMSENSE_PAPER", "cost_usd", "All")

        rows.append(
            {
                "Dataset": DATASET_LABELS[dataset_name],
                "ReAct_tokens": react_tokens,
                "LLMSense_tokens": llmsense_tokens,
                "Token_reduction_pct_vs_LLMSense": _safe_reduction_percent(llmsense_tokens, react_tokens),
                "ReAct_cost_usd": react_cost,
                "LLMSense_cost_usd": llmsense_cost,
                "Cost_reduction_pct_vs_LLMSense": _safe_reduction_percent(llmsense_cost, react_cost),
            }
        )

    return pd.DataFrame(rows)


def _build_flash_fusion_vs_react_accuracy_summary(
    dataset_summaries: Dict[str, pd.DataFrame],
    react_summaries: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows = []
    for dataset_name in DATASET_ORDER:
        for baseline, summary in (
            ("FLASH_FUSION", dataset_summaries[dataset_name]),
            ("AUTOIOT_ONLY", react_summaries[dataset_name]),
        ):
            for query_type in QUERY_TYPES:
                mean, _ = _metric_mean_std(
                    summary,
                    baseline,
                    "accuracy_percent",
                    query_type,
                )
                rows.append(
                    {
                        "dataset": dataset_name,
                        "baseline": baseline,
                        "query_type": query_type,
                        "mean": mean,
                    }
                )

    combined = pd.DataFrame(rows)
    agg = (
        combined.groupby(["baseline", "query_type"], as_index=False)
        .agg(
            mean=("mean", "mean"),
            std=("mean", "std"),
            dataset_count=("dataset", "nunique"),
        )
        .copy()
    )
    agg["std"] = agg["std"].fillna(0.0)
    return agg


def _plot_all_datasets_accuracy(
    metric_summary: pd.DataFrame,
    out_png: Path,
    overwrite: bool,
    title: str = "Accuracy by Query Type - All Datasets",
) -> bool:
    if out_png.exists() and not overwrite:
        print(f"  Skip existing: {out_png}")
        return False

    plt = _import_matplotlib()
    _apply_llamas_rc(plt)

    baselines = _ordered_present_baselines(metric_summary["baseline"])
    qtypes = QUERY_TYPES
    x = list(range(len(qtypes)))
    width = 0.8 / max(len(baselines), 1)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))
    for i, baseline in enumerate(baselines):
        bdf = metric_summary[metric_summary["baseline"] == baseline]
        means = []
        stds = []
        for qt in qtypes:
            qdf = bdf[bdf["query_type"] == qt]
            means.append(float(qdf["mean"].iloc[0]) if not qdf.empty else 0.0)
            stds.append(float(qdf["std"].iloc[0]) if not qdf.empty else 0.0)

        xpos = [p - 0.4 + (i + 0.5) * width for p in x]
        bars = ax.bar(
            xpos,
            means,
            width,
            label=_display_baseline(baseline),
            color=BASELINE_COLORS.get(baseline, "#999999"),
            edgecolor="#333333",
            linewidth=0.6,
            yerr=stds,
            error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#222222"},
            hatch=BASELINE_HATCHES.get(baseline),
        )
        for bar, val in zip(bars, means):
            label = f"{val:.1f}%"
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + max(abs(val) * 0.03, 0.4),
                label,
                ha="center",
                va="bottom",
                fontsize=7.5,
                fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(qtypes)
    ax.set_xlabel("Query Type")
    ax.set_ylabel("LLM Verdict Accuracy (%)")
    ax.set_ylim(0, 115)
    ax.yaxis.grid(linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ncol = 3 if len(baselines) >= 3 else max(len(baselines), 1)
    ax.legend(
        ncol=ncol,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.25),
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_png}")
    return True


def _plot_all_datasets_avg_metrics(
    metric_summary: pd.DataFrame,
    out_png: Path,
    overwrite: bool,
) -> bool:
    if out_png.exists() and not overwrite:
        print(f"  Skip existing: {out_png}")
        return False

    plt = _import_matplotlib()
    _apply_llamas_rc(plt)

    metrics = [
        ("latency_s", "Latency (s)"),
        ("input_tokens", "Input Tokens"),
        ("cost_usd", "Cost (USD)"),
    ]
    baselines = _ordered_present_baselines(metric_summary["baseline"])

    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.8))
    if len(metrics) == 1:
        axes = [axes]

    for ax, (metric_key, ylabel) in zip(axes, metrics):
        mdf = metric_summary[
            (metric_summary["metric"] == metric_key)
            & (metric_summary["query_type"] == "All")
        ]
        means = []
        for baseline in baselines:
            bdf = mdf[mdf["baseline"] == baseline]
            mean_val = float(bdf["mean"].iloc[0]) if not bdf.empty else 0.0
            means.append(_plot_value(metric_key, mean_val))

        x = list(range(len(baselines)))
        colors = [BASELINE_COLORS.get(b, "#999999") for b in baselines]
        hatches = [BASELINE_HATCHES.get(b) for b in baselines]
        bars = ax.bar(
            x,
            means,
            color=colors,
            edgecolor="#333333",
            linewidth=0.8,
        )
        for bar, hatch in zip(bars, hatches):
            if hatch:
                bar.set_hatch(hatch)

        ax.set_xticks(x)
        ax.set_xticklabels([_display_baseline(b) for b in baselines], rotation=20)
        ax.set_ylabel(_plot_ylabel(metric_key, ylabel))
        ax.set_yscale("log")
        ax.yaxis.grid(linestyle="--", alpha=0.35)
        ax.set_axisbelow(True)
        _clean_axes(ax)

    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_png}")
    return True


def _plot_dataset_avg_metrics(
    metric_summary: pd.DataFrame,
    out_png: Path,
    overwrite: bool,
    title: str,
) -> bool:
    if out_png.exists() and not overwrite:
        print(f"  Skip existing: {out_png}")
        return False

    plt = _import_matplotlib()
    _apply_llamas_rc(plt)

    metrics = [
        ("latency_s", "Latency (s)"),
        ("input_tokens", "Input Tokens"),
        ("cost_usd", "Cost (USD)"),
    ]
    baselines = _ordered_present_baselines(metric_summary["baseline"])

    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.8))
    if len(metrics) == 1:
        axes = [axes]

    for ax, (metric_key, ylabel) in zip(axes, metrics):
        mdf = metric_summary[
            (metric_summary["metric"] == metric_key)
            & (metric_summary["query_type"] == "All")
        ]
        means = []
        for baseline in baselines:
            bdf = mdf[mdf["baseline"] == baseline]
            mean_val = float(bdf["mean"].iloc[0]) if not bdf.empty else 0.0
            means.append(_plot_value(metric_key, mean_val))

        x = list(range(len(baselines)))
        colors = [BASELINE_COLORS.get(b, "#999999") for b in baselines]
        hatches = [BASELINE_HATCHES.get(b) for b in baselines]
        bars = ax.bar(
            x,
            means,
            color=colors,
            edgecolor="#333333",
            linewidth=0.8,
        )
        for bar, hatch in zip(bars, hatches):
            if hatch:
                bar.set_hatch(hatch)

        ax.set_xticks(x)
        ax.set_xticklabels([_display_baseline(b) for b in baselines], rotation=20)
        ax.set_ylabel(_plot_ylabel(metric_key, ylabel))
        ax.set_yscale("log")
        ax.yaxis.grid(linestyle="--", alpha=0.35)
        ax.set_axisbelow(True)
        _clean_axes(ax)

    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_png}")
    return True


def _replace_baseline_rows(
    base_df: pd.DataFrame,
    replacement_df: pd.DataFrame,
    baseline_code: str,
    dataset_name: str,
) -> pd.DataFrame:
    replacement = replacement_df[replacement_df["baseline"] == baseline_code].copy()
    if replacement.empty:
        raise ValueError(
            f"Missing {baseline_code} rows for {dataset_name} in replacement run"
        )
    base = base_df[base_df["baseline"] != baseline_code].copy()
    return pd.concat([base, replacement], ignore_index=True)


def _build_dataset_frames(
    run_all_root: Path,
    run_ecg_root: Path,
    run_hargpt_root: Path,
) -> Dict[str, pd.DataFrame]:
    bus = _load_metrics_csv(run_all_root / "bus" / "benchmark" / "metrics.csv", "bus")
    wisdm = _load_metrics_csv(run_all_root / "wisdm" / "benchmark" / "metrics.csv", "wisdm")
    bus_hargpt = _load_metrics_csv(
        run_hargpt_root / "bus" / "benchmark" / "metrics.csv",
        "bus",
    )
    wisdm_hargpt = _load_metrics_csv(
        run_hargpt_root / "wisdm" / "benchmark" / "metrics.csv",
        "wisdm",
    )
    bus = _replace_baseline_rows(bus, bus_hargpt, "HARGPT_PAPER", "bus")
    wisdm = _replace_baseline_rows(wisdm, wisdm_hargpt, "HARGPT_PAPER", "wisdm")

    ecg_all = _load_metrics_csv(run_all_root / "mit_ecg" / "benchmark" / "metrics.csv", "ecg")
    ecg_flash = _load_metrics_csv(run_ecg_root / "benchmark" / "metrics.csv", "ecg")
    ecg_hargpt = _load_metrics_csv(
        run_hargpt_root / "mit_ecg" / "benchmark" / "metrics.csv",
        "ecg",
    )

    ecg_non_flash = ecg_all[ecg_all["baseline"] != "FLASH_FUSION"].copy()
    ecg_flash = ecg_flash[ecg_flash["baseline"] == "FLASH_FUSION"].copy()
    if ecg_flash.empty:
        raise ValueError(
            "ECG merge failed: run_ecg source does not contain FLASH_FUSION rows"
        )

    ecg_non_flash = _replace_baseline_rows(
        ecg_non_flash,
        ecg_hargpt,
        "HARGPT_PAPER",
        "ecg",
    )
    ecg = pd.concat([ecg_non_flash, ecg_flash], ignore_index=True)
    return {"bus": bus, "wisdm": wisdm, "ecg": ecg}


def _validate_dataset_coverage(df: pd.DataFrame, dataset_name: str) -> None:
    expected = set(QUERY_TYPES[:-1])
    grouped = df.groupby("baseline")["query_type"].agg(lambda s: set(s.tolist())).to_dict()
    for baseline, seen in grouped.items():
        missing = sorted(expected - seen)
        if missing:
            print(
                f"  Warning: {dataset_name}/{baseline} missing query types: {missing}"
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate benchmark plots/tables for bus, wisdm, ecg, and all-datasets summary tables."
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--run-all-root",
        default=str(script_dir / "data" / "run_all_remaining_20260528_215519"),
        help="Path to run_all_remaining_20260528_215519 root.",
    )
    parser.add_argument(
        "--run-ecg-root",
        default=str(script_dir / "data" / "run_ecg_ff_20260529_115359"),
        help="Path to run_ecg_ff_20260529_115359 root.",
    )
    parser.add_argument(
        "--run-react-root",
        default=str(script_dir / "data" / "run_all_react_20260526_102138"),
        help="Path to run_all_react_20260526_102138 root.",
    )
    parser.add_argument(
        "--run-hargpt-root",
        default=str(script_dir / "data" / "run_hargpt_20260603_111000"),
        help="Path to run_hargpt_20260603_111000 root.",
    )
    parser.add_argument(
        "--output-root",
        default=str(script_dir / "results"),
        help="Root output directory containing bus/wisdm/ecg/all_datasets folders.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without writing files.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    run_all_root = Path(args.run_all_root).resolve()
    run_ecg_root = Path(args.run_ecg_root).resolve()
    run_react_root = Path(args.run_react_root).resolve()
    run_hargpt_root = Path(args.run_hargpt_root).resolve()
    output_root = Path(args.output_root).resolve()

    print("Preparing dataset frames...")
    dataset_frames = _build_dataset_frames(
        run_all_root,
        run_ecg_root,
        run_hargpt_root,
    )

    dataset_summaries: Dict[str, pd.DataFrame] = {}
    write_count = 0

    for dataset_name in ["bus", "wisdm", "ecg"]:
        df = dataset_frames[dataset_name]
        _validate_dataset_coverage(df, dataset_name)
        summary = _aggregate_dataset(df)
        dataset_summaries[dataset_name] = summary

        dataset_dir = output_root / dataset_name
        if not args.dry_run:
            dataset_dir.mkdir(parents=True, exist_ok=True)

        print(f"Generating artifacts for {dataset_name}...")
        for spec in METRICS:
            metric_summary = summary[summary["metric"] == spec.key].copy()
            metric_summary["query_type"] = pd.Categorical(
                metric_summary["query_type"],
                categories=QUERY_TYPES,
                ordered=True,
            )
            metric_summary = metric_summary.sort_values(["baseline", "query_type"])

            out_png = dataset_dir / f"{spec.slug}.png"
            out_csv = dataset_dir / f"{spec.slug}.csv"
            out_md = dataset_dir / f"{spec.slug}.md"

            wide = _summary_to_wide(metric_summary)

            if args.dry_run:
                print(f"  Would write {out_png}")
                print(f"  Would write {out_csv}")
                print(f"  Would write {out_md}")
            else:
                if _plot_metric(
                    metric_summary,
                    spec,
                    out_png,
                    DATASET_LABELS[dataset_name],
                    args.overwrite,
                ):
                    write_count += 1
                if _write_csv(wide, out_csv, args.overwrite):
                    write_count += 1
                if _write_markdown_table(wide, out_md, spec.fmt, args.overwrite):
                    write_count += 1

        avg_png = dataset_dir / "avg_latency_tokens_cost.png"
        title = f"{DATASET_LABELS[dataset_name]} Dataset - Average Latency, Tokens, and Cost"
        if args.dry_run:
            print(f"  Would write {avg_png}")
        else:
            if _plot_dataset_avg_metrics(summary, avg_png, args.overwrite, title):
                write_count += 1

    print("Generating all-datasets tables...")
    all_summary = _aggregate_all_datasets(dataset_summaries)
    all_dir = output_root / "all_datasets"
    if not args.dry_run:
        all_dir.mkdir(parents=True, exist_ok=True)

    for spec in METRICS:
        metric_summary = all_summary[all_summary["metric"] == spec.key].copy()
        metric_summary["query_type"] = pd.Categorical(
            metric_summary["query_type"],
            categories=QUERY_TYPES,
            ordered=True,
        )
        metric_summary = metric_summary.sort_values(["baseline", "query_type"])
        wide = _summary_to_wide(metric_summary)

        if spec.key == "accuracy_percent":
            out_png = all_dir / "cross_accuracy_by_query_type.png"
            if args.dry_run:
                print(f"  Would write {out_png}")
            else:
                if _plot_all_datasets_accuracy(metric_summary, out_png, args.overwrite):
                    write_count += 1

        out_csv = all_dir / f"cross_{spec.slug}.csv"
        out_md = all_dir / f"cross_{spec.slug}.md"

        if args.dry_run:
            print(f"  Would write {out_csv}")
            print(f"  Would write {out_md}")
        else:
            if _write_csv(wide, out_csv, args.overwrite):
                write_count += 1
            if _write_markdown_table(wide, out_md, spec.fmt, args.overwrite):
                write_count += 1

    print("Generating all-datasets average metric plot...")
    avg_png = all_dir / "cross_avg_latency_tokens_cost.png"
    if args.dry_run:
        print(f"  Would write {avg_png}")
    else:
        if _plot_all_datasets_avg_metrics(all_summary, avg_png, args.overwrite):
            write_count += 1

    print("Generating all-datasets accuracy-by-dataset plot...")
    accuracy_by_dataset = _build_cross_dataset_accuracy_frame(dataset_summaries)
    out_png = all_dir / "cross_accuracy_by_dataset.png"
    if args.dry_run:
        print(f"  Would write {out_png}")
    else:
        if _plot_accuracy_by_dataset(accuracy_by_dataset, out_png, args.overwrite):
            write_count += 1

    print("Generating cross-dataset baseline metrics table...")
    baseline_table = _build_dataset_baseline_metric_table(dataset_summaries)
    baseline_csv = all_dir / "cross_tokens_cost_reduction_by_dataset.csv"
    baseline_md = all_dir / "cross_tokens_cost_reduction_by_dataset.md"
    baseline_formats = {
        col: "int"
        for col in baseline_table.columns
        if col.endswith("_tokens")
    }
    baseline_formats.update(
        {
            col: "usd"
            for col in baseline_table.columns
            if col.endswith("_cost_usd")
        }
    )
    baseline_formats.update(
        {
            col: "percent"
            for col in baseline_table.columns
            if col.endswith("_pct")
        }
    )
    if args.dry_run:
        print(f"  Would write {baseline_csv}")
        print(f"  Would write {baseline_md}")
    else:
        if _write_csv(baseline_table, baseline_csv, args.overwrite):
            write_count += 1
        if _write_markdown_dataframe(
            baseline_table,
            baseline_md,
            args.overwrite,
            column_formats=baseline_formats,
        ):
            write_count += 1

    print("Generating ReAct vs LLMSense table...")
    react_frames = _build_react_dataset_frames(run_react_root)
    react_summaries = {
        dataset_name: _aggregate_dataset(df)
        for dataset_name, df in react_frames.items()
    }
    react_table = _build_react_vs_llmsense_table(react_summaries, dataset_summaries)
    react_csv = all_dir / "react_vs_llmsense_tokens_cost_reduction.csv"
    react_md = all_dir / "react_vs_llmsense_tokens_cost_reduction.md"
    react_formats = {
        "ReAct_tokens": "int",
        "LLMSense_tokens": "int",
        "Token_reduction_pct_vs_LLMSense": "percent",
        "ReAct_cost_usd": "usd",
        "LLMSense_cost_usd": "usd",
        "Cost_reduction_pct_vs_LLMSense": "percent",
    }
    if args.dry_run:
        print(f"  Would write {react_csv}")
        print(f"  Would write {react_md}")
    else:
        if _write_csv(react_table, react_csv, args.overwrite):
            write_count += 1
        if _write_markdown_dataframe(
            react_table,
            react_md,
            args.overwrite,
            column_formats=react_formats,
        ):
            write_count += 1

    print("Generating Flash-Fusion vs ReAct accuracy plot...")
    ff_vs_react_accuracy = _build_flash_fusion_vs_react_accuracy_summary(
        dataset_summaries,
        react_summaries,
    )
    ff_vs_react_accuracy["query_type"] = pd.Categorical(
        ff_vs_react_accuracy["query_type"],
        categories=QUERY_TYPES,
        ordered=True,
    )
    ff_vs_react_accuracy = ff_vs_react_accuracy.sort_values(
        ["baseline", "query_type"]
    )
    ff_vs_react_png = all_dir / "flash_fusion_vs_react_accuracy_by_query_type.png"
    if args.dry_run:
        print(f"  Would write {ff_vs_react_png}")
    else:
        if _plot_all_datasets_accuracy(
            ff_vs_react_accuracy,
            ff_vs_react_png,
            args.overwrite,
            title=(
                "Flash-Fusion vs ReAct - Accuracy by Query Type - All Datasets\n"
            ),
        ):
            write_count += 1

    print("Done.")
    print(f"Datasets processed: bus, wisdm, ecg")
    print(f"Output root: {output_root}")
    if args.dry_run:
        print("Dry run mode: no files written")
    else:
        print(f"Files written: {write_count}")


if __name__ == "__main__":
    main()
