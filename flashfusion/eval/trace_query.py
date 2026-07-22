"""
eval/trace_query.py — Step-by-step debug trace for a single Flash-Fusion query.

Runs the Flash-Fusion pipeline (S1 -> S2 -> guardrail -> S3 -> agent) for ONE
query, selected by dataset + query id, and prints every intermediate artifact:
  - S1 concept extraction (DATA / REASONING concepts)
  - S2 schema grounding (raw concept -> column mappings)
  - Guardrail verdict (proceed / reject + reason)
  - S3 sub-query decomposition + synthesis hint
  - The final grounded query handed to the pandas agent
  - The agent's ReAct trace, final code, and answer
  - Per-stage latency and token/cost totals
  - Side-by-side comparison against the known ground-truth answer

This is a read-only debugging aid; it does not write benchmark artifacts.

Usage:
    python -m flashfusion.eval.trace_query --dataset wisdm --query-id 5
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

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS, DEFAULT_GROUND_TRUTH_PATHS
from flashfusion.eval.ground_truth import load_ground_truth
from flashfusion.eval.queries import SUPPORTED_DATASETS, get_queries
from flashfusion.eval.semantic_scorer import SemanticScorer
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import BaselineRunner, LLMClient


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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, choices=SUPPORTED_DATASETS)
    p.add_argument("--query-id", required=True, type=int, help="1-indexed query id from eval/queries.py")
    p.add_argument("--data", default=None, help="Override path to the raw dataset file")
    p.add_argument("--ground-truth", default=None, help="Override path to ground-truth JSON")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Primary model for S3 + agent")
    p.add_argument(
        "--stage12-model",
        default="meta-llama/llama-3.1-8b-instruct",
        help=(
            "Lighter model used for S1 (concept extraction) and S2 (schema grounding) "
            "via client.light. Pass the same value as --model (or an empty string) to "
            "disable and run S1/S2 on the primary model instead."
        ),
    )
    p.add_argument("--max-rows", type=int, default=None, help="Row cap forwarded to mit_ecg loader")
    p.add_argument(
        "--react",
        action="store_true",
        help="Run REACT_ONLY baseline trace instead of FLASH_FUSION",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    data_path = args.data or DEFAULT_DATA_PATHS[args.dataset]
    gt_path = args.ground_truth or DEFAULT_GROUND_TRUTH_PATHS[args.dataset]

    query_def = _find_query(args.dataset, args.query_id)
    query_text = query_def["text"]

    _hr(f"QUERY  (dataset={args.dataset}  id={args.query_id}  complexity={query_def.get('complexity')})")
    print(query_text)

    ground_truth_by_id = load_ground_truth(gt_path)
    gt_entry = ground_truth_by_id.get(args.query_id)
    if gt_entry is None:
        print(f"\n[WARN] No ground-truth entry for query_id={args.query_id} in {gt_path}")

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
            f"Mode: FLASH_FUSION | Models: S1/S2 (client.light) = {s12_model!r}   |   S3/agent (client) = {client.model_name!r}",
            file=sys.stderr,
        )

    print("\nRunning Flash-Fusion pipeline...", file=sys.stderr)
    r = runner.run(query_text)

    if args.react:
        _hr("REACT_ONLY EXECUTION")
        print("Guardrail/Stage 1/2/3 are skipped in this mode.")
        _hr("EXECUTION TRACE")
        print(r.trace or "(no trace captured)")
        _hr("FINAL EXECUTED CODE")
        print(r.final_code or "(none)")
        print(f"\nagent_tries={r.agent_tries}")
    else:
        # --- S1: concept extraction ---------------------------------------
        _hr(f"S1 — CONCEPT EXTRACTION  (model={s12_model})")
        print(json.dumps(r.s1_concepts, indent=2))

        # --- S2 concepts as sent to grounding, pre-mapping -------------------
        # _hr("S2 — CONCEPTS PASSED TO GROUNDING (post query-critical filter, pre-mapping)")
        # print(json.dumps(r.s2_filtered_concepts, indent=2) if r.s2_filtered_concepts else "(not reached)")

        # --- S2: schema grounding -------------------------------------------
        _hr(f"S2 — SCHEMA GROUNDING (model={s12_model}, raw LLM output)")
        print(r.s2_grounding or "(not reached)")

        # --- Guardrail --------------------------------------------------------
        _hr("GUARDRAIL INPUT (exact post-S2 prompt)")
        print(r.guardrail_input or "(not captured)")

        _hr("GUARDRAIL (post-S2)")
        if r.rejected:
            print(f"VERDICT: REJECTED\nREASON: {r.rejection_reason}")
        else:
            print("VERDICT: PROCEED (query accepted for S3 + agent execution)")

        if not r.rejected:
            # --- S3: sub-query decomposition (or direct-aggregate bypass) ----
            if "S3_bypass_predictive" in r.stages_run:
                _hr("S3 — BYPASSED (predictive CHRONO_SPLIT+CLASSIFY template detected)")
                print(
                    "  S1/S2 ran normally (grounding context is still needed by the "
                    "guardrail); only S3's FILTER/AGGREGATE/RANK decomposition was "
                    "skipped in favor of the deterministic PREDICTIVE_PIPELINE executor."
                )
                print(f"  Plan: {r.s3_sub_queries[0] if r.s3_sub_queries else '(none)'}")
                print(f"\nSynthesis hint: {r.s3_synthesis_hint}")
            elif "S3_bypass" in r.stages_run:
                _hr("S3 — BYPASSED (direct single-column aggregate detected)")
                print(f"  Expression: {r.s3_sub_queries[0] if r.s3_sub_queries else '(none)'}")
                print(f"\nSynthesis hint: {r.s3_synthesis_hint}")
            elif "S3_compiled" in r.stages_run:
                _hr("S3 — COMPILED EXECUTABLE PLAN")
                for i, sq in enumerate(r.s3_sub_queries or [], start=1):
                    print(f"  {i}. {sq}")
                print(f"\nSynthesis hint: {r.s3_synthesis_hint}")
            elif "S3_skipped_proxy" in r.stages_run:
                _hr("S3 — SKIPPED (PROXY concept detected)")
                print("No S3 sub-query plan was generated.")
                print(f"\nSynthesis hint: {r.s3_synthesis_hint}")
            else:
                _hr("S3 — SUB-QUERY DECOMPOSITION")
                for i, sq in enumerate(r.s3_sub_queries or [], start=1):
                    print(f"  {i}. {sq}")
                print(f"\nSynthesis hint: {r.s3_synthesis_hint}")

            # --- Deterministic predictive pipeline outcome ---------------------
            if "S3_bypass_predictive" in r.stages_run:
                _hr("PREDICTIVE PIPELINE EXECUTION")
                if "deterministic_exec" in r.stages_run:
                    print("Executed deterministically (no ReAct agent code-gen needed).")
                elif "deterministic_fallback" in r.stages_run:
                    print(
                        "Deterministic predictive execution FAILED and fell back to the "
                        f"ReAct agent. Reason: {r.deterministic_fallback_reason or '(none captured)'}"
                    )
                else:
                    print("(predictive plan detected, but execution stage unclear — see stages_run below)")

            # --- Prompts constructed for deterministic/ReAct execution --------
            _hr("GROUNDED QUERY (S3-oriented prompt)")
            print(r.grounded_query or "(not captured)")

            _hr("REACT QUERY (exact agent input)")
            if "agent" in r.stages_run or "agent_timeout" in r.stages_run or "react_delegate" in r.stages_run:
                print(r.react_query or "(not captured)")
            else:
                print("ReAct was not invoked; deterministic execution handled the plan.")

            if "react_delegate" in r.stages_run or "deterministic_fallback" in r.stages_run:
                _hr("REACT DELEGATION REASON")
                print(r.deterministic_fallback_reason or "(no reason captured)")

            # --- Execution trace ----------------------------------------------
            _hr("EXECUTION TRACE")
            print(r.trace or "(no trace captured)")

            _hr("FINAL EXECUTED CODE")
            print(r.final_code or "(none)")
            print(f"\nagent_tries={r.agent_tries}")

    # --- Stages run + latency -----------------------------------------------
    _hr("STAGES RUN / LATENCY (s)")
    print("stages_run:", r.stages_run)
    if "deterministic_fallback" in r.stages_run:
        print(
            "deterministic_fallback_reason:",
            r.deterministic_fallback_reason or "(none captured)",
        )
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


if __name__ == "__main__":
    main()
