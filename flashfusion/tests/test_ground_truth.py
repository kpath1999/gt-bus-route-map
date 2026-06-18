from __future__ import annotations

from flashfusion.eval.ground_truth import GroundTruthEntry
from flashfusion.eval.semantic_scorer import SemanticScorer
from flashfusion.pipeline.runner import RunResult


def _result(answer: str = "", rejected: bool = False) -> RunResult:
    return RunResult(
        baseline="FLASH_FUSION",
        model="test-model",
        query="q",
        answer=answer,
        rejected=rejected,
        executed=not rejected,
    )


def test_text_similarity_identical_high():
    s = SemanticScorer()
    v = s.score("Top activity is jogging", "Top activity is jogging")
    assert v > 0.99


def test_text_similarity_unrelated_low():
    s = SemanticScorer()
    v = s.score("subject duration", "banana sandwich forecast")
    assert v < 0.4


def test_rejection_expected_scores_one():
    s = SemanticScorer()
    gt = GroundTruthEntry(
        query_id=4,
        query_text="q4",
        reference_answer="Reject",
        expected_rejection=True,
    )
    out = s.score_result(_result(rejected=True), gt)
    assert out["score"] == 1.0


def test_rejection_expected_not_rejected_scores_zero():
    s = SemanticScorer()
    gt = GroundTruthEntry(
        query_id=10,
        query_text="q10",
        reference_answer="Reject",
        expected_rejection=True,
    )
    out = s.score_result(_result(answer="It is jogging", rejected=False), gt)
    assert out["score"] == 0.0
