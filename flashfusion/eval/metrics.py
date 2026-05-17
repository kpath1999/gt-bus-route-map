"""
eval/metrics.py — Accuracy, latency, and cost metrics for benchmark results.

Functions:
  compute_accuracy(result, ground_truth=None) → dict
  compute_latency(result) → dict
  compute_cost(result) → dict
  aggregate_metrics(results) → pd.DataFrame

Accuracy scoring rules (see CLAUDE.md §eval/metrics.py):
  1.0  — executed=True AND judge_verdict["verdict"] == "PASS"
  0.5  — executed=True AND (judge_verdict["verdict"] == "FAIL" OR judge_verdict == {})
  0.0  — rejected=True OR executed=False

Note: AutoIOT-Only always has judge_verdict == {} (no judge). Its score is 0.5
when it executes, which reflects unverified alignment — intentionally lower than
Flash-Fusion's 1.0 on correctly-judged runs.
"""

from __future__ import annotations

import pandas as pd

from flashfusion.config import (
    ACCURACY_EXEC_SCORE,
    ACCURACY_FAIL_SCORE,
    ACCURACY_PASS_SCORE,
)
from flashfusion.pipeline.runner import RunResult


def compute_accuracy(
    result: RunResult,
    ground_truth: str | None = None,
) -> dict:
    """
    Compute the accuracy score for a single benchmark result.

    Scoring logic:
        if result.rejected or not result.executed:
            score = ACCURACY_FAIL_SCORE  (0.0)
        elif result.judge_verdict.get("verdict") == "PASS":
            score = ACCURACY_PASS_SCORE  (1.0)
        elif result.judge_verdict.get("verdict") == "FAIL":
            score = ACCURACY_EXEC_SCORE  (0.5)
        else:  # executed but no judge (AutoIOT-Only)
            score = ACCURACY_EXEC_SCORE  (0.5)

    If ground_truth is provided (future extension), it can override the judge-based
    score via an exact-match or substring check. Leave as None for current benchmarks.

    Args:
        result:       RunResult from BaselineRunner.run().
        ground_truth: Optional reference answer string (unused unless provided).

    Returns:
        dict with keys:
            score      (float)        — 0.0, 0.5, or 1.0
            executed   (bool)         — whether pandas agent ran code
            judge_pass (bool | None)  — True/False from judge, None if no judge
            rejected   (bool)         — whether query was rejected by guardrail/S2
    """
    verdict = result.judge_verdict.get("verdict") if result.judge_verdict else None
    judge_pass: bool | None
    if verdict == "PASS":
        judge_pass = True
    elif verdict == "FAIL":
        judge_pass = False
    else:
        judge_pass = None

    if result.rejected or not result.executed:
        score = ACCURACY_FAIL_SCORE
    elif verdict == "PASS":
        score = ACCURACY_PASS_SCORE
    elif verdict == "FAIL":
        score = ACCURACY_EXEC_SCORE
    else:
        score = ACCURACY_EXEC_SCORE

    return {
        "score": score,
        "executed": result.executed,
        "judge_pass": judge_pass,
        "rejected": result.rejected,
    }


def compute_latency(result: RunResult) -> dict:
    """
    Extract latency metrics from a RunResult.

    Args:
        result: RunResult from BaselineRunner.run().

    Returns:
        dict with keys:
            total_s (float) — end-to-end wall-clock latency in seconds
    """
    return {"total_s": result.latency_s}


def compute_cost(result: RunResult) -> dict:
    """
    Extract cost metrics from a RunResult.

    Args:
        result: RunResult from BaselineRunner.run().

    Returns:
        dict with keys:
            total_usd     (float) — total estimated cost in USD
            input_tokens  (int)   — total estimated input tokens
            output_tokens (int)   — total estimated output tokens
    """
    return {
        "total_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def aggregate_metrics(results: list[RunResult]) -> pd.DataFrame:
    """
    Build a tidy DataFrame of per-(baseline, query) metrics.

    Each row corresponds to one RunResult. The DataFrame is sorted by
    (baseline, query_id) for consistent reporting.

    Args:
        results: List of RunResult objects from run_benchmark().

    Returns:
        pd.DataFrame with columns:
            baseline        (str)
            query_id        (int)   — 1-indexed position in WISDM_QUERIES
            accuracy_score  (float) — from compute_accuracy()
            latency_s       (float) — from compute_latency()
            cost_usd        (float) — from compute_cost()
            input_tokens    (int)
            output_tokens   (int)
            executed        (bool)
            rejected        (bool)
            judge_verdict   (str)   — "PASS", "FAIL", "N/A", or "UNKNOWN"
            stages_run      (str)   — comma-joined list of stage names

    Implementation:
        from flashfusion.eval.queries import WISDM_QUERIES
        Build a lookup: query_text → query_id from WISDM_QUERIES.
        For each result:
            acc = compute_accuracy(result)
            lat = compute_latency(result)
            cost = compute_cost(result)
            query_id = lookup.get(result.query, 0)
            Append row dict to list.
        return pd.DataFrame(rows).sort_values(["baseline", "query_id"]).reset_index(drop=True)
    """
    from flashfusion.eval.queries import WISDM_QUERIES

    query_lookup = {q["text"]: q["id"] for q in WISDM_QUERIES}
    rows: list[dict] = []
    for idx, r in enumerate(results, start=1):
        acc = compute_accuracy(r)
        lat = compute_latency(r)
        cost = compute_cost(r)
        query_id = query_lookup.get(r.query, idx)
        rows.append(
            {
                "baseline": r.baseline,
                "query_id": query_id,
                "accuracy_score": acc["score"],
                "latency_s": lat["total_s"],
                "cost_usd": cost["total_usd"],
                "input_tokens": cost["input_tokens"],
                "output_tokens": cost["output_tokens"],
                "executed": r.executed,
                "rejected": r.rejected,
                "judge_verdict": (
                    r.judge_verdict.get("verdict", "N/A")
                    if r.judge_verdict
                    else "N/A"
                ),
                "stages_run": ",".join(r.stages_run),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["baseline", "query_id"]).reset_index(drop=True)
    return df
