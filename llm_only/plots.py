"""
flashfusion.llm_only.plots — publication figures.

Reads `results/trials.csv` and `results/summary.csv` and emits PNG figures into
`results/figures/`. Matches the FF visualization style (color palette,
dpi=180, tight layout).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRIALS_CSV = RESULTS_DIR / "trials.csv"
SUMMARY_CSV = RESULTS_DIR / "summary.csv"
FIG_DIR = RESULTS_DIR / "figures"

DATASET_ORDER = ["bus", "wisdm", "ecg"]
DATASET_LABEL = {"bus": "Bus\n(~1.2k rows)", "wisdm": "WISDM IMU\n(~1.1M rows)", "ecg": "MIT ECG\n(~21M rows)"}

# Match flashfusion/eval/visualize_comparison.py
BASELINE_COLORS = {
    "LLM_ONLY":     "#d62728",
    "FLASH_FUSION": "#2c8c4a",
}
BASELINE_LABEL = {"LLM_ONLY": "LLM-Only", "FLASH_FUSION": "Flash-Fusion"}


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "axes.grid": True,
        "grid.alpha": 0.25,
    })


def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not TRIALS_CSV.exists():
        raise SystemExit(f"Missing {TRIALS_CSV}; run experiment.py first.")
    trials = pd.read_csv(TRIALS_CSV)
    summary = pd.read_csv(SUMMARY_CSV) if SUMMARY_CSV.exists() else pd.DataFrame()
    return trials, summary


def _grouped_bar(ax, summary: pd.DataFrame, metric: str, std_col: str | None,
                  ylabel: str, log_y: bool = False) -> None:
    """One panel: x=dataset, grouped bars by baseline."""
    width = 0.36
    x = np.arange(len(DATASET_ORDER))
    baselines = ["LLM_ONLY", "FLASH_FUSION"]
    for i, b in enumerate(baselines):
        values, errs = [], []
        for d in DATASET_ORDER:
            row = summary[(summary["baseline"] == b) & (summary["dataset"] == d)]
            if row.empty or pd.isna(row[metric].iloc[0]):
                values.append(0.0)
                errs.append(0.0)
            else:
                values.append(float(row[metric].iloc[0]))
                errs.append(float(row[std_col].iloc[0]) if std_col and std_col in row else 0.0)
        offset = (i - 0.5) * width
        ax.bar(
            x + offset, values, width=width, yerr=errs,
            color=BASELINE_COLORS[b], label=BASELINE_LABEL[b],
            capsize=3, edgecolor="black", linewidth=0.4,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABEL[d] for d in DATASET_ORDER])
    ax.set_ylabel(ylabel)
    if log_y:
        ax.set_yscale("log")


def fig_headline(summary: pd.DataFrame) -> None:
    """4 panels (accuracy / latency / input tokens / cost) × 3 datasets."""
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    _grouped_bar(axes[0, 0], summary, "accuracy_mean", "accuracy_std",
                 "LLM-judge accuracy [0, 1]")
    axes[0, 0].set_ylim(0, 1.05)
    axes[0, 0].set_title("Accuracy")

    _grouped_bar(axes[0, 1], summary, "latency_mean", "latency_std",
                 "Latency (s)", log_y=True)
    axes[0, 1].set_title("Latency (log scale)")

    _grouped_bar(axes[1, 0], summary, "input_tokens_mean", "input_tokens_std",
                 "Input tokens (billed)", log_y=True)
    axes[1, 0].set_title("Input tokens consumed (log scale)")

    _grouped_bar(axes[1, 1], summary, "cost_mean", "cost_std",
                 "Cost per query (USD)", log_y=True)
    axes[1, 1].set_title("Cost per query (log scale)")

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, bbox_to_anchor=(0.5, 0.99))
    fig.suptitle(
        "LLM-Only vs Flash-Fusion across dataset sizes (Llama 3.3 70B, 128k context)",
        fontsize=12, y=0.94,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / "fig_headline.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_truncation(trials: pd.DataFrame) -> None:
    """Truncation_pct by dataset — only LLM_ONLY rows have nonzero truncation."""
    sub = trials[trials["baseline"] == "LLM_ONLY"].copy()
    if sub.empty:
        return
    agg = sub.groupby("dataset")["truncation_pct"].agg(["mean", "std"]).reindex(DATASET_ORDER)

    fig, ax = plt.subplots(figsize=(6.5, 4))
    x = np.arange(len(DATASET_ORDER))
    ax.bar(
        x, agg["mean"].fillna(0) * 100, yerr=agg["std"].fillna(0) * 100,
        color=BASELINE_COLORS["LLM_ONLY"], capsize=4, edgecolor="black", linewidth=0.5,
    )
    for xi, val in zip(x, agg["mean"].fillna(0).values):
        ax.text(xi, val * 100 + 2, f"{val*100:.1f}%", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABEL[d] for d in DATASET_ORDER])
    ax.set_ylabel("Truncation of input data (% of full dataset dropped)")
    ax.set_ylim(0, 105)
    ax.set_title("LLM-Only must drop most data on WISDM and ECG to fit 128k context")
    fig.tight_layout()
    out = FIG_DIR / "fig_truncation.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def fig_scaling(summary: pd.DataFrame) -> None:
    """
    Accuracy and cost vs dataset row count, both baselines overlaid.
    Datasets serve as the size axis.
    """
    fig, (ax_acc, ax_cost) = plt.subplots(1, 2, figsize=(11, 4.2))
    size_map = {"bus": 1_220, "wisdm": 1_098_210, "ecg": 20_800_000}

    for b in ["LLM_ONLY", "FLASH_FUSION"]:
        xs, accs, costs = [], [], []
        for d in DATASET_ORDER:
            row = summary[(summary["baseline"] == b) & (summary["dataset"] == d)]
            if row.empty:
                continue
            xs.append(size_map[d])
            accs.append(row["accuracy_mean"].iloc[0])
            costs.append(row["cost_mean"].iloc[0])
        ax_acc.plot(xs, accs, marker="o", linewidth=2,
                    color=BASELINE_COLORS[b], label=BASELINE_LABEL[b])
        ax_cost.plot(xs, costs, marker="o", linewidth=2,
                     color=BASELINE_COLORS[b], label=BASELINE_LABEL[b])

    ax_acc.set_xscale("log")
    ax_acc.set_xlabel("Dataset size (rows)")
    ax_acc.set_ylabel("Accuracy [0, 1]")
    ax_acc.set_title("Accuracy vs dataset size")
    ax_acc.set_ylim(0, 1.05)
    ax_acc.legend()

    ax_cost.set_xscale("log")
    ax_cost.set_yscale("log")
    ax_cost.set_xlabel("Dataset size (rows)")
    ax_cost.set_ylabel("Cost per query (USD)")
    ax_cost.set_title("Cost vs dataset size (log–log)")
    ax_cost.axvline(x=size_map["wisdm"], linestyle="--", color="gray", alpha=0.5)
    ax_cost.legend()

    fig.suptitle("Scaling characteristics — LLM-Only forced into truncation past ~50k rows",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    out = FIG_DIR / "fig_scaling.png"
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    _style()
    trials, summary = _load()
    if summary.empty:
        raise SystemExit("Missing summary.csv; run analyze.py first.")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig_headline(summary)
    fig_truncation(trials)
    fig_scaling(summary)
    print(f"All figures in {FIG_DIR}")


if __name__ == "__main__":
    main()
