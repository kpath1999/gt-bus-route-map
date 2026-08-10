"""
eval/trace_query.py — Step-by-step debug trace for Flash-Fusion queries.

Runs the Flash-Fusion pipeline for one or more queries and shows execution details:
  - Bypass detector (zero-LLM predictive template matching)
  - Single structured guardrail+plan call
  - Gate 1: Pydantic structural validation
  - Gate 2: DataFrame schema validation
  - Typed operator execution (in-process, no sandbox)
  - ReAct fallback (only when needed, with optional S1/S2 grounding)
  - Final answer vs ground truth comparison

When multiple queries are traced, displays a summary table showing execution
paths and validation outcomes across all queries.

This is a read-only debugging aid; it does not write benchmark artifacts.

Usage:
    # Single query detailed trace
    python -m flashfusion.eval.trace_query --dataset wisdm --query-id 5
    
    # Multiple queries with summary
    python -m flashfusion.eval.trace_query --dataset bus --query-id 1,3,5,7
    
    # All queries in a dataset
    python -m flashfusion.eval.trace_query --dataset wisdm --query-id all
    
    # With custom model
    python -m flashfusion.eval.trace_query --dataset bus --query-id 9 \\
        --model meta-llama/llama-3.3-70b-instruct

Environment:
    OPENROUTER_API_KEY — preferred; GROQ_API_KEY accepted during transition.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS, DEFAULT_GROUND_TRUTH_PATHS
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.queries import SUPPORTED_DATASETS, get_queries
from flashfusion.eval.semantic_scorer import SemanticScorer
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient, RunResult


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _resolve_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit(
            "Set OPENROUTER_API_KEY (or GROQ_API_KEY) in the environment before running."
        )
    return key


def _find_query(dataset: str, query_id: int) -> dict:
    defs = get_queries(dataset)
    for q in defs:
        if q["id"] == query_id:
            return q
    raise SystemExit(f"Query id {query_id} not found for dataset {dataset!r} (have {[d['id'] for d in defs]})")


def _parse_query_ids(query_id_arg: str, dataset: str) -> list[int]:
    """Parse comma-separated query IDs or 'all' into a list of integer IDs."""
    if query_id_arg.lower() == "all":
        return [q["id"] for q in get_queries(dataset)]
    try:
        return [int(x.strip()) for x in query_id_arg.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"Invalid --query-id format: {query_id_arg!r}. Use comma-separated integers or 'all'.")


def _print_summary_table(results: list[tuple[int, RunResult, Any]]) -> None:
    """Print a summary table showing execution paths across multiple queries."""
    _hr("SUMMARY: EXECUTION PATHS ACROSS QUERIES")
    
    # Header
    print(f"{'ID':<4} {'Path':<18} {'Source':<18} {'Gates':<12} {'Operators':<30} {'Time(s)':<8} {'Score':<6}")
    print("-" * 110)
    
    # Rows
    for query_id, result, gt_entry in results:
        r = result
        path = r.execution_path or "unknown"
        path_short = path.replace("_", " ").title()[:17]
        
        source = r.plan_source or "-"
        source_short = source.replace("_", " ")[:17]
        
        # Gate status
        if r.rejected:
            gates = "REJECTED"
        elif r.plan_validation_stage_failed:
            gates = f"✗ {r.plan_validation_stage_failed}"
        elif path == "typed_operator":
            gates = "✓ Both"
        else:
            gates = "-"
        
        # Operators used
        ops = ", ".join(r.operators_used[:2]) if r.operators_used else "-"
        if len(r.operators_used) > 2:
            ops += f" +{len(r.operators_used)-2}"
        ops = ops[:29]
        
        # Latency
        latency = f"{r.latency_s:.2f}"
        
        # Score
        if gt_entry is not None:
            scorer = SemanticScorer()
            score_result = scorer.score_result(r, gt_entry)
            score_str = f"{score_result['score']:.3f}"
        else:
            score_str = "N/A"
        
        print(f"{query_id:<4} {path_short:<18} {source_short:<18} {gates:<12} {ops:<30} {latency:<8} {score_str:<6}")
    
    # Statistics
    _hr("STATISTICS")
    total = len(results)
    by_path = {}
    by_source = {}
    gate_failures = {}
    
    for _, r, _ in results:
        path = r.execution_path or "unknown"
        by_path[path] = by_path.get(path, 0) + 1
        
        source = r.plan_source or "none"
        by_source[source] = by_source.get(source, 0) + 1
        
        if r.plan_validation_stage_failed:
            stage = r.plan_validation_stage_failed
            gate_failures[stage] = gate_failures.get(stage, 0) + 1
    
    print(f"Total queries: {total}")
    print("\nExecution paths:")
    for path, count in sorted(by_path.items()):
        pct = 100.0 * count / total
        print(f"  {path:<25} {count:>3} ({pct:>5.1f}%)")
    
    print("\nPlan sources:")
    for source, count in sorted(by_source.items()):
        pct = 100.0 * count / total
        print(f"  {source:<25} {count:>3} ({pct:>5.1f}%)")
    
    if gate_failures:
        print("\nValidation failures (fallback triggers):")
        for stage, count in sorted(gate_failures.items()):
            print(f"  {stage:<25} {count:>3}")
    
    # Aggregate latency
    total_latency = sum(r.latency_s for _, r, _ in results)
    avg_latency = total_latency / total if total > 0 else 0
    print(f"\nTotal latency: {total_latency:.2f}s")
    print(f"Average latency: {avg_latency:.2f}s")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, choices=SUPPORTED_DATASETS)
    p.add_argument(
        "--query-id", 
        required=True, 
        type=str, 
        help="Query ID(s): single integer, comma-separated (e.g., '1,3,5'), or 'all'"
    )
    p.add_argument("--data", default=None, help="Override path to the raw dataset file")
    p.add_argument("--ground-truth", default=None, help="Override path to ground-truth JSON")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Primary model for guardrail+plan and typed execution")
    p.add_argument(
        "--stage12-model",
        default="qwen/qwen-2.5-7b-instruct",
        help=(
            "Lighter model used for S1 (concept extraction) and S2 (schema grounding) "
            "in ReAct fallback only, via client.light. Pass the same value as --model "
            "(or an empty string) to disable and run S1/S2 on the primary model instead."
        ),
    )
    p.add_argument("--max-rows", type=int, default=None, help="Row cap forwarded to mit_ecg loader")
    p.add_argument(
        "--react",
        action="store_true",
        help="Run REACT_ONLY baseline trace instead of FLASH_FUSION",
    )
    return p.parse_args()


def trace_single_query(
    query_id: int,
    query_def: dict,
    df,
    runner: BaselineRunner,
    client: LLMClient,
    gt_entry,
    args,
    verbose: bool = True,
):
    """Trace execution of a single query. Returns (query_id, result, gt_entry)."""
    query_text = query_def["text"]
    
    if verbose:
        _hr(f"QUERY  (dataset={args.dataset}  id={query_id}  complexity={query_def.get('complexity')})")
        print(query_text)
        if gt_entry is None:
            print(f"\n[WARN] No ground-truth entry for query_id={query_id}")
    
    r = runner.run(query_text)

    if not verbose:
        return (query_id, r, gt_entry)

    # Detailed trace output follows (only when verbose=True)
    if args.react:
        _hr("REACT_ONLY EXECUTION")
        print("Guardrail planning and typed operators are skipped in this mode.")
        _hr("EXECUTION TRACE")
        print(r.trace or "(no trace captured)")
        _hr("FINAL EXECUTED CODE")
        print(r.final_code or "(none)")
        print(f"\nagent_tries={r.agent_tries}")
    else:
        # --- Single structured guardrail + plan call --------------------------
        _hr("GUARDRAIL + PLAN (single structured LM call)")
        print(f"execution_path : {r.execution_path or '(unset)'}")
        print(f"plan_source    : {r.plan_source or '(unset)'}")
        print(f"query          : {r.guardrail_input or query_text}")
        _hr("ROUTER TELEMETRY")
        print(
            "fast_path      : "
            f"used={r.ff_fast_path_used} "
            f"latency={r.ff_fast_path_latency_s:.3f}s "
            f"tokens={r.ff_fast_path_input_tokens}in/"
                f"{r.ff_fast_path_output_tokens}out "
                f"cost=${r.ff_fast_path_cost_usd:.6f}"
        )
        print(
            "full_planner   : "
            f"used={r.ff_planner_used} "
            f"latency={r.ff_planner_latency_s:.3f}s "
            f"tokens={r.ff_planner_input_tokens}in/"
                f"{r.ff_planner_output_tokens}out "
                f"cost=${r.ff_planner_cost_usd:.6f}"
        )
        if r.ambiguous_concepts:
            print(f"ambiguous      : {', '.join(r.ambiguous_concepts)}")
        if r.rejected:
            print(f"\nVERDICT: REJECTED\nREASON: {r.rejection_reason}")
        else:
            print("\nVERDICT: IN SCOPE")

        if not r.rejected:
            # --- Two-gate validation -------------------------------------------
            _hr("TWO-GATE VALIDATION")
            
            if r.typed_plan:
                print("✓ GATE 1 (Pydantic structural validation): PASSED")
                print("✓ GATE 2 (DataFrame schema validation): PASSED")
            else:
                print("✗ VALIDATION FAILED")
                if r.plan_validation_stage_failed:
                    print(f"  Failed at: {r.plan_validation_stage_failed}")
                    print(f"  Reason: {r.deterministic_fallback_reason or '(none)'}")

            # --- Typed plan ----------------------------------------------------
            if r.typed_plan:
                _hr("TYPED PLAN (validated, ready for execution)")
                print(json.dumps(r.typed_plan, indent=2))
                print(f"\noperators_used: {', '.join(r.operators_used)}")
            else:
                print("\n(no validated plan — falling back to ReAct)")

            # --- Execution engine ----------------------------------------------
            _hr("EXECUTION ENGINE")
            if r.execution_path == "typed_operator":
                print("✓ TYPED OPERATORS (in-process, no sandbox)")
                print(f"  Execution time: {r.stage_latency_s.get('typed_exec', 0):.3f}s")
                print(f"  Agent tries: {r.agent_tries}")
            elif r.execution_path == "react_fallback":
                print("✗ REACT FALLBACK (typed vocabulary insufficient)")
                print(f"  Trigger: {r.deterministic_fallback_reason or '(unspecified)'}")
                
                # --- Fallback S1/S2 grounding ----------------------------------
                if r.s1_concepts:
                    s12_model = client.light.model_name
                    _hr(f"FALLBACK S1 — CONCEPT EXTRACTION  (model={s12_model})")
                    print(json.dumps(r.s1_concepts, indent=2))
                
                if r.s2_grounding:
                    _hr(f"FALLBACK S2 — SCHEMA GROUNDING  (model={client.light.model_name})")
                    print(r.s2_grounding)
                
                _hr("REACT QUERY (exact agent input)")
                print(r.react_query or "(not captured)")

            # --- Execution trace ----------------------------------------------
            _hr("EXECUTION TRACE")
            print(r.trace or "(no trace captured)")

            _hr("FINAL EXECUTED CODE")
            print(r.final_code or "(none)")
            print(f"\nagent_tries={r.agent_tries}")

    # --- Stages run + latency -----------------------------------------------
    _hr("STAGES RUN / LATENCY (s)")
    print("stages_run:", r.stages_run)
    if r.deterministic_fallback_reason:
        print("fallback_reason:", r.deterministic_fallback_reason)
    print(json.dumps(r.stage_latency_s, indent=2))

    # --- Final answer vs ground truth --------------------------------------
    if r.raw_answer:
        _hr("RAW ANSWER (pre-synthesis, machine output)")
        print(r.raw_answer)

    _hr("FINAL ANSWER")
    print(r.answer)

    if gt_entry is not None:
        _hr("GROUND TRUTH COMPARISON")
        print(f"Expected rejection : {gt_entry.expected_rejection}")
        print(f"Actual rejected    : {r.rejected}")
        print(f"Reference answer   : {gt_entry.reference_answer}")
        print(f"Model answer       : {r.answer}")
        scorer = SemanticScorer()
        result_dict = scorer.score_result(r, gt_entry)
        print(f"Score ({result_dict['method']}): {result_dict['score']:.3f}")

    _hr("COST / TOKENS / LATENCY")
    print(f"total_latency_s : {r.latency_s:.2f}")
    print(f"input_tokens    : {r.input_tokens}")
    print(f"output_tokens   : {r.output_tokens}")
    print(f"cost_usd        : {r.cost_usd:.6f}")
    print(f"cached_tokens   : {r.cached_tokens} (prompt tokens served from provider cache)")
    print(f"cache_write_tokens : {r.cache_write_tokens}")
    print(f"cache_discount_usd : {r.cache_discount_usd:.6f}")

    return (query_id, r, gt_entry)


def main() -> None:
    args = parse_args()

    data_path = args.data or DEFAULT_DATA_PATHS[args.dataset]
    gt_path = args.ground_truth or DEFAULT_GROUND_TRUTH_PATHS[args.dataset]

    query_ids = _parse_query_ids(args.query_id, args.dataset)
    multi_query_mode = len(query_ids) > 1

    if multi_query_mode:
        print(f"Tracing {len(query_ids)} queries from dataset '{args.dataset}'", file=sys.stderr)
    
    ground_truth_by_id = load_ground_truth(gt_path)

    print("\nLoading dataset...", file=sys.stderr)
    df = load_dataset_by_name(data_path, args.dataset, max_rows=args.max_rows)
    print(f"Loaded {len(df)} rows, columns={list(df.columns)}", file=sys.stderr)

    api_key = _resolve_api_key()
    client = LLMClient(model_name=args.model, api_key=api_key, light_model_name=args.stage12_model)
    baseline_mode = "REACT_ONLY" if args.react else "FLASH_FUSION"
    runner = BaselineRunner(mode=baseline_mode, df=df, client=client)

    s12_model = client.light.model_name
    if args.react:
        print(
            f"Mode: REACT_ONLY   |   Model: {client.model_name!r}",
            file=sys.stderr,
        )
    else:
        print(
            f"Mode: FLASH_FUSION | Models: S1/S2 (fallback only, client.light) = {s12_model!r}   |   Guardrail/Plan/Typed (client) = {client.model_name!r}",
            file=sys.stderr,
        )

    # Execute queries
    results = []
    for query_id in query_ids:
        query_def = _find_query(args.dataset, query_id)
        gt_entry = ground_truth_by_id.get(query_id)
        
        if multi_query_mode:
            print(f"\n[{query_id}] Running...", file=sys.stderr, end=" ", flush=True)
        
        result_tuple = trace_single_query(
            query_id=query_id,
            query_def=query_def,
            df=df,
            runner=runner,
            client=client,
            gt_entry=gt_entry,
            args=args,
            verbose=not multi_query_mode,
        )
        results.append(result_tuple)
        
        if multi_query_mode:
            _, r, _ = result_tuple
            print(f"done ({r.execution_path}, {r.latency_s:.2f}s)", file=sys.stderr)

    # Show summary for multiple queries
    if multi_query_mode:
        print()  # blank line before summary
        _print_summary_table(results)


if __name__ == "__main__":
    main()
