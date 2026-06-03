"""
eval/benchmark.py — CLI entry point for the Flash-Fusion benchmark.

Usage:
    python -m flashfusion.eval.benchmark --help

    # Smoke test (3 queries × default 2 baselines)
    python -m flashfusion.eval.benchmark \\
        --data chat/data/imu/WISDM_ar_v1.1_raw.txt \\
        --queries 1,4,10 \\
        --output flashfusion/eval_results/

    # Full benchmark
    python -m flashfusion.eval.benchmark \\
        --data chat/data/imu/WISDM_ar_v1.1_raw.txt \\
        --baselines AGENT_ONLY,FLASH_FUSION \\
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
import signal
import sys
import time

import pandas as pd

from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.ground_truth_llm_judge import (
    run_llm_ground_truth_judge,
    summarize_judgments,
    _rows_from_run_results,
)
from flashfusion.eval.metrics import aggregate_metrics
from flashfusion.eval.queries import (
    DATASET_WISDM,
    SUPPORTED_DATASETS,
    get_queries,
)
from flashfusion.eval.reporter import print_table, save_csv, save_markdown
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult
from flashfusion.config import DEFAULT_MODEL

ALL_BASELINES = [
    "LLM_ONLY",
    "WELLMAX_ONLY",
    "AGENT_ONLY",
    "AUTOIOT_PAPER",
    "FLASH_FUSION",
    "HARGPT_PAPER",
    "LLMSENSE_PAPER",
]

DEFAULT_DATA_PATHS = {
    "wisdm": "chat/data/imu/WISDM_ar_v1.1_raw.txt",
    "mit_ecg": "data/AutoIOT_dataset/ECG.0/",
    "bus": "data/bus/bus_data.csv",
}

DEFAULT_GROUND_TRUTH_PATHS = {
    "wisdm": "flashfusion/eval/ground_truth/ground_truth_wisdm.json",
    "mit_ecg": "flashfusion/eval/ground_truth/ground_truth_mit_ecg.json",
    "bus": "flashfusion/eval/ground_truth/ground_truth_bus.json",
}


class QueryTimeoutError(TimeoutError):
    """Raised when a single query run exceeds the configured latency budget."""


def _baseline_summary(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Compute baseline-level averages from a per-query metrics dataframe."""
    metric_cols = [
        c
        for c in ["gt_score", "latency_s", "cost_usd", "input_tokens", "output_tokens"]
        if c in metrics_df.columns
    ]
    if metrics_df.empty:
        return pd.DataFrame(columns=["baseline", *metric_cols])
    return (
        metrics_df.groupby("baseline", as_index=False)[metric_cols]
        .mean()
        .sort_values("baseline")
        .reset_index(drop=True)
    )


def _run_single_benchmark_iteration(
    *,
    baselines: list[str],
    query_ids: list[int],
    df_base: pd.DataFrame,
    output_dir: str,
    model_name: str,
    api_key: str,
    ground_truth_by_id,
    data_path: str,
    max_query_latency: float,
    llm_judge_max_answer_chars: int,
    llm_judge_max_code_chars: int,
    dataset: str,
    query_defs: list[dict],
) -> tuple[list[RunResult], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Execute one full baseline x query benchmark run and persist artifacts."""
    os.makedirs(output_dir, exist_ok=True)
    raw_results_path = os.path.join(output_dir, "raw_results.jsonl")
    if os.path.exists(raw_results_path):
        os.remove(raw_results_path)

    results: list[RunResult] = []
    for baseline in baselines:
        for qid in query_ids:
            query_def = query_defs[qid - 1]
            query_text = query_def["text"]
            print(
                f"\n[{baseline}] Q{qid}: {query_text[:60]}...",
                flush=True,
            )
            # print(f"  [DEBUG] Starting runner.run() at {time.strftime('%H:%M:%S')}", flush=True)

            # DEBUG: Check df_base before passing to runner
            import sys
            print(f"[BENCHMARK DEBUG] df_base len={len(df_base)}, cols={list(df_base.columns)}", file=sys.stderr, flush=True)
            if len(df_base) > 0:
                print(f"[BENCHMARK DEBUG] df_base.head(3):\n{df_base.head(3)}", file=sys.stderr, flush=True)

            client = LLMClient(model_name=model_name, api_key=api_key)
            runner = BaselineRunner(
                mode=baseline,
                df=df_base.copy(),
                client=client,
            )
            t0 = time.time()

            def _timeout_handler(signum, frame):
                raise QueryTimeoutError()

            prev_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, max_query_latency)
            try:
                result = runner.run(query_text)
            except QueryTimeoutError:
                elapsed = time.time() - t0
                result = RunResult(
                    baseline=baseline,
                    model=model_name,
                    query=query_text,
                    answer=(
                        f"[TIMEOUT] Query exceeded {max_query_latency:.1f}s "
                        "latency budget; skipped to next query."
                    ),
                    rejected=False,
                    executed=False,
                )
                result.latency_s = elapsed
                result.rejection_reason = (
                    f"Timed out after {elapsed:.2f}s (budget {max_query_latency:.2f}s)"
                )
                result.stages_run = ["timeout"]
                result.input_tokens = client.total_input_tokens()
                result.output_tokens = client.total_output_tokens()
                result.cost_usd = client.total_cost_usd()
            except Exception as e:
                import traceback
                tb_lines = traceback.format_exc().splitlines()
                traceback_tail = "\n".join(tb_lines[-10:]) if len(tb_lines) > 10 else traceback.format_exc()
                error_msg = f"[ERROR] {type(e).__name__}: {e}"
                result = RunResult(
                    baseline=baseline,
                    model=model_name,
                    query=query_text,
                    answer=error_msg,
                    rejected=False,
                    executed=False,
                )
                result.alignment_explanation = f"Exception during {baseline} execution:\n{traceback_tail}"
                result.input_tokens = client.total_input_tokens()
                result.output_tokens = client.total_output_tokens()
                result.cost_usd = client.total_cost_usd()
                print(f"  [ERROR] {baseline} failed: {e}", file=sys.stderr, flush=True)
                print(f"  Traceback (last 10 lines):\n{traceback_tail}", file=sys.stderr, flush=True)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, prev_handler)
            results.append(result)

            j = result.judge_verdict.get("verdict", "N/A") if result.judge_verdict else "N/A"
            print(
                f"  -> executed={result.executed} rejected={result.rejected} "
                f"alignment={j} latency={result.latency_s:.1f}s "
                f"cost=${result.cost_usd:.4f}",
                flush=True,
            )

            with open(raw_results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dataclasses.asdict(result)) + "\n")

    judge_out_dir = os.path.join(output_dir, "ground_truth_llm_judge")
    rows_for_judge: list[dict] = []
    skipped_judge_count = 0
    for result, row in zip(results, _rows_from_run_results(results)):
        # Out-of-scope guardrail rejections are scored deterministically in metrics.
        if result.rejected and not result.executed:
            skipped_judge_count += 1
            continue
        rows_for_judge.append(row)

    if not rows_for_judge:
        # print(
        #     "  [DEBUG] Skipping LLM judge because no rows are eligible "
        #     f"(skipped {skipped_judge_count} guardrail-rejected rows).",
        #     flush=True,
        # )
        judgments_df = pd.DataFrame()
        sanity_df = pd.DataFrame()
    else:
        # print(
        #     f"  [DEBUG] Ground truth LLM initiates at {time.strftime('%H:%M:%S')} "
        #     f"({len(rows_for_judge)} eligible rows; skipped {skipped_judge_count} guardrail-rejected rows)",
        #     flush=True,
        # )
        judgments_df, _, sanity_df = run_llm_ground_truth_judge(
            rows=rows_for_judge,
            ground_truth_by_id=ground_truth_by_id,
            output_dir=judge_out_dir,
            model_name=model_name,
            api_key=api_key,
            data_path=data_path,
            dataset=dataset,
            max_answer_chars=llm_judge_max_answer_chars,
            max_code_chars=llm_judge_max_code_chars,
        )
        print(f"Ground-truth LLM responses written to {judge_out_dir}")

    metrics_df = aggregate_metrics(
        results,
        llm_judgments_df=judgments_df,
        ground_truth_by_id=ground_truth_by_id,
        query_defs=query_defs,
    )

    save_csv(metrics_df, os.path.join(output_dir, "metrics.csv"))
    save_markdown(
        results,
        os.path.join(output_dir, "report.md"),
        metrics_df=metrics_df,
        query_defs=query_defs,
    )
    print("\n=== Summary ===")
    print_table(metrics_df)
    print(f"\nResults written to {output_dir}")
    return results, judgments_df, metrics_df, sanity_df


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
        5. os.makedirs(args.output, exist_ok=True)
        6. raw_results_path = os.path.join(args.output, "raw_results.jsonl")
        7. results = []

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

        8. metrics_df = aggregate_metrics(results)
        9. save_csv(metrics_df, os.path.join(args.output, "metrics.csv"))
        10. save_markdown(results, os.path.join(args.output, "report.md"))
        11. print("\n=== Summary ===")
        12. print_table(metrics_df)
        13. print(f"\nResults written to {args.output}")
        14. return results
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("Error: GROQ_API_KEY environment variable not set")

    # Infer data path from dataset if not provided
    if args.data is None:
        args.data = DEFAULT_DATA_PATHS.get(args.dataset)
        if args.data is None:
            sys.exit(
                f"Error: --data not provided and no default path configured for dataset '{args.dataset}'. "
                f"Please specify --data explicitly."
            )

    # Infer ground truth path from dataset if using default
    if args.ground_truth == "flashfusion/eval/ground_truth/ground_truth_wisdm.json":
        args.ground_truth = DEFAULT_GROUND_TRUTH_PATHS.get(
            args.dataset, args.ground_truth
        )

    try:
        ground_truth_by_id = load_ground_truth(args.ground_truth)
    except FileNotFoundError as e:
        sys.exit(str(e))
    except Exception as e:
        sys.exit(f"Invalid ground truth file: {e}")

    if args.ground_truth_measurement != "llm":
        sys.exit(
            "Error: --ground-truth-measurement must be 'llm'. Semantic scoring "
            "is no longer supported."
        )

    if args.baselines == "all":
        baselines = list(ALL_BASELINES)
    else:
        baselines = [b.strip().upper() for b in args.baselines.split(",") if b.strip()]
    for b in baselines:
        if b not in ALL_BASELINES:
            sys.exit(f"Error: unknown baseline {b!r}. Options: {ALL_BASELINES}")

    query_defs = get_queries(args.dataset)

    if args.queries == "all":
        query_ids = [q["id"] for q in query_defs]
    else:
        query_ids = [int(x.strip()) for x in args.queries.split(",") if x.strip()]
    valid_ids = {q["id"] for q in query_defs}
    for qid in query_ids:
        if qid not in valid_ids:
            sys.exit(f"Error: unknown query id {qid}. Valid: {sorted(valid_ids)}")

    if args.runs < 1:
        sys.exit("Error: --runs must be >= 1")

    print(f"[DEBUG] Loading dataset from {args.data!r} with dataset={args.dataset!r} …", flush=True)
    _t_load = time.time()
    df_base = load_dataset_by_name(args.data, args.dataset)
    print(f"[DEBUG] Dataset loaded in {time.time()-_t_load:.2f}s  shape={df_base.shape}  len={len(df_base)}", flush=True)
    print(f"[DEBUG] df_base columns: {list(df_base.columns)}", flush=True)
    if len(df_base) > 0:
        print(f"[DEBUG] df_base.head(3):\n{df_base.head(3)}", flush=True)
    else:
        print(f"[DEBUG] WARNING: df_base is EMPTY after loading!", flush=True)
    os.makedirs(args.output, exist_ok=True)
    if args.runs == 1:
        results, _, _, _ = _run_single_benchmark_iteration(
            baselines=baselines,
            query_ids=query_ids,
            df_base=df_base,
            output_dir=args.output,
            model_name=args.model,
            api_key=api_key,
            ground_truth_by_id=ground_truth_by_id,
            data_path=args.data,
            dataset=args.dataset,
            query_defs=query_defs,
            max_query_latency=args.max_query_latency,
            llm_judge_max_answer_chars=args.llm_judge_max_answer_chars,
            llm_judge_max_code_chars=args.llm_judge_max_code_chars,
        )
        return results

    all_results: list[RunResult] = []
    all_metrics: list[pd.DataFrame] = []
    all_judgments: list[pd.DataFrame] = []
    baseline_per_run: list[pd.DataFrame] = []
    first_sanity_df: pd.DataFrame | None = None

    for run_id in range(1, args.runs + 1):
        run_output_dir = os.path.join(args.output, f"run_{run_id}")
        print(f"\n##### Run {run_id}/{args.runs} -> {run_output_dir} #####", flush=True)
        run_results, run_judgments, run_metrics, run_sanity = _run_single_benchmark_iteration(
            baselines=baselines,
            query_ids=query_ids,
            df_base=df_base,
            output_dir=run_output_dir,
            model_name=args.model,
            api_key=api_key,
            ground_truth_by_id=ground_truth_by_id,
            data_path=args.data,
            dataset=args.dataset,
            query_defs=query_defs,
            max_query_latency=args.max_query_latency,
            llm_judge_max_answer_chars=args.llm_judge_max_answer_chars,
            llm_judge_max_code_chars=args.llm_judge_max_code_chars,
        )

        all_results.extend(run_results)
        if first_sanity_df is None:
            first_sanity_df = run_sanity

        run_metrics = run_metrics.copy()
        run_metrics.insert(0, "run_id", run_id)
        all_metrics.append(run_metrics)

        run_judgments = run_judgments.copy()
        run_judgments.insert(0, "run_id", run_id)
        all_judgments.append(run_judgments)

        run_summary = _baseline_summary(run_metrics)
        run_summary.insert(0, "run_id", run_id)
        baseline_per_run.append(run_summary)

    combined_metrics_df = pd.concat(all_metrics, ignore_index=True)
    save_csv(combined_metrics_df, os.path.join(args.output, "metrics.csv"))

    top_raw_results_path = os.path.join(args.output, "raw_results.jsonl")
    with open(top_raw_results_path, "w", encoding="utf-8") as f:
        for run_id in range(1, args.runs + 1):
            run_dir = os.path.join(args.output, f"run_{run_id}")
            run_raw_path = os.path.join(run_dir, "raw_results.jsonl")
            if not os.path.exists(run_raw_path):
                continue
            with open(run_raw_path, "r", encoding="utf-8") as rf:
                for line in rf:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    payload["run_id"] = run_id
                    f.write(json.dumps(payload) + "\n")

    judge_out_dir = os.path.join(args.output, "ground_truth_llm_judge")
    os.makedirs(judge_out_dir, exist_ok=True)
    combined_judgments_df = pd.concat(all_judgments, ignore_index=True)
    combined_judgments_df.to_csv(
        os.path.join(judge_out_dir, "llm_judgments.csv"), index=False
    )
    with open(os.path.join(judge_out_dir, "llm_judgments.jsonl"), "w", encoding="utf-8") as fh:
        for row in combined_judgments_df.to_dict(orient="records"):
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    summarize_judgments(combined_judgments_df).to_csv(
        os.path.join(judge_out_dir, "llm_judgments_summary.csv"),
        index=False,
    )
    if first_sanity_df is None:
        first_sanity_df = pd.DataFrame()
    first_sanity_df.to_csv(os.path.join(judge_out_dir, "ground_truth_sanity.csv"), index=False)

    baseline_per_run_df = pd.concat(baseline_per_run, ignore_index=True)
    save_csv(baseline_per_run_df, os.path.join(args.output, "baseline_summary_per_run.csv"))

    metric_cols = [
        c
        for c in ["gt_score", "latency_s", "cost_usd", "input_tokens", "output_tokens"]
        if c in baseline_per_run_df.columns
    ]
    baseline_avg_df = (
        baseline_per_run_df.groupby("baseline", as_index=False)[metric_cols]
        .mean()
        .sort_values("baseline")
        .reset_index(drop=True)
    )
    baseline_avg_df["runs"] = args.runs
    save_csv(baseline_avg_df, os.path.join(args.output, "metrics_baseline_avg.csv"))

    save_markdown(
        all_results,
        os.path.join(args.output, "report.md"),
        metrics_df=combined_metrics_df,
        query_defs=query_defs,
    )
    save_markdown([], os.path.join(args.output, "report_avg.md"), metrics_df=baseline_avg_df)

    print("\n=== Combined Summary (all per-query rows across runs) ===")
    print_table(combined_metrics_df)
    print("\n=== Baseline Average Summary (across runs) ===")
    print_table(baseline_avg_df)
    print(f"\nResults written to {args.output}")
    return all_results


def _build_parser() -> argparse.ArgumentParser:
    """
    Build and return the argparse parser for the benchmark CLI.

    Arguments:
        --data       (required) Path to WISDM_ar_v1.1_raw.txt
        --baselines  (default "AGENT_ONLY,FLASH_FUSION") "all" or comma-separated baseline names
        --queries    (default "all") "all" or comma-separated 1-indexed query IDs
        --model      (default config.DEFAULT_MODEL) Groq model identifier
        --output     (default "flashfusion/eval_results/") Output directory path
    """
    parser = argparse.ArgumentParser(
        description=(
            "Flash-Fusion Benchmark — evaluate default Agent-Only vs Flash-Fusion "
            "(or any selected baselines) across query sets "
            "measuring accuracy, latency, and token cost."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m flashfusion.eval.benchmark --data chat/data/imu/WISDM_ar_v1.1_raw.txt "
            "--baselines AGENT_ONLY,FLASH_FUSION --queries 1,5,9,12\n"
            "  python -m flashfusion.eval.benchmark --data ... --baselines all"
        ),
    )
    parser.add_argument(
        "--data",
        default=None,
        help=(
            "Path to dataset file (relative to repo root or absolute). "
            "If omitted, uses default path for --dataset."
        ),
    )
    parser.add_argument(
        "--dataset",
        default=DATASET_WISDM,
        choices=list(SUPPORTED_DATASETS),
        help="Dataset profile for loading and query-bank selection",
    )
    parser.add_argument(
        "--baselines",
        default="AGENT_ONLY,LLMSENSE_PAPER,FLASH_FUSION",
        help=(
            'Comma-separated baseline names or "all". '
            "Default focuses on Agent-Only, LLMSENSE_PAPER, and FLASH_FUSION. "
            f"Options: {', '.join(ALL_BASELINES)}"
        ),
    )
    parser.add_argument(
        "--queries",
        default="all",
        help='Comma-separated 1-indexed query IDs or "all". E.g. "1,5,9,12"',
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
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of repeated benchmark runs to execute (default: 1)",
    )
    parser.add_argument(
        "--ground-truth",
        default="flashfusion/eval/ground_truth/ground_truth_wisdm.json",
        help="Path to ground truth JSON file (required to score answers)",
    )
    parser.add_argument(
        "--max-query-latency",
        type=float,
        default=20.0,
        help="Per-query latency budget in seconds; timed-out queries are skipped",
    )
    parser.add_argument(
        "--ground-truth-measurement",
        default="llm",
        choices=["llm"],
        help=(
            "Ground-truth scoring mode. gt_score is LLM-verdict-based only."
        ),
    )
    parser.add_argument(
        "--llm-judge-max-answer-chars",
        type=int,
        default=1800,
        help="Max answer chars passed to LLM judge",
    )
    parser.add_argument(
        "--llm-judge-max-code-chars",
        type=int,
        default=1400,
        help="Max generated-code chars passed to LLM judge",
    )
    return parser


def main() -> None:
    """Parse arguments and run the benchmark."""
    parser = _build_parser()
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
