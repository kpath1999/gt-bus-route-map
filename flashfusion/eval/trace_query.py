"""
eval/trace_query.py — Step-by-step debug trace for Flash-Fusion queries.

Runs the Flash-Fusion pipeline for one or more queries and shows execution details:
  - Deterministic operator router (zero-LLM bucket narrowing of the vocabulary)
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

    # Cache-first trace: show how the light model refills columns/values into
    # the cached operator skeleton on an exact-query cache hit
    python -m flashfusion.eval.trace_query --dataset bus --query-id 4 --cache

    # Run reworded queries (v2/v3) against semantic cache built offline
    python -m flashfusion.eval.trace_query --dataset bus --query-id 4 --cache \
        --query-version v2 --semantic-cache-path flashfusion/eval/cache/semantic_registry_bus_v1.json

Environment:
    OPENROUTER_API_KEY — for OpenRouter primary/light models.
    GROQ_API_KEY       — for Groq primary/light models (e.g. allam-2-7b).
    Ollama light models (default: ollama/qwen2.5:3b-instruct) need no API key, just a
    running local server (see OLLAMA_BASE_URL, default http://localhost:11434).
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
from flashfusion.eval import queries as queries_v1
from flashfusion.eval import queries_v2, queries_v3
from flashfusion.eval.queries import SUPPORTED_DATASETS
from flashfusion.eval.semantic_scorer import SemanticScorer
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.operator_router import ALL_OPERATOR_NAMES
from flashfusion.pipeline.operators import OPERATOR_VOCABULARY_SPEC, build_vocabulary_spec
from flashfusion.pipeline.runner import (
    BaselineRunner,
    LLMClient,
    RunResult,
    _is_groq_model,
)


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _resolve_api_keys(model: str, stage12_model: str | None) -> tuple[str, str | None]:
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    primary_is_groq = _is_groq_model(model)
    light_is_groq = bool(stage12_model) and _is_groq_model(stage12_model)
    primary_key = groq_key if primary_is_groq else openrouter_key
    light_key = groq_key if light_is_groq else openrouter_key
    if not primary_key:
        raise SystemExit(
            "Set GROQ_API_KEY for the primary Groq model."
            if primary_is_groq
            else "Set OPENROUTER_API_KEY for the primary OpenRouter model."
        )
    if light_is_groq and not light_key:
        raise SystemExit("Set GROQ_API_KEY for --stage12-model.")
    return primary_key, light_key


def _find_query(dataset: str, query_id: int) -> dict:
    defs = _get_queries_by_version(dataset, "v1")
    for q in defs:
        if q["id"] == query_id:
            return q
    raise SystemExit(f"Query id {query_id} not found for dataset {dataset!r} (have {[d['id'] for d in defs]})")


def _get_queries_by_version(dataset: str, version: str) -> list[dict]:
    if version == "v1":
        return queries_v1.get_queries(dataset)
    if version == "v2":
        return queries_v2.get_queries(dataset)
    if version == "v3":
        return queries_v3.get_queries(dataset)
    raise ValueError(f"Unsupported query version {version!r}; expected one of v1,v2,v3")


def _parse_query_ids(query_id_arg: str, dataset: str, version: str) -> list[int]:
    """Parse comma-separated query IDs or 'all' into a list of integer IDs."""
    if query_id_arg.lower() == "all":
        return [q["id"] for q in _get_queries_by_version(dataset, version)]
    try:
        return [int(x.strip()) for x in query_id_arg.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"Invalid --query-id format: {query_id_arg!r}. Use comma-separated integers or 'all'.")


def _print_summary_table(results: list[tuple[int, RunResult, Any]]) -> None:
    """Print a summary table showing execution paths across multiple queries."""
    _hr("SUMMARY: EXECUTION PATHS ACROSS QUERIES")
    
    # Header
    print(
        f"{'ID':<4} {'Path':<18} {'Source':<18} {'Gates':<12} {'Ops':<30} "
        f"{'#Cand':<6} {'Full?':<6} {'Time(s)':<8} {'Score':<6}"
    )
    print("-" * 128)

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

        n_candidates = len(r.operator_route_candidate_ops)
        full_flag = "yes" if r.operator_route_full_fallback else "no"

        # Latency
        latency = f"{r.latency_s:.2f}"
        
        # Score
        if gt_entry is not None:
            scorer = SemanticScorer()
            score_result = scorer.score_result(r, gt_entry)
            score_str = f"{score_result['score']:.3f}"
        else:
            score_str = "N/A"
        
        print(
            f"{query_id:<4} {path_short:<18} {source_short:<18} {gates:<12} {ops:<30} "
            f"{n_candidates:<6} {full_flag:<6} {latency:<8} {score_str:<6}"
        )
    
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

    exact_hits = by_source.get("exact_query_cache_light_grounded", 0)
    semantic_hits = by_source.get("semantic_cache_light_grounded", 0)
    cache_hits = exact_hits + semantic_hits
    if cache_hits:
        print("\nCache hit breakdown:")
        print(f"  exact cache hits         {exact_hits:>3}")
        print(f"  semantic cache hits      {semantic_hits:>3}")
        print(f"  total cache hits         {cache_hits:>3} ({100.0 * cache_hits / total:>5.1f}%)")
    
    if gate_failures:
        print("\nValidation failures (fallback triggers):")
        for stage, count in sorted(gate_failures.items()):
            print(f"  {stage:<25} {count:>3}")

    # Operator router diagnostics: this is the primary thing the router is meant
    # to move — fewer candidate operators means a shorter, faster-to-prefill prompt.
    _hr("OPERATOR ROUTER")
    n_ops = [len(r.operator_route_candidate_ops) for _, r, _ in results]
    n_full = sum(1 for _, r, _ in results if r.operator_route_full_fallback)
    avg_ops = sum(n_ops) / total if total else 0.0
    print(f"Full vocabulary size    : {len(ALL_OPERATOR_NAMES)} operators")
    print(f"Avg candidate ops sent  : {avg_ops:.1f} ({100.0 * avg_ops / len(ALL_OPERATOR_NAMES):.1f}% of full)")
    print(f"Full-vocabulary fallback: {n_full}/{total} queries")
    rule_counts: dict[str, int] = {}
    for _, r, _ in results:
        for rule in r.operator_route_matched_rules:
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
    if rule_counts:
        print("\nRule firings across queries:")
        for rule, count in sorted(rule_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {rule:<30} {count:>3}")

    avg_route_latency = sum(r.stage_latency_s.get("operator_route", 0.0) for _, r, _ in results) / total if total else 0.0
    avg_planner_latency = sum(r.ff_planner_latency_s for _, r, _ in results) / total if total else 0.0
    print(f"\nAvg operator_route latency: {avg_route_latency:.5f}s (deterministic, no LLM call)")
    print(f"Avg guardrail+plan latency: {avg_planner_latency:.3f}s")

    # Aggregate latency
    total_latency = sum(r.latency_s for _, r, _ in results)
    avg_latency = total_latency / total if total > 0 else 0
    print(f"\nTotal latency: {total_latency:.2f}s")
    print(f"Average latency: {avg_latency:.2f}s")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, choices=SUPPORTED_DATASETS)
    p.add_argument(
        "--query-version",
        default="v1",
        choices=("v1", "v2", "v3"),
        help="Which query wording set to run: v1 (original), v2, or v3 reworded variants.",
    )
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
        default="ollama/qwen2.5:3b-instruct",
        help=(
            "Lighter model used for S1/S2 grounding and FLASH_FUSION_CACHE light-model "
            "grounding via client.light (default: local Ollama qwen2.5:3b-instruct, no API key "
            "needed; requires `ollama serve` running). Pass the same value as --model "
            "(or an empty string) to disable and run S1/S2 on the primary model instead."
        ),
    )
    p.add_argument("--max-rows", type=int, default=None, help="Row cap forwarded to mit_ecg loader")
    p.add_argument(
        "--react",
        action="store_true",
        help="Run REACT_ONLY baseline trace instead of FLASH_FUSION",
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help=(
            "Run the FLASH_FUSION_CACHE baseline and print the cache section: "
            "lookup status, cached operator skeleton, the exact grounding prompt, "
            "the light model's raw JSON, and the revalidated typed plan. "
            "Falls back to FLASH_FUSION on a miss or any failed gate."
        ),
    )
    p.add_argument(
        "--cache-path",
        default=None,
        help="Override the cache registry path used by --cache",
    )
    p.add_argument(
        "--semantic-cache-path",
        default=None,
        help=(
            "Optional semantic cache registry used after exact cache miss. "
            "Useful when running reworded query versions."
        ),
    )
    p.add_argument(
        "--progress",
        action="store_true",
        help="Stream progress updates during execution (useful for diagnosing stalls)",
    )
    return p.parse_args()


def _run_cache_traced(query_text: str, df, client: LLMClient, args):
    """Run FLASH_FUSION_CACHE directly so the cache trace record is observable.

    BaselineRunner cannot surface the grounding trace, and this debug path
    needs the exact prompt/raw JSON the light model produced.
    """
    import time

    from flashfusion.baselines.flash_fusion_cache import (
        BASELINE_NAME,
        DEFAULT_CACHE_PATH,
        CacheGroundingTrace,
        run_flash_fusion_cache,
    )

    r = RunResult(baseline=BASELINE_NAME, model=client.model_name, query=query_text)
    cache_trace = CacheGroundingTrace()
    t0 = time.time()
    run_flash_fusion_cache(
        query_text,
        df,
        client,
        r,
        dataset=args.dataset,
        cache_path=args.cache_path or DEFAULT_CACHE_PATH,
        semantic_cache_path=args.semantic_cache_path,
        trace=cache_trace,
    )
    r.latency_s = time.time() - t0 - r.stage_latency_s.get("column_metadata", 0.0)
    r.input_tokens = client.total_input_tokens()
    r.output_tokens = client.total_output_tokens()
    r.cost_usd = client.total_cost_usd()
    r.cached_tokens = client.total_cached_tokens()
    r.cache_write_tokens = client.total_cache_write_tokens()
    r.cache_discount_usd = client.total_cache_discount_usd()
    return r, cache_trace


def _print_cache_trace(t) -> None:
    """Show every cache gate, including the light model's grounding attempt."""
    _hr("EXACT-QUERY SKELETON CACHE")
    print(f"registry        : {t.cache_path}")
    print(f"dataset (canon) : {t.requested_dataset or '(unspecified)'}")
    print(f"lookup_status   : {t.lookup_status or '(not reached)'}")
    if t.entry:
        print(f"entry query_id  : {t.entry.get('query_id')}  dataset={t.entry.get('dataset')}")
        print(f"agreement       : {t.entry.get('n_runs_agreeing')}/{t.entry.get('n_runs_observed')} runs")
    print(f"cached skeleton : {t.operator_skeleton or '(none)'}")

    if t.prompt:
        _hr("CACHE GROUNDING PROMPT (sent to client.light)")
        print(t.prompt)

    if t.raw_light_output:
        _hr(f"LIGHT MODEL RAW OUTPUT  ({t.grounding_latency_s:.3f}s)")
        print(t.raw_light_output)

    if t.validated_plan:
        _hr("REGROUNDED TYPED PLAN (skeleton preserved, values refilled)")
        print(json.dumps(t.validated_plan, indent=2))
    elif t.parsed_plan:
        _hr("PARSED PLAN (rejected by a validation gate)")
        print(json.dumps(t.parsed_plan, indent=2))

    _hr("CACHE VERDICT")
    if t.hit:
        print(f"✓ CACHE HIT — executed typed plan; value={t.executed_value!r}")
    elif t.semantic_match_evidence:
        print(f"✓ CACHE HIT — semantic match!")
        _hr("SEMANTIC MATCH EVIDENCE")
        print(json.dumps(t.semantic_match_evidence, indent=2))
    else:
        print("✗ CACHE NOT USED — falling back to the full Flash-Fusion planner")
        print(f"  reason: {t.failure_reason or t.lookup_status or '(unknown)'}")

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
    
    if args.progress:
        print(f"\n[PROGRESS] Starting execution for query {query_id}...", file=sys.stderr, flush=True)

    cache_trace = None
    if getattr(args, "cache", False):
        r, cache_trace = _run_cache_traced(query_text, df, client, args)
    else:
        r = runner.run(query_text)

    if cache_trace is not None:
        _print_cache_trace(cache_trace)

    if args.progress:
        print(f"[PROGRESS] Execution complete for query {query_id} ({r.execution_path}, {r.latency_s:.2f}s)", file=sys.stderr, flush=True)
        _hr("REACT_ONLY EXECUTION")
        print("Guardrail planning and typed operators are skipped in this mode.")
        _hr("EXECUTION TRACE")
        print(r.trace or "(no trace captured)")
        _hr("FINAL EXECUTED CODE")
        print(r.final_code or "(none)")
        print(f"\nagent_tries={r.agent_tries}")
    else:
        # --- Deterministic operator router (no LLM call) -----------------------
        _hr("OPERATOR ROUTER (deterministic, zero-LLM bucket narrowing)")
        n_candidates = len(r.operator_route_candidate_ops)
        narrowed_spec_len = len(build_vocabulary_spec(r.operator_route_candidate_ops))
        full_spec_len = len(OPERATOR_VOCABULARY_SPEC)
        print(
            f"candidate_ops     : {n_candidates}/{len(ALL_OPERATOR_NAMES)} "
            f"({', '.join(sorted(r.operator_route_candidate_ops)) or '(none)'})"
        )
        print(f"excluded_buckets  : {r.operator_route_excluded_buckets or '(none)'}")
        print(f"matched_rules     : {r.operator_route_matched_rules or '(none)'}")
        print(f"used_full_fallback: {r.operator_route_full_fallback}")
        print(
            f"vocabulary spec   : {narrowed_spec_len} chars vs {full_spec_len} full "
            f"({100.0 * narrowed_spec_len / full_spec_len:.1f}%)"
        )
        print(
            f"router latency    : {r.stage_latency_s.get('operator_route', 0.0):.5f}s"
        )

        # --- Single structured guardrail + plan call --------------------------
        _hr("GUARDRAIL + PLAN (single structured LM call)")
        print(f"execution_path : {r.execution_path or '(unset)'}")
        print(f"plan_source    : {r.plan_source or '(unset)'}")
        print(f"query          : {r.guardrail_input or query_text}")
        _hr("PLANNER TELEMETRY")
        print(
            "guardrail+plan : "
            f"used={r.ff_planner_used} "
            f"latency={r.ff_planner_latency_s:.3f}s "
            f"tokens={r.ff_planner_input_tokens}in/"
                f"{r.ff_planner_output_tokens}out "
                f"cost=${r.ff_planner_cost_usd:.6f}"
        )
        print(
            f"planner_prefix : version={r.planner_prefix_version} "
            f"sha256={r.planner_prefix_sha256[:12]}..."
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
            if r.execution_path == "typed_operator_cache":
                print("✓ TYPED OPERATORS VIA CACHE (skeleton reused, values regrounded)")
                print(f"  Cache grounding: {r.stage_latency_s.get('cache_grounding', 0):.3f}s")
                print(f"  Execution time : {r.stage_latency_s.get('typed_exec', 0):.3f}s")
            elif r.execution_path == "typed_operator":
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
    
    # Enable progress streaming in the pipeline if requested
    if args.progress:
        os.environ["FF_PROGRESS"] = "1"

    data_path = args.data or DEFAULT_DATA_PATHS[args.dataset]
    gt_path = args.ground_truth or DEFAULT_GROUND_TRUTH_PATHS[args.dataset]

    query_ids = _parse_query_ids(args.query_id, args.dataset, args.query_version)
    multi_query_mode = len(query_ids) > 1
    query_defs = _get_queries_by_version(args.dataset, args.query_version)

    if multi_query_mode:
        print(
            f"Tracing {len(query_ids)} queries from dataset '{args.dataset}' "
            f"using query_version={args.query_version}",
            file=sys.stderr,
        )
    
    ground_truth_by_id = load_ground_truth(gt_path)

    print("\nLoading dataset...", file=sys.stderr)
    df = load_dataset_by_name(data_path, args.dataset, max_rows=args.max_rows)
    print(f"Loaded {len(df)} rows, columns={list(df.columns)}", file=sys.stderr)

    api_key, light_api_key = _resolve_api_keys(args.model, args.stage12_model)
    client = LLMClient(
        model_name=args.model,
        api_key=api_key,
        light_model_name=args.stage12_model,
        light_api_key=light_api_key,
    )
    if args.react and args.cache:
        raise SystemExit("--react and --cache are mutually exclusive")
    if args.react:
        baseline_mode = "REACT_ONLY"
    elif args.cache:
        baseline_mode = "FLASH_FUSION_CACHE"
    else:
        baseline_mode = "FLASH_FUSION"
    runner = BaselineRunner(
        mode=baseline_mode,
        df=df,
        client=client,
        dataset=args.dataset,
        cache_path=args.cache_path,
    )

    s12_model = client.light.model_name
    if args.cache:
        print(
            f"Mode: FLASH_FUSION_CACHE | Cache grounding (client.light) = {s12_model!r}   "
            f"|   Fallback planner (client) = {client.model_name!r}   "
            f"|   query_version={args.query_version}   "
            f"|   semantic_cache_path={args.semantic_cache_path or '(none)'}",
            file=sys.stderr,
        )
    elif args.react:
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
        query_def = next((q for q in query_defs if q["id"] == query_id), None)
        if query_def is None:
            raise SystemExit(f"Query id {query_id} not found for dataset={args.dataset} version={args.query_version}")
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
