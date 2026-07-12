"""
baselines/flash_fusion.py — Flash-Fusion baseline (B4).

S1 → S2 → guardrail(query + S2 grounding) → judge_plan → S3 → agent(grounded_query)
                                      [one S3 refinement on FAIL]

The stages build a grounded query that maps indirect concepts to exact column
names and injects sub-task structure. The agent resolves sub-tasks autonomously
via its ReAct loop. The guardrail runs after S2 (before S3) so OOS queries are
rejected early without wasting S3 + agent cost.

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (guardrail on query + S2 grounding)
    - Q1–Q3, Q5–Q9: executed=True, judge_verdict={"verdict": "PASS"|"FAIL"}

See CLAUDE.md §_run_flash_fusion for the full algorithm.
"""

from __future__ import annotations

import os
import sys
import time

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.pipeline.stages import (
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    Stage3_SubqueryGeneration,
)

FF_DEBUG = os.getenv("FF_DEBUG", "").lower() in ("1", "true", "yes")


def _build_grounded_query(
    query: str,
    raw_grounding: str,
    sub_queries: list,
    synthesis_hint: str,
) -> str:
    """Construct an enriched agent prompt from S2 grounding and S3 decomposition."""
    sub_tasks = "\n".join(f"- {sq}" for sq in sub_queries) if sub_queries else "(none)"
    return (
        f"{query}\n\n"
        f"Concept-to-column mappings (use these exactly):\n{raw_grounding}\n\n"
        f"Sub-tasks to address:\n{sub_tasks}\n\n"
        f"Hint: {synthesis_hint}"
    )


def run_flash_fusion(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """
    Execute the full Flash-Fusion pipeline.

    Args:
        query:   Raw natural language query.
        df:      WISDM DataFrame (deterministically enriched by BaselineRunner).
        client:  LLMClient instance for this run.
        r:       RunResult to populate.

    Returns:
        Populated RunResult.

    Algorithm:
        S1 → S2 → guardrail(query + S2 grounding) → reject or proceed
        S3 → build grounded_query
        judge_plan(query, grounding, sub_queries, synthesis_hint) → plan verdict
        if FAIL + suggestion:
            rerun S3 once with correction note appended to query context
            rebuild grounded_query
            judge_plan again → final plan verdict
        execute_single(grounded_query) → raw_answer, trace, details
        r.answer = raw_answer; r.executed = True
    """
    last_stage = "init"
    stage_latency_s = {
        "s1": 0.0,
        "s2": 0.0,
        "guardrail": 0.0,
        "s3": 0.0,
        "agent": 0.0,
    }

    def record_stage(stage_key: str, start_s: float) -> None:
        elapsed = max(0.0, time.time() - start_s)
        stage_latency_s[stage_key] = stage_latency_s.get(stage_key, 0.0) + elapsed
        r.stage_latency_s = dict(stage_latency_s)

    r.stage_latency_s = dict(stage_latency_s)
    try:
        meta_str = meta_to_str(build_column_metadata(df))

        # Stages 1 and 2 may run on a lighter sibling model (client.light) when a
        # --stage12-model is configured; client.light is client itself otherwise.
        stage1 = Stage1_ConceptExtraction(client.light)
        stage2 = Stage2_SchemaGrounding(client.light)
        stage3 = Stage3_SubqueryGeneration(client)
        executor = ExecutionLayer(df, client)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting S1 for query: {query[:80]}...", file=sys.stderr, flush=True)
        last_stage = "S1"
        stage_t0 = time.time()
        concepts = stage1.run(query)
        record_stage("s1", stage_t0)
        r.s1_concepts = concepts
        r.stages_run.append("S1")
        if FF_DEBUG:
            print(f"[FF_DEBUG] S1 complete. Concepts: {str(concepts)[:100]}...", file=sys.stderr, flush=True)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting S2...", file=sys.stderr, flush=True)
        last_stage = "S2"
        stage_t0 = time.time()
        grounding = stage2.run(concepts, query, meta_str, df)
        record_stage("s2", stage_t0)
        r.s2_grounding = grounding["raw_grounding"]
        r.stages_run.append("S2")
        if FF_DEBUG:
            print(f"[FF_DEBUG] S2 complete. Grounding: {grounding['raw_grounding'][:100]}...", file=sys.stderr, flush=True)

        # Guardrail runs after S2, before S3.
        # Pass query + S2 grounding so the guardrail can detect unmappable concepts
        # (e.g. OOS requests for data not in the schema) before wasting S3 + agent cost.
        post_s2_query = (
            f"{query}\n\n"
            f"Concept-to-column mappings produced by schema grounding:\n{grounding['raw_grounding']}"
        )
        last_stage = "guardrail"
        stage_t0 = time.time()
        proceed, reason = executor.guardrail(post_s2_query)
        record_stage("guardrail", stage_t0)
        r.stages_run.append("guardrail")
        if not proceed:
            r.rejected = True
            r.rejection_reason = reason
            r.alignment_explanation = (
                "Rejected after schema grounding because the query cannot be "
                f"answered from available dataset fields. Reason: {reason}"
            )
            r.answer = (
                "Query rejected. "
                f"Reason: {reason}. "
                "This request is not supported by the current dataset schema or task scope."
            )
            r.executed = False
            return r

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting S3...", file=sys.stderr, flush=True)
        last_stage = "S3"
        stage_t0 = time.time()
        sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
        record_stage("s3", stage_t0)
        r.s3_sub_queries = sub_result["sub_queries"]
        r.s3_synthesis_hint = sub_result["synthesis_hint"]
        r.stages_run.append("S3")
        if FF_DEBUG:
            print(f"[FF_DEBUG] S3 complete. Sub-queries: {str(r.s3_sub_queries)[:200]}...", file=sys.stderr, flush=True)

        grounded_query = _build_grounded_query(
            query,
            grounding["raw_grounding"],
            sub_result["sub_queries"],
            sub_result["synthesis_hint"],
        )

        # TEMPORARILY COMMENTED OUT: Judge plan and refinement loop
        # last_stage = "judge_plan"
        # plan_verdict = executor.judge_plan(
        #     query,
        #     grounding["raw_grounding"],
        #     sub_result["sub_queries"],
        #     sub_result["synthesis_hint"],
        # )
        # r.stages_run.append("judge_plan")
        #
        # if plan_verdict.get("verdict") == "FAIL" and plan_verdict.get("suggestion"):
        #     if FF_DEBUG:
        #         print(f"[FF_DEBUG] Judge plan failed, refining S3...", file=sys.stderr, flush=True)
        #     last_stage = "S3_refine"
        #     refine_input = (
        #         f"{query}\n\n"
        #         f"Plan correction note: {plan_verdict['suggestion']}"
        #     )
        #     refined_sub_result = stage3.run(refine_input, grounding["raw_grounding"], meta_str)
        #     sub_result = refined_sub_result
        #     r.s3_sub_queries = sub_result["sub_queries"]
        #     r.s3_synthesis_hint = sub_result["synthesis_hint"]
        #     r.stages_run.append("S3_refine")
        #     grounded_query = _build_grounded_query(
        #         query,
        #         grounding["raw_grounding"],
        #         sub_result["sub_queries"],
        #         sub_result["synthesis_hint"],
        #     )
        #     last_stage = "judge_plan_retry"
        #     plan_verdict = executor.judge_plan(
        #         query,
        #         grounding["raw_grounding"],
        #         sub_result["sub_queries"],
        #         sub_result["synthesis_hint"],
        #     )
        #     r.stages_run.append("judge_plan_retry")
        #
        # r.judge_verdict = plan_verdict
        # r.alignment_explanation = executor.explain_alignment(query, plan_verdict)

        if FF_DEBUG:
            print(f"[FF_DEBUG] Starting agent execution...", file=sys.stderr, flush=True)
        last_stage = "agent"
        stage_t0 = time.time()
        raw_answer, trace, details = executor.execute_single(grounded_query)
        record_stage("agent", stage_t0)
        r.trace = trace
        r.executed = True
        r.final_code = details.final_code or ""
        r.agent_tries = details.tries
        r.execution_attempts = list(details.attempts)
        r.stages_run.append("agent")

        r.answer = raw_answer
        if FF_DEBUG:
            print(f"[FF_DEBUG] Flash-Fusion complete. Answer: {raw_answer[:100]}...", file=sys.stderr, flush=True)
        return r
    except Exception as e:
        if FF_DEBUG:
            import traceback
            print(f"[FF_DEBUG] Flash-Fusion FAILED at stage {last_stage}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        r.answer = f"[ERROR in {last_stage}] {type(e).__name__}: {e}"
        r.alignment_explanation = f"Flash-Fusion failed during {last_stage}: {e}"
        r.executed = False
        raise
