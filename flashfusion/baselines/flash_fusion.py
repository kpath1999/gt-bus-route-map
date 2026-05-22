"""
baselines/flash_fusion.py — Flash-Fusion baseline (B4).

S1 → S2 → S3 → guardrail(grounded_query) → judge_plan → agent(grounded_query)
                                      [one S3 refinement on FAIL]

The stages build a grounded query that maps indirect concepts to exact column
names and injects sub-task structure. The agent resolves sub-tasks autonomously
via its ReAct loop. The pre-agent judge verifies that Stage-3 decomposition is
likely to answer the original intent and can request one Stage-3 refinement.

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (guardrail on grounded_query)
    - Q1–Q3, Q5–Q9: executed=True, judge_verdict={"verdict": "PASS"|"FAIL"}

See CLAUDE.md §_run_flash_fusion for the full algorithm.
"""

from __future__ import annotations

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.pipeline.stages import (
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    Stage3_SubqueryGeneration,
)


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
        S1 → S2 → S3 → build grounded_query
        guardrail(grounded_query) → reject or proceed
        judge_plan(query, grounding, sub_queries, synthesis_hint) → plan verdict
        if FAIL + suggestion:
            rerun S3 once with correction note appended to query context
            rebuild grounded_query
            judge_plan again → final plan verdict
        execute_single(grounded_query) → raw_answer, trace, details
        r.answer = raw_answer; r.executed = True
    """
    meta_str = meta_to_str(build_column_metadata(df))

    stage1 = Stage1_ConceptExtraction(client)
    stage2 = Stage2_SchemaGrounding(client)
    stage3 = Stage3_SubqueryGeneration(client)
    executor = ExecutionLayer(df, client)

    concepts = stage1.run(query)
    r.s1_concepts = concepts
    r.stages_run.append("S1")

    grounding = stage2.run(concepts, query, meta_str, df)
    r.s2_grounding = grounding["raw_grounding"]
    r.stages_run.append("S2")

    sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
    r.s3_sub_queries = sub_result["sub_queries"]
    r.s3_synthesis_hint = sub_result["synthesis_hint"]
    r.stages_run.append("S3")

    grounded_query = _build_grounded_query(
        query,
        grounding["raw_grounding"],
        sub_result["sub_queries"],
        sub_result["synthesis_hint"],
    )

    proceed, reason = executor.guardrail(grounded_query)
    r.stages_run.append("guardrail")
    if not proceed:
        r.rejected = True
        r.rejection_reason = reason
        r.alignment_explanation = (
            "Rejected before execution because the grounded query cannot be "
            f"answered from available dataset fields. Reason: {reason}"
        )
        r.answer = (
            "Query rejected. "
            f"Reason: {reason}. "
            "This request is not supported by the current dataset schema or task scope."
        )
        r.executed = False
        return r

    plan_verdict = executor.judge_plan(
        query,
        grounding["raw_grounding"],
        sub_result["sub_queries"],
        sub_result["synthesis_hint"],
    )
    r.stages_run.append("judge_plan")

    if plan_verdict.get("verdict") == "FAIL" and plan_verdict.get("suggestion"):
        refine_input = (
            f"{query}\n\n"
            f"Plan correction note: {plan_verdict['suggestion']}"
        )
        refined_sub_result = stage3.run(refine_input, grounding["raw_grounding"], meta_str)
        sub_result = refined_sub_result
        r.s3_sub_queries = sub_result["sub_queries"]
        r.s3_synthesis_hint = sub_result["synthesis_hint"]
        r.stages_run.append("S3_refine")
        grounded_query = _build_grounded_query(
            query,
            grounding["raw_grounding"],
            sub_result["sub_queries"],
            sub_result["synthesis_hint"],
        )
        plan_verdict = executor.judge_plan(
            query,
            grounding["raw_grounding"],
            sub_result["sub_queries"],
            sub_result["synthesis_hint"],
        )
        r.stages_run.append("judge_plan_retry")

    r.judge_verdict = plan_verdict
    r.alignment_explanation = executor.explain_alignment(query, plan_verdict)

    raw_answer, trace, details = executor.execute_single(grounded_query)
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code or ""
    r.agent_tries = details.tries
    r.execution_attempts = list(details.attempts)
    r.stages_run.append("agent")

    r.answer = raw_answer
    return r
