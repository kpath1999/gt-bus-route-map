"""
baselines/autoiot_only.py — AutoIOT-Only baseline.

Hands the raw query directly to the pandas DataFrame agent without any
Stage 1/2/3 concept extraction, codebook injection, or feasibility guardrail.

This baseline demonstrates the value of real code execution but exposes the
weakness of missing semantic grounding:
  - Cannot resolve English activity group names to letter codes (no codebook)
  - Cannot reliably derive `magnitude` without explicit schema grounding
  - Has no post-execution judge for intent alignment

Expected benchmark behaviour:
    - All queries: execution attempted
    - Q1, Q7, Q8: likely correct
    - Q2, Q3, Q5, Q6: potentially wrong proxy/filter
    - Q4, Q10: may produce unsupported/low-quality answers because no feasibility gate

See CLAUDE.md §_run_autoiot_only for the full algorithm.
"""

from __future__ import annotations

from flashfusion.pipeline.executor import ExecutionLayer
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
        2. raw_answer, trace, details = executor.execute_single(query)
        3. r.answer = raw_answer
        4. r.trace = trace
        5. r.executed = True
        6. r.final_code = details.final_code
        7. r.agent_tries = details.tries
        8. r.stages_run.append("agent")
        9. r.judge_verdict = {}  # AutoIOT-Only has no judge
        10. return r

    Note: AutoIOT-Only does NOT call Stage 1/2/3 and does not run guardrail.
    """
    executor = ExecutionLayer(df, client)
    raw_answer, trace, details = executor.execute_single(query)
    r.answer = raw_answer
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code
    r.agent_tries = details.tries
    r.stages_run.append("agent")
    r.judge_verdict = {}
    return r
