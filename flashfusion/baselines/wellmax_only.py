"""
baselines/wellmax_only.py — WellMax-Only baseline (B3).

Runs the full 3-stage query rewriting pipeline (S1 → S2 → S3) plus a guardrail
check, then makes a single grounded LLM call that DESCRIBES what the computation
would yield — without executing any pandas code.

This baseline shows what schema-aware prompt rewriting alone contributes, without
the accuracy gains from real execution. It should:
  - Correctly identify UNMAPPABLE concepts (e.g. heart_rate) and reject
  - Produce method descriptions that are schema-correct
  - Always return executed=False

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (UNMAPPABLE / guardrail)
  - Q1–Q3, Q5–Q9: executed=False, answer describes the methodology

See CLAUDE.md §_run_wellmax_only for the full algorithm.
"""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.runner import LLMClient, RunResult
from flashfusion.pipeline.stages import (
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    Stage3_SubqueryGeneration,
)


_WELLMAX_SYSTEM = (
    "You are a precise data analyst. Based on the schema grounding provided, "
    "describe what the computation would yield if executed against the WISDM dataset. "
    "Cite specific column operations and expected patterns. Do NOT execute code."
)

_WELLMAX_HUMAN_TEMPLATE = """\
Original question: {query}

Schema grounding:
{grounding_raw}

Sub-questions to address:
{sub_questions}

Synthesis guidance: {synthesis_hint}

Describe what the data would show, referencing specific columns and operations.\
"""


def run_wellmax_only(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
    adapter=None,
) -> RunResult:
    """
    Execute the WellMax-Only baseline.

    Args:
        query:   Raw natural language query.
        df:      Enriched WISDM DataFrame (derived features already applied by caller).
        client:  LLMClient instance for this run.
        r:       RunResult to populate.
        adapter: Optional WISDMAdapter for codebook injection into Stage 2.

    Returns:
        Populated RunResult.

    Implementation steps (see CLAUDE.md §_run_wellmax_only):
        1. meta_str = meta_to_str(build_column_metadata(df))
        2. Initialise Stage1, Stage2 (inject codebook_str if adapter), Stage3
        3. concepts = stage1.run(query); r.stages_run.append("S1")
        4. grounding = stage2.run(concepts, query, meta_str, df)
           r.stages_run.append("S2")
           if grounding["unmappable"]: set rejected + reason + answer; return r
        5. sub_result = stage3.run(query, grounding["raw_grounding"], meta_str)
           r.stages_run.append("S3")
        6. executor = ExecutionLayer(df, client)
           proceed, reason = executor.guardrail(query)
           r.stages_run.append("guardrail")
           if not proceed: set rejected + reason + answer; return r
        7. Build grounded prompt using _WELLMAX_HUMAN_TEMPLATE
        8. Single LLM call (stage="wellmax_synthesis")
        9. r.answer = response
           r.executed = False
           r.stages_run.append("llm_synthesis")
           return r
    """
    meta_str = meta_to_str(build_column_metadata(df))

    stage1 = Stage1_ConceptExtraction(client)
    stage2 = Stage2_SchemaGrounding(client)
    if adapter is not None:
        stage2.codebook_str = adapter.get_codebook_str()
    stage3 = Stage3_SubqueryGeneration(client)

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

    executor = ExecutionLayer(df, client)
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

    sub_questions = "\n".join(
        f"  {i + 1}. {q}" for i, q in enumerate(sub_result["sub_queries"])
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _WELLMAX_SYSTEM),
            ("human", _WELLMAX_HUMAN_TEMPLATE),
        ]
    )
    chain = prompt | client.llm | StrOutputParser()
    r.answer = client.invoke_chain(
        chain,
        {
            "query": query,
            "grounding_raw": grounding["raw_grounding"],
            "sub_questions": sub_questions,
            "synthesis_hint": sub_result["synthesis_hint"],
        },
        stage="wellmax_synthesis",
    )
    r.executed = False
    r.stages_run.append("llm_synthesis")
    return r
