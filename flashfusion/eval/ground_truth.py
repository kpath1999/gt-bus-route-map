"""
eval/ground_truth.py — Ground-truth schema and loader for benchmark scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class GroundTruthEntry:
    """Canonical answer for one benchmark query."""

    query_id: int
    query_text: str
    reference_answer: str
    expected_rejection: bool = False


def load_ground_truth(path: str) -> dict[int, GroundTruthEntry]:
    """
    Load ground-truth entries from JSON.

    Expected JSON format:
    [
      {
        "query_id": 1,
        "query_text": "...",
        "reference_answer": "...",
        "expected_rejection": false
      }
    ]
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Ground truth file not found: {path}. "
            "Generate it first or pass --ground-truth with a valid JSON file."
        )

    with p.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, list):
        raise ValueError("Ground truth JSON must be a list of entries")

    out: dict[int, GroundTruthEntry] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = int(item["query_id"])
        out[qid] = GroundTruthEntry(
            query_id=qid,
            query_text=str(item["query_text"]),
            reference_answer=str(item["reference_answer"]),
            expected_rejection=bool(item.get("expected_rejection", False)),
        )

    if not out:
        raise ValueError("Ground truth file is empty")

    return out
