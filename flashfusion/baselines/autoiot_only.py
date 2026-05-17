"""
baselines/autoiot_only.py — AutoIOT-Only baseline.

Injects schema metadata (column descriptions) as context, runs a guardrail
check on the raw query, then hands the original query directly to the pandas
DataFrame agent without any Stage 1/2/3 concept extraction or codebook injection.

This baseline demonstrates the value of real code execution but exposes the
weakness of missing semantic grounding:
  - Cannot resolve English activity group names to letter codes (no codebook)
  - Cannot reliably derive `magnitude` without explicit schema grounding
  - Has no post-execution judge for intent alignment

Expected benchmark behaviour:
  - Q4, Q10: rejected=True (guardrail)
  - Q1, Q7, Q8: executed=True, likely correct
  - Q2, Q3, Q5, Q6: executed=True, potentially wrong proxy/filter

See CLAUDE.md §_run_autoiot_only for the full algorithm.
"""

from __future__ import annotations

from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.pipeline.runner import LLMClient, RunResult


def run_autoiot_only(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """
    Execute the AutoIOT-Only baseline.

    Args:
        query:  Raw natural language query.
        df:     Enriched WISDM DataFrame (note: magnitude/activity_name may not be
                present if adapter was not applied — AutoIOT-Only does not apply it).
        client: LLMClient instance for this run.
        r:      RunResult to populate.

    Returns:
        Populated RunResult.

    Implementation steps (see CLAUDE.md §_run_autoiot_only):
        1. executor = ExecutionLayer(df, client)
        2. proceed, reason = executor.guardrail(query)
           r.stages_run.append("guardrail")
           if not proceed:
               r.rejected = True; r.rejection_reason = reason
               r.answer = f"Query rejected: {reason}"
               return r
        3. raw_answer, trace, details = executor.execute_single(query)
        4. r.answer = raw_answer
        5. r.trace = trace
        6. r.executed = True
        7. r.final_code = details.final_code
        8. r.agent_tries = details.tries
        9. r.stages_run.append("agent")
        10. r.judge_verdict = {}  # AutoIOT-Only has no judge
        11. return r

    Note: AutoIOT-Only does NOT call Stage 1/2/3. The guardrail uses the raw query
    with schema metadata that ExecutionLayer builds from df.columns internally.
    """
    executor = ExecutionLayer(df, client)
    proceed, reason = executor.guardrail(query)
    r.stages_run.append("guardrail")
    if not proceed:
        r.rejected = True
        r.rejection_reason = reason
        r.answer = f"Query rejected: {reason}"
        r.executed = False
        return r

    raw_answer, trace, details = executor.execute_single(query)
    r.answer = raw_answer
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code
    r.agent_tries = details.tries
    r.stages_run.append("agent")
    r.judge_verdict = {}
    return r
