from __future__ import annotations

from flashfusion.eval.ground_truth import GroundTruthEntry
from flashfusion.eval.ground_truth_llm_judge import (
    _resolve_candidate_code,
    judge_rows_with_llm,
)
from flashfusion.eval.queries import DATASET_WISDM, get_queries


def test_resolve_candidate_code_prefers_final_code():
    row = {
        "final_code": "result = 1",
        "execution_attempts": [{"code": "result = 2"}],
        "trace": "Action Input: result = 3",
    }
    assert _resolve_candidate_code(row) == "result = 1"


def test_resolve_candidate_code_falls_back_to_last_attempt_code():
    row = {
        "final_code": "",
        "execution_attempts": [
            {"code": "result = 10"},
            {"code": "result = 20"},
        ],
    }
    assert _resolve_candidate_code(row) == "result = 20"


def test_resolve_candidate_code_falls_back_to_trace_action_input():
    row = {
        "final_code": "",
        "execution_attempts": [],
        "trace": "Thought: x\nAction Input: result = 99\nObservation: ok",
    }
    assert _resolve_candidate_code(row) == "result = 99"


def test_resolve_candidate_code_returns_empty_when_unavailable():
    row = {"final_code": "", "execution_attempts": [], "trace": ""}
    assert _resolve_candidate_code(row) == ""


def test_judge_rows_with_llm_accepts_expected_rejection(monkeypatch):
    queries = get_queries(DATASET_WISDM)
    q9 = next(q for q in queries if q["id"] == 9)

    ground_truth_by_id = {
        9: GroundTruthEntry(
            query_id=9,
            query_text=q9["text"],
            reference_answer="Reject: out of scope.",
            expected_rejection=True,
        )
    }

    rows = [
        {
            "baseline": "AGENT_ONLY",
            "query": q9["text"],
            "answer": "This is out of scope for the dataset, so I cannot answer.",
            "executed": True,
            "rejected": False,
            "final_code": "",
            "execution_attempts": [],
            "trace": "",
        }
    ]

    class _DummyPrompt:
        def __or__(self, other):
            return self

    class _DummyLLM:
        def __ror__(self, other):
            return self

        def __or__(self, other):
            return self

    class _DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            self.llm = _DummyLLM()

        def invoke_chain(self, chain, inputs, stage: str) -> str:
            return (
                '{"verdict":"PASS","reason":"Rejects as out of scope.",' 
                '"ground_truth_sanity":"SOUND","ground_truth_note":""}'
            )

    monkeypatch.setattr(
        "flashfusion.eval.ground_truth_llm_judge.ChatPromptTemplate.from_messages",
        lambda *args, **kwargs: _DummyPrompt(),
    )
    monkeypatch.setattr(
        "flashfusion.eval.ground_truth_llm_judge.LLMClient",
        _DummyClient,
    )

    judged = judge_rows_with_llm(
        rows=rows,
        ground_truth_by_id=ground_truth_by_id,
        dataset=DATASET_WISDM,
        model_name="test-model",
        api_key="test-key",
    )

    assert len(judged) == 1
    assert judged.loc[0, "llm_verdict"] == "PASS"
    assert judged.loc[0, "llm_score"] == 1.0
