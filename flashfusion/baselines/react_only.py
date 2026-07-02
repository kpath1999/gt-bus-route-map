"""
baselines/react_only.py — ReAct-Only baseline.

Hands the raw query directly to the pandas DataFrame agent without any
Stage 1/2/3 concept extraction, codebook injection, or feasibility guardrail.

This baseline is a paper-faithful implementation of ReAct (Yao et al., 2022).
It uses the default ReActSingleInputOutputParser (no resilient fallback) and
omits handle_parsing_errors so that parse failures propagate naturally, matching
the original evaluation conditions.

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

from flashfusion.pipeline.executor import ExecutionLayer
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
        1. executor = ExecutionLayer(df, client, react_faithful=True)
        2. raw_answer, trace, details = executor.execute_single(query)
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
    executor = ExecutionLayer(df, client, react_faithful=True)
    raw_answer, trace, details = executor.execute_single(query)
    r.answer = raw_answer
    r.trace = trace
    r.executed = True
    r.final_code = details.final_code
    r.agent_tries = details.tries
    r.execution_attempts = list(details.attempts)
    r.stages_run.append("react_agent")
    r.judge_verdict = {}
    return r
