"""
baselines/wellmax_only.py — WellMax-Only baseline.

Runs the full 3-stage query rewriting pipeline (S1 → S2 → S3), constructs a
grounded query from the stage outputs, then executes that query against the
pandas DataFrame agent — without a judge.

The stages map indirect concepts (e.g. "sedentary", "hand-related") to exact
column names and letter codes before the agent sees the query.

Expected benchmark behaviour:
    - All queries: grounded execution attempted
    - Q1–Q3, Q5–Q9: typically stronger execution due to grounding
    - Q4, Q10: may still produce weak/unsupported results because no guardrail

See CLAUDE.md §_run_wellmax_only for the full algorithm.
"""

from __future__ import annotations

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.features import resolve_grounded_features
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


def run_wellmax_only(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """
    Execute the WellMax-Only baseline.

    Args:
        query:   Raw natural language query.
        df:      WISDM DataFrame (deterministically enriched by BaselineRunner).
        client:  LLMClient instance for this run.
        r:       RunResult to populate.

    Returns:
        Populated RunResult.

    Algorithm:
        1. S1: concept extraction
        2. S2: schema grounding
        3. S3: sub-query generation
        4. Build grounded_query from S2 mappings + S3 sub-tasks
        5. execute_single(grounded_query) — single agent call
        6. r.executed = True; r.judge_verdict = {} (no judge)
    """
    meta_str = meta_to_str(build_column_metadata(df))

    stage1 = Stage1_ConceptExtraction(client)
    stage2 = Stage2_SchemaGrounding(client)
    stage3 = Stage3_SubqueryGeneration(client)

    concepts = stage1.run(query, df)
    r.s1_concepts = concepts
    r.stages_run.append("S1")

    grounding = stage2.run(concepts, query, meta_str, df)
    r.s2_grounding = grounding["raw_grounding"]
    r.stages_run.append("S2")

    df, derived_features = resolve_grounded_features(df, grounding["raw_grounding"])
    if derived_features:
        meta_str = meta_to_str(build_column_metadata(df))

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

    executor = ExecutionLayer(df, client)  # df includes any features resolved above
    raw_answer, trace, details = executor.execute_single(grounded_query)
    r.answer = raw_answer
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code
    r.agent_tries = details.tries
    r.execution_attempts = list(details.attempts)
    r.stages_run.append("agent")
    r.judge_verdict = {}
    return r
