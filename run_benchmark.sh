#!/usr/bin/env bash
# =============================================================================
# run_benchmark.sh
# End-to-end Flash-Fusion benchmark: 3 baselines × 12 WISDM queries
#   → integrated LLM judge  → visualizations  → console summary
#
# Usage:
#   export GROQ_API_KEY="gsk_..."
#   ./run_benchmark.sh
#
# Optional overrides (env vars):
#   WISDM_DATA    Path to raw WISDM .txt file
#                   (default: chat/data/imu/WISDM_ar_v1.1_raw.txt)
#   GROUND_TRUTH  Path to ground_truth.json
#                   (default: flashfusion/eval/ground_truth.json)
#   BASELINES     Comma-separated baselines
#                   (default: AUTOIOT_ONLY,WELLMAX_ONLY,FLASH_FUSION)
#   QUERIES       Comma-separated query IDs or "all"  (default: all)
#   MAX_LATENCY   Per-query timeout in seconds        (default: 90)
#   MODEL         Groq model override (empty = benchmark config default)
#
# Output layout (relative to repo root):
#   flashfusion/eval_results/runs/
#     run_YYYYMMDD_HHMMSS/
#       benchmark/                   ← raw benchmark output
#         metrics.csv                ← semantic scores, latency, cost, tokens
#         raw_results.jsonl
#         report.md
#         ground_truth_llm_judge/    ← LLM judge artefacts (auto-generated)
#           llm_judgments.csv
#           llm_judgments_summary.csv
#       per_baseline/                ← metrics.csv split per baseline
#         AUTOIOT_ONLY/metrics.csv
#         WELLMAX_ONLY/metrics.csv
#         FLASH_FUSION/metrics.csv
#       visuals/                     ← PNG charts + summary tables
#         accuracy_latency_cost_bars.png
#         token_usage_bars.png
#         llm_judge_bars.png
#         per_query_llm_scores.csv
#         baseline_summary.csv
#         baseline_summary.md
#         per_query_metrics.csv
#     latest -> run_YYYYMMDD_HHMMSS  ← symlink to most recent run
#
# Previous runs are never deleted — each timestamped directory is permanent.
# =============================================================================
set -euo pipefail

# ── Locate repo root ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Detect Python (prefer venv) ───────────────────────────────────────────────
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    PYTHON="python"
fi

# ── Configuration (overridable via env) ───────────────────────────────────────
WISDM_DATA="${WISDM_DATA:-chat/data/imu/WISDM_ar_v1.1_raw.txt}"
GROUND_TRUTH="${GROUND_TRUTH:-flashfusion/eval/ground_truth.json}"
BASELINES="${BASELINES:-AUTOIOT_ONLY,WELLMAX_ONLY,FLASH_FUSION}"
QUERIES="${QUERIES:-all}"
MAX_LATENCY="${MAX_LATENCY:-30.0}"
MODEL="${MODEL:-}"

# ── Validate pre-conditions ───────────────────────────────────────────────────
if [ -z "${GROQ_API_KEY:-}" ]; then
    echo "ERROR: GROQ_API_KEY is not set."
    echo "       Export it with:  export GROQ_API_KEY='gsk_...'"
    exit 1
fi
if [ ! -f "$WISDM_DATA" ]; then
    echo "ERROR: WISDM data file not found: $WISDM_DATA"
    exit 1
fi
if [ ! -f "$GROUND_TRUTH" ]; then
    echo "ERROR: Ground-truth file not found: $GROUND_TRUTH"
    exit 1
fi

if ! "$PYTHON" -c "import matplotlib" >/dev/null 2>&1; then
    echo "ERROR: matplotlib is not installed in the active Python environment."
    echo "       Install dependencies with one of:"
    echo "         $PYTHON -m pip install -r requirements.txt"
    echo "         $PYTHON -m pip install -e flashfusion/"
    exit 1
fi

# ── Directory layout ──────────────────────────────────────────────────────────
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUNS_BASE="flashfusion/eval_results/runs"
RUN_DIR="$RUNS_BASE/run_$TIMESTAMP"
BENCHMARK_DIR="$RUN_DIR/benchmark"
JUDGE_DIR="$BENCHMARK_DIR/ground_truth_llm_judge"
PER_BASELINE_DIR="$RUN_DIR/per_baseline"
VISUALS_DIR="$RUN_DIR/visuals"
LATEST_LINK="$RUNS_BASE/latest"

mkdir -p "$BENCHMARK_DIR" "$PER_BASELINE_DIR" "$VISUALS_DIR"

# ── Opening banner ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Flash-Fusion  —  End-to-End Benchmark"
echo "  Timestamp  : $TIMESTAMP"
echo "  Baselines  : $BASELINES"
echo "  Queries    : $QUERIES"
echo "  Max latency: ${MAX_LATENCY}s per query"
echo "  Output dir : $RUN_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Benchmark + integrated LLM judge ─────────────────────────────────
echo ""
echo "▶  [1/4]  Running benchmark…"
echo "          (--ground-truth-measurement both runs semantic scoring"
echo "           and the LLM judge together; judge output → $JUDGE_DIR)"
echo ""

# Build command as array so optional --model flag is handled cleanly
CMD=("$PYTHON" -m flashfusion.eval.benchmark
    --data          "$WISDM_DATA"
    --baselines     "$BASELINES"
    --queries       "$QUERIES"
    --ground-truth  "$GROUND_TRUTH"
    --ground-truth-measurement both
    --max-query-latency "$MAX_LATENCY"
    --output        "$BENCHMARK_DIR"
)
[ -n "$MODEL" ] && CMD+=(--model "$MODEL")

"${CMD[@]}"

echo ""
echo "  ✓  Benchmark complete → $BENCHMARK_DIR"

# ── Step 2: Split metrics.csv per baseline ────────────────────────────────────
echo ""
echo "▶  [2/4]  Splitting metrics per baseline…"

"$PYTHON" - "$BENCHMARK_DIR/metrics.csv" "$PER_BASELINE_DIR" <<'PYEOF'
import sys
import pandas as pd
from pathlib import Path

metrics_path = Path(sys.argv[1])
out_base = Path(sys.argv[2])

if not metrics_path.exists():
    print(f"  Warning: metrics file not found at {metrics_path}")
    sys.exit(0)

df = pd.read_csv(metrics_path)
if "baseline" not in df.columns:
    print("  Warning: metrics.csv has no 'baseline' column; skipping split.")
    sys.exit(0)

for baseline, grp in df.groupby("baseline"):
    dest = out_base / str(baseline)
    dest.mkdir(parents=True, exist_ok=True)
    grp.to_csv(dest / "metrics.csv", index=False)
    print(f"  Wrote {dest / 'metrics.csv'}  ({len(grp)} rows)")
PYEOF

echo "  ✓  Split complete"

# ── Step 3: Visualizations ────────────────────────────────────────────────────
echo ""
echo "▶  [3/4]  Generating visualizations…"

# 3a: Semantic score + latency + cost + token charts via existing module.
#     Only called when all three default baseline files are present.
WELLMAX_CSV="$PER_BASELINE_DIR/WELLMAX_ONLY/metrics.csv"
AUTOIOT_CSV="$PER_BASELINE_DIR/AUTOIOT_ONLY/metrics.csv"
FF_CSV="$PER_BASELINE_DIR/FLASH_FUSION/metrics.csv"

if [ -f "$WELLMAX_CSV" ] && [ -f "$AUTOIOT_CSV" ] && [ -f "$FF_CSV" ]; then
    "$PYTHON" -m flashfusion.eval.visualize_comparison \
        --wellmax    "$WELLMAX_CSV" \
        --autoiot    "$AUTOIOT_CSV" \
        --flashfusion "$FF_CSV" \
        --accuracy-column gt_score \
        --title "Baseline Comparison — $TIMESTAMP" \
        --output "$VISUALS_DIR"
    echo "  ✓  Semantic charts written"
else
    echo "  Warning: one or more per-baseline files missing; using combined metrics for charts."
    # Fallback: generate charts directly from the combined metrics.csv
    "$PYTHON" -m flashfusion.eval.visualize_comparison \
        --wellmax    "$BENCHMARK_DIR/metrics.csv" \
        --autoiot    "$BENCHMARK_DIR/metrics.csv" \
        --flashfusion "$BENCHMARK_DIR/metrics.csv" \
        --accuracy-column gt_score \
        --title "Baseline Comparison — $TIMESTAMP (combined)" \
        --output "$VISUALS_DIR" || true
fi

# 3b: LLM judge charts — avg score and verdict distribution.
#     Paths passed as positional args; heredoc is single-quoted (no shell expansion).
"$PYTHON" - "$JUDGE_DIR" "$VISUALS_DIR" "$TIMESTAMP" <<'PYEOF'
import sys
from pathlib import Path

judge_dir  = Path(sys.argv[1])
visuals_dir = Path(sys.argv[2])
timestamp  = sys.argv[3]

summary_path   = judge_dir / "llm_judgments_summary.csv"
judgments_path = judge_dir / "llm_judgments.csv"

if not summary_path.exists():
    print(f"  Warning: {summary_path} not found; skipping LLM judge charts.")
    sys.exit(0)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
except ImportError as exc:
    print(f"  Warning: {exc}; skipping LLM judge charts.")
    sys.exit(0)

df = pd.read_csv(summary_path)
if df.empty:
    print("  Warning: LLM judge summary is empty.")
    sys.exit(0)

PALETTE = {
    "AUTOIOT_ONLY": "#f4a259",
    "WELLMAX_ONLY":  "#136f63",
    "FLASH_FUSION":  "#2d6cdf",
}
colors = [PALETTE.get(str(b), "#999999") for b in df["baseline"]]

fig, (ax_score, ax_dist) = plt.subplots(1, 2, figsize=(13, 5.2))

# --- Avg LLM score bar ---
ax_score.bar(df["baseline"], df["avg_llm_score"], color=colors)
ax_score.set_title("LLM Judge — Avg Score (0–1)", fontweight="bold")
ax_score.set_ylabel("Score")
ax_score.set_ylim(0, 1.15)
ax_score.tick_params(axis="x", rotation=15)
for i, v in enumerate(df["avg_llm_score"]):
    ax_score.text(i, v + 0.025, f"{v:.3f}", ha="center", fontsize=10, fontweight="bold")

# --- Verdict distribution stacked bar ---
bar_x = list(range(len(df)))
pass_vals    = df["pass_rate"].tolist()
partial_vals = df["partial_rate"].tolist()
fail_vals    = df["fail_rate"].tolist()
bottom_partial = pass_vals
bottom_fail    = [p + q for p, q in zip(pass_vals, partial_vals)]

ax_dist.bar(bar_x, pass_vals,    label="PASS",    color="#4caf50")
ax_dist.bar(bar_x, partial_vals, bottom=bottom_partial, label="PARTIAL", color="#ff9800")
ax_dist.bar(bar_x, fail_vals,    bottom=bottom_fail,    label="FAIL",    color="#f44336")
ax_dist.set_xticks(bar_x)
ax_dist.set_xticklabels(df["baseline"].tolist())
ax_dist.set_title("LLM Judge — Verdict Distribution", fontweight="bold")
ax_dist.set_ylabel("Rate")
ax_dist.set_ylim(0, 1.1)
ax_dist.tick_params(axis="x", rotation=15)
ax_dist.legend(loc="upper right")

fig.suptitle(f"LLM Judge Results  —  {timestamp}", fontsize=13, fontweight="bold")
fig.tight_layout()

out_png = visuals_dir / "llm_judge_bars.png"
fig.savefig(out_png, dpi=180, bbox_inches="tight")
plt.close(fig)
print(f"  Wrote {out_png}")

# Per-query LLM scores CSV (convenience file for further analysis)
if judgments_path.exists():
    jdf = pd.read_csv(judgments_path)
    keep = [c for c in ["query_id", "baseline", "query_text", "llm_verdict", "llm_score", "llm_reason"]
            if c in jdf.columns]
    out_q = visuals_dir / "per_query_llm_scores.csv"
    jdf[keep].sort_values(["query_id", "baseline"]).to_csv(out_q, index=False)
    print(f"  Wrote {out_q}")
PYEOF

echo "  ✓  LLM judge charts written"

# ── Step 4: Console summary tables ───────────────────────────────────────────
echo ""
echo "▶  [4/4]  Summary"
echo ""

"$PYTHON" - "$BENCHMARK_DIR/metrics.csv" "$JUDGE_DIR/llm_judgments_summary.csv" <<'PYEOF'
import sys
from pathlib import Path
import pandas as pd

metrics_path      = Path(sys.argv[1])
judge_summary_path = Path(sys.argv[2])

DIVIDER = "─" * 74

def _tabulate(df, title):
    """Print df as a formatted table with optional tabulate dependency."""
    print(f"\n  {title}")
    print(f"  {DIVIDER}")
    try:
        from tabulate import tabulate
        print(tabulate(df, headers="keys", tablefmt="github",
                       showindex=False, floatfmt=".4f"))
    except ImportError:
        print(df.to_string(index=False))
    print()

# --- Semantic score summary ---
if metrics_path.exists():
    df = pd.read_csv(metrics_path)
    agg_cols = [c for c in ["gt_score", "latency_s", "cost_usd",
                             "input_tokens", "output_tokens"]
                if c in df.columns]
    summary = df.groupby("baseline")[agg_cols].mean().round(4).reset_index()
    if "input_tokens" in summary.columns and "output_tokens" in summary.columns:
        summary["total_tokens"] = (
            summary["input_tokens"] + summary["output_tokens"]
        ).astype(int)
    sort_col = "gt_score" if "gt_score" in summary.columns else agg_cols[0]
    summary = summary.sort_values(sort_col, ascending=False).reset_index(drop=True)
    _tabulate(summary, "Semantic Score Summary  (averages per baseline)")
else:
    print(f"  Warning: {metrics_path} not found; skipping semantic summary.")

# --- LLM judge summary ---
if judge_summary_path.exists():
    jsum = pd.read_csv(judge_summary_path).round(4)
    jsum = jsum.sort_values("avg_llm_score", ascending=False).reset_index(drop=True)
    _tabulate(jsum, "LLM Judge Summary  (averages per baseline)")
else:
    print(f"  Note: {judge_summary_path} not found; LLM judge may not have run.")
PYEOF

# ── Update "latest" symlink ───────────────────────────────────────────────────
# Target is relative to RUNS_BASE so the symlink is portable.
rm -f "$LATEST_LINK"
ln -s "run_$TIMESTAMP" "$LATEST_LINK"

# ── Closing banner ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Run complete!"
echo ""
echo "  Run directory  : $RUN_DIR"
echo "  Latest symlink : $LATEST_LINK  →  run_$TIMESTAMP"
echo ""
echo "  Key artefacts:"
printf "    %-22s %s\n" "Benchmark report:"  "$BENCHMARK_DIR/report.md"
printf "    %-22s %s\n" "Semantic metrics:"  "$BENCHMARK_DIR/metrics.csv"
printf "    %-22s %s\n" "LLM judge detail:"  "$JUDGE_DIR/llm_judgments.csv"
printf "    %-22s %s\n" "LLM judge summary:" "$JUDGE_DIR/llm_judgments_summary.csv"
printf "    %-22s %s\n" "Visualizations:"    "$VISUALS_DIR/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
