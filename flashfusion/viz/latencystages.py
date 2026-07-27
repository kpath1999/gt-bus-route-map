#!/usr/bin/env python3
"""Generate primary latency-stage figures from benchmark results.

Outputs:
1) Flash-Fusion native stage latency by query type (N=3)
2) Semantic-stage comparison across Flash-Fusion, AutoIOT, and ReAct

AutoIOT uses native workflow timings when available. Older artifacts without
stage telemetry are retained as explicitly estimated summaries.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from measure import (
    BASELINE_ORDER,
    BASELINE_COLORS,
    DATASET_ORDER,
    QUERY_TYPE_ORDER,
    SEMANTIC_STAGE_ORDER,
    aggregate_flash_fusion_stage_latency_by_query_type,
    aggregate_latency_by_baseline_query_type,
    aggregate_semantic_stage_latency_by_query_type,
    aggregate_semantic_stage_latency_overall,
    display_baseline,
    load_all_metrics,
)

RC: dict[str, Any] = {
    "font.family": "DejaVu Sans",
    "font.size": 13.5,
    "axes.labelsize": 13.5,
    "axes.labelweight": "bold",
    "xtick.labelsize": 13.0,
    "ytick.labelsize": 13.0,
    "legend.fontsize": 12.0,
    "legend.title_fontsize": 12.0,
    "axes.facecolor": "#ffffff",
    "figure.facecolor": "#ffffff",
}


def _apply_rc() -> None:
    plt.rcParams.update(cast(Any, RC))

FF_STAGE_SPECS = [
    ("s1_latency_s", "Stage 1", "#2f8f57"),
    ("s2_latency_s", "Stage 2", "#386cc8"),
    ("s3_latency_s", "Stage 3", "#ef8b2c"),
    ("guardrail_latency_s", "Guardrail", "#df2127"),
    ("agent_latency_s", "Agent", "#8d67b8"),
]

SEMANTIC_STAGE_COLORS = {
    "Grounding": "#2f8f57",
    "Validation": "#df2127",
    "Planning": "#ef8b2c",
    "Execution": "#8d67b8",
}

SEMANTIC_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
LATENCY_COMPARE_BASELINES = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]


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

def _clean_axes(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _metric_mean(summary, query_type: str, metric: str) -> float:
    row = summary[(summary["query_type"] == query_type) & (summary["metric"] == metric)]
    if row.empty:
        return 0.0
    return float(row["mean"].iloc[0])


def plot_flash_fusion_native_latency(summary, out_path: Path, query_types: list[str] | None = None) -> None:
    _apply_rc()

    if query_types is None:
        present_query_types = [
            str(value)
            for value in summary["query_type"].dropna().unique().tolist()
            if str(value) in QUERY_TYPE_ORDER
        ]
        qtypes = [qt for qt in QUERY_TYPE_ORDER if qt in present_query_types]
    else:
        qtypes = [qt for qt in QUERY_TYPE_ORDER if qt in query_types]
    y = list(range(len(qtypes)))
    left = [0.0 for _ in qtypes]

    fig, ax = plt.subplots(figsize=(7.1, 3.8))
    for metric, label, color in FF_STAGE_SPECS:
        vals = [_metric_mean(summary, qt, metric) for qt in qtypes]
        ax.barh(
            y,
            vals,
            height=0.56,
            left=left,
            color=color,
            edgecolor="#f5f5f5",
            linewidth=0.8,
            label=label,
        )
        left = [b + v for b, v in zip(left, vals)]

    ax.set_yticks(y)
    ax.set_yticklabels(qtypes)
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s)")
    ax.xaxis.grid(linestyle="--", alpha=0.30, linewidth=0.9)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    # ax.text(
    #     0.99,
    #     0.02,
    #     "N=3 runs",
    #     transform=ax.transAxes,
    #     ha="right",
    #     va="bottom",
    #     fontsize=10.5,
    # )

    ax.legend(ncol=5, loc="upper left", bbox_to_anchor=(-0.20, -0.18), frameon=False, columnspacing=0.8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.30)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_semantic_stage_comparison_overall(summary, out_path: Path) -> None:
    """Stacked horizontal bar per baseline, stages averaged across all query types.

    Uses log x-scale with nonpositive='clip' to gracefully handle zero values
    in stacking — the first segment (Grounding) will now render even though
    stacking starts at left=0 (which gets clipped to a small positive value).
    """
    _apply_rc()

    baselines = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
    y = list(range(len(baselines)))
    left = [0.0 for _ in baselines]

    fig, ax = plt.subplots(figsize=(7.1, 3.8))
    for stage in SEMANTIC_STAGE_ORDER:
        color = SEMANTIC_STAGE_COLORS[stage]
        vals = []
        for baseline in baselines:
            row = summary[(summary["baseline"] == baseline) & (summary["stage"] == stage)]
            vals.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)

        ax.barh(
            y,
            vals,
            height=0.56,
            left=left,
            color=color,
            edgecolor="#f5f5f5",
            linewidth=0.8,
            label=stage,
        )
        left = [b + v for b, v in zip(left, vals)]

    ax.set_yticks(y)
    ax.set_yticklabels([display_baseline(b) for b in baselines])
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s)")
    # ax.set_xscale("log", nonpositive="clip")
    ax.xaxis.grid(linestyle="--", alpha=0.30, linewidth=0.9)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    uses_estimate = bool(summary["uses_estimate"].any()) if "uses_estimate" in summary.columns else False
    # if uses_estimate:
    #     ax.text(
    #         0.99,
    #         0.02,
    #         "AutoIOT stages use 1:3:2 semantic allocation (Grounding:Planning:Execution)",
    #         transform=ax.transAxes,
    #         ha="right",
    #         va="bottom",
    #         fontsize=9.5,
    #     )

    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(-0.05, -0.18), frameon=False, columnspacing=0.8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.28)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_semantic_stage_comparison_overall_log(summary, out_path: Path) -> None:
    """Stacked horizontal bar per baseline, stages averaged across all query types (log scale).

    Uses log x-scale with nonpositive='clip' to gracefully handle zero values
    in stacking — the first segment (Grounding) will now render even though
    stacking starts at left=0 (which gets clipped to a small positive value).
    """
    _apply_rc()

    baselines = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER"]
    y = list(range(len(baselines)))
    left = [0.0 for _ in baselines]

    fig, ax = plt.subplots(figsize=(7.1, 3.8))
    for stage in SEMANTIC_STAGE_ORDER:
        color = SEMANTIC_STAGE_COLORS[stage]
        vals = []
        for baseline in baselines:
            row = summary[(summary["baseline"] == baseline) & (summary["stage"] == stage)]
            vals.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)

        ax.barh(
            y,
            vals,
            height=0.56,
            left=left,
            color=color,
            edgecolor="#f5f5f5",
            linewidth=0.8,
            label=stage,
        )
        left = [b + v for b, v in zip(left, vals)]

    ax.set_yticks(y)
    ax.set_yticklabels([display_baseline(b) for b in baselines])
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s, log scale)")
    ax.set_xscale("log", nonpositive="clip")
    ax.xaxis.grid(linestyle="--", alpha=0.30, linewidth=0.9)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    uses_estimate = bool(summary["uses_estimate"].any()) if "uses_estimate" in summary.columns else False
    # if uses_estimate:
    #     ax.text(
    #         0.99,
    #         0.02,
    #         "AutoIOT stages use 1:3:2 semantic allocation (Grounding:Planning:Execution)",
    #         transform=ax.transAxes,
    #         ha="right",
    #         va="bottom",
    #         fontsize=9.5,
    #     )

    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(-0.05, -0.22), frameon=False, columnspacing=0.8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.28)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_semantic_stage_comparison(
    summary,
    out_path: Path,
    baselines: list[str] | None = None,
    query_types: list[str] | None = None,
) -> None:
    _apply_rc()

    if baselines is None:
        baselines = list(SEMANTIC_BASELINES)
    if query_types is None:
        present_query_types = [
            str(value)
            for value in summary["query_type"].dropna().unique().tolist()
            if str(value) in QUERY_TYPE_ORDER
        ]
        query_types = [qt for qt in QUERY_TYPE_ORDER if qt in present_query_types]
    else:
        query_types = [qt for qt in QUERY_TYPE_ORDER if qt in query_types]

    # Arrange rows grouped by query type; each group has one row per baseline.
    y_positions: list[float] = []
    y_labels: list[str] = []
    rows: list[tuple[str, str]] = []

    cursor = 0.0
    gap = 0.75
    for query_type in query_types:
        for baseline in baselines:
            y_positions.append(cursor)
            y_labels.append(f"{query_type} - {display_baseline(baseline)}")
            rows.append((query_type, baseline))
            cursor += 1.0
        cursor += gap

    fig, ax = plt.subplots(figsize=(9.6, 5.8))
    left = [0.0 for _ in rows]

    for stage in SEMANTIC_STAGE_ORDER:
        vals: list[float] = []
        for query_type, baseline in rows:
            row = summary[
                (summary["query_type"] == query_type)
                & (summary["baseline"] == baseline)
                & (summary["stage"] == stage)
            ]
            vals.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)

        ax.barh(
            y_positions,
            vals,
            height=0.64,
            left=left,
            color=SEMANTIC_STAGE_COLORS[stage],
            edgecolor="#f5f5f5",
            linewidth=0.8,
            label=stage,
        )
        left = [a + b for a, b in zip(left, vals)]

    ax.set_yticks(y_positions)
    ax.set_yticklabels(y_labels)
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s, log scale)")
    ax.set_xscale("log")
    ax.xaxis.grid(linestyle="--", alpha=0.30, linewidth=0.9)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    uses_estimate = bool(summary["uses_estimate"].any()) if "uses_estimate" in summary.columns else False
    # if uses_estimate:
    #     ax.text(
    #         0.99,
    #         0.02,
    #         "AutoIOT stages use 1:3:2 semantic allocation (Grounding:Planning:Execution)",
    #         transform=ax.transAxes,
    #         ha="right",
    #         va="bottom",
    #         fontsize=9.5,
    #     )

    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(0.0, -0.18), frameon=False, columnspacing=0.8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.28)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_cumulative_latency_comparison(
    summary,
    out_path: Path,
    baselines: list[str] | None = None,
    query_types: list[str] | None = None,
) -> None:
    _apply_rc()

    if query_types is None:
        present_query_types = [
            str(value)
            for value in summary["query_type"].dropna().unique().tolist()
            if str(value) in QUERY_TYPE_ORDER
        ]
        qtypes = [qt for qt in QUERY_TYPE_ORDER if qt in present_query_types]
    else:
        qtypes = [qt for qt in QUERY_TYPE_ORDER if qt in query_types]
    baselines = baselines or LATENCY_COMPARE_BASELINES
    y = list(range(len(qtypes)))
    width = 0.22

    fig, ax = plt.subplots(figsize=(8.8, 4.6))
    for i, baseline in enumerate(baselines):
        vals = []
        for qt in qtypes:
            row = summary[(summary["query_type"] == qt) & (summary["baseline"] == baseline)]
            vals.append(float(row["mean"].iloc[0]) if not row.empty else 0.0)

        ypos = [p - width + i * width for p in y]
        ax.barh(
            ypos,
            vals,
            height=width,
            color=BASELINE_COLORS.get(baseline, "#999999"),
            edgecolor="#333333",
            linewidth=0.8,
            label=display_baseline(baseline),
        )

    ax.set_yticks(y)
    ax.set_yticklabels(qtypes)
    ax.invert_yaxis()
    ax.set_xlabel("Avg Latency (s, log scale)")
    ax.set_xscale("log")
    ax.xaxis.grid(linestyle="--", alpha=0.30, linewidth=0.9)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    ax.legend(ncol=3, loc="upper left", bbox_to_anchor=(0.2, -0.18), frameon=False, columnspacing=0.8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    fig.subplots_adjust(bottom=0.27)
    fig.tight_layout(rect=(0.0, 0.04, 1.0, 1.0))
    fig.savefig(out_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate latency-stage figures for July26.")
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--results-root",
        default=str(script_dir.parent / "results" / "with_slm_predictive"),
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
        default=None,
        help="Optional override root for AUTOIOT_PAPER baseline data.",
    )
    parser.add_argument(
        "--run-dir",
        default="july26_full",
        help="Per-dataset run folder name under each baseline/dataset.",
    )
    parser.add_argument(
        "--baseline-set",
        default=",".join(LATENCY_COMPARE_BASELINES),
        help="Comma-separated baseline codes to include in figures.",
    )
    parser.add_argument(
        "--query-types",
        default=",".join(QUERY_TYPE_ORDER),
        help="Comma-separated query types to include in figures.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(script_dir / "results" / "primary_visualizations"),
        help="Output folder for primary figures.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    results_root = Path(args.results_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_baselines = [
        b.strip().upper() for b in _parse_csv_list(args.baseline_set) or list(LATENCY_COMPARE_BASELINES)
    ]
    selected_query_types = _parse_csv_list(args.query_types) or list(QUERY_TYPE_ORDER)

    df = load_all_metrics(results_root=results_root, run_dir=args.run_dir)

    def _apply_override(frame, override_df, baseline: str):
        if override_df.empty:
            return frame
        without = frame[frame["baseline"] != baseline].copy()
        out = pd.concat([without, override_df], ignore_index=True)
        out["baseline"] = pd.Categorical(out["baseline"], categories=list(BASELINE_ORDER), ordered=True)
        out["dataset"] = pd.Categorical(out["dataset"], categories=list(DATASET_ORDER), ordered=True)
        out["query_type"] = pd.Categorical(out["query_type"], categories=list(QUERY_TYPE_ORDER), ordered=True)
        return out

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
                run_dir=args.run_dir,
            )
            df = _apply_override(df, override_df, baseline_code)
        except ValueError:
            print(f"[WARN] Could not load override data for {baseline_code} from {override_root}")

    df = _filter_metrics(df, selected_baselines, selected_query_types)

    ff_summary = aggregate_flash_fusion_stage_latency_by_query_type(df)
    ff_out = output_dir / "per_stage_latency_breakdown_across_query_types_n3.png"
    plot_flash_fusion_native_latency(ff_summary, ff_out, query_types=selected_query_types)
    ff_summary.to_csv(output_dir / "per_stage_latency_breakdown_across_query_types_n3_summary.csv", index=False)

    semantic = aggregate_semantic_stage_latency_by_query_type(df, baselines=selected_baselines)
    semantic_out = output_dir / "semantic_stage_latency_comparison_by_baseline_n3.png"
    plot_semantic_stage_comparison(
        semantic,
        semantic_out,
        baselines=selected_baselines,
        query_types=selected_query_types,
    )
    semantic.to_csv(output_dir / "semantic_stage_latency_comparison_by_baseline_n3_summary.csv", index=False)

    semantic_overall = aggregate_semantic_stage_latency_overall(df, baselines=selected_baselines)
    semantic_overall_out = output_dir / "semantic_stage_comparison_overall_n3.png"
    plot_semantic_stage_comparison_overall(semantic_overall, semantic_overall_out)
    semantic_overall.to_csv(output_dir / "semantic_stage_comparison_overall_n3_summary.csv", index=False)

    semantic_overall_log_out = output_dir / "semantic_stage_comparison_overall_log_n3.png"
    plot_semantic_stage_comparison_overall_log(semantic_overall, semantic_overall_log_out)

    latency_compare = aggregate_latency_by_baseline_query_type(df, baselines=selected_baselines)
    latency_compare_out = output_dir / "cumulative_latency_comparison_log_by_baseline_n3.png"
    plot_cumulative_latency_comparison(
        latency_compare,
        latency_compare_out,
        baselines=selected_baselines,
        query_types=selected_query_types,
    )
    latency_compare.to_csv(output_dir / "cumulative_latency_comparison_log_by_baseline_n3_summary.csv", index=False)

    print(f"Wrote {ff_out}")
    print(f"Wrote {semantic_out}")
    print(f"Wrote {semantic_overall_out}")
    print(f"Wrote {semantic_overall_log_out}")
    print(f"Wrote {latency_compare_out}")


if __name__ == "__main__":
    main()
