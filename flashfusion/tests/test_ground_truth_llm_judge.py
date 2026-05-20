from __future__ import annotations

from flashfusion.eval.ground_truth_llm_judge import _resolve_candidate_code


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
