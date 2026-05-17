"""
baselines/flash_fusion.py — Flash-Fusion baseline (B4).

The complete pipeline: S1 → S2 (+ codebook) → S3 → guardrail →
per-sub-query pandas agent → synthesise → judge → [one synthesis retry if FAIL].

This baseline adds two key components over AutoIOT-Only:
  1. Structured query rewriting (S1+S2+S3) with activity codebook injection,
     resolving semantic group names and grounding abstract concepts.
  2. A post-execution LLM judge that verifies intent alignment and triggers
     one synthesis retry with the judge's suggestion if the verdict is FAIL.

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (S2 UNMAPPABLE or guardrail)
  - Q1–Q3, Q5–Q9: executed=True, judge_verdict={"verdict": "PASS"|"FAIL"}
  - Flash-Fusion should outperform AutoIOT-Only on Q2, Q3, Q5, Q6 (codebook + magnitude)

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

    Implementation steps (see CLAUDE.md §_run_flash_fusion):

        Setup:
            meta_str = meta_to_str(build_column_metadata(df))
            stage1 = Stage1_ConceptExtraction(client)
            stage2 = Stage2_SchemaGrounding(client)
            if adapter: stage2.codebook_str = adapter.get_codebook_str()
            stage3 = Stage3_SubqueryGeneration(client)
            executor = ExecutionLayer(df, client)

        Stage 1:
            concepts = stage1.run(query)
            r.stages_run.append("S1")

        Stage 2:
            grounding = stage2.run(concepts, query, meta_str, df)
            r.stages_run.append("S2")
            if grounding["unmappable"]:
                r.rejected = True
                r.rejection_reason = f"Unmappable concepts: {grounding['unmappable']}"
                r.answer = f"Query rejected: {r.rejection_reason}"
                return r

        Guardrail:
            proceed, reason = executor.guardrail(query)
            r.stages_run.append("guardrail")
            if not proceed:
                r.rejected = True; r.rejection_reason = reason
                r.answer = f"Query rejected: {reason}"
                return r

        Stage 3:
            sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
            r.stages_run.append("S3")

        Agent execution (one agent call per sub-query):
            sub_answers = []
            for sq in sub_result["sub_queries"]:
                executor.reset_agent()
                raw_ans, trace, details = executor.execute_single(sq)
                sub_answers.append(raw_ans)
                r.agent_tries += details.tries
                if details.final_code:
                    r.final_code = details.final_code  # keep last non-empty code
            r.executed = True
            r.stages_run.append("agent")

        Synthesis:
            synthesis = executor.synthesize(query, sub_answers, sub_result["synthesis_hint"])
            r.stages_run.append("synthesis")

        Judge:
            verdict = executor.judge_result(query, r.final_code, synthesis)
            r.stages_run.append("judge")
            r.judge_verdict = verdict

        Retry (one time only if FAIL):
            if verdict.get("verdict") == "FAIL" and verdict.get("suggestion"):
                retry_hint = sub_result["synthesis_hint"] + " Additionally: " + verdict["suggestion"]
                synthesis = executor.synthesize(query, sub_answers, retry_hint)
                r.stages_run.append("synthesis_retry")

        Finalise:
            r.answer = synthesis
            return r
    """
    meta_str = meta_to_str(build_column_metadata(df))

    stage1 = Stage1_ConceptExtraction(client)
    stage2 = Stage2_SchemaGrounding(client)
    if adapter is not None:
        stage2.codebook_str = adapter.get_codebook_str()
    stage3 = Stage3_SubqueryGeneration(client)
    executor = ExecutionLayer(df, client)

    concepts = stage1.run(query)
    r.stages_run.append("S1")

    grounding = stage2.run(concepts, query, meta_str, df)
    r.stages_run.append("S2")
    if grounding["unmappable"]:
        r.rejected = True
        r.rejection_reason = (
            f"Unmappable concepts: {grounding['unmappable']}"
        )
        r.answer = f"Query rejected: {r.rejection_reason}"
        r.executed = False
        return r

    proceed, reason = executor.guardrail(query)
    r.stages_run.append("guardrail")
    if not proceed:
        r.rejected = True
        r.rejection_reason = reason
        r.answer = f"Query rejected: {reason}"
        r.executed = False
        return r

    sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
    r.stages_run.append("S3")

    sub_answers: list[str] = []
    for sq in sub_result["sub_queries"]:
        executor.reset_agent()
        raw_ans, trace, details = executor.execute_single(sq)
        sub_answers.append(raw_ans)
        r.agent_tries += details.tries
        if details.final_code:
            r.final_code = details.final_code
        if trace:
            r.trace = (r.trace + "\n---\n" + trace) if r.trace else trace
    r.executed = True
    r.stages_run.append("agent")

    synthesis = executor.synthesize(
        query, sub_answers, sub_result["synthesis_hint"]
    )
    r.stages_run.append("synthesis")

    verdict = executor.judge_result(query, r.final_code, synthesis)
    r.stages_run.append("judge")
    r.judge_verdict = verdict

    if verdict.get("verdict") == "FAIL" and verdict.get("suggestion"):
        retry_hint = (
            f"{sub_result['synthesis_hint']} Additionally: {verdict['suggestion']}"
        )
        synthesis = executor.synthesize(query, sub_answers, retry_hint)
        r.stages_run.append("synthesis_retry")

    r.answer = synthesis
    return r
