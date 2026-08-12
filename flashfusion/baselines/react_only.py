"""
baselines/react_only.py — ReAct-Only baseline.

Hands the raw query directly to the pandas DataFrame agent without any
Stage 1/2/3 concept extraction, codebook injection, or feasibility guardrail.

This baseline uses the paper-style ReAct prompt and can independently select
Flash-Fusion's resilient parser or the original strict parser for ablations.

The schema prefix (column names + dtypes + row count) is kept to provide the
minimum DataFrame context required for the pandas tool to function correctly.

Expected benchmark behaviour:
    - All queries: execution attempted
    - Q1, Q7, Q8: likely correct
    - Q2, Q3, Q5, Q6: potentially wrong proxy/filter (no codebook, no grounding)
    - Q9–Q12: may loop or stall until max_iterations exhausted (no feasibility gate)
    - Parse failures raise OutputParserException and are NOT silently recovered

See CLAUDE.md §_run_agent_only for the original algorithm this replaced.
"""

from __future__ import annotations

import os

from flashfusion.pipeline.executor import ExecutionLayer, ReActResult
from flashfusion.pipeline.runner import LLMClient, RunResult


def run_react_only(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """
    Execute the ReAct-Only baseline (paper-faithful).

    Args:
        query:  Raw natural language query.
        df:     WISDM DataFrame (unenriched — no magnitude/activity_name added).
        client: LLMClient instance for this run.
        r:      RunResult to populate.

    Returns:
        Populated RunResult.

    Key differences from earlier Agent-Only implementation:
        - Uses default ReActSingleInputOutputParser (no ResilientReActOutputParser)
        - AgentExecutor constructed without handle_parsing_errors
        - max_iterations unchanged at 6

    Steps:
        1. executor = ExecutionLayer(df, client, include_abstention_clause=True)
        2. result = executor.execute_single(query)
        3. r.answer = raw_answer
        4. r.trace = trace
        5. r.executed = True
        6. r.final_code = details.final_code
        7. r.agent_tries = details.tries
        8. r.stages_run.append("react_agent")
        9. r.judge_verdict = {}  # ReAct-Only has no judge
        10. return r

    Note: ReAct-Only does NOT call Stage 1/2/3 and does not run guardrail.
    """
    no_abstention = os.environ.get("REACT_NO_ABSTENTION", "0").strip().lower() in {"1", "true", "yes"}
    no_resilient_parser = os.environ.get("REACT_STRICT_PARSER", "0").strip().lower() in {"1", "true", "yes"}
    executor = ExecutionLayer(
        df,
        client,
        include_abstention_clause=not no_abstention,
        use_resilient_parser=not no_resilient_parser,
    )
    result = executor.execute_single(query)
    if isinstance(result, tuple):
        raw_answer, trace, details = result
        legacy_rejection = raw_answer.strip() if raw_answer.strip().startswith("REJECT:") else None
        result = ReActResult(
            raw_answer=raw_answer,
            trace=trace,
            rejected=legacy_rejection is not None,
            rejection_reason=legacy_rejection,
            details=details,
        )
    r.answer = result.raw_answer
    r.trace = result.trace
    r.executed = not result.rejected
    r.rejected = result.rejected
    r.rejection_reason = result.rejection_reason or ""
    r.final_code = result.details.final_code
    r.agent_tries = result.details.tries
    r.execution_attempts = list(result.details.attempts)
    r.stages_run.append("react_agent")
    r.judge_verdict = {}
    return r
