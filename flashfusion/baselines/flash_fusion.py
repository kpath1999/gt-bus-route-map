"""
baselines/flash_fusion.py — Flash-Fusion baseline (B4).

S1 → S2 → S3 → guardrail(grounded_query) → agent(grounded_query) → judge
                                               [one agent retry on FAIL]

The stages build a grounded query that maps indirect concepts to exact column
names and injects sub-task structure. The agent resolves sub-tasks autonomously
via its ReAct loop. The judge verifies intent alignment and triggers one agent
retry with the correction note appended to the grounded query if FAIL.

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
    adapter=None,
) -> RunResult:
    """
    Execute the full Flash-Fusion pipeline.

    Args:
        query:   Raw natural language query.
        df:      Enriched WISDM DataFrame (WISDMAdapter.get_derived_features()
                 already applied by BaselineRunner before dispatch).
        client:  LLMClient instance for this run.
        r:       RunResult to populate.
        adapter: Optional WISDMAdapter — used to inject codebook_str into Stage 2.

    Returns:
        Populated RunResult.

    Algorithm:
        S1 → S2 → S3 → build grounded_query
        guardrail(grounded_query) → reject or proceed
        execute_single(grounded_query) → raw_answer, trace, details
        judge_result(query, final_code, raw_answer) → verdict
        if FAIL + suggestion:
            reset_agent()
            execute_single(grounded_query + correction_note) → retry
            judge_result again → final verdict
        r.answer = final raw_answer; r.executed = True
    """
    meta_str = meta_to_str(build_column_metadata(df))

    stage1 = Stage1_ConceptExtraction(client)
    stage2 = Stage2_SchemaGrounding(client)
    if adapter is not None:
        stage2.codebook_str = adapter.get_codebook_str()
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

    raw_answer, trace, details = executor.execute_single(grounded_query)
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code or ""
    r.agent_tries = details.tries
    r.stages_run.append("agent")

    verdict = executor.judge_result(query, r.final_code, raw_answer)
    r.stages_run.append("judge")
    r.judge_verdict = verdict
    r.alignment_explanation = executor.explain_alignment(query, verdict)

    if verdict.get("verdict") == "FAIL" and verdict.get("suggestion"):
        retry_query = grounded_query + f"\n\nCorrection note: {verdict['suggestion']}"
        executor.reset_agent()
        retry_answer, retry_trace, retry_details = executor.execute_single(retry_query)
        if retry_trace:
            r.trace = (r.trace + "\n---[RETRY]---\n" + retry_trace) if r.trace else retry_trace
        if retry_details.final_code:
            r.final_code = retry_details.final_code
        r.agent_tries += retry_details.tries
        r.stages_run.append("agent_retry")
        retry_verdict = executor.judge_result(query, r.final_code, retry_answer)
        r.stages_run.append("judge_retry")
        r.judge_verdict = retry_verdict
        r.alignment_explanation = executor.explain_alignment(query, retry_verdict)
        raw_answer = retry_answer

    r.answer = raw_answer
    return r
