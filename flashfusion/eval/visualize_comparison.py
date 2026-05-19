"""
eval/visualize_comparison.py — Visualize baseline comparison metrics.

Creates bar charts and tables to compare:
- Accuracy (ground-truth score)
- Latency (seconds)
- Cost (USD)
- Token usage (input/output/total)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception as e:  # pragma: no cover - import guard for runtime env
        raise SystemExit(
            "matplotlib is required for visualization. "
            "Install it with: pip install matplotlib"
        ) from e
    return plt


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


def _build_summary(df: pd.DataFrame, accuracy_col: str) -> pd.DataFrame:
    if "input_tokens" not in df.columns:
        df["input_tokens"] = 0
    if "output_tokens" not in df.columns:
        df["output_tokens"] = 0
    df["total_tokens"] = df["input_tokens"] + df["output_tokens"]

    needed = ["baseline", accuracy_col, "latency_s", "cost_usd", "input_tokens", "output_tokens", "total_tokens"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required metric columns: {missing}")

    summary = (
        df.groupby("baseline", as_index=False)[
            [accuracy_col, "latency_s", "cost_usd", "input_tokens", "output_tokens", "total_tokens"]
        ]
        .mean()
        .rename(columns={accuracy_col: "accuracy"})
    )
    return summary


def _save_table_markdown(df: pd.DataFrame, out_path: Path) -> None:
    lines = ["# Baseline Comparison Summary", "", "| Baseline | Accuracy | Latency (s) | Cost (USD) | Input Tokens | Output Tokens | Total Tokens |", "|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in df.iterrows():
        lines.append(
            "| "
            f"{r['baseline']} | {r['accuracy']:.4f} | {r['latency_s']:.4f} | {r['cost_usd']:.6f} | "
            f"{r['input_tokens']:.1f} | {r['output_tokens']:.1f} | {r['total_tokens']:.1f} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _plot_main_bars(summary: pd.DataFrame, output_png: Path, title: str) -> None:
    plt = _import_matplotlib()
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.8))
    colors = ["#136f63", "#f4a259", "#2d6cdf"]

    summary.plot(x="baseline", y="accuracy", kind="bar", ax=axes[0], legend=False, color=colors)
    axes[0].set_title("Accuracy")
    axes[0].set_ylabel("Avg Score")
    axes[0].tick_params(axis="x", rotation=0)

    summary.plot(x="baseline", y="latency_s", kind="bar", ax=axes[1], legend=False, color=colors)
    axes[1].set_title("Latency")
    axes[1].set_ylabel("Avg Seconds")
    axes[1].tick_params(axis="x", rotation=0)

    summary.plot(x="baseline", y="cost_usd", kind="bar", ax=axes[2], legend=False, color=colors)
    axes[2].set_title("Token Cost")
    axes[2].set_ylabel("Avg USD")
    axes[2].tick_params(axis="x", rotation=0)

    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _plot_token_bars(summary: pd.DataFrame, output_png: Path, title: str) -> None:
    plt = _import_matplotlib()
    fig, ax = plt.subplots(figsize=(8.8, 5.0))

    token_df = summary[["baseline", "input_tokens", "output_tokens", "total_tokens"]].set_index("baseline")
    token_df.plot(kind="bar", ax=ax, color=["#3fa7d6", "#f08700", "#6d597a"])
    ax.set_title(title)
    ax.set_ylabel("Average Tokens")
    ax.tick_params(axis="x", rotation=0)
    ax.legend(title="Metric")
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _build_per_query_table(df: pd.DataFrame, accuracy_col: str) -> pd.DataFrame:
    cols = ["query_id", "baseline", accuracy_col, "latency_s", "cost_usd", "input_tokens", "output_tokens"]
    cols = [c for c in cols if c in df.columns]
    out = df[cols].copy()
    out = out.rename(columns={accuracy_col: "accuracy"})
    out = out.sort_values(["query_id", "baseline"]).reset_index(drop=True)
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize baseline comparison (WellMax vs AutoIOT vs FlashFusion)."
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
        default="flashfusion/eval_results/comparison_visuals",
        help="Output directory for charts and tables",
    )
    parser.add_argument(
        "--title",
        default="Baseline Comparison: WellMax vs AutoIOT vs FlashFusion",
        help="Chart title",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    df_w = _load_metrics(args.wellmax, "WELLMAX_ONLY")
    df_a = _load_metrics(args.autoiot, "AUTOIOT_ONLY")
    df_f = _load_metrics(args.flashfusion, "FLASH_FUSION")
    all_df = pd.concat([df_w, df_a, df_f], ignore_index=True)

    summary = _build_summary(all_df, accuracy_col=args.accuracy_column)
    summary = summary.sort_values("accuracy", ascending=False).reset_index(drop=True)

    summary_csv = out_dir / "baseline_summary.csv"
    summary_md = out_dir / "baseline_summary.md"
    per_query_csv = out_dir / "per_query_metrics.csv"
    main_png = out_dir / "accuracy_latency_cost_bars.png"
    tokens_png = out_dir / "token_usage_bars.png"

    summary.to_csv(summary_csv, index=False)
    _save_table_markdown(summary, summary_md)

    per_query = _build_per_query_table(all_df, accuracy_col=args.accuracy_column)
    per_query.to_csv(per_query_csv, index=False)

    _plot_main_bars(summary, main_png, args.title)
    _plot_token_bars(summary, tokens_png, "Average Token Usage by Baseline")

    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {summary_md}")
    print(f"Wrote: {per_query_csv}")
    print(f"Wrote: {main_png}")
    print(f"Wrote: {tokens_png}")


if __name__ == "__main__":
    main()
