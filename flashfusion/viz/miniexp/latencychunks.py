"""Mini-experiment: OpenRouter latency vs chunk count.

This script benchmarks a direct single-call prompt (no multi-stage Flash-Fusion)
to isolate context-length effects on:
- TTFT: time to first streamed token
- TTLT: time to last streamed token

Data chunks are built to an approximate fixed size (default ~500 tokens/chunk).
The plot includes dashed context-window markers for Llama-3.3-70B (128k tokens),
which should be crossed near 256 chunks at 500 tokens/chunk.

Terminal command (ECG):
  /Users/kausar/Documents/backups/flash-fusion/.venv/bin/python -m flashfusion.miniexp.latencychunks \
    --dataset ecg \
    --data-path /Users/kausar/Documents/backups/flash-fusion/data/AutoIOT_dataset/ECG.0/tmp_csv/100.csv \
    --chunks 50 100 150 200 250 300 350 400 450 500 \
    --chunk-token-target 200 \
    --output-dir flashfusion/miniexp/results \
    --plot

Plot from existing CSV:
    python -m flashfusion.miniexp.latencychunks \
        --plot --ttft-only \
        --csv-input flashfusion/miniexp/results_new/latency_vs_chunks.csv \
        --output-dir flashfusion/miniexp/results_new \
        --actual-token-ratio 1.9
"""

## DETAILS: setting to 200 tokens (estimate)/chunk; context window gets exhausted at the (300,350] mark.

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import math
from langchain_core.messages import HumanMessage
from langchain_openrouter import ChatOpenRouter

from flashfusion.config import DEFAULT_MODEL, TOKEN_ESTIMATE_MULTIPLIER
from flashfusion.pipeline.loader import load_dataset_by_name

DEFAULT_CHUNK_COUNTS = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500]
DEFAULT_QUERY = "What is the average MLII observed in this dataset?"
DEFAULT_CHUNK_TOKEN_TARGET = 200  # chars/4 ≈ actual tokens; 200 actual tokens/chunk
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_DATA_PATHS = {
    "bus": "data/bus/bus_data.csv",
}


def estimate_tokens(text: str) -> int:
    """Approximate token count using character-based heuristic (chars / 4).

    Word-splitting severely undercounts numeric CSV data because comma-separated
    numbers don't split on whitespace.  The chars/4 rule is a standard LLM
    approximation and stays within ~10-15% of tiktoken counts for mixed text.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def resolve_data_path(dataset_name: str, data_path: str | None) -> Path:
    """Resolve the on-disk dataset file path from CLI inputs."""
    if data_path:
        candidate = Path(data_path)
        if candidate.is_dir():
            if dataset_name == "bus":
                candidate = candidate / "bus_data.csv"
            else:
                raise ValueError(
                    f"Directory path is only auto-resolved for bus dataset, got {dataset_name!r}."
                )
        if not candidate.exists():
            raise FileNotFoundError(f"Dataset path not found: {candidate}")
        return candidate

    rel_default = DEFAULT_DATA_PATHS.get(dataset_name)
    if not rel_default:
        # For ad-hoc datasets (e.g. ecg), a --data-path must be supplied explicitly.
        raise ValueError(
            f"No default data path configured for dataset {dataset_name!r}; pass --data-path."
        )
    repo_root = Path(__file__).resolve().parents[2]
    resolved = repo_root / rel_default
    if not resolved.exists():
        raise FileNotFoundError(f"Default dataset path not found: {resolved}")
    return resolved


def build_token_chunks(df: pd.DataFrame, target_chunk_tokens: int) -> list[str]:
    """Build CSV chunks with approximately target_chunk_tokens per chunk."""
    if target_chunk_tokens <= 0:
        raise ValueError("target_chunk_tokens must be > 0")

    csv_text = df.to_csv(index=False)
    lines = csv_text.splitlines()
    if len(lines) <= 1:
        return []

    header = lines[0]
    row_lines = lines[1:]
    header_tokens = estimate_tokens(header)
    chunks: list[str] = []
    current_rows: list[str] = []
    current_tokens = header_tokens

    for row in row_lines:
        row_tokens = estimate_tokens(row)
        # If adding this row overshoots target and we already have content,
        # flush current chunk first.
        if current_rows and current_tokens + row_tokens > target_chunk_tokens:
            chunks.append(header + "\n" + "\n".join(current_rows))
            current_rows = []
            current_tokens = header_tokens

        current_rows.append(row)
        current_tokens += row_tokens

    if current_rows:
        chunks.append(header + "\n" + "\n".join(current_rows))

    return chunks


def build_prompt(query: str, chunk_texts: list[str]) -> str:
    """Build a deterministic direct prompt for single-call latency measurement."""
    chunk_blocks = "\n\n".join(
        f"### CHUNK {i + 1}\n{chunk}" for i, chunk in enumerate(chunk_texts)
    )
    return (
        "You are a data analyst. Use ONLY the provided CSV chunks to answer the query.\n"
        "If the answer is not inferable from the data, respond with UNKNOWN.\n"
        "Keep the response short and deterministic.\n\n"
        f"Query: {query}\n\n"
        "CSV data:\n"
        f"{chunk_blocks}\n\n"
        "Output format: MAX_ACCEL_VARIANCE=<number>"
    )


def _stream_chunk_to_text(chunk: Any) -> str:
    """Normalize streamed chunk content into plain text."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def measure_streaming_latency(
    llm: ChatOpenRouter,
    prompt: str,
) -> tuple[float, float, str]:
    """Measure true TTFT and TTLT from a streaming LLM response."""
    start_s = time.perf_counter()
    first_token_s: float | None = None
    output_parts: list[str] = []

    for chunk in llm.stream([HumanMessage(content=prompt)]):
        text = _stream_chunk_to_text(chunk)
        if text:
            if first_token_s is None:
                first_token_s = time.perf_counter()
            output_parts.append(text)

    end_s = time.perf_counter()
    ttft_ms = ((first_token_s if first_token_s is not None else end_s) - start_s) * 1000
    ttlt_ms = (end_s - start_s) * 1000
    return ttft_ms, ttlt_ms, "".join(output_parts).strip()


def run_experiment(
    dataset_name: str = "bus",
    data_path: str | None = None,
    chunk_counts: list[int] | None = None,
    model: str = DEFAULT_MODEL,
    output_dir: str = "flashfusion/miniexp/results",
    query: str = DEFAULT_QUERY,
    chunk_token_target: int = DEFAULT_CHUNK_TOKEN_TARGET,
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    retries: int = 0,
    dry_run: bool = False,
) -> pd.DataFrame:
    """Run direct single-call OpenRouter latency measurements across chunk counts."""
    if chunk_counts is None:
        chunk_counts = DEFAULT_CHUNK_COUNTS
    chunk_counts = sorted({c for c in chunk_counts if c > 0})
    if not chunk_counts:
        raise ValueError("chunk_counts must contain at least one positive integer")

    resolved_data_path = resolve_data_path(dataset_name, data_path)
    # Load directly from CSV when given a plain CSV file, bypassing typed loaders.
    if str(resolved_data_path).endswith(".csv") and dataset_name not in ("bus", "wisdm", "mit_ecg"):
        df = pd.read_csv(str(resolved_data_path))
    else:
        df = load_dataset_by_name(str(resolved_data_path), dataset_name)

    chunks = build_token_chunks(df, target_chunk_tokens=chunk_token_target)
    if not chunks:
        raise ValueError("Chunk builder produced no chunks; dataset may be empty.")

    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
    if not dry_run and not api_key:
        raise ValueError("OPENROUTER_API_KEY or GROQ_API_KEY environment variable required")

    llm = ChatOpenRouter(model=model, api_key=api_key, temperature=0, max_retries=2) if not dry_run else None
    prompt_overhead_tokens = estimate_tokens(build_prompt(query=query, chunk_texts=[]))

    print(f"Loaded dataset: {resolved_data_path}")
    print(f"Rows: {len(df):,} | built chunks: {len(chunks):,} at ~{chunk_token_target} tokens/chunk")

    results: list[dict[str, Any]] = []
    for requested_chunks in chunk_counts:
        row: dict[str, Any] = {
            "chunk_count": requested_chunks,
            "effective_chunks": requested_chunks,
            "chunk_token_target": chunk_token_target,
            "context_window_tokens": context_window_tokens,
            "query": query,
            "model": model,
            "success": False,
            "error": "",
            "ttft_ms": None,
            "ttlt_ms": None,
            "context_tokens_est": None,
            "data_tokens_est": None,
            "prompt_overhead_tokens_est": prompt_overhead_tokens,
            "context_pct_window": None,
            "crosses_context_window": None,
            "answer": "",
        }

        if requested_chunks > len(chunks):
            row["error"] = (
                f"Requested {requested_chunks} chunks but only {len(chunks)} available from dataset."
            )
            results.append(row)
            print(f"Chunks={requested_chunks}: skipped ({row['error']})")
            continue

        selected_chunks = chunks[:requested_chunks]
        prompt = build_prompt(query=query, chunk_texts=selected_chunks)
        context_tokens_est = estimate_tokens(prompt)
        data_tokens_est = max(0, context_tokens_est - prompt_overhead_tokens)
        context_pct_window = (context_tokens_est / context_window_tokens) * 100
        crosses_context_window = context_tokens_est >= context_window_tokens

        row["context_tokens_est"] = context_tokens_est
        row["data_tokens_est"] = data_tokens_est
        row["context_pct_window"] = context_pct_window
        row["crosses_context_window"] = crosses_context_window

        if dry_run:
            row["success"] = True
            results.append(row)
            print(
                f"Chunks={requested_chunks}: dry-run | context~{context_tokens_est:,} "
                f"({context_pct_window:.1f}% of window)"
            )
            continue

        assert llm is not None
        last_error = ""
        for attempt in range(retries + 1):
            try:
                ttft_ms, ttlt_ms, answer = measure_streaming_latency(llm=llm, prompt=prompt)
                row["ttft_ms"] = ttft_ms
                row["ttlt_ms"] = ttlt_ms
                row["answer"] = answer
                row["success"] = True
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < retries:
                    print(
                        f"Chunks={requested_chunks}: attempt {attempt + 1} failed, retrying... {last_error}"
                    )

        row["error"] = last_error
        results.append(row)
        if row["success"]:
            print(
                f"Chunks={requested_chunks}: TTFT={row['ttft_ms']:.2f}ms, "
                f"TTLT={row['ttlt_ms']:.2f}ms, "
                f"context~{context_tokens_est:,} ({context_pct_window:.1f}% of window)"
            )
        else:
            print(f"Chunks={requested_chunks}: failed ({row['error']})")

    results_df = pd.DataFrame(results)
    os.makedirs(output_dir, exist_ok=True)
    out_csv = Path(output_dir) / "latency_vs_chunks.csv"
    results_df.to_csv(out_csv, index=False)
    print(f"\nResults saved to {out_csv}")
    return results_df


def average_runs(csv_paths: list[str]) -> pd.DataFrame:
    """Load multiple per-run CSVs and return a DataFrame with ttft/ttlt averaged per chunk_count."""
    dfs: list[pd.DataFrame] = []
    for i, p in enumerate(csv_paths, 1):
        df = pd.read_csv(p)
        df["run_id"] = i
        df["source_csv"] = p
        dfs.append(df)
    combined = pd.concat(dfs, ignore_index=True)
    for field in ["chunk_token_target", "context_window_tokens", "query", "model"]:
        unique_values = combined[field].dropna().unique().tolist()
        if len(unique_values) > 1:
            raise ValueError(
                f"Cannot average runs with different {field} values: {unique_values}. "
                "Regenerate or filter the inputs so all runs use the same settings."
            )
    # Keep only successful rows for averaging
    success = combined[combined["success"] == True].copy()  # noqa: E712
    if success.empty:
        raise ValueError("No successful rows found across provided CSVs.")
    agg = (
        success.groupby("chunk_count", sort=True)
        .agg(
            effective_chunks=("effective_chunks", "first"),
            chunk_token_target=("chunk_token_target", "first"),
            context_window_tokens=("context_window_tokens", "first"),
            query=("query", "first"),
            model=("model", "first"),
            success=("success", "first"),
            error=("error", "first"),
            ttft_ms=("ttft_ms", "mean"),
            ttft_ms_std=("ttft_ms", "std"),
            ttlt_ms=("ttlt_ms", "mean"),
            ttlt_ms_std=("ttlt_ms", "std"),
            context_tokens_est=("context_tokens_est", "first"),
            data_tokens_est=("data_tokens_est", "first"),
            prompt_overhead_tokens_est=("prompt_overhead_tokens_est", "first"),
            context_pct_window=("context_pct_window", "first"),
            crosses_context_window=("crosses_context_window", "first"),
            run_count=("run_id", "count"),
        )
        .reset_index()
    )
    return agg


def plot_results(
    results_df: pd.DataFrame,
    output_dir: str = "flashfusion/miniexp/results",
    chunk_token_target: int = DEFAULT_CHUNK_TOKEN_TARGET,
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    ttft_only: bool = False,
    actual_token_ratio: float = 1.9,
    max_chunks: int | None = None,
    total_rows: int | None = None,
    rows_per_chunk: int | None = None,
) -> None:
    """Plot TTFT (and optionally TTLT) on a single graph with publication-quality styling.

    Pass total_rows and rows_per_chunk together to replace the x-axis with
    "% of dataset" instead of raw chunk count.

    For ECG 101.csv (65 000 rows) at chunk_token_target=200, rows_per_chunk=21:
      x% = chunk_count * 21 / 65000 * 100
    This answers: at N chunks, what percentage of the ECG dataset was in context?
    """
    success_df = results_df[results_df["success"] == True].copy()  # noqa: E712
    if success_df.empty:
        print("No successful rows to plot.")
        return

    success_df = success_df.sort_values("chunk_count")
    if max_chunks is not None:
        success_df = success_df[success_df["chunk_count"] <= max_chunks]
    if success_df.empty:
        print("No successful rows remain after applying max_chunks filter.")
        return

    # Whether to show % of dataset on the x-axis instead of raw chunk count.
    use_pct_axis = (total_rows is not None) and (rows_per_chunk is not None) and total_rows > 0

    def _to_pct(chunks: Any) -> Any:
        return chunks * rows_per_chunk / total_rows * 100  # type: ignore[operator]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "figure.facecolor": "#ffffff",
            "axes.facecolor": "#ffffff",
            "axes.edgecolor": "#222222",
            "axes.linewidth": 1.8,
            "axes.titlesize": 22,
            "axes.titleweight": "bold",
            "axes.labelsize": 17,
            "axes.labelweight": "bold",
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 16,
            "legend.title_fontsize": 16,
            "grid.alpha": 0.55,
            "grid.color": "#cccccc",
            "grid.linewidth": 1.0,
        }
    )

    fig, ax = plt.subplots(figsize=(11, 6.5))

    if use_pct_axis:
        x_values = _to_pct(success_df["chunk_count"])
    else:
        x_values = success_df["chunk_count"]
    ttft_mean_s = success_df["ttft_ms"] / 1000
    ttft_std_s = (
        success_df["ttft_ms_std"].fillna(0) / 1000
        if "ttft_ms_std" in success_df.columns
        else None
    )
    ax.plot(
        x_values,
        ttft_mean_s,
        marker="o",
        color="#2f6ad9",
        linewidth=2.2,
        markersize=7,
        alpha=0.9,
        label="TTFT (Time To First Token)",
    )
    if ttft_std_s is not None:
        ax.fill_between(
            x_values,
            ttft_mean_s - ttft_std_s,
            ttft_mean_s + ttft_std_s,
            color="#2f6ad9",
            alpha=0.18,
            linewidth=0,
            label="TTFT ±1 SD",
        )
    if not ttft_only:
        ttlt_mean_s = success_df["ttlt_ms"] / 1000
        ttlt_std_s = (
            success_df["ttlt_ms_std"].fillna(0) / 1000
            if "ttlt_ms_std" in success_df.columns
            else None
        )
        ax.plot(
            x_values,
            ttlt_mean_s,
            marker="s",
            color="#f28e2b",
            linewidth=2.2,
            markersize=7,
            alpha=0.9,
            label="TTLT (Time To Last Token)",
        )
        if ttlt_std_s is not None:
            ax.fill_between(
                x_values,
                ttlt_mean_s - ttlt_std_s,
                ttlt_mean_s + ttlt_std_s,
                color="#f28e2b",
                alpha=0.14,
                linewidth=0,
                label="TTLT ±1 SD",
            )

    ax.set_xlabel("Fraction of ECG Data (rounded %)" if use_pct_axis else "Number of Chunks")
    ax.set_ylabel("Time to First Token (s)" if ttft_only else "Latency (s)")
    # ax.set_title("Time To First Token vs. Number of Chunks" if ttft_only else "Latency vs. Number of Chunks", pad=16)
    ax.grid(axis="y")
    ax.set_axisbelow(True)

    if use_pct_axis:
        raw_ticks = sorted(success_df["chunk_count"].astype(int).unique().tolist())
        pct_ticks = [_to_pct(c) for c in raw_ticks]  # true float positions
        ax.set_xticks(pct_ticks)
        ax.set_xticklabels([f"{round(v,1)}" for v in pct_ticks])
    else:
        xticks = sorted(success_df["chunk_count"].astype(int).unique().tolist())
        ax.set_xticks(xticks)

    # Compute context-window threshold in chunk units from the estimated token slope.
    # actual_token_ratio corrects for the chars/4 estimator undercounting numeric CSV.
    threshold_chunks: float | None = None
    if len(success_df) >= 2:
        first, last = success_df.iloc[0], success_df.iloc[-1]
        slope_est_per_chunk = (
            (last["context_tokens_est"] - first["context_tokens_est"])
            / (last["chunk_count"] - first["chunk_count"])
        )
        overhead_est = float(success_df["prompt_overhead_tokens_est"].iloc[0]) if "prompt_overhead_tokens_est" in success_df.columns else 0.0
        threshold_chunks = (context_window_tokens / actual_token_ratio - overhead_est) / slope_est_per_chunk

    # Convert threshold to the same units as x_values before drawing.
    threshold_x = _to_pct(threshold_chunks) if (use_pct_axis and threshold_chunks is not None) else threshold_chunks

    # Determine x-axis right bound: extend just past the threshold when it's
    # within 2× the data range so the dashed line is always visible.
    data_min = float(x_values.min())
    data_max = float(x_values.max())
    data_span = data_max - data_min
    far_threshold = data_max + 2.0 * data_span  # beyond this → omit marker
    if max_chunks is not None:
        x_right = float(_to_pct(max_chunks)) if use_pct_axis else float(max_chunks)
    else:
        x_right = float(data_max)
    if threshold_x is not None and threshold_x <= far_threshold:
        x_right = max(x_right, threshold_x * 1.05)  # small right padding
    ax.set_xlim(data_min - data_span * 0.02, x_right)

    if threshold_x is not None:
        if threshold_x <= far_threshold:
            ax.axvline(
                threshold_x,
                linestyle="--",
                color="#888888",
                linewidth=1.4,
            )
            # Add rotated text label aligned with the line
            y_max = ax.get_ylim()[1]
            ax.text(
                threshold_x,
                y_max * 0.88,
                f"Context limit: {context_window_tokens // 1000}k tokens",
                rotation=90,
                ha="right",
                va="top",
                fontsize=17.25,
                color="#d62728",
                style="italic",
                fontweight="bold",
            )
            if use_pct_axis:
                print(f"Context-window threshold drawn at ~{threshold_x:.1f}% of dataset (~{threshold_chunks:.0f} chunks)")
            else:
                print(f"Context-window threshold drawn at ~{threshold_x:.0f} chunks")
        else:
            print(
                f"Context-window threshold is far outside plotted range; skipping marker."
            )

    # ax.legend(
    #     title="Metric",
    #     loc="upper left",
    #     bbox_to_anchor=(1.01, 1),
    #     borderaxespad=0,
    #     frameon=True,
    #     fontsize=14,
    #     title_fontsize=14,
    # )

    fig.subplots_adjust(left=0.10, right=0.78, bottom=0.12, top=0.88)
    os.makedirs(output_dir, exist_ok=True)
    out_png = Path(output_dir) / "latency_vs_chunks.png"
    out_pdf = Path(output_dir) / "latency_vs_chunks.pdf"
    fig.savefig(out_png, dpi=180, bbox_inches="tight")
    fig.savefig(out_pdf, bbox_inches="tight")
    print(f"Plot saved to {out_png}")
    print(f"Plot saved to {out_pdf}")
    plt.close(fig)


def main() -> None:
    """CLI entry point for the latency vs. chunks experiment."""
    parser = argparse.ArgumentParser(
        description="Latency vs. Chunks (direct OpenRouter streaming benchmark)"
    )
    parser.add_argument("--dataset", default="bus", help="Dataset name (default: bus)")
    parser.add_argument(
        "--data-path",
        default="/Users/kausar/Documents/backups/flash-fusion/data/bus",
        help=(
            "Path to dataset file or folder. For bus, passing the folder is supported "
            "and bus_data.csv is auto-selected."
        ),
    )
    parser.add_argument(
        "--chunks",
        nargs="+",
        type=int,
        default=DEFAULT_CHUNK_COUNTS,
        help=f"Chunk counts to test (default: {DEFAULT_CHUNK_COUNTS})",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--output-dir", default="flashfusion/miniexp/results", help="Output directory")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Natural language query")
    parser.add_argument(
        "--chunk-token-target",
        type=int,
        default=DEFAULT_CHUNK_TOKEN_TARGET,
        help=f"Approx tokens per chunk (default: {DEFAULT_CHUNK_TOKEN_TARGET})",
    )
    parser.add_argument(
        "--context-window-tokens",
        type=int,
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        help=f"Model context window token budget (default: {DEFAULT_CONTEXT_WINDOW_TOKENS})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retries per chunk count on API errors (default: 0)",
    )
    parser.add_argument("--csv-input", help="Path to existing latency_vs_chunks.csv to plot (skips experiment)")
    parser.add_argument(
        "--avg-inputs",
        nargs="+",
        help="Paths to 2+ per-run CSVs; averages ttft/ttlt and plots the result",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="Cap the plot x-axis at this many chunks (default: no cap)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute chunk/context stats without API calls")
    parser.add_argument("--plot", action="store_true", help="Generate latency plots")
    parser.add_argument("--ttft-only", action="store_true", help="Plot only TTFT (omit TTLT line)")
    parser.add_argument(
        "--actual-token-ratio",
        type=float,
        default=1.9,
        help="Correction factor: actual model tokens / chars-4 estimate (default: 1.9 for numeric CSV)",
    )
    parser.add_argument(
        "--total-rows",
        type=int,
        default=None,
        help=(
            "Total rows in the reference dataset (e.g. 65000 for ECG 101.csv). "
            "When provided together with --rows-per-chunk, the x-axis shows %% of dataset "
            "instead of raw chunk count."
        ),
    )
    parser.add_argument(
        "--rows-per-chunk",
        type=int,
        default=None,
        help=(
            "Rows packed per chunk at the given --chunk-token-target. "
            "For ECG 101.csv at --chunk-token-target 200, use 21. "
            "Required alongside --total-rows to enable %%-of-dataset x-axis."
        ),
    )

    args = parser.parse_args()

    if args.avg_inputs:
        print(f"Averaging {len(args.avg_inputs)} run(s): {args.avg_inputs}")
        avg_df = average_runs(args.avg_inputs)
        out_csv = Path(args.output_dir) / "latency_vs_chunks_avg.csv"
        os.makedirs(args.output_dir, exist_ok=True)
        avg_df.to_csv(out_csv, index=False)
        print(f"Averaged results saved to {out_csv}")
        if args.plot:
            ttft_only = args.ttft_only or True
            plot_results(
                avg_df,
                output_dir=args.output_dir,
                chunk_token_target=args.chunk_token_target,
                context_window_tokens=args.context_window_tokens,
                ttft_only=ttft_only,
                actual_token_ratio=args.actual_token_ratio,
                max_chunks=args.max_chunks,
                total_rows=args.total_rows,
                rows_per_chunk=args.rows_per_chunk,
            )
        return

    if args.csv_input:
        # Load from CSV and plot
        results_df = pd.read_csv(args.csv_input)
        if args.plot:
            chunk_token_target = args.chunk_token_target
            context_window_tokens = args.context_window_tokens
            plot_results(
                results_df,
                output_dir=args.output_dir,
                chunk_token_target=chunk_token_target,
                context_window_tokens=context_window_tokens,
                ttft_only=args.ttft_only,
                actual_token_ratio=args.actual_token_ratio,
                max_chunks=args.max_chunks,
                total_rows=args.total_rows,
                rows_per_chunk=args.rows_per_chunk,
            )
        else:
            print(f"Loaded {len(results_df)} rows from {args.csv_input}")
            print("Pass --plot to generate plots.")
        return

    results_df = run_experiment(
        dataset_name=args.dataset,
        data_path=args.data_path,
        chunk_counts=args.chunks,
        model=args.model,
        output_dir=args.output_dir,
        query=args.query,
        chunk_token_target=args.chunk_token_target,
        context_window_tokens=args.context_window_tokens,
        retries=args.retries,
        dry_run=args.dry_run,
    )

    if args.plot:
        plot_results(
            results_df,
            output_dir=args.output_dir,
            chunk_token_target=args.chunk_token_target,
            context_window_tokens=args.context_window_tokens,
            ttft_only=args.ttft_only,
            actual_token_ratio=args.actual_token_ratio,
            max_chunks=args.max_chunks,
            total_rows=args.total_rows,
            rows_per_chunk=args.rows_per_chunk,
        )


if __name__ == "__main__":
    main()