"""
tests/test_metrics.py — Unit tests for eval/metrics.py accuracy scoring rules.

Run with: pytest flashfusion/tests/test_metrics.py -v
"""

from __future__ import annotations

import pytest

from flashfusion.config import (
    ACCURACY_EXEC_SCORE,
    ACCURACY_FAIL_SCORE,
    ACCURACY_PASS_SCORE,
)
from flashfusion.eval.metrics import aggregate_metrics, compute_accuracy
from flashfusion.pipeline.runner import RunResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_result(**kwargs) -> RunResult:
    """
    Build a RunResult with sensible defaults, overriding with kwargs.
    """
    defaults = dict(
        baseline="FLASH_FUSION",
        model="llama-3.3-70b-versatile",
        query="Test query",
        answer="Answer text",
        executed=True,
        rejected=False,
        judge_verdict={},
        latency_s=1.0,
        cost_usd=0.001,
        input_tokens=100,
        output_tokens=50,
        stages_run=["S1", "S2", "S3", "guardrail", "agent", "judge"],
    )
    defaults.update(kwargs)
    return RunResult(**defaults)


# ---------------------------------------------------------------------------
# Accuracy scoring tests
# ---------------------------------------------------------------------------

class TestComputeAccuracy:
    """Validate the 0.0 / 0.5 / 1.0 accuracy scoring rules."""

    def test_flash_fusion_pass_scores_10(self):
        """
        Flash-Fusion: executed=True + judge verdict PASS → score == 1.0
        """
        r = make_result(
            baseline="FLASH_FUSION",
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "PASS", "issue": "", "suggestion": ""},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_PASS_SCORE  # 1.0

    def test_flash_fusion_fail_scores_05(self):
        """
        Flash-Fusion: executed=True + judge verdict FAIL → score == 0.5
        """
        r = make_result(
            baseline="FLASH_FUSION",
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "FAIL", "issue": "Wrong column", "suggestion": "Fix it"},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_EXEC_SCORE  # 0.5

    def test_autoiot_only_no_judge_scores_05(self):
        """
        AutoIOT-Only: executed=True + no judge (judge_verdict == {}) → score == 0.5
        """
        r = make_result(
            baseline="AUTOIOT_ONLY",
            executed=True,
            rejected=False,
            judge_verdict={},  # AutoIOT-Only has no judge
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_EXEC_SCORE  # 0.5

    def test_rejected_scores_00(self):
        """
        Any baseline: rejected=True → score == 0.0
        """
        r = make_result(
            baseline="WELLMAX_ONLY",
            executed=False,
            rejected=True,
            rejection_reason="heart_rate column does not exist",
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_FAIL_SCORE  # 0.0

    def test_llm_only_not_executed_scores_00(self):
        """
        LLM-Only: executed=False + rejected=False → score == 0.0
        """
        r = make_result(
            baseline="LLM_ONLY",
            executed=False,
            rejected=False,
            judge_verdict={},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_FAIL_SCORE  # 0.0

    def test_returns_correct_flags(self):
        """
        compute_accuracy() should return correct executed, rejected, and judge_pass flags.
        """
        r = make_result(
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "PASS"},
        )
        acc = compute_accuracy(r)
        assert acc["executed"] is True
        assert acc["rejected"] is False
        assert acc["judge_pass"] is True

    def test_judge_pass_none_when_no_judge(self):
        """
        When judge_verdict == {}, judge_pass should be None (no judge ran).
        """
        r = make_result(
            baseline="AUTOIOT_ONLY",
            executed=True,
            rejected=False,
            judge_verdict={},
        )
        acc = compute_accuracy(r)
        assert acc["judge_pass"] is None


# ---------------------------------------------------------------------------
# aggregate_metrics tests
# ---------------------------------------------------------------------------

class TestAggregateMetrics:
    """Tests for aggregate_metrics()."""

    def test_returns_dataframe_with_required_columns(self):
        """
        aggregate_metrics() should return a DataFrame with standard columns.
        """
        results = [
            make_result(baseline="LLM_ONLY", executed=False, rejected=False, query="Q1"),
            make_result(
                baseline="FLASH_FUSION",
                executed=True,
                rejected=False,
                judge_verdict={"verdict": "PASS"},
                query="Q1",
            ),
        ]
        df = aggregate_metrics(results)
        required = {"baseline", "accuracy_score", "latency_s", "cost_usd"}
        assert required.issubset(set(df.columns))

    def test_flash_fusion_higher_accuracy_than_llm_only(self):
        """
        On a PASS result, Flash-Fusion should have higher accuracy than LLM-Only.
        """
        results = [
            make_result(baseline="LLM_ONLY", executed=False, rejected=False, query="Q1"),
            make_result(
                baseline="FLASH_FUSION",
                executed=True,
                rejected=False,
                judge_verdict={"verdict": "PASS"},
                query="Q1",
            ),
        ]
        df = aggregate_metrics(results)
        ff_score = df[df["baseline"] == "FLASH_FUSION"]["accuracy_score"].iloc[0]
        llm_score = df[df["baseline"] == "LLM_ONLY"]["accuracy_score"].iloc[0]
        assert ff_score > llm_score

    def test_aggregate_multiple_queries(self):
        """
        aggregate_metrics() should handle multiple queries per baseline.
        """
        results = [
            make_result(baseline="LLM_ONLY", executed=False, rejected=False, query="Q1"),
            make_result(baseline="LLM_ONLY", executed=False, rejected=False, query="Q2"),
            make_result(
                baseline="FLASH_FUSION",
                executed=True,
                rejected=False,
                judge_verdict={"verdict": "PASS"},
                query="Q1",
            ),
        ]
        df = aggregate_metrics(results)
        assert len(df) == 3  # one row per result
