#!/usr/bin/env bash
# =============================================================================
# run_benchmark.sh
# End-to-end Flash-Fusion benchmark
#
# DEFAULT (no flags): runs all 3 datasets (wisdm, ecg, bus) sequentially and
# stores results under:
#   flashfusion/eval_results/runs/run_all_YYYYMMDD_HHMMSS/
#     wisdm/   ecg/   bus/         ← per-dataset subdirectories
#     visuals_all/                 ← cross-dataset balanced charts + tables
#
# Each per-dataset subdirectory contains the same layout as a single-dataset run:
#   benchmark/         metrics.csv, raw_results.jsonl, report.md,
#                      ground_truth_llm_judge/
#   per_baseline/      AUTOIOT_ONLY/metrics.csv  FLASH_FUSION/metrics.csv ...
#   visuals/           per-dataset PNG charts + CSV tables
#
# Cross-dataset visuals (visuals_all/) show balanced averages across all
# 3 datasets for each query type (Direct / Reasoning / Out-of-Scope + overall).
# Balanced means: mean of per-dataset means — no dataset dominates due to size.
#
# Single-dataset usage (produces run_{dataset}_YYYYMMDD_HHMMSS as before):
#   ./run_benchmark.sh --wisdm
#   ./run_benchmark.sh --ecg
#   ./run_benchmark.sh --bus
#
# CLI shortcuts:
#   --wisdm | --ecg | --bus           Choose a single dataset
#   --all                             Explicit all-datasets run (also the default)
#   --quick                           RUNS=1 and QUERIES=1,5,9 for a fast smoke pass
#   --help                            Show all options
#
# Optional overrides (env vars):
#   DATASET       wisdm | mit_ecg | bus | all   (default: all)
#   WISDM_DATA    Path to raw WISDM .txt file
#                   (default: chat/data/imu/WISDM_ar_v1.1_raw.txt)
#   MIT_ECG_DATA  Path to consolidated MIT ECG txt file
#                   (default: data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt)
#   BUS_DATA      Path to bus telemetry CSV file
#                   (default: data/bus/bus_data.csv)
#   GROUND_TRUTH  Override ground-truth JSON (single-dataset mode only)
#   BASELINES     Comma-separated baselines
#                   (default: AUTOIOT_ONLY,FLASH_FUSION)
#   QUERIES       Comma-separated query IDs or "all"  (default: all)
#   RUNS          Number of repeated benchmark runs      (default: 3)
#   MAX_LATENCY   Per-query timeout in seconds        (default: 30)
#   MODEL         Groq model override (empty = benchmark config default)
#
# Previous runs are never deleted — each timestamped directory is permanent.
# =============================================================================
#
# Output layout (relative to repo root):
#
# All-datasets run:
#   flashfusion/eval_results/runs/
#     run_all_YYYYMMDD_HHMMSS/
#       wisdm/benchmark/           ecg/benchmark/       bus/benchmark/
#       wisdm/per_baseline/        ecg/per_baseline/    bus/per_baseline/
#       wisdm/visuals/             ecg/visuals/         bus/visuals/
#       visuals_all/               ← cross-dataset charts (balanced avg)
#         cross_accuracy_by_query_type.png
#         cross_latency_by_query_type.png
#         cross_input_tokens_by_query_type.png
#         cross_output_tokens_by_query_type.png
#         cross_cost_by_query_type.png
#         cross_aggregate_summary.csv
#         cross_per_dataset_breakdown.csv
#         cross_*_by_query_type.{csv,md}  ← tables per metric
#     latest -> run_all_YYYYMMDD_HHMMSS
#
# Single-dataset run:
#   flashfusion/eval_results/runs/
#     run_{dataset}_YYYYMMDD_HHMMSS/
#       benchmark/  per_baseline/  visuals/
#     latest -> run_{dataset}_YYYYMMDD_HHMMSS
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
# Default to running all datasets when no --dataset flag is provided
DATASET="${DATASET:-all}"
WISDM_DATA="${WISDM_DATA:-chat/data/imu/WISDM_ar_v1.1_raw.txt}"
MIT_ECG_DATA="${MIT_ECG_DATA:-data/AutoIOT_dataset/ECG.0/MIT_arrythmia_v1.txt}"
BUS_DATA="${BUS_DATA:-data/bus/bus_data.csv}"
BASELINES="${BASELINES:-AUTOIOT_ONLY,FLASH_FUSION}"
QUERIES="${QUERIES:-all}"
RUNS="${RUNS:-3}"
MAX_LATENCY="${MAX_LATENCY:-30.0}"
MODEL="${MODEL:-}"
GROUND_TRUTH="${GROUND_TRUTH:-}"

print_help() {
    cat <<'EOF'
Usage (default — all datasets):
  ./run_benchmark.sh

Usage (single dataset):
  ./run_benchmark.sh --wisdm
  ./run_benchmark.sh --ecg
  ./run_benchmark.sh --bus

Options:
  --all                        Run all 3 datasets (default when no dataset flag given)
  --wisdm                      Set DATASET=wisdm
  --ecg                        Set DATASET=mit_ecg
  --bus                        Set DATASET=bus
  --dataset <name>             Dataset profile: wisdm | mit_ecg | bus | all
  --baselines <csv>            Baselines list (default AUTOIOT_ONLY,FLASH_FUSION)
  --queries <csv|all>          Query IDs, e.g. 1,5,9 or all
  --runs <n>                   Number of repeated runs
  --max-latency <seconds>      Per-query timeout
  --model <name>               Groq model override
  --ground-truth <path>        Override ground-truth JSON (single-dataset mode only)
  --quick                      Shortcut for RUNS=1 and QUERIES=1,5,9
  -h, --help                   Show this help message

Examples:
  ./run_benchmark.sh                        # all 3 datasets, full benchmark
  ./run_benchmark.sh --wisdm                # WISDM only
  ./run_benchmark.sh --bus --queries 1,2,3 --runs 1
  ./run_benchmark.sh --ecg --quick
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            DATASET="all"
            shift
            ;;
        --wisdm)
            DATASET="wisdm"
            shift
            ;;
        --ecg|--mit-ecg|--mit_ecg)
            DATASET="mit_ecg"
            shift
            ;;
        --bus)
            DATASET="bus"
            shift
            ;;
        --dataset)
            DATASET="${2:-}"
            shift 2
            ;;
        --baselines)
            BASELINES="${2:-}"
            shift 2
            ;;
        --queries)
            QUERIES="${2:-}"
            shift 2
            ;;
        --runs)
            RUNS="${2:-}"
            shift 2
            ;;
        --max-latency)
            MAX_LATENCY="${2:-}"
            shift 2
            ;;
        --model)
            MODEL="${2:-}"
            shift 2
            ;;
        --ground-truth)
            GROUND_TRUTH="${2:-}"
            shift 2
            ;;
        --quick)
            BASELINES="AUTOIOT_ONLY,FLASH_FUSION"
            RUNS="1"
            QUERIES="1,5,9"
            shift
            ;;
        -h|--help)
            print_help
            exit 0
            ;;
        *)
            echo "ERROR: Unknown option: $1"
            echo "Run ./run_benchmark.sh --help for usage."
            exit 1
            ;;
    esac
done

case "$DATASET" in
    all|wisdm|mit_ecg|bus)
        : # valid
        ;;
    *)
        echo "ERROR: Unsupported DATASET '$DATASET'. Use wisdm, mit_ecg, bus, or all."
        exit 1
        ;;
esac

# ── Validate common pre-conditions ───────────────────────────────────────────
if [ -z "${GROQ_API_KEY:-}" ]; then
    echo "ERROR: GROQ_API_KEY is not set."
    echo "       Export it with:  export GROQ_API_KEY='gsk_...'"
    exit 1
fi

if ! "$PYTHON" -c "import matplotlib" >/dev/null 2>&1; then
    echo "ERROR: matplotlib is not installed in the active Python environment."
    echo "       Install dependencies with one of:"
    echo "         $PYTHON -m pip install -r requirements.txt"
    echo "         $PYTHON -m pip install -e flashfusion/"
    exit 1
fi

# ── [DEBUG] Groq API connectivity probe ──────────────────────────────────────
# echo ""
# echo "▶  [DEBUG]  Probing Groq API connectivity (curl + Python)…"
# _GROQ_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
#     --max-time 10 \
#     -H "Authorization: Bearer ${GROQ_API_KEY}" \
#     "https://api.groq.com/openai/v1/models" 2>/dev/null || true)
# echo "  curl HTTP status for api.groq.com/models: ${_GROQ_HTTP_STATUS}"
# if [[ "$_GROQ_HTTP_STATUS" != "200" ]]; then
#     echo "  WARNING: Groq API probe returned non-200. Network or key issue likely."
# fi

# echo "  [curl] testing chat completion endpoint..."
# _GROQ_CHAT_STATUS=$(curl -s -o /tmp/flashfusion_groq_probe.json -w "%{http_code}" \
#     --max-time 20 \
#     -H "Authorization: Bearer ${GROQ_API_KEY}" \
#     -H "Content-Type: application/json" \
#     -d '{"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":"Reply with PING"}],"temperature":0}' \
#     "https://api.groq.com/openai/v1/chat/completions" 2>/dev/null || true)
# echo "  [curl] chat completion HTTP status: ${_GROQ_CHAT_STATUS}"
# if [[ -f /tmp/flashfusion_groq_probe.json ]]; then
#     echo "  [curl] response preview: $(head -c 160 /tmp/flashfusion_groq_probe.json | tr '\n' ' ')"
# fi

# "$PYTHON" - <<PYEOF
# import os, sys, time
# print("  [PY] Python version:", sys.version.split()[0], flush=True)
# try:
#     print("  [PY] importing langchain_groq...", flush=True)
#     from langchain_groq import ChatGroq
#     print("  [PY] langchain_groq imported OK", flush=True)
# except ImportError as e:
#     print(f"  [PY] ERROR: {e}", flush=True)
#     sys.exit(1)
# try:
#     print("  [PY] importing langchain_core pieces...", flush=True)
#     from langchain_core.prompts import ChatPromptTemplate
#     from langchain_core.output_parsers import StrOutputParser
#     print("  [PY] langchain_core imports OK", flush=True)
#     key = os.environ.get("GROQ_API_KEY","")
#     print("  [PY] constructing ChatGroq client...", flush=True)
#     llm = ChatGroq(model="llama-3.1-8b-instant", groq_api_key=key, temperature=0)
#     print("  [PY] ChatGroq client constructed", flush=True)
#     chain = ChatPromptTemplate.from_template("Say PING") | llm | StrOutputParser()
#     print("  [PY] invoking LangChain pipeline...", flush=True)
#     t0 = time.time()
#     resp = chain.invoke({})
#     latency = time.time() - t0
#     print(f"  [PY] Groq API ping OK  ({latency:.2f}s): {resp[:60]!r}", flush=True)
# except Exception as e:
#     print(f"  [PY] Groq API ping FAILED: {type(e).__name__}: {e}", flush=True)
# PYEOF
# echo ""

# ── Shared helpers ────────────────────────────────────────────────────────────
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUNS_BASE="flashfusion/eval_results/runs"
LATEST_LINK="$RUNS_BASE/latest"

# run_single_dataset DATASET DATA_PATH GROUND_TRUTH_PATH OUTPUT_DIR
# Runs steps 1-4 (benchmark → split → per-dataset visuals → summary)
# and returns the benchmark dir path via stdout.
run_single_dataset() {
    local ds="$1"
    local data_path="$2"
    local gt_path="$3"
    local ds_out="$4"

    local bench_dir="$ds_out/benchmark"
    local judge_dir="$bench_dir/ground_truth_llm_judge"
    local per_bl_dir="$ds_out/per_baseline"
    local vis_dir="$ds_out/visuals"
    mkdir -p "$bench_dir" "$per_bl_dir" "$vis_dir"

    echo ""
    echo "  ▷  [DS: $ds]  Step 1/3 — Running benchmark…"

    local cmd=("$PYTHON" -m flashfusion.eval.benchmark
        --data          "$data_path"
        --dataset       "$ds"
        --baselines     "$BASELINES"
        --queries       "$QUERIES"
        --runs          "$RUNS"
        --ground-truth  "$gt_path"
        --ground-truth-measurement llm
        --max-query-latency "$MAX_LATENCY"
        --output        "$bench_dir"
    )
    [ -n "$MODEL" ] && cmd+=(--model "$MODEL")
    "${cmd[@]}"
    echo "  ✓  [DS: $ds]  Benchmark → $bench_dir"

    echo ""
    echo "  ▷  [DS: $ds]  Step 2/3 — Splitting metrics per baseline…"
    "$PYTHON" - "$bench_dir/metrics.csv" "$per_bl_dir" <<'PYEOF'
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
    echo "  ✓  [DS: $ds]  Split complete"

    echo ""
    echo "  ▷  [DS: $ds]  Step 3/3 — Generating per-dataset visualizations…"

    "$PYTHON" -m flashfusion.eval.visualize_comparison \
        --metrics "$bench_dir/metrics.csv" \
        --dataset "$ds" \
        --accuracy-column gt_score \
        --title "Baseline Comparison ($ds)" \
        --output "$vis_dir"
    echo "  ✓  [DS: $ds]  Per-dataset charts → $vis_dir"

    # LLM judge summary chart
    "$PYTHON" - "$judge_dir" "$vis_dir" <<'PYEOF'
import sys
from pathlib import Path
judge_dir  = Path(sys.argv[1])
visuals_dir = Path(sys.argv[2])
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
PALETTE = {"AUTOIOT_ONLY": "#f4a259", "WELLMAX_ONLY": "#136f63", "FLASH_FUSION": "#2d6cdf"}
colors = [PALETTE.get(str(b), "#999999") for b in df["baseline"]]
fig, (ax_score, ax_dist) = plt.subplots(1, 2, figsize=(13, 5.2))
ax_score.bar(df["baseline"], df["pass_rate"], color=colors)
ax_score.set_title("LLM Judge — Pass Rate", fontweight="bold")
ax_score.set_ylabel("Rate")
ax_score.set_ylim(0, 1.15)
ax_score.tick_params(axis="x", rotation=15)
for i, v in enumerate(df["pass_rate"]):
    ax_score.text(i, v + 0.025, f"{v:.3f}", ha="center", fontsize=10, fontweight="bold")
bar_x = list(range(len(df)))
pass_vals = df["pass_rate"].tolist()
fail_vals = df["fail_rate"].tolist()
ax_dist.bar(bar_x, pass_vals, label="PASS", color="#4caf50")
ax_dist.bar(bar_x, fail_vals, bottom=pass_vals, label="FAIL", color="#f44336")
ax_dist.set_xticks(bar_x)
ax_dist.set_xticklabels(df["baseline"].tolist())
ax_dist.set_title("LLM Judge — Verdict Distribution", fontweight="bold")
ax_dist.set_ylabel("Rate")
ax_dist.set_ylim(0, 1.1)
ax_dist.tick_params(axis="x", rotation=15)
ax_dist.legend(loc="upper right")
fig.suptitle("LLM Judge Results", fontsize=13, fontweight="bold")
fig.tight_layout()
out_png = visuals_dir / "llm_judge_bars.png"
fig.savefig(out_png, dpi=180, bbox_inches="tight")
plt.close(fig)
print(f"  Wrote {out_png}")
if judgments_path.exists():
    jdf = pd.read_csv(judgments_path)
    keep = [c for c in ["query_id","baseline","query_text","llm_verdict","llm_score","llm_reason"] if c in jdf.columns]
    out_q = visuals_dir / "per_query_llm_scores.csv"
    jdf[keep].sort_values(["query_id","baseline"]).to_csv(out_q, index=False)
    print(f"  Wrote {out_q}")
PYEOF

    # Console summary for this dataset
    "$PYTHON" - "$bench_dir/metrics.csv" "$judge_dir/llm_judgments_summary.csv" "$ds" <<'PYEOF'
import sys
from pathlib import Path
import pandas as pd
metrics_path = Path(sys.argv[1])
judge_summary_path = Path(sys.argv[2])
ds_label = sys.argv[3]
DIVIDER = "─" * 70
def _tabulate(df, title):
    print(f"\n  {title}")
    print(f"  {DIVIDER}")
    try:
        from tabulate import tabulate
        print(tabulate(df, headers="keys", tablefmt="github", showindex=False, floatfmt=".4f"))
    except ImportError:
        print(df.to_string(index=False))
    print()
if metrics_path.exists():
    df = pd.read_csv(metrics_path)
    agg_cols = [c for c in ["gt_score","latency_s","cost_usd","input_tokens","output_tokens"] if c in df.columns]
    summary = df.groupby("baseline")[agg_cols].mean().round(4).reset_index()
    sort_col = "gt_score" if "gt_score" in summary.columns else agg_cols[0]
    summary = summary.sort_values(sort_col, ascending=False).reset_index(drop=True)
    _tabulate(summary, f"{ds_label.upper()} — LLM Accuracy Summary")
if judge_summary_path.exists():
    jsum = pd.read_csv(judge_summary_path).round(4)
    jsum = jsum.sort_values("pass_rate", ascending=False).reset_index(drop=True)
    _tabulate(jsum, f"{ds_label.upper()} — LLM Judge Summary")
PYEOF
}

# ── Opening banner ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Flash-Fusion  —  End-to-End Benchmark"
echo "  Timestamp  : $TIMESTAMP"
echo "  Baselines  : $BASELINES"
echo "  Queries    : $QUERIES"
echo "  Dataset    : $DATASET"
echo "  Runs       : $RUNS"
echo "  Max latency: ${MAX_LATENCY}s per query"
if [ "$DATASET" = "all" ]; then
    echo "  Mode       : All datasets → run_all_${TIMESTAMP}/"
    echo "               Cross-dataset balanced visuals → visuals_all/"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─────────────────────────────────────────────────────────────────────────────
# ALL-DATASETS MODE
# ─────────────────────────────────────────────────────────────────────────────
if [ "$DATASET" = "all" ]; then

    RUN_DIR="$RUNS_BASE/run_all_${TIMESTAMP}"
    VISUALS_ALL_DIR="$RUN_DIR/visuals_all"
    mkdir -p "$VISUALS_ALL_DIR"

    # Dataset data file paths
    DS_DATA_wisdm="$WISDM_DATA"
    DS_DATA_mit_ecg="$MIT_ECG_DATA"
    DS_DATA_bus="$BUS_DATA"

    # Dataset ground truth file paths
    DS_GT_wisdm="flashfusion/eval/ground_truth/ground_truth_wisdm.json"
    DS_GT_mit_ecg="flashfusion/eval/ground_truth/ground_truth_mit_ecg.json"
    DS_GT_bus="flashfusion/eval/ground_truth/ground_truth_bus.json"

    DS_LIST=(wisdm mit_ecg bus)

    # Validate all data + GT files before starting
    for ds in "${DS_LIST[@]}"; do
        DATA_VAR="DS_DATA_${ds//-/_}"
        GT_VAR="DS_GT_${ds//-/_}"
        dp="${!DATA_VAR}"
        gt="${!GT_VAR}"
        if [ ! -f "$dp" ]; then
            echo "ERROR: Data file for $ds not found: $dp"
            exit 1
        fi
        if [ ! -f "$gt" ]; then
            echo "ERROR: Ground-truth for $ds not found: $gt"
            exit 1
        fi
    done

    # Run each dataset and collect benchmark paths
    echo ""
    echo "▶  [1/${#DS_LIST[@]}×3+1]  Running all datasets…"
    STEP=1
    BENCH_WISDM=""
    BENCH_ECG=""
    BENCH_BUS=""
    for ds in "${DS_LIST[@]}"; do
        echo ""
        echo "━━━ Dataset ${STEP}/${#DS_LIST[@]}: $ds ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        DS_OUT_DIR="$RUN_DIR/$ds"
        DATA_VAR="DS_DATA_${ds//-/_}"
        GT_VAR="DS_GT_${ds//-/_}"
        run_single_dataset "$ds" "${!DATA_VAR}" "${!GT_VAR}" "$DS_OUT_DIR"
        
        # Store benchmark path in appropriate variable
        case "$ds" in
            wisdm)   BENCH_WISDM="$DS_OUT_DIR/benchmark" ;;
            mit_ecg) BENCH_ECG="$DS_OUT_DIR/benchmark" ;;
            bus)     BENCH_BUS="$DS_OUT_DIR/benchmark" ;;
        esac
        STEP=$(( STEP + 1 ))
    done

    # Cross-dataset visualization
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "▶  Cross-dataset balanced visualization…"
    echo "   Balanced mean: average of per-dataset means (no size-bias)"
    echo ""

    CROSS_CMD=("$PYTHON" -m flashfusion.eval.visualize_cross_dataset
        --wisdm-metrics "$BENCH_WISDM/metrics.csv"
        --ecg-metrics   "$BENCH_ECG/metrics.csv"
        --bus-metrics   "$BENCH_BUS/metrics.csv"
        --output        "$VISUALS_ALL_DIR"
        --accuracy-column gt_score
    )
    "${CROSS_CMD[@]}"
    echo "  ✓  Cross-dataset visuals → $VISUALS_ALL_DIR"

    # Update latest symlink
    rm -f "$LATEST_LINK"
    ln -s "run_all_${TIMESTAMP}" "$LATEST_LINK"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  All-datasets run complete!"
    echo ""
    echo "  Run directory  : $RUN_DIR"
    echo "  Latest symlink : $LATEST_LINK  →  run_all_${TIMESTAMP}"
    echo ""
    echo "  Per-dataset results:"
    for ds in "${DS_LIST[@]}"; do
        printf "    %-10s %s\n" "$ds" "$RUN_DIR/$ds/benchmark/metrics.csv"
    done
    echo ""
    echo "  Cross-dataset visuals:"
    printf "    %-30s %s\n" "Accuracy (balanced)" "$VISUALS_ALL_DIR/cross_accuracy_by_query_type.png"
    printf "    %-30s %s\n" "Latency (balanced)"  "$VISUALS_ALL_DIR/cross_latency_by_query_type.png"
    printf "    %-30s %s\n" "Aggregate summary"   "$VISUALS_ALL_DIR/cross_aggregate_summary.csv"
    printf "    %-30s %s\n" "Per-dataset detail"  "$VISUALS_ALL_DIR/cross_per_dataset_breakdown.csv"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-DATASET MODE (original behaviour, preserved exactly)
# ─────────────────────────────────────────────────────────────────────────────
case "$DATASET" in
    wisdm)
        DATA_PATH="$WISDM_DATA"
        DEFAULT_GROUND_TRUTH="flashfusion/eval/ground_truth/ground_truth_wisdm.json"
        RUN_PREFIX="run_wisdm"
        ;;
    mit_ecg)
        DATA_PATH="$MIT_ECG_DATA"
        DEFAULT_GROUND_TRUTH="flashfusion/eval/ground_truth/ground_truth_mit_ecg.json"
        RUN_PREFIX="run_ecg"
        ;;
    bus)
        DATA_PATH="$BUS_DATA"
        DEFAULT_GROUND_TRUTH="flashfusion/eval/ground_truth/ground_truth_bus.json"
        RUN_PREFIX="run_bus"
        ;;
esac

GROUND_TRUTH="${GROUND_TRUTH:-$DEFAULT_GROUND_TRUTH}"

if [ ! -f "$DATA_PATH" ]; then
    echo "ERROR: Dataset file not found for DATASET=$DATASET: $DATA_PATH"
    exit 1
fi
if [ ! -f "$GROUND_TRUTH" ]; then
    echo "ERROR: Ground-truth file not found: $GROUND_TRUTH"
    exit 1
fi

RUN_DIR="$RUNS_BASE/${RUN_PREFIX}_${TIMESTAMP}"
BENCHMARK_DIR="$RUN_DIR/benchmark"
JUDGE_DIR="$BENCHMARK_DIR/ground_truth_llm_judge"
PER_BASELINE_DIR="$RUN_DIR/per_baseline"
VISUALS_DIR="$RUN_DIR/visuals"

mkdir -p "$BENCHMARK_DIR" "$PER_BASELINE_DIR" "$VISUALS_DIR"

echo ""
echo "  Data path  : $DATA_PATH"
echo "  Output dir : $RUN_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Benchmark + integrated LLM judge ─────────────────────────────────
echo ""
echo "▶  [1/4]  Running benchmark…"
echo "          (--ground-truth-measurement llm uses LLM-verdict scoring;"
echo "           LLM judge artefacts written to $JUDGE_DIR)"
echo ""

# Build command as array so optional --model flag is handled cleanly
CMD=("$PYTHON" -m flashfusion.eval.benchmark
    --data          "$DATA_PATH"
    --dataset       "$DATASET"
    --baselines     "$BASELINES"
    --queries       "$QUERIES"
    --runs          "$RUNS"
    --ground-truth  "$GROUND_TRUTH"
    --ground-truth-measurement llm
    --max-query-latency "$MAX_LATENCY"
    --output        "$BENCHMARK_DIR"
)
[ -n "$MODEL" ] && CMD+=(--model "$MODEL")

# _STEP1_START=$(date +%s)
# echo "  [DEBUG] Step 1 started at $(date)"
"${CMD[@]}"
# _STEP1_END=$(date +%s)
# echo "  [DEBUG] Step 1 finished at $(date)  ($(( _STEP1_END - _STEP1_START ))s)"

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

# 3a: Baseline comparison charts/tables grouped by query type.
"$PYTHON" -m flashfusion.eval.visualize_comparison \
    --metrics "$BENCHMARK_DIR/metrics.csv" \
    --dataset "$DATASET" \
    --accuracy-column gt_score \
    --title "Baseline Comparison" \
    --output "$VISUALS_DIR"
echo "  ✓  Baseline comparison charts written"

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

# --- Pass rate bar ---
ax_score.bar(df["baseline"], df["pass_rate"], color=colors)
ax_score.set_title("LLM Judge — Pass Rate", fontweight="bold")
ax_score.set_ylabel("Rate")
ax_score.set_ylim(0, 1.15)
ax_score.tick_params(axis="x", rotation=15)
for i, v in enumerate(df["pass_rate"]):
    ax_score.text(i, v + 0.025, f"{v:.3f}", ha="center", fontsize=10, fontweight="bold")

# --- Verdict distribution stacked bar ---
bar_x = list(range(len(df)))
pass_vals    = df["pass_rate"].tolist()
fail_vals    = df["fail_rate"].tolist()

ax_dist.bar(bar_x, pass_vals, label="PASS", color="#4caf50")
ax_dist.bar(bar_x, fail_vals, bottom=pass_vals, label="FAIL", color="#f44336")
ax_dist.set_xticks(bar_x)
ax_dist.set_xticklabels(df["baseline"].tolist())
ax_dist.set_title("LLM Judge — Verdict Distribution", fontweight="bold")
ax_dist.set_ylabel("Rate")
ax_dist.set_ylim(0, 1.1)
ax_dist.tick_params(axis="x", rotation=15)
ax_dist.legend(loc="upper right")

fig.suptitle(f"LLM Judge Results", fontsize=13, fontweight="bold")
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

# --- LLM verdict accuracy summary ---
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
    _tabulate(summary, "LLM Verdict Accuracy Summary  (averages per baseline)")
else:
    print(f"  Warning: {metrics_path} not found; skipping LLM accuracy summary.")

# --- LLM judge summary ---
if judge_summary_path.exists():
    jsum = pd.read_csv(judge_summary_path).round(4)
    jsum = jsum.sort_values("pass_rate", ascending=False).reset_index(drop=True)
    _tabulate(jsum, "LLM Judge Summary  (averages per baseline)")
else:
    print(f"  Note: {judge_summary_path} not found; LLM judge may not have run.")
PYEOF

# ── Update "latest" symlink ───────────────────────────────────────────────────
# Target is relative to RUNS_BASE so the symlink is portable.
rm -f "$LATEST_LINK"
ln -s "${RUN_PREFIX}_${TIMESTAMP}" "$LATEST_LINK"

# ── Closing banner ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Run complete!"
echo ""
echo "  Run directory  : $RUN_DIR"
echo "  Latest symlink : $LATEST_LINK  →  ${RUN_PREFIX}_${TIMESTAMP}"
echo ""
echo "  Key artefacts:"
printf "    %-22s %s\n" "Benchmark report:"  "$BENCHMARK_DIR/report.md"
printf "    %-22s %s\n" "LLM metrics:"       "$BENCHMARK_DIR/metrics.csv"
printf "    %-22s %s\n" "LLM judge detail:"  "$JUDGE_DIR/llm_judgments.csv"
printf "    %-22s %s\n" "LLM judge summary:" "$JUDGE_DIR/llm_judgments_summary.csv"
printf "    %-22s %s\n" "Visualizations:"    "$VISUALS_DIR/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
