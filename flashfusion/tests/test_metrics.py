"""
tests/test_metrics.py — Unit tests for eval/metrics.py LLM-verdict scoring rules.

Run with: pytest flashfusion/tests/test_metrics.py -v
"""

from __future__ import annotations

import pandas as pd

from flashfusion.config import (
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
    """Validate binary PASS/PARTIAL vs FAIL/UNSURE scoring rules."""

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

    def test_partial_scores_00(self):
        """
        PARTIAL is not emitted by the binary judge — treated as FAIL (0.0).
        """
        r = make_result(
            baseline="FLASH_FUSION",
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "PARTIAL", "issue": "", "suggestion": ""},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_FAIL_SCORE  # 0.0

    def test_fail_scores_00(self):
        """
        FAIL should count as incorrect (0.0).
        """
        r = make_result(
            baseline="FLASH_FUSION",
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "FAIL", "issue": "Wrong column", "suggestion": "Fix it"},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_FAIL_SCORE  # 0.0

    def test_unsure_scores_00(self):
        """
        UNSURE should count as incorrect (0.0) under strict policy.
        """
        r = make_result(
            baseline="WELLMAX_ONLY",
            executed=True,
            rejected=False,
            judge_verdict={"verdict": "UNSURE"},
        )
        acc = compute_accuracy(r)
        assert acc["score"] == ACCURACY_FAIL_SCORE  # 0.0

    def test_missing_verdict_scores_00(self):
        """
        Missing verdict should count as incorrect (0.0).
        """
        r = make_result(
            baseline="AUTOIOT_ONLY",
            executed=True,
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

    def test_judge_pass_false_when_no_judge(self):
        """
        Missing verdict should return judge_pass=False.
        """
        r = make_result(
            baseline="AUTOIOT_ONLY",
            executed=True,
            rejected=False,
            judge_verdict={},
        )
        acc = compute_accuracy(r)
        assert acc["judge_pass"] is False


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
        required = {"baseline", "gt_score", "latency_s", "cost_usd"}
        assert required.issubset(set(df.columns))

    def test_uses_binary_llm_score_for_gt_score(self):
        """
        gt_score should reflect binary llm_score values (1.0 for PASS, 0.0 for FAIL).
        """
        q1_text = "What is the maximum recorded x-acceleration for user 15?"
        q2_text = "How many total samples in the dataset are classified as the Walking activity?"
        q3_text = "What is the average y-accel value for user 5 during the Sitting activity?"
        q4_text = "Which user has the highest total number of recorded data samples?"
        results = [
            make_result(
                baseline="AUTOIOT_ONLY",
                executed=True,
                rejected=False,
                query=q1_text,
            ),
            make_result(
                baseline="FLASH_FUSION",
                executed=True,
                rejected=False,
                query=q2_text,
            ),
            make_result(
                baseline="WELLMAX_ONLY",
                executed=True,
                rejected=False,
                query=q3_text,
            ),
            make_result(
                baseline="FLASH_FUSION",
                executed=True,
                rejected=False,
                query=q4_text,
            ),
        ]

        judgments = pd.DataFrame(
            [
                {
                    "baseline": "AUTOIOT_ONLY",
                    "query_id": 1,
                    "llm_verdict": "PASS",
                    "llm_score": 1.0,
                },
                {
                    "baseline": "FLASH_FUSION",
                    "query_id": 2,
                    "llm_verdict": "FAIL",
                    "llm_score": 0.0,
                },
                {
                    "baseline": "WELLMAX_ONLY",
                    "query_id": 3,
                    "llm_verdict": "FAIL",
                    "llm_score": 0.0,
                },
                {
                    "baseline": "FLASH_FUSION",
                    "query_id": 4,
                    "llm_verdict": "PASS",
                    "llm_score": 1.0,
                },
            ]
        )

        df = aggregate_metrics(results, llm_judgments_df=judgments)

        by_key = {
            (row["baseline"], int(row["query_id"])): float(row["gt_score"])
            for _, row in df.iterrows()
        }
        assert by_key[("AUTOIOT_ONLY", 1)] == 1.0
        assert by_key[("FLASH_FUSION", 2)] == 0.0
        assert by_key[("WELLMAX_ONLY", 3)] == 0.0
        assert by_key[("FLASH_FUSION", 4)] == 1.0

    def test_missing_judgment_defaults_to_zero(self):
        """
        Rows without llm_judgment should be strict-0 with missing method.
        """
        q1_text = "What is the maximum recorded x-acceleration for user 15?"
        q2_text = "How many total samples in the dataset are classified as the Walking activity?"
        results = [
            make_result(baseline="FLASH_FUSION", executed=True, rejected=False, query=q1_text),
            make_result(baseline="FLASH_FUSION", executed=True, rejected=False, query=q2_text),
        ]
        judgments = pd.DataFrame(
            [
                {
                    "baseline": "FLASH_FUSION",
                    "query_id": 1,
                    "llm_verdict": "PASS",
                    "llm_score": 0.97,
                }
            ]
        )

        df = aggregate_metrics(results, llm_judgments_df=judgments)
        q1 = df[df["query_id"] == 1].iloc[0]
        q2 = df[df["query_id"] == 2].iloc[0]
        assert float(q1["gt_score"]) == 0.97
        assert str(q1["gt_method"]) == "llm_judge_score"
        assert float(q2["gt_score"]) == 0.0
        assert str(q2["gt_method"]) == "llm_judge_score_missing"

    def test_rejects_invalid_llm_judgments_schema(self):
        """
        aggregate_metrics() should fail fast when llm_judgments columns are missing.
        """
        results = [make_result(query="What is the maximum recorded x-acceleration for user 15?")]
        invalid = pd.DataFrame([{"baseline": "FLASH_FUSION", "query_id": 1}])
        try:
            aggregate_metrics(results, llm_judgments_df=invalid)
            assert False, "Expected ValueError for missing llm_score/llm_verdict columns"
        except ValueError as e:
            assert "llm_judgments_df missing required columns" in str(e)

    def test_clamps_out_of_range_llm_scores(self):
        """
        llm_score should be clamped into [0,1] before writing gt_score.
        """
        q1_text = "What is the maximum recorded x-acceleration for user 15?"
        q2_text = "How many total samples in the dataset are classified as the Walking activity?"
        results = [
            make_result(baseline="FLASH_FUSION", executed=True, rejected=False, query=q1_text),
            make_result(baseline="FLASH_FUSION", executed=True, rejected=False, query=q2_text),
        ]
        judgments = pd.DataFrame(
            [
                {"baseline": "FLASH_FUSION", "query_id": 1, "llm_verdict": "PASS", "llm_score": 1.4},
                {"baseline": "FLASH_FUSION", "query_id": 2, "llm_verdict": "FAIL", "llm_score": -0.2},
            ]
        )

        df = aggregate_metrics(results, llm_judgments_df=judgments)
        q1 = df[df["query_id"] == 1].iloc[0]
        q2 = df[df["query_id"] == 2].iloc[0]
        assert float(q1["gt_score"]) == 1.0
        assert float(q2["gt_score"]) == 0.0
