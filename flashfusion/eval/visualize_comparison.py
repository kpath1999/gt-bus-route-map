"""Visualize baseline metrics by query type.

Creates grouped bar charts and tables for:
- Accuracy (%)
- Latency (seconds)
- Input tokens
- Output tokens
- Cost (USD)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from flashfusion.eval.queries import WISDM_QUERIES


QUERY_TYPE_ORDER = ["Direct", "Reasoning", "Out-of-Scope"]
QUERY_TYPE_LABELS = {
    "direct": "Direct",
    "intermediate": "Reasoning",
    "out_of_scope": "Out-of-Scope",
}

BASELINE_ORDER = ["FLASH_FUSION", "WELLMAX_ONLY", "AUTOIOT_ONLY"]
BASELINE_LABELS = {
    "FLASH_FUSION": "Flash-Fusion",
    "WELLMAX_ONLY": "WellMax",
    "AUTOIOT_ONLY": "AutoIOT",
}
BASELINE_COLORS = {
    "FLASH_FUSION": "#2c8c4a",
    "WELLMAX_ONLY": "#2f6ad9",
    "AUTOIOT_ONLY": "#f28e2b",
}


METRICS = [
    {
        "key": "accuracy_percent",
        "ylabel": "Accuracy (%)",
        "title": "Accuracy by Query Type",
        "filename": "accuracy_by_query_type.png",
        "table_base": "accuracy_by_query_type",
        "format_kind": "percent",
        "ylim": (0.0, 110.0),  # Extend y-axis to 110 for accuracy
    },
    {
        "key": "latency_s",
        "ylabel": "Latency (s)",
        "title": "Latency by Query Type",
        "filename": "latency_by_query_type.png",
        "table_base": "latency_by_query_type",
        "format_kind": "float2",
        "ylim": None,
    },
    {
        "key": "input_tokens",
        "ylabel": "Input Tokens",
        "title": "Input Tokens by Query Type",
        "filename": "input_tokens_by_query_type.png",
        "table_base": "input_tokens_by_query_type",
        "format_kind": "int",
        "ylim": None,
    },
    {
        "key": "output_tokens",
        "ylabel": "Output Tokens",
        "title": "Output Tokens by Query Type",
        "filename": "output_tokens_by_query_type.png",
        "table_base": "output_tokens_by_query_type",
        "format_kind": "int",
        "ylim": None,
    },
    {
        "key": "cost_usd",
        "ylabel": "Cost (USD)",
        "title": "Cost by Query Type",
        "filename": "cost_by_query_type.png",
        "table_base": "cost_by_query_type",
        "format_kind": "usd",
        "ylim": None,
    },
]


def _import_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except Exception as e:  # pragma: no cover - import guard for runtime env
        raise SystemExit(
            "matplotlib is required for visualization. "
            "Install it with: pip install matplotlib"
        ) from e
    return plt


def _configure_plot_style(plt) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "figure.facecolor": "#d9d9d9",
            "axes.facecolor": "#d9d9d9",
            "axes.edgecolor": "#222222",
            "axes.linewidth": 1.8,
            "axes.titlesize": 22,
            "axes.titleweight": "bold",
            "axes.labelsize": 17,
            "axes.labelweight": "bold",
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 18,
            "legend.title_fontsize": 18,
            "grid.alpha": 0.55,
            "grid.color": "#ffffff",
            "grid.linewidth": 1.0,
        }
    )


def _load_metrics(path: str, baseline_name: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Metrics file not found: {path}")
    df = pd.read_csv(p)
    if "baseline" not in df.columns:
        df["baseline"] = baseline_name
    else:
        df["baseline"] = baseline_name
    return df


def _load_metrics_any(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Metrics file not found: {path}")
    return pd.read_csv(p)


def _resolve_input_df(args: argparse.Namespace) -> pd.DataFrame:
    metrics_path = Path(args.metrics)
    if args.metrics and metrics_path.exists():
        df = _load_metrics_any(args.metrics)
        if "baseline" not in df.columns:
            raise ValueError("--metrics file must include a 'baseline' column")
        return df

    legacy_candidates = [args.wellmax, args.autoiot, args.flashfusion]
    if all(Path(x).exists() for x in legacy_candidates):
        df_w = _load_metrics(args.wellmax, "WELLMAX_ONLY")
        df_a = _load_metrics(args.autoiot, "AUTOIOT_ONLY")
        df_f = _load_metrics(args.flashfusion, "FLASH_FUSION")
        return pd.concat([df_w, df_a, df_f], ignore_index=True)

    raise FileNotFoundError(
        "Could not find input metrics. Pass --metrics or provide all of "
        "--wellmax/--autoiot/--flashfusion."
    )


def _prepare_query_types(df: pd.DataFrame) -> pd.DataFrame:
    if "query_id" not in df.columns:
        raise ValueError("Input metrics must include 'query_id' column")

    complexity_by_id = {int(q["id"]): str(q["complexity"]) for q in WISDM_QUERIES}
    df = df.copy()
    df["query_id"] = pd.to_numeric(df["query_id"], errors="coerce")
    if df["query_id"].isna().any():
        raise ValueError("query_id contains non-numeric values")
    df["query_id"] = df["query_id"].astype(int)
    df["query_type"] = df["query_id"].map(complexity_by_id).map(QUERY_TYPE_LABELS)

    unknown = sorted(df[df["query_type"].isna()]["query_id"].unique().tolist())
    if unknown:
        raise ValueError(f"Unknown query_id values not found in WISDM_QUERIES: {unknown}")

    return df


def _prepare_metrics(df: pd.DataFrame, accuracy_col: str) -> pd.DataFrame:
    if "input_tokens" not in df.columns:
        df["input_tokens"] = 0
    if "output_tokens" not in df.columns:
        df["output_tokens"] = 0

    needed = ["baseline", "query_id", accuracy_col, "latency_s", "cost_usd", "input_tokens", "output_tokens"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required metric columns: {missing}")

    return df


def _aggregate_by_query_type(df: pd.DataFrame, accuracy_col: str) -> pd.DataFrame:
    summary = (
        df.groupby(["baseline", "query_type"], as_index=False)
        .agg(
            accuracy_raw=(accuracy_col, "mean"),
            latency_s=("latency_s", "mean"),
            input_tokens=("input_tokens", "mean"),
            output_tokens=("output_tokens", "mean"),
            cost_usd=("cost_usd", "mean"),
        )
    )
    summary["accuracy_percent"] = summary["accuracy_raw"] * 100.0

    ordered_baselines = [b for b in BASELINE_ORDER if b in set(summary["baseline"])]
    full_index = pd.MultiIndex.from_product(
        [ordered_baselines, QUERY_TYPE_ORDER], names=["baseline", "query_type"]
    )
    summary = summary.set_index(["baseline", "query_type"]).reindex(full_index).reset_index()
    return summary


def _format_for_table(v: float, kind: str) -> str:
    if pd.isna(v):
        return "N/A"
    if kind == "percent":
        return f"{v:.1f}%"
    if kind == "float2":
        return f"{v:.2f}"
    if kind == "int":
        return f"{v:.0f}"
    if kind == "usd":
        return f"{v:.6f}"
    return f"{v:.4f}"


def _metric_table(summary: pd.DataFrame, metric_key: str) -> pd.DataFrame:
    table = summary.pivot(index="baseline", columns="query_type", values=metric_key)
    table = table.reindex(index=[b for b in BASELINE_ORDER if b in table.index])
    table = table.reindex(columns=QUERY_TYPE_ORDER)
    table.index = [BASELINE_LABELS.get(b, str(b)) for b in table.index]
    table.index.name = "Baseline"
    return table


def _save_metric_table_markdown(df: pd.DataFrame, out_path: Path, heading: str, format_kind: str) -> None:
    lines = [f"# {heading}", ""]
    header = "| Baseline | " + " | ".join(QUERY_TYPE_ORDER) + " |"
    sep = "|---|" + "|".join(["---:" for _ in QUERY_TYPE_ORDER]) + "|"
    lines.extend([header, sep])

    for baseline_name, row in df.iterrows():
        vals = [
            _format_for_table(float(row[c]) if pd.notna(row[c]) else float("nan"), format_kind)
            for c in QUERY_TYPE_ORDER
        ]
        lines.append("| " + str(baseline_name) + " | " + " | ".join(vals) + " |")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_value_label(v: float, kind: str) -> str:
    if kind == "percent":
        return f"{v:.1f}%"
    if kind == "float2":
        return f"{v:.2f}"
    if kind == "int":
        return f"{v:.0f}"
    if kind == "usd":
        return f"${v:.6f}"
    return f"{v:.3f}"


def _plot_grouped_metric(
    summary: pd.DataFrame,
    metric_key: str,
    output_png: Path,
    title: str,
    ylabel: str,
    format_kind: str,
    ylim: tuple[float, float] | None,
) -> None:
    plt = _import_matplotlib()
    _configure_plot_style(plt)
    fig, ax = plt.subplots(figsize=(12.5, 7.2))

    x = np.arange(len(QUERY_TYPE_ORDER))
    width = 0.24

    for idx, baseline in enumerate(BASELINE_ORDER):
        base_df = summary[summary["baseline"] == baseline].set_index("query_type")
        vals = [base_df[metric_key].get(qt, np.nan) for qt in QUERY_TYPE_ORDER]
        centers = x + (idx - 1) * width
        bars = ax.bar(
            centers,
            vals,
            width=width,
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
                bar.get_height() + (0.8 if format_kind == "percent" else max(0.01 * float(bar.get_height()), 0.02)),
                _format_value_label(float(v), format_kind),
                ha="center",
                va="bottom",
                fontsize=12,
                fontweight="bold",
            )

    ax.set_title(title, pad=20)  # Increased padding to prevent overlap
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Query Type", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(QUERY_TYPE_ORDER)
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    if ylim is not None:
        ax.set_ylim(*ylim)
    ax.legend(title="Baseline", loc="upper left", frameon=True)

    fig.subplots_adjust(left=0.09, right=0.98, bottom=0.12, top=0.85)  # Adjusted top spacing
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _build_per_query_table(df: pd.DataFrame, accuracy_col: str) -> pd.DataFrame:
    cols = [
        "query_id",
        "query_type",
        "baseline",
        accuracy_col,
        "latency_s",
        "cost_usd",
        "input_tokens",
        "output_tokens",
    ]
    cols = [c for c in cols if c in df.columns]
    out = df[cols].copy()
    out = out.rename(columns={accuracy_col: "accuracy_raw"})
    out["accuracy_percent"] = out["accuracy_raw"] * 100.0
    out = out.sort_values(["query_id", "baseline"]).reset_index(drop=True)
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Visualize baseline comparison by query type. "
            "Simple usage: python -m flashfusion.eval.visualize_comparison "
            "--metrics flashfusion/eval_results/runs/latest/benchmark/metrics.csv"
        )
    )
    parser.add_argument(
        "--metrics",
        default="flashfusion/eval_results/runs/latest/benchmark/metrics.csv",
        help="Path to combined metrics.csv containing all baselines",
    )
    parser.add_argument(
        "--wellmax",
        default="flashfusion/eval_results/wellmax_all/metrics.csv",
        help="Path to wellmax metrics.csv",
    )
    parser.add_argument(
        "--autoiot",
        default="flashfusion/eval_results/autoiot_all/metrics.csv",
        help="Path to autoiot metrics.csv",
    )
    parser.add_argument(
        "--flashfusion",
        default="flashfusion/eval_results/ff_accuracy_all/metrics.csv",
        help="Path to flashfusion metrics.csv",
    )
    parser.add_argument(
        "--accuracy-column",
        default="gt_score",
        help="Accuracy column to compare (default: gt_score)",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/eval_results/runs/latest/visuals",
        help="Output directory for charts and tables",
    )
    parser.add_argument(
        "--title",
        default="Baseline Comparison by Query Type",
        help="Chart title",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_df = _resolve_input_df(args)
    all_df = _prepare_metrics(all_df, accuracy_col=args.accuracy_column)
    all_df = _prepare_query_types(all_df)
    summary = _aggregate_by_query_type(all_df, accuracy_col=args.accuracy_column)

    summary_csv = out_dir / "query_type_summary.csv"
    per_query_csv = out_dir / "per_query_metrics.csv"

    summary.to_csv(summary_csv, index=False)

    per_query = _build_per_query_table(all_df, accuracy_col=args.accuracy_column)
    per_query.to_csv(per_query_csv, index=False)
    output_paths: list[Path] = [summary_csv, per_query_csv]

    for spec in METRICS:
        chart_path = out_dir / spec["filename"]
        _plot_grouped_metric(
            summary=summary,
            metric_key=str(spec["key"]),
            output_png=chart_path,
            title=f"{args.title} - {spec['title']}",
            ylabel=str(spec["ylabel"]),
            format_kind=str(spec["format_kind"]),
            ylim=spec["ylim"],
        )
        output_paths.append(chart_path)

        table = _metric_table(summary, str(spec["key"]))
        csv_path = out_dir / f"{spec['table_base']}.csv"
        md_path = out_dir / f"{spec['table_base']}.md"
        table.to_csv(csv_path)
        _save_metric_table_markdown(
            table,
            md_path,
            heading=str(spec["title"]),
            format_kind=str(spec["format_kind"]),
        )
        output_paths.extend([csv_path, md_path])

    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {per_query_csv}")
    for p in output_paths:
        if p not in {summary_csv, per_query_csv}:
            print(f"Wrote: {p}")


if __name__ == "__main__":
    main()
