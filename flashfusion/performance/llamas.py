#!/usr/bin/env python3
"""Generate Flash-Fusion benchmark visualizations per LLAMAS Lab writing guidelines.

Style rules applied:
  - No title inside the image file (caption handles it)
  - y-axis always starts at 0
  - Consistent font family and sizes (matching 11pt main text)
  - No top/right spines (clean academic look)
  - Consistent colors for each system across ALL figures
  - Labels legible at standard scale (no zooming required)
  - Error bars with std-dev

Figure 1 : Query accuracy of ALL baselines (+ReAct) across Bus, WISDM, ECG datasets
Figure 2 : Query accuracy of top-3 (Flash-Fusion, ReAct, AutoIOT) by query type
           (Direct, Reasoning, Out-of-Scope) -- averaged across datasets; "All" used
           only for internal sorting, not visualized
Table    : Latency, input tokens, cost + avg accuracy for all baselines (+ReAct),
           sorted descending by avg accuracy; ReAct flagged as ablation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

if sys.version_info >= (3, 14):
    raise SystemExit(
        "This script is not compatible with Python 3.14 in this repository (pandas import stalls). "
        "Run with system Python 3.11 (e.g., `python3 performance/llamas.py ...`) or recreate .venv with Python 3.11/3.12."
    )

print("Importing dependencies (numpy/pandas/matplotlib)...", flush=True)

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ── Identity mappings ─────────────────────────────────────────────────────────

QUERY_TYPE_BY_ID = {
    1: "Direct", 2: "Direct", 3: "Direct", 4: "Direct",
    5: "Reasoning", 6: "Reasoning", 7: "Reasoning", 8: "Reasoning",
    9: "Out-of-Scope", 10: "Out-of-Scope", 11: "Out-of-Scope", 12: "Out-of-Scope",
}
QUERY_TYPES = ["Direct", "Reasoning", "Out-of-Scope", "All"]

# QUERY_TYPES_VIZ: "All" excluded from Figure 2 visualization; used only for sorting/table
QUERY_TYPES_VIZ = ["Direct", "Reasoning", "Out-of-Scope"]  # [CHANGE 1]

# Paper baseline order: Flash-Fusion first, then ablation (ReAct), then baselines
BASELINE_ORDER_ALL  = ["FLASH_FUSION", "AUTOIOT_ONLY", "AUTOIOT_PAPER", "HARGPT_PAPER", "LLMSENSE_PAPER"]
BASELINE_ORDER_TOP3 = ["FLASH_FUSION", "AUTOIOT_ONLY", "AUTOIOT_PAPER"]

BASELINE_LABELS = {
    "FLASH_FUSION":    "Flash-Fusion",
    "AUTOIOT_PAPER":   "AutoIOT",
    "AUTOIOT_ONLY":    "ReAct",        # ablation study
    "HARGPT_PAPER":    "HARGPT",
    "LLMSENSE_PAPER":  "LLMSense",
}

# Consistent colors across every figure in the paper (§5c)
BASELINE_COLORS = {
    "FLASH_FUSION":   "#2f8f57",
    "AUTOIOT_ONLY":   "#7fba00",
    "AUTOIOT_PAPER":  "#4c78a8",
    "HARGPT_PAPER":   "#f58518",
    "LLMSENSE_PAPER": "#b279a2",
}
BASELINE_HATCHES = {
    "AUTOIOT_ONLY": "//",    # ReAct is an ablation → hatched to distinguish from full systems
}

DATASET_ORDER  = ["bus", "wisdm", "ecg"]
DATASET_LABELS = {"bus": "Bus", "wisdm": "WISDM", "ecg": "ECG"}
COST_DISPLAY_SCALE = 1e5
FONT_SCALE = 1.25


# ── Style constants — match 11pt LaTeX body text (§5c) ────────────────────────

RC = {
    "font.family":           "DejaVu Sans",
    "font.size":             11 * FONT_SCALE,
    "axes.labelsize":        11 * FONT_SCALE,
    "axes.labelweight":      "bold",
    "xtick.labelsize":       10 * FONT_SCALE,
    "ytick.labelsize":       10 * FONT_SCALE,
    "legend.fontsize":       12 * FONT_SCALE,
    "legend.title_fontsize": 12 * FONT_SCALE,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(v) -> str:
    return str(v).strip().upper()

def _label(code: str) -> str:
    return BASELINE_LABELS.get(code, code)

def _fmt_pct(v) -> str:
    return "N/A" if pd.isna(v) else f"{v:.1f}%"

def _fmt_int(v) -> str:
    return "N/A" if pd.isna(v) else f"{int(round(v)):,}"

def _fmt_usd(v) -> str:
    return "N/A" if pd.isna(v) else f"${v:.4f}"

def _fmt_usd_e5(v) -> str:
    return "N/A" if pd.isna(v) else f"{v * COST_DISPLAY_SCALE:.2f}"

def _fmt_s(v) -> str:
    return "N/A" if pd.isna(v) else f"{v:.2f}s"

def _clean_axes(ax):
    """Remove top and right spines — no unnecessary box borders (§5a)."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

def _scaled_font_size(size: float) -> float:
    return size * FONT_SCALE

def _load_csv(path: Path, dataset: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["baseline"]         = df["baseline"].map(_norm)
    df["query_id"]         = pd.to_numeric(df["query_id"],      errors="coerce").astype("Int64")
    df["query_type"]       = df["query_id"].map(QUERY_TYPE_BY_ID)
    df["accuracy_percent"] = pd.to_numeric(df["gt_score"],      errors="coerce") * 100.0
    df["latency_s"]        = pd.to_numeric(df["latency_s"],     errors="coerce")
    df["cost_usd"]         = pd.to_numeric(df["cost_usd"],      errors="coerce")
    df["input_tokens"]     = pd.to_numeric(df["input_tokens"],  errors="coerce")
    df["output_tokens"]    = pd.to_numeric(df["output_tokens"], errors="coerce")
    df["run_id"]           = pd.to_numeric(df["run_id"],        errors="coerce").astype("Int64")
    df["dataset"]          = dataset
    return df

def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Return mean±std per (baseline, query_type, metric) aggregated over run_ids."""
    metrics  = ["accuracy_percent", "latency_s", "cost_usd", "input_tokens", "output_tokens"]
    by_type  = df.groupby(["baseline", "run_id", "query_type"], as_index=False)[metrics].mean()
    by_all   = df.groupby(["baseline", "run_id"],               as_index=False)[metrics].mean()
    by_all["query_type"] = "All"
    combined = pd.concat([by_type, by_all], ignore_index=True)
    long     = combined.melt(id_vars=["baseline", "run_id", "query_type"],
                             value_vars=metrics, var_name="metric", value_name="val")
    agg      = (long.groupby(["baseline", "query_type", "metric"], as_index=False)
                    .agg(mean=("val", "mean"), std=("val", "std")))
    agg["std"] = agg["std"].fillna(0.0)
    return agg

def _replace_rows(base: pd.DataFrame, repl: pd.DataFrame, code: str) -> pd.DataFrame:
    return pd.concat([base[base["baseline"] != code], repl[repl["baseline"] == code]],
                     ignore_index=True)

def _get(summary, baseline, metric, query_type="All"):
    row = summary[(summary["baseline"]   == baseline) &
                  (summary["metric"]     == metric)   &
                  (summary["query_type"] == query_type)]
    if row.empty:
        return float("nan"), float("nan")
    return float(row["mean"].iloc[0]), float(row["std"].iloc[0])

def _place_grouped_bar_labels(
    ax,
    group_xpos,
    group_vals,
    group_stds,
    min_gap=2.2,
    fontsize=9.2,
):
    """
    Place one dataset group's labels while enforcing minimum vertical spacing.

    min_gap is in y-axis data units (percentage points).
    """
    items = []
    for x, val, std in zip(group_xpos, group_vals, group_stds):
        if val <= 0:
            continue
        base_y = val + std + max(0.8, val * 0.012)
        items.append([x, val, base_y])

    items.sort(key=lambda t: t[2])

    for j in range(1, len(items)):
        prev_y = items[j - 1][2]
        items[j][2] = max(items[j][2], prev_y + min_gap)

    for x, val, y in items:
        ax.text(
            x,
            y,
            f"{val:.0f}%",
            ha="center",
            va="bottom",
            fontsize=_scaled_font_size(fontsize),
            fontweight="bold",
            clip_on=False,
        )

# ── Figure 1: All baselines + ReAct, accuracy across Bus / WISDM / ECG ────────
# x-axis groups = datasets; bars = systems (all 5)
# No title in image — caption handles it (§5a)
# y-axis starts at 0 (§5b)

def plot_fig1_accuracy_by_dataset(
    dataset_summaries: Dict[str, pd.DataFrame],
    react_summaries:   Dict[str, pd.DataFrame],
    out_path: Path,
) -> None:
    plt.rcParams.update(RC)

    baselines = BASELINE_ORDER_ALL
    datasets  = DATASET_ORDER
    x         = list(range(len(datasets)))
    width     = 0.8 / len(baselines)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))

    group_positions = {ds: [] for ds in datasets}
    group_means     = {ds: [] for ds in datasets}
    group_stds      = {ds: [] for ds in datasets}

    for i, bl in enumerate(baselines):
        means, stds = [], []
        for ds in datasets:
            summary = react_summaries[ds] if bl == "AUTOIOT_ONLY" else dataset_summaries[ds]
            m, s = _get(summary, bl, "accuracy_percent", "All")
            means.append(0.0 if pd.isna(m) else m)
            stds.append(0.0 if pd.isna(s) else s)

        xpos  = [p - 0.4 + (i + 0.5) * width for p in x]
        color = BASELINE_COLORS.get(bl, "#aaaaaa")
        hatch = BASELINE_HATCHES.get(bl)

        ax.bar(
            xpos, means, width,
            label=_label(bl),
            color=color,
            edgecolor="#333333",
            linewidth=0.6,
            yerr=stds,
            error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#222222"},
            hatch=hatch
        )

        for ds, xp, m, s in zip(datasets, xpos, means, stds):
            group_positions[ds].append(xp)
            group_means[ds].append(m)
            group_stds[ds].append(s)

    for ds in datasets:
        _place_grouped_bar_labels(
            ax,
            group_positions[ds],
            group_means[ds],
            group_stds[ds],
            min_gap=2.4,
            fontsize=9.2,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in datasets])
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 115)
    ax.yaxis.grid(linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    handles = [mpatches.Patch(facecolor=BASELINE_COLORS[b],
                              edgecolor="#333333",
                              hatch=BASELINE_HATCHES.get(b),
                              label=_label(b))
               for b in baselines]
    ax.legend(
        handles=handles,
        ncol=len(baselines),
        mode="expand",
        loc="upper center",
        bbox_to_anchor=(-0.03, -0.21, 1.06, 0.1),
        borderaxespad=0.0,
        frameon=False,
        fontsize=_scaled_font_size(10),
        columnspacing=5.0,
        handlelength=1.8,
        handletextpad=0.6,
        labelspacing=2.0,
    )

    fig.tight_layout()
    fig.savefig(str(out_path), dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_path}")


# ── Figure 2: Top-3 systems, accuracy by query type (avg across datasets) ──────
# x-axis groups = query types (Direct / Reasoning / Out-of-Scope only — "All" excluded)
# bars = Flash-Fusion, ReAct, AutoIOT
# "All" is still computed internally but NOT visualized (used only for table sorting)

def plot_fig2_top3_by_query_type(
    dataset_summaries: Dict[str, pd.DataFrame],
    react_summaries:   Dict[str, pd.DataFrame],
    out_path: Path,
) -> None:
    plt.rcParams.update(RC)

    baselines = BASELINE_ORDER_TOP3
    # [CHANGE 1] Use QUERY_TYPES_VIZ (no "All") for the plot x-axis
    qtypes    = QUERY_TYPES_VIZ
    x         = list(range(len(qtypes)))
    width     = 0.8 / len(baselines)

    # Collect per-dataset values across ALL query types (including "All" for internal use),
    # then average across datasets — but only plot QUERY_TYPES_VIZ
    rows = []
    for ds in DATASET_ORDER:
        for bl in baselines:
            summary = react_summaries[ds] if bl == "AUTOIOT_ONLY" else dataset_summaries[ds]
            for qt in QUERY_TYPES:          # still gather "All" for completeness
                m, _ = _get(summary, bl, "accuracy_percent", qt)
                rows.append({"baseline": bl, "query_type": qt, "dataset": ds, "mean": m})
    combined = pd.DataFrame(rows)
    agg = (combined.groupby(["baseline", "query_type"], as_index=False)
                   .agg(mean=("mean", "mean"), std=("mean", "std")))
    agg["std"] = agg["std"].fillna(0.0)

    fig, ax = plt.subplots(figsize=(7.0, 3.6))

    for i, bl in enumerate(baselines):
        bdf   = agg[agg["baseline"] == bl]
        # [CHANGE 1] Only iterate over QUERY_TYPES_VIZ for bar rendering
        means = [float(bdf[bdf["query_type"] == qt]["mean"].iloc[0])
                 if not bdf[bdf["query_type"] == qt].empty else 0.0 for qt in qtypes]
        stds  = [float(bdf[bdf["query_type"] == qt]["std"].iloc[0])
                 if not bdf[bdf["query_type"] == qt].empty else 0.0 for qt in qtypes]

        xpos  = [p - 0.4 + (i + 0.5) * width for p in x]
        color = BASELINE_COLORS.get(bl, "#aaaaaa")
        hatch = BASELINE_HATCHES.get(bl)
        bars  = ax.bar(xpos, means, width,
                       label=_label(bl), color=color,
                       edgecolor="#333333", linewidth=0.6,
                       yerr=stds,
                       error_kw={"elinewidth": 1.0, "capsize": 3, "ecolor": "#222222"},
                       hatch=hatch)
        for bar, val in zip(bars, means):
            if val <= 0:
                continue
            ax.text(bar.get_x() + bar.get_width() / 2.0,
                    bar.get_height() + max(val * 0.02, 0.5),
                    f"{val:.0f}%",
                    ha="center", va="bottom", fontsize=_scaled_font_size(11), fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(qtypes)
    ax.set_xlabel("Query Type")
    ax.set_ylabel("Query Accuracy (%)")
    ax.set_ylim(0, 115)
    ax.yaxis.grid(linestyle="--", alpha=0.35)
    ax.set_axisbelow(True)
    _clean_axes(ax)

    handles = [mpatches.Patch(facecolor=BASELINE_COLORS[b],
                               edgecolor="#333333",
                               hatch=BASELINE_HATCHES.get(b),
                               label=_label(b))
               for b in baselines]
    ax.legend(handles=handles, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.22), frameon=False, fontsize=_scaled_font_size(11))

    fig.tight_layout()
    fig.savefig(str(out_path), dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out_path}")


# ── Table: Latency / Input tokens / Cost / Avg Accuracy — all baselines + ReAct
# [CHANGE 3] Added "Avg Accuracy (%)" column (mean of "All" query_type across datasets).
# Cost is displayed as ×10^-5 USD/query to avoid long decimals.
# Rows sorted in descending order by avg accuracy.

def build_efficiency_table(
    dataset_summaries: Dict[str, pd.DataFrame],
    react_summaries:   Dict[str, pd.DataFrame],
    out_csv: Path,
    out_md:  Path,
) -> pd.DataFrame:
    rows = []
    for bl in BASELINE_ORDER_ALL:
        lats, toks, costs, accs = [], [], [], []
        for ds in DATASET_ORDER:
            summary  = react_summaries[ds] if bl == "AUTOIOT_ONLY" else dataset_summaries[ds]
            lat,  _  = _get(summary, bl, "latency_s",       "All")
            tok,  _  = _get(summary, bl, "input_tokens",    "All")
            cost, _  = _get(summary, bl, "cost_usd",        "All")
            acc,  _  = _get(summary, bl, "accuracy_percent","All")  # [CHANGE 3]
            lats.append(lat); toks.append(tok); costs.append(cost); accs.append(acc)

        avg_acc = float(np.nanmean(accs))   # raw float for sorting
        rows.append({
            "System":             _label(bl),
            "Ablation":           "Yes" if bl == "AUTOIOT_ONLY" else "No",
            "Avg Accuracy (%)":   avg_acc,           # kept as float until after sort
            "Latency (s)":        _fmt_s(  float(np.nanmean(lats))),
            "Input Tokens":       _fmt_int(float(np.nanmean(toks))),
            r"Cost ($\times 10^{-5}$ USD/query)": _fmt_usd_e5(float(np.nanmean(costs))),
        })

    df = pd.DataFrame(rows)

    # [CHANGE 3] Sort descending by avg accuracy, then format the column
    df = df.sort_values("Avg Accuracy (%)", ascending=False).reset_index(drop=True)
    df["Avg Accuracy (%)"] = df["Avg Accuracy (%)"].map(_fmt_pct)

    df.to_csv(str(out_csv), index=False)

    # Markdown for easy LaTeX conversion
    lines = ["| " + " | ".join(df.columns) + " |",
             "|" + "|".join(["---"] * len(df.columns)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  Wrote {out_csv}")
    print(f"  Wrote {out_md}")
    return df


# ── Data loading ──────────────────────────────────────────────────────────────

def _build_dataset_frames(run_all_root, run_ecg_root, run_hargpt_root):
    bus   = _load_csv(run_all_root / "bus"   / "benchmark" / "metrics.csv", "bus")
    wisdm = _load_csv(run_all_root / "wisdm" / "benchmark" / "metrics.csv", "wisdm")

    bus_hargpt   = _load_csv(run_hargpt_root / "bus"   / "benchmark" / "metrics.csv", "bus")
    wisdm_hargpt = _load_csv(run_hargpt_root / "wisdm" / "benchmark" / "metrics.csv", "wisdm")
    bus   = _replace_rows(bus,   bus_hargpt,   "HARGPT_PAPER")
    wisdm = _replace_rows(wisdm, wisdm_hargpt, "HARGPT_PAPER")

    ecg_all    = _load_csv(run_all_root    / "mit_ecg"  / "benchmark" / "metrics.csv", "ecg")
    ecg_flash  = _load_csv(run_ecg_root    / "benchmark" / "metrics.csv",              "ecg")
    ecg_hargpt = _load_csv(run_hargpt_root / "mit_ecg"  / "benchmark" / "metrics.csv", "ecg")

    ecg_non_flash = _replace_rows(ecg_all[ecg_all["baseline"] != "FLASH_FUSION"],
                                  ecg_hargpt, "HARGPT_PAPER")
    ecg = pd.concat([ecg_non_flash, ecg_flash[ecg_flash["baseline"] == "FLASH_FUSION"]],
                    ignore_index=True)
    return {"bus": bus, "wisdm": wisdm, "ecg": ecg}


def _build_react_frames(run_react_root):
    return {
        "bus":   _load_csv(run_react_root / "bus"     / "benchmark" / "metrics.csv", "bus"),
        "wisdm": _load_csv(run_react_root / "wisdm"   / "benchmark" / "metrics.csv", "wisdm"),
        "ecg":   _load_csv(run_react_root / "mit_ecg" / "benchmark" / "metrics.csv", "ecg"),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser():
    p  = argparse.ArgumentParser(description="Flash-Fusion paper figures.")
    sd = Path(__file__).resolve().parent
    p.add_argument("--run-all-root",
                   default=str(sd / "data" / "run_all_remaining_20260528_215519"))
    p.add_argument("--run-ecg-root",
                   default=str(sd / "data" / "run_ecg_ff_20260529_115359"))
    p.add_argument("--run-react-root",
                   default=str(sd / "data" / "run_all_react_20260526_102138"))
    p.add_argument("--run-hargpt-root",
                   default=str(sd / "data" / "run_hargpt_20260603_111000"))
    p.add_argument("--output-root",
                   default=str(sd / "results"))
    p.add_argument("--overwrite", action="store_true")
    return p


def main():
    args       = _build_parser().parse_args()
    run_all    = Path(args.run_all_root).resolve()
    run_ecg    = Path(args.run_ecg_root).resolve()
    run_react  = Path(args.run_react_root).resolve()
    run_hargpt = Path(args.run_hargpt_root).resolve()
    out_root   = Path(args.output_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    paper_frames    = _build_dataset_frames(run_all, run_ecg, run_hargpt)
    react_frames    = _build_react_frames(run_react)

    print("Aggregating...")
    paper_summaries = {ds: _aggregate(df) for ds, df in paper_frames.items()}
    react_summaries = {ds: _aggregate(df) for ds, df in react_frames.items()}

    print("Figure 1: All-baseline accuracy by dataset...")
    for ext in ("pdf", "png"):
        plot_fig1_accuracy_by_dataset(
            paper_summaries, react_summaries,
            out_root / f"fig1_accuracy_by_dataset.{ext}",
        )

    print("Figure 2: Top-3 accuracy by query type...")
    for ext in ("pdf", "png"):
        plot_fig2_top3_by_query_type(
            paper_summaries, react_summaries,
            out_root / f"fig2_top3_accuracy_by_query_type.{ext}",
        )

    print("Efficiency table (latency / tokens / cost / avg accuracy)...")
    build_efficiency_table(
        paper_summaries, react_summaries,
        out_root / "table_efficiency.csv",
        out_root / "table_efficiency.md",
    )

    print(f"\nDone. Outputs in {out_root}")


if __name__ == "__main__":
    main()