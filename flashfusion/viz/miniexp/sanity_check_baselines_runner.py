"""Sanity-check runner called by sanity_check_baselines.sh.

Must be executed as a real file (not via stdin) because ExecutionLayer's safe
backend uses multiprocessing.get_context("spawn"), which re-executes __main__
from disk.  Running as a heredoc (python - <<'PY') sets __file__ to <stdin>
and the spawned child process crashes with FileNotFoundError.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root is in sys.path so flashfusion can be imported.
# This runner is at flashfusion/miniexp/sanity_check_baselines_runner.py,
# so repo root is two levels up.
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
	sys.path.insert(0, str(_repo_root))

import pandas as pd

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.queries import get_queries
from flashfusion.performance.miniexp.latencystages import _resolve_dataset_file, map_semantic_stage_latency_s
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient


def _check_increasing(
    summary: pd.DataFrame,
    baseline_order: list[str],
    failures: list[str],
    order_tol_rel: float,
    metric: str,
    display_name: str,
) -> None:
    vals = summary[metric].astype(float).tolist()
    for i in range(len(vals) - 1):
        left = vals[i]
        right = vals[i + 1]
        if left > right * (1.0 + order_tol_rel):
            failures.append(
                f"Order violation for {display_name}: {baseline_order[i]}={left:.6g} "
                f"> {baseline_order[i + 1]}={right:.6g} (tol={order_tol_rel:.2%})"
            )


def _log_baseline_compact(baseline: str, query_id: int, latency_s: float, total_tokens: int, cost_usd: float) -> None:
    """Compact log line for non-AutoIOT baselines."""
    print(f"  ✓ executed: {baseline:20s} qid={query_id}  latency={latency_s:7.3f}s  tokens={total_tokens:6d}  cost=${cost_usd:8.6f}")


def _log_autoiot_verbose(baseline: str, query_id: int, client, result) -> None:
    """Verbose debug logging for AutoIOT baseline."""
    print(f"\n  ▶ AutoIOT baseline (qid={query_id}):")
    print(f"     Stages run: {', '.join(result.stages_run)}")
    
    # Log LLM call details from client.call_log
    print(f"     LLM calls ({len(client.call_log)}):")
    for i, call in enumerate(client.call_log, 1):
        stage = getattr(call, 'stage', '?')
        tokens_in = getattr(call, 'input_tokens', 0)
        tokens_out = getattr(call, 'output_tokens', 0)
        latency = getattr(call, 'latency_s', 0.0)
        cost = getattr(call, 'cost_usd', 0.0)
        print(f"       [{i}] {stage:30s}  tokens={tokens_in+tokens_out:6d} (in={tokens_in}, out={tokens_out})  lat={latency:7.3f}s  cost=${cost:8.6f}")
    
    print(f"     Total: latency={result.latency_s:.3f}s, tokens={int(result.input_tokens) + int(result.output_tokens):6d}, cost=${result.cost_usd:.6f}")
    print(f"     ✓ complete\n")


if __name__ == "__main__":
    dataset = os.getenv("SANITY_DATASET", "wisdm")
    model = os.getenv("SANITY_MODEL", DEFAULT_MODEL)
    order_tol_rel = float(os.getenv("SANITY_ORDER_TOL_REL", "0.10"))
    latency_sum_rel_tol = float(os.getenv("SANITY_STAGE_LATENCY_REL_TOL", "0.20"))
    latency_sum_abs_tol = float(os.getenv("SANITY_STAGE_LATENCY_ABS_TOL_S", "0.75"))
    sanity_baseline = os.getenv("SANITY_BASELINE", "").strip()

    baseline_order = ["FLASH_FUSION", "REACT_ONLY", "AUTOIOT_PAPER", "HARGPT_PAPER"]
    
    # Filter to single baseline if specified
    if sanity_baseline:
        if sanity_baseline not in baseline_order:
            print(f"ERROR: unknown baseline '{sanity_baseline}'")
            print(f"       valid options: {', '.join(baseline_order)}")
            sys.exit(1)
        baseline_order = [sanity_baseline]
        verbose = True  # Extra detail when running single baseline
        if sanity_baseline == "AUTOIOT_PAPER":
            os.environ.setdefault("AUTOIOT_DEBUG", "1")
    else:
        verbose = False

    queries = get_queries(dataset)
    selected_queries: list[dict] = []
    for complexity in ("direct", "intermediate", "out_of_scope"):
        match = next((q for q in queries if str(q.get("complexity", "")).lower() == complexity), None)
        if match is not None:
            selected_queries.append(match)
    if not selected_queries:
        selected_queries = queries[:3]

    if not selected_queries:
        raise RuntimeError(f"No queries available for dataset={dataset}")

    data_path = _resolve_dataset_file(dataset, None)
    df_full = load_dataset_by_name(str(data_path), dataset)
    if df_full.empty:
        raise RuntimeError(f"Loaded dataset is empty: {data_path}")

    print(f"Dataset={dataset}  model={model}")
    print(f"Using query IDs={[int(q['id']) for q in selected_queries]}")
    if sanity_baseline:
        print(f"Running baseline={sanity_baseline} (verbose mode)")
        if sanity_baseline == "AUTOIOT_PAPER":
            import flashfusion.baselines.autoiot_paper as autoiot_paper_module

            print(f"AutoIOT module path={autoiot_paper_module.__file__}")
            print(f"AUTOIOT_DEBUG={os.getenv('AUTOIOT_DEBUG', '')}")
    print()

    rows: list[dict] = []
    failures: list[str] = []

    for baseline in baseline_order:
        is_autoiot = baseline == "AUTOIOT_PAPER"
        for q in selected_queries:
            query_id = int(q["id"])
            query_text = str(q["text"])

            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("GROQ_API_KEY")
            client = LLMClient(model_name=model, api_key=api_key)
            runner = BaselineRunner(mode=baseline, df=df_full, client=client)
            result = runner.run(query_text)

            # Log execution
            total_tokens = int(result.input_tokens) + int(result.output_tokens)
            if is_autoiot and verbose:
                _log_autoiot_verbose(baseline, query_id, client, result)
            else:
                _log_baseline_compact(baseline, query_id, result.latency_s or 0.0, total_tokens, result.cost_usd or 0.0)

            semantic_stage = map_semantic_stage_latency_s(result, list(client.call_log))
            stacked_latency_s = float(sum(semantic_stage.values()))
            total_latency_s = float(result.latency_s or 0.0)
            delta_abs = abs(total_latency_s - stacked_latency_s)
            allowed_delta = max(latency_sum_abs_tol, total_latency_s * latency_sum_rel_tol)
            stage_sum_ok = delta_abs <= allowed_delta
            if not stage_sum_ok:
                failures.append(
                    f"Stage latency sum mismatch: baseline={baseline}, qid={query_id}, "
                    f"total={total_latency_s:.3f}s, stacked={stacked_latency_s:.3f}s, delta={delta_abs:.3f}s"
                )

            rows.append(
                {
                    "baseline": baseline,
                    "query_id": query_id,
                    "latency_s": total_latency_s,
                    "stacked_stage_latency_s": stacked_latency_s,
                    "latency_delta_abs_s": delta_abs,
                    "input_tokens": int(result.input_tokens),
                    "output_tokens": int(result.output_tokens),
                    "total_tokens": total_tokens,
                    "cost_usd": float(result.cost_usd),
                    "stage_sum_ok": stage_sum_ok,
                }
            )

    df = pd.DataFrame(rows)
    summary = (
        df.groupby("baseline", as_index=False)
        .agg(
            avg_latency_s=("latency_s", "mean"),
            avg_tokens=("total_tokens", "mean"),
            avg_cost_usd=("cost_usd", "mean"),
            avg_abs_stage_delta_s=("latency_delta_abs_s", "mean"),
            query_count=("query_id", "count"),
        )
    )
    summary["baseline"] = pd.Categorical(summary["baseline"], categories=baseline_order, ordered=True)
    summary = summary.sort_values("baseline").reset_index(drop=True)

    _check_increasing(summary, baseline_order, failures, order_tol_rel, "avg_tokens", "tokens")
    _check_increasing(summary, baseline_order, failures, order_tol_rel, "avg_latency_s", "latency")
    _check_increasing(summary, baseline_order, failures, order_tol_rel, "avg_cost_usd", "cost")

    print("\nPer-baseline summary:")
    print(summary.to_string(index=False))

    if failures:
        print("\nSANITY CHECK FAILED:")
        for item in failures:
            print(f"- {item}")
        sys.exit(1)

    print("\nSANITY CHECK PASSED")
    print("Validated:")
    print("- Stage latency sums are close to total latency")
    print("- Ordering trend: FF < ReAct < AutoIOT < HARGPT for tokens, latency, and cost")
