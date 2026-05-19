"""
tests/test_baselines.py — Flow-shape tests for baseline pipelines.

Run with: pytest flashfusion/tests/test_baselines.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from flashfusion.pipeline.runner import RunResult


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "subject_id": [1600, 1601],
            "activity_label": ["A", "B"],
            "timestamp": [1000, 2000],
            "x": [0.1, 0.2],
            "y": [0.1, 0.2],
            "z": [0.1, 0.2],
            "magnitude": [0.173, 0.346],
            "activity_name": ["Walking", "Jogging"],
        }
    )


def _client() -> MagicMock:
    c = MagicMock()
    c.model_name = "llama-3.3-70b-versatile"
    c.llm = MagicMock()
    return c


def _result(mode: str, query: str) -> RunResult:
    return RunResult(baseline=mode, model="llama-3.3-70b-versatile", query=query)


def test_autoiot_runs_agent_without_guardrail() -> None:
    from flashfusion.baselines.autoiot_only import run_autoiot_only

    query = "What is the average x-axis acceleration?"
    r = _result("AUTOIOT_ONLY", query)

    with patch("flashfusion.baselines.autoiot_only.ExecutionLayer") as execution_layer_cls:
        executor = execution_layer_cls.return_value
        executor.execute_single.return_value = ("answer", "trace", MagicMock(final_code="code", tries=1))

        out = run_autoiot_only(query, _df(), _client(), r)

    executor.guardrail.assert_not_called()
    executor.execute_single.assert_called_once_with(query)
    assert out.executed is True
    assert out.rejected is False
    assert out.stages_run == ["agent"]


def test_wellmax_executes_grounded_query_without_guardrail() -> None:
    from flashfusion.baselines.wellmax_only import run_wellmax_only

    query = "Compare sedentary and locomotion magnitude."
    r = _result("WELLMAX_ONLY", query)

    with patch("flashfusion.baselines.wellmax_only.Stage1_ConceptExtraction") as s1_cls, patch(
        "flashfusion.baselines.wellmax_only.Stage2_SchemaGrounding"
    ) as s2_cls, patch(
        "flashfusion.baselines.wellmax_only.Stage3_SubqueryGeneration"
    ) as s3_cls, patch(
        "flashfusion.baselines.wellmax_only.ExecutionLayer"
    ) as execution_layer_cls:
        s1_cls.return_value.run.return_value = {"DATA": ["magnitude"], "REASONING": ["sedentary"]}
        s2_cls.return_value.run.return_value = {
            "raw_grounding": "MAPPINGS:\n  sedentary -> D,E",
            "mappings": ["sedentary -> D,E"],
            "unmappable": [],
        }
        s3_cls.return_value.run.return_value = {
            "sub_queries": ["[FILTER] activity_label in D,E", "[AGGREGATE] mean magnitude"],
            "synthesis_hint": "Compare means",
        }

        executor = execution_layer_cls.return_value
        executor.execute_single.return_value = ("answer", "trace", MagicMock(final_code="code", tries=2))

        out = run_wellmax_only(query, _df(), _client(), r, adapter=None)

    executor.guardrail.assert_not_called()
    assert executor.execute_single.call_count == 1
    grounded_query_arg = executor.execute_single.call_args[0][0]
    assert "Concept-to-column mappings" in grounded_query_arg
    assert out.executed is True
    assert out.rejected is False
    assert out.stages_run == ["S1", "S2", "S3", "agent"]


def test_flash_fusion_rejection_sets_explanation() -> None:
    from flashfusion.baselines.flash_fusion import run_flash_fusion

    query = "What is average heart rate during jogging?"
    r = _result("FLASH_FUSION", query)

    with patch("flashfusion.baselines.flash_fusion.Stage1_ConceptExtraction") as s1_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage2_SchemaGrounding"
    ) as s2_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage3_SubqueryGeneration"
    ) as s3_cls, patch(
        "flashfusion.baselines.flash_fusion.ExecutionLayer"
    ) as execution_layer_cls:
        s1_cls.return_value.run.return_value = {"DATA": ["heart_rate"], "REASONING": []}
        s2_cls.return_value.run.return_value = {
            "raw_grounding": "MAPPINGS:\nUNMAPPABLE: heart_rate",
            "mappings": [],
            "unmappable": ["heart_rate"],
        }
        s3_cls.return_value.run.return_value = {
            "sub_queries": ["[FILTER] heart_rate during jogging"],
            "synthesis_hint": "Compute mean",
        }

        executor = execution_layer_cls.return_value
        executor.guardrail.return_value = (False, "heart_rate is not in schema")

        out = run_flash_fusion(query, _df(), _client(), r, adapter=None)

    executor.execute_single.assert_not_called()
    executor.judge_result.assert_not_called()
    assert out.rejected is True
    assert out.executed is False
    assert "heart_rate is not in schema" in out.rejection_reason
    assert "Rejected before execution" in out.alignment_explanation
    assert out.stages_run == ["S1", "S2", "S3", "guardrail"]
