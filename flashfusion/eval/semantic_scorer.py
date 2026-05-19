"""
eval/semantic_scorer.py — Minimal text-similarity scorer for benchmark answers.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from flashfusion.eval.ground_truth import GroundTruthEntry
from flashfusion.pipeline.runner import RunResult


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s.%-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


class SemanticScorer:
    """Simple and fast similarity scorer with no external model dependency."""

    @staticmethod
    def score(answer: str, reference: str) -> float:
        a = _normalize(answer)
        b = _normalize(reference)
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0

        seq = SequenceMatcher(None, a, b).ratio()
        ta = set(a.split())
        tb = set(b.split())
        jacc = (len(ta & tb) / len(ta | tb)) if (ta or tb) else 1.0
        # Blend order-sensitive and token-overlap similarity.
        return max(0.0, min(1.0, 0.6 * seq + 0.4 * jacc))

    def score_result(self, result: RunResult, gt: GroundTruthEntry) -> dict:
        if gt.expected_rejection:
            s = 1.0 if result.rejected else 0.0
            return {"score": s, "method": "rejection_binary"}

        if result.rejected:
            return {"score": 0.0, "method": "rejection_binary"}

        s = self.score(result.answer or "", gt.reference_answer)
        return {"score": s, "method": "text_similarity"}
