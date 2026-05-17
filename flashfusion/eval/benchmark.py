"""
eval/benchmark.py — CLI entry point for the Flash-Fusion benchmark.

Usage:
    python -m flashfusion.eval.benchmark --help

    # Smoke test (3 queries × 4 baselines)
    python -m flashfusion.eval.benchmark \\
        --data chat/data/imu/WISDM_ar_v1.1_raw.txt \\
        --baselines all --queries 1,4,10 \\
        --output flashfusion/eval_results/

    # Full benchmark
    python -m flashfusion.eval.benchmark \\
        --data chat/data/imu/WISDM_ar_v1.1_raw.txt \\
        --baselines all \\
        --output flashfusion/eval_results/

Environment:
    GROQ_API_KEY — required; Groq API key for ChatGroq

See CLAUDE.md §eval/benchmark.py for the full run_benchmark() algorithm.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from flashfusion.adapters.wisdm_adapter import WISDMAdapter
from flashfusion.eval.metrics import aggregate_metrics
from flashfusion.eval.queries import WISDM_QUERIES
from flashfusion.eval.reporter import print_table, save_csv, save_markdown
from flashfusion.pipeline.loader import load_wisdm
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult
from flashfusion.config import DEFAULT_MODEL

ALL_BASELINES = ["LLM_ONLY", "WELLMAX_ONLY", "AUTOIOT_ONLY", "FLASH_FUSION"]


def run_benchmark(args: argparse.Namespace) -> list[RunResult]:
    """
    Execute the benchmark for all requested baseline × query combinations.

    Args:
        args: Parsed argparse.Namespace with attributes:
              data, baselines, queries, model, output

    Returns:
        List of RunResult objects (one per baseline × query combination).

    Implementation (see CLAUDE.md §eval/benchmark.py for full algorithm):

        1. Validate GROQ_API_KEY in environment.
        2. Resolve baselines list from args.baselines ("all" or comma-separated).
        3. Resolve query_ids list from args.queries ("all" or comma-separated ints).
        4. df_base = load_wisdm(args.data)
        5. adapter = WISDMAdapter()
        6. os.makedirs(args.output, exist_ok=True)
        7. raw_results_path = os.path.join(args.output, "raw_results.jsonl")
        8. results = []

        For each baseline in baselines:
          For each query_id in query_ids:
            query_def = WISDM_QUERIES[query_id - 1]
            query_text = query_def["text"]
            print progress header

            client = LLMClient(model_name=args.model, api_key=api_key)
            runner = BaselineRunner(
                mode=baseline,
                df=df_base.copy(),    # fresh copy per run
                client=client,
                adapter=adapter,
            )
            result = runner.run(query_text)
            results.append(result)

            # Print one-line progress summary
            j = result.judge_verdict.get("verdict", "N/A")
            print(f"  → executed={result.executed} rejected={result.rejected} "
                  f"judge={j} latency={result.latency_s:.1f}s cost=${result.cost_usd:.4f}")

            # Append to JSONL
            with open(raw_results_path, "a") as f:
                f.write(json.dumps(dataclasses.asdict(result)) + "\n")

        9. metrics_df = aggregate_metrics(results)
        10. save_csv(metrics_df, os.path.join(args.output, "metrics.csv"))
        11. save_markdown(results, os.path.join(args.output, "report.md"))
        12. print("\\n=== Summary ===")
        13. print_table(metrics_df)
        14. print(f"\\nResults written to {args.output}")
        15. return results
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("Error: GROQ_API_KEY environment variable not set")

    if args.baselines == "all":
        baselines = list(ALL_BASELINES)
    else:
        baselines = [b.strip().upper() for b in args.baselines.split(",") if b.strip()]
    for b in baselines:
        if b not in ALL_BASELINES:
            sys.exit(f"Error: unknown baseline {b!r}. Options: {ALL_BASELINES}")

    if args.queries == "all":
        query_ids = [q["id"] for q in WISDM_QUERIES]
    else:
        query_ids = [int(x.strip()) for x in args.queries.split(",") if x.strip()]
    valid_ids = {q["id"] for q in WISDM_QUERIES}
    for qid in query_ids:
        if qid not in valid_ids:
            sys.exit(f"Error: unknown query id {qid}. Valid: {sorted(valid_ids)}")

    df_base = load_wisdm(args.data)
    adapter = WISDMAdapter()

    os.makedirs(args.output, exist_ok=True)
    raw_results_path = os.path.join(args.output, "raw_results.jsonl")
    if os.path.exists(raw_results_path):
        os.remove(raw_results_path)

    results: list[RunResult] = []
    for baseline in baselines:
        for qid in query_ids:
            query_def = WISDM_QUERIES[qid - 1]
            query_text = query_def["text"]
            print(
                f"\n[{baseline}] Q{qid}: {query_text[:60]}...",
                flush=True,
            )

            client = LLMClient(model_name=args.model, api_key=api_key)
            runner = BaselineRunner(
                mode=baseline,
                df=df_base.copy(),
                client=client,
                adapter=adapter,
            )
            try:
                result = runner.run(query_text)
            except Exception as e:
                result = RunResult(
                    baseline=baseline,
                    model=args.model,
                    query=query_text,
                    answer=f"[ERROR] {e}",
                    rejected=False,
                    executed=False,
                )
                result.input_tokens = client.total_input_tokens()
                result.output_tokens = client.total_output_tokens()
                result.cost_usd = client.total_cost_usd()
            results.append(result)

            j = result.judge_verdict.get("verdict", "N/A") if result.judge_verdict else "N/A"
            print(
                f"  → executed={result.executed} rejected={result.rejected} "
                f"judge={j} latency={result.latency_s:.1f}s "
                f"cost=${result.cost_usd:.4f}",
                flush=True,
            )

            with open(raw_results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dataclasses.asdict(result)) + "\n")

    metrics_df = aggregate_metrics(results)
    save_csv(metrics_df, os.path.join(args.output, "metrics.csv"))
    save_markdown(results, os.path.join(args.output, "report.md"))
    print("\n=== Summary ===")
    print_table(metrics_df)
    print(f"\nResults written to {args.output}")
    return results


def _build_parser() -> argparse.ArgumentParser:
    """
    Build and return the argparse parser for the benchmark CLI.

    Arguments:
        --data       (required) Path to WISDM_ar_v1.1_raw.txt
        --baselines  (default "all") "all" or comma-separated baseline names
        --queries    (default "all") "all" or comma-separated 1-indexed query IDs
        --model      (default config.DEFAULT_MODEL) Groq model identifier
        --output     (default "flashfusion/eval_results/") Output directory path
    """
    parser = argparse.ArgumentParser(
        description=(
            "Flash-Fusion Benchmark — evaluate 4 baselines × 10 WISDM queries "
            "measuring accuracy, latency, and token cost."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m flashfusion.eval.benchmark --data chat/data/imu/WISDM_ar_v1.1_raw.txt "
            "--baselines all --queries 1,4,10\n"
            "  python -m flashfusion.eval.benchmark --data ... --baselines FLASH_FUSION,LLM_ONLY"
        ),
    )
    parser.add_argument(
        "--data",
        required=True,
        help="Path to WISDM_ar_v1.1_raw.txt (relative to repo root or absolute)",
    )
    parser.add_argument(
        "--baselines",
        default="all",
        help=(
            'Comma-separated baseline names or "all". '
            f"Options: {', '.join(ALL_BASELINES)}"
        ),
    )
    parser.add_argument(
        "--queries",
        default="all",
        help='Comma-separated 1-indexed query IDs or "all". E.g. "1,4,10"',
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Groq model identifier (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/eval_results/",
        help="Output directory for metrics.csv, report.md, raw_results.jsonl",
    )
    return parser


def main() -> None:
    """Parse arguments and run the benchmark."""
    parser = _build_parser()
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
