from __future__ import annotations

from flashfusion.eval.benchmark_hybrid_cache import _metric_summary


def _row(expected: str | None, predicted: str | None) -> dict:
    return {
        "expected_match_id": expected,
        "predicted_match_id": predicted,
        "correct_match": expected == predicted,
        "elapsed_ms": {"total_match_ms": 1.0},
        "expected_in_dense_top_1": False,
        "expected_in_lexical_top_1": False,
        "dense_top_k_ids_by_report_k": {"1": []},
        "lexical_top_k_ids_by_report_k": {"1": []},
        "union_ids_by_report_k": {"1": []},
        "abstained": predicted is None,
        "ambiguous": False,
        "potential_ambiguity": False,
        "correct_but_abstained": expected is not None and predicted is None,
    }


def test_metric_summary_grades_nullable_match_identity() -> None:
    summary = _metric_summary(
        [_row("1", "1"), _row("2", None), _row(None, "3"), _row(None, None)],
        [1],
    )

    assert summary["match_accuracy"] == 0.5
    assert summary["false_match_rate"] == 0.5
    assert summary["miss_rate"] == 0.5