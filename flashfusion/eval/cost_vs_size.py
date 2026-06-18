"""
Cost vs. Dataset Size

- Bus   = 2,000 rows
- WISDM = 2.2M rows
- ECG   = 20M rows

Flash-Fusion costs are empirical from the benchmark table.
LLM-Only is shown as a simple linear estimate.
Flash-Fusion is extrapolated beyond ECG with a dashed blue line.
"""

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

# ---------------------------------------------------------------------
# Empirical data
# ---------------------------------------------------------------------
datasets = ["Bus", "WISDM", "ECG"]
rows = np.array([2_000, 2_200_000, 20_000_000], dtype=float)

flash_fusion_cost = np.array([0.000592, 0.000577, 0.000562], dtype=float)
llmsense_cost = np.array([0.000495, 0.011964, 0.138886], dtype=float)

# ---------------------------------------------------------------------
# LLM-Only estimate
# ---------------------------------------------------------------------
# Anchor the estimate using Bus and ECG proxy points.
x0, y0 = rows[0], llmsense_cost[0]
x1, y1 = rows[2], llmsense_cost[2]

slope = (y1 - y0) / (x1 - x0)
intercept = y0 - slope * x0

def llm_only_estimate(x_rows):
    return intercept + slope * np.asarray(x_rows)

# ---------------------------------------------------------------------
# Flash-Fusion extrapolation
# ---------------------------------------------------------------------
# Use the average measured FF cost as the extrapolated level.
ff_level = float(np.mean(flash_fusion_cost))

target_cost = 30.0
target_rows = (target_cost - intercept) / slope
x_max = 10 ** np.ceil(np.log10(target_rows))

x_llm = np.logspace(np.log10(rows.min()), np.log10(x_max), 500)
y_llm = llm_only_estimate(x_llm)

x_ff_measured = rows
y_ff_measured = flash_fusion_cost

x_ff_extra = np.logspace(np.log10(rows[-1]), np.log10(x_max), 200)
y_ff_extra = np.full_like(x_ff_extra, ff_level)

# ---------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------
FF_COLOR = "#2f8f57"
LLM_COLOR = "#d62728"
FONT_SCALE = 1.25

RC = {
    "font.family": "DejaVu Sans",
    "font.size": 5.25 * FONT_SCALE,
    "axes.labelsize": 6.75 * FONT_SCALE,
    "axes.labelweight": "bold",
    "xtick.labelsize": 6 * FONT_SCALE,
    "ytick.labelsize": 6 * FONT_SCALE,
    "legend.fontsize": 6.75 * FONT_SCALE,
    "legend.title_fontsize": 6.375 * FONT_SCALE,
}

plt.rcParams.update(RC)

def _scaled_font_size(size: float) -> float:
    return size * FONT_SCALE

def usd_fmt(y, _):
    if y >= 1:
        return f"${y:.0f}"
    if y >= 0.01:
        return f"${y:.2f}"
    if y >= 0.001:
        return f"${y:.3f}"
    return f"${y:.4f}"

def rows_fmt(x, _):
    if x >= 1_000_000_000:
        return f"{x/1_000_000_000:.0f}B"
    if x >= 1_000_000:
        val = x / 1_000_000
        return f"{val:.0f}M" if abs(val - round(val)) < 1e-9 else f"{val:.1f}M"
    if x >= 1_000:
        val = x / 1_000
        return f"{val:.0f}K" if abs(val - round(val)) < 1e-9 else f"{val:.1f}K"
    return f"{int(x)}"

# ---------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(3.45, 2.45), constrained_layout=True)

# LLM-Only
ax.plot(
    x_llm,
    y_llm,
    color=LLM_COLOR,
    linestyle="--",
    linewidth=2.0,
    label="LLM-Only"
)

# Flash-Fusion measured
ax.plot(
    x_ff_measured,
    y_ff_measured,
    color=FF_COLOR,
    marker="o",
    markersize=4.2,
    linewidth=2.0,
    label="Flash-Fusion"
)

# Flash-Fusion extrapolated
ax.plot(
    x_ff_extra,
    y_ff_extra,
    color=FF_COLOR,
    linestyle="--",
    linewidth=1.8,
    alpha=0.95
)

# Label measured datasets
for name, x, y in zip(datasets, rows, flash_fusion_cost):
    x_offset = 10 if name == "Bus" else 0

    ax.annotate(
        name,
        (x, y),
        textcoords="offset points",
        xytext=(x_offset, 5),
        ha="center",
        fontsize=_scaled_font_size(6.5)
    )

# Axes
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel("Dataset size (rows)")
ax.set_ylabel("Cost per query (USD)")

ax.set_xlim(rows.min(), x_max)
ax.set_ylim(3e-4, 4e1)

ax.xaxis.set_major_locator(LogLocator(base=10))
ax.yaxis.set_major_locator(LogLocator(base=10))
ax.xaxis.set_minor_formatter(NullFormatter())
ax.yaxis.set_minor_formatter(NullFormatter())

ax.xaxis.set_major_formatter(FuncFormatter(rows_fmt))
ax.yaxis.set_major_formatter(FuncFormatter(usd_fmt))

ax.grid(True, which="major", linestyle="--", alpha=0.25)
ax.legend(loc="upper left", frameon=False, handlelength=2.2)

# ---------------------------------------------------------------------
# Save beside this script
# ---------------------------------------------------------------------
script_dir = Path(__file__).resolve().parent
png_path = script_dir / "cost_vs_dataset_size.png"
pdf_path = script_dir / "cost_vs_dataset_size.pdf"

fig.savefig(pdf_path, bbox_inches="tight")
fig.savefig(png_path, dpi=300, bbox_inches="tight")
plt.close(fig)

print(f"Saved: {pdf_path}")
print(f"Saved: {png_path}")