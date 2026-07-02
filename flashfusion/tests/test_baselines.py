"""
tests/test_baselines.py — Flow-shape tests for baseline pipelines.

Run with: pytest flashfusion/tests/test_baselines.py -v
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from flashfusion.pipeline.runner import BaselineRunner, RunResult


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


def test_agent_runs_agent_without_guardrail() -> None:
    from flashfusion.baselines.react_only import run_react_only

    query = "What is the average x-axis acceleration?"
    r = _result("REACT_ONLY", query)

    with patch("flashfusion.baselines.react_only.ExecutionLayer") as execution_layer_cls:
        executor = execution_layer_cls.return_value
        executor.execute_single.return_value = ("answer", "trace", MagicMock(final_code="code", tries=1))

        out = run_react_only(query, _df(), _client(), r)

    executor.guardrail.assert_not_called()
    executor.execute_single.assert_called_once_with(query)
    assert out.executed is True
    assert out.rejected is False
    assert out.stages_run == ["react_agent"]


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

        out = run_wellmax_only(query, _df(), _client(), r)

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

        out = run_flash_fusion(query, _df(), _client(), r)

    executor.execute_single.assert_not_called()
    executor.judge_plan.assert_not_called()
    assert out.rejected is True
    assert out.executed is False
    assert "heart_rate is not in schema" in out.rejection_reason
    assert "Rejected before execution" in out.alignment_explanation
    assert out.stages_run == ["S1", "S2", "S3", "guardrail"]


def test_flash_fusion_runs_plan_judge_before_agent() -> None:
    from flashfusion.baselines.flash_fusion import run_flash_fusion

    query = "Compare mean magnitude for walking and jogging."
    r = _result("FLASH_FUSION", query)

    with patch("flashfusion.baselines.flash_fusion.Stage1_ConceptExtraction") as s1_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage2_SchemaGrounding"
    ) as s2_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage3_SubqueryGeneration"
    ) as s3_cls, patch(
        "flashfusion.baselines.flash_fusion.ExecutionLayer"
    ) as execution_layer_cls:
        s1_cls.return_value.run.return_value = {"DATA": ["magnitude"], "REASONING": ["compare"]}
        s2_cls.return_value.run.return_value = {
            "raw_grounding": "MAPPINGS:\n  compare -> activity_name + mean(magnitude)",
            "mappings": ["compare -> activity_name + mean(magnitude)"],
            "unmappable": [],
        }
        s3_cls.return_value.run.return_value = {
            "sub_queries": [
                "[FILTER] Keep rows where activity_name is Walking or Jogging",
                "[GROUPBY] Group by activity_name and compute mean(magnitude)",
            ],
            "synthesis_hint": "Compare the two means directly.",
        }

        executor = execution_layer_cls.return_value
        executor.guardrail.return_value = (True, "")
        executor.judge_plan.return_value = {
            "verdict": "PASS",
            "issue": "No alignment issues detected against the original question.",
            "suggestion": "",
        }
        executor.explain_alignment.return_value = "Judge sanity check: PASS."
        executor.execute_single.return_value = (
            "Jogging has higher mean magnitude.",
            "trace",
            MagicMock(final_code="code", tries=2, attempts=[]),
        )

        out = run_flash_fusion(query, _df(), _client(), r)

    executor.judge_plan.assert_called_once()
    executor.execute_single.assert_called_once()
    assert out.executed is True
    assert out.stages_run == ["S1", "S2", "S3", "guardrail", "judge_plan", "agent"]


def test_flash_fusion_refines_plan_once_on_plan_judge_fail() -> None:
    from flashfusion.baselines.flash_fusion import run_flash_fusion

    query = "Find users with longer stationary than locomotion durations."
    r = _result("FLASH_FUSION", query)

    with patch("flashfusion.baselines.flash_fusion.Stage1_ConceptExtraction") as s1_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage2_SchemaGrounding"
    ) as s2_cls, patch(
        "flashfusion.baselines.flash_fusion.Stage3_SubqueryGeneration"
    ) as s3_cls, patch(
        "flashfusion.baselines.flash_fusion.ExecutionLayer"
    ) as execution_layer_cls:
        s1_cls.return_value.run.return_value = {"DATA": ["timestamp"], "REASONING": ["stationary", "locomotion"]}
        s2_cls.return_value.run.return_value = {
            "raw_grounding": "MAPPINGS:\n  stationary -> Sitting,Standing\n  locomotion -> Walking,Jogging,Stairs",
            "mappings": [
                "stationary -> Sitting,Standing",
                "locomotion -> Walking,Jogging,Stairs",
            ],
            "unmappable": [],
        }
        s3_cls.return_value.run.side_effect = [
            {
                "sub_queries": ["[AGGREGATE] Compute total duration overall"],
                "synthesis_hint": "Return the duration.",
            },
            {
                "sub_queries": [
                    "[FILTER] Split stationary vs locomotion rows",
                    "[GROUPBY] Aggregate duration by subject_id and category",
                    "[RANK] Keep users where stationary total exceeds locomotion total",
                ],
                "synthesis_hint": "Report matching users and both totals.",
            },
        ]

        executor = execution_layer_cls.return_value
        executor.guardrail.return_value = (True, "")
        executor.judge_plan.side_effect = [
            {
                "verdict": "FAIL",
                "issue": "Plan misses category split before aggregation.",
                "suggestion": "Add explicit stationary vs locomotion split before per-user aggregation.",
            },
            {
                "verdict": "PASS",
                "issue": "No alignment issues detected against the original question.",
                "suggestion": "",
            },
        ]
        executor.explain_alignment.return_value = "Judge sanity check: PASS."
        executor.execute_single.return_value = (
            "Users 1600 and 1601 have longer stationary totals.",
            "trace",
            MagicMock(final_code="code", tries=1, attempts=[]),
        )

        out = run_flash_fusion(query, _df(), _client(), r)

    assert s3_cls.return_value.run.call_count == 2
    assert executor.judge_plan.call_count == 2
    assert out.stages_run == [
        "S1",
        "S2",
        "S3",
        "guardrail",
        "judge_plan",
        "S3_refine",
        "judge_plan_retry",
        "agent",
    ]


def test_autoiot_paper_requires_tavily_key() -> None:
    from flashfusion.baselines.autoiot_paper import run_autoiot_paper

    query = "Find the average acceleration magnitude for walking."
    r = _result("AUTOIOT_PAPER", query)

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
            run_autoiot_paper(query, _df(), _client(), r)


def test_runner_dispatches_autoiot_paper_mode() -> None:
    client = _client()
    runner = BaselineRunner(mode="AUTOIOT_PAPER", df=_df(), client=client)

    with patch("flashfusion.baselines.autoiot_paper.run_autoiot_paper") as fn:
        runner.run("Compare mean x by activity")

    fn.assert_called_once()


def test_autoiot_paper_uses_generated_search_queries_for_tavily() -> None:
    from flashfusion.baselines import autoiot_paper

    query = "Find average magnitude while walking"
    r = _result("AUTOIOT_PAPER", query)

    stage_inputs: dict[str, str] = {}

    def fake_invoke(client, stage, system_prompt, user_input):
        stage_inputs[stage] = user_input
        if stage == "autoiot_terms":
            return "magnitude"
        if stage == "autoiot_search_queries":
            return "wisdm magnitude walking"
        if stage == "autoiot_design_high":
            return "Step 1: prepare"
        if stage == "autoiot_design_detail":
            return "Step 1: prepare\n- clean data"
        if stage.startswith("autoiot_module_gen_"):
            return "```python\ndef module_part():\n    return 1\n```"
        if stage == "autoiot_code_integration":
            return "```python\ndef run():\n    return 1\n```"
        if stage.startswith("autoiot_improve_"):
            return "tighten aggregation"
        if stage == "autoiot_select":
            return "1"
        return ""

    class FakeExecutionLayer:
        def __init__(self, df, client):
            pass

        def execute_single(self, query_text):
            return (
                "ok",
                "Observation: success",
                MagicMock(final_code="result = 1", tries=1, attempts=[]),
            )

    tavily_calls: list[str] = []

    def fake_tavily_search(**kwargs):
        tavily_calls.append(kwargs["query"])
        return [{"url": "https://example.com", "content": "context"}]

    with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}, clear=True), patch.object(
        autoiot_paper, "AUTOIOT_PAPER_ITERATIONS", 2
    ), patch.object(
        autoiot_paper, "_invoke", side_effect=fake_invoke
    ), patch.object(
        autoiot_paper, "ExecutionLayer", FakeExecutionLayer
    ), patch.object(
        autoiot_paper, "_tavily_search", side_effect=fake_tavily_search
    ):
        out = autoiot_paper.run_autoiot_paper(query, _df(), _client(), r)

    assert "wisdm magnitude walking" in tavily_calls
    assert "autoiot_search_queries" in out.stages_run
    assert "autoiot_module_gen" in out.stages_run
    assert "autoiot_code_integration" in out.stages_run


def test_autoiot_paper_improvement_prompt_uses_execution_feedback() -> None:
    from flashfusion.baselines import autoiot_paper

    query = "Find average magnitude while walking"
    r = _result("AUTOIOT_PAPER", query)

    improve_inputs: list[str] = []

    def fake_invoke(client, stage, system_prompt, user_input):
        if stage == "autoiot_terms":
            return "magnitude"
        if stage == "autoiot_search_queries":
            return "wisdm magnitude"
        if stage == "autoiot_design_high":
            return "Step 1: prepare"
        if stage == "autoiot_design_detail":
            return "Step 1: prepare\n- clean data"
        if stage.startswith("autoiot_module_gen_"):
            return "```python\ndef module_part():\n    return 1\n```"
        if stage == "autoiot_code_integration":
            return "```python\ndef run():\n    return 1\n```"
        if stage.startswith("autoiot_correct_"):
            return "```python\ndef run():\n    return 2\n```"
        if stage.startswith("autoiot_improve_"):
            improve_inputs.append(user_input)
            return "use corrected code"
        if stage == "autoiot_select":
            return "1"
        return ""

    class FakeExecutionLayer:
        def __init__(self, df, client):
            pass

        def execute_single(self, query_text):
            details = MagicMock(
                final_code="",
                tries=1,
                attempts=[{"attempt": 1, "ok": False, "output": "NameError: x is not defined"}],
            )
            return ("[ERROR] NameError", "Observation: NameError: x is not defined", details)

    with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}, clear=True), patch.object(
        autoiot_paper, "AUTOIOT_PAPER_ITERATIONS", 2
    ), patch.object(
        autoiot_paper, "_invoke", side_effect=fake_invoke
    ), patch.object(
        autoiot_paper, "ExecutionLayer", FakeExecutionLayer
    ), patch.object(
        autoiot_paper, "_tavily_search", return_value=[]
    ):
        autoiot_paper.run_autoiot_paper(query, _df(), _client(), r)

    assert len(improve_inputs) == 1
    assert "Execution stderr" in improve_inputs[0]
    assert "NameError: x is not defined" in improve_inputs[0]


def test_hargpt_paper_classification_happy_path() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What activity is user 1600 doing based on these IMU readings?"
    r = _result("HARGPT_PAPER", query)

    with patch.object(
        hargpt_paper,
        "_invoke_rewritten",
        return_value="Step-by-step analysis...\nFinal answer: Walking",
    ):
        out = hargpt_paper.run_hargpt_paper(query, _df(), _client(), r)

    assert out.rejected is False
    assert out.executed is False
    assert out.answer == "Walking"
    assert "hargpt_wisdm_window" in out.stages_run
    assert "hargpt_wisdm_rewrite" in out.stages_run
    assert "hargpt_wisdm_infer" in out.stages_run
    assert "hargpt_wisdm_parse" in out.stages_run
    assert out.execution_attempts


def test_hargpt_paper_non_classification_query_uses_best_effort() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What is the maximum recorded x-acceleration for user 15?"
    r = _result("HARGPT_PAPER", query)

    with patch.object(hargpt_paper, "_invoke_rewritten", return_value="Final answer: 0.2") as fake_invoke:
        out = hargpt_paper.run_hargpt_paper(query, _df(), _client(), r)

    fake_invoke.assert_called_once()
    assert out.rejected is False
    assert out.executed is False
    assert out.answer == "0.2"


def test_hargpt_paper_non_imu_schema_uses_best_effort() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What activity is this ECG trace showing?"
    r = _result("HARGPT_PAPER", query)
    ecg_df = pd.DataFrame({"record_id": [101], "MLII": [0.1], "time_s": [0.0]})

    with patch.object(hargpt_paper, "_invoke_rewritten", return_value="Final answer: Best effort"):
        out = hargpt_paper.run_hargpt_paper(query, ecg_df, _client(), r)

    assert out.rejected is False
    assert out.executed is False
    assert "hargpt_wisdm_rewrite" in out.stages_run


def test_runner_dispatches_hargpt_paper_mode() -> None:
    client = _client()
    runner = BaselineRunner(mode="HARGPT_PAPER", df=_df(), client=client)

    with patch("flashfusion.baselines.hargpt_paper.run_hargpt_paper") as fn:
        runner.run("What activity is user 1600 doing?")

    fn.assert_called_once()


def test_runner_dispatches_llmsense_paper_mode() -> None:
    client = _client()
    runner = BaselineRunner(mode="LLMSENSE_PAPER", df=_df(), client=client)

    with patch("flashfusion.baselines.llmsense_paper.run_llmsense_paper") as fn:
        runner.run("Summarize user activities")

    fn.assert_called_once()


def test_llmsense_paper_short_trace_uses_narration_path() -> None:
    from flashfusion.baselines.llmsense_paper import run_llmsense_paper

    query = "What activities appear in this trace?"
    r = _result("LLMSENSE_PAPER", query)

    with patch("flashfusion.baselines.llmsense_paper._stage_narrate", return_value="narrative") as narrate_fn, patch(
        "flashfusion.baselines.llmsense_paper._stage_summarize"
    ) as summarize_fn, patch(
        "flashfusion.baselines.llmsense_paper._stage_reason", return_value="answer"
    ) as reason_fn:
        out = run_llmsense_paper(query, _df(), _client(), r)

    narrate_fn.assert_called_once()
    summarize_fn.assert_not_called()
    reason_fn.assert_called_once()
    assert out.answer == "answer"
    assert out.trace == "narrative"
    assert out.executed is False
    assert out.rejected is False
    assert out.judge_verdict == {}
    assert out.stages_run == ["N_narrate", "R_reason"]


def test_llmsense_paper_long_trace_uses_summarization_path() -> None:
    from flashfusion.baselines.llmsense_paper import run_llmsense_paper

    query = "Which users show transitions?"
    r = _result("LLMSENSE_PAPER", query)

    long_df = pd.DataFrame(
        {
            "subject_id": [1600] * 130,
            "activity_label": ["A"] * 130,
            "timestamp": list(range(130)),
            "x": [0.1] * 130,
            "y": [0.2] * 130,
            "z": [0.3] * 130,
        }
    )

    with patch("flashfusion.baselines.llmsense_paper._stage_narrate") as narrate_fn, patch(
        "flashfusion.baselines.llmsense_paper._stage_summarize", return_value="summary"
    ) as summarize_fn, patch(
        "flashfusion.baselines.llmsense_paper._stage_reason", return_value="answer"
    ) as reason_fn:
        out = run_llmsense_paper(query, long_df, _client(), r)

    narrate_fn.assert_not_called()
    summarize_fn.assert_called_once()
    reason_fn.assert_called_once()
    assert out.answer == "answer"
    assert out.trace == "summary"
    assert out.executed is False
    assert out.rejected is False
    assert out.judge_verdict == {}
    assert out.stages_run == ["S_summarize", "R_reason"]


def _ecg_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "record_id": ["100", "100"],
            "time_s": [0.0, 0.003],
            "MLII": [0.96, 0.97],
            "V1": [0.01, 0.02],
            "annotation": ["N", "N"],
        }
    )


def _bus_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": ["2024-01-01 08:00:00", "2024-01-01 08:01:00"],
            "latitude": [51.5, 51.51],
            "longitude": [-0.1, -0.11],
            "accel_mean": [0.05, 0.08],
            "accel_variance": [0.001, 0.003],
        }
    )


def test_hargpt_paper_ecg_fallback_uses_narrative_path() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What is the dominant rhythm in this ECG segment?"
    r = _result("HARGPT_PAPER", query)

    with patch.object(
        hargpt_paper,
        "_invoke_rewritten",
        return_value="Step 1: signals appear normal.\nFinal answer: Normal sinus rhythm",
    ):
        out = hargpt_paper.run_hargpt_paper(query, _ecg_df(), _client(), r)

    assert out.rejected is False
    assert out.executed is False
    assert out.answer == "Normal sinus rhythm"
    assert "hargpt_ecg_window" in out.stages_run
    assert "hargpt_ecg_rewrite" in out.stages_run
    assert "hargpt_ecg_infer" in out.stages_run
    assert "hargpt_ecg_parse" in out.stages_run


def test_hargpt_paper_bus_fallback_uses_narrative_path() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "Are there any high-vibration segments in this bus route?"
    r = _result("HARGPT_PAPER", query)

    with patch.object(
        hargpt_paper,
        "_invoke_rewritten",
        return_value="Step 1: accel variance spikes detected.\nFinal answer: High vibration road segment identified",
    ):
        out = hargpt_paper.run_hargpt_paper(query, _bus_df(), _client(), r)

    assert out.rejected is False
    assert out.executed is False
    assert out.answer == "High vibration road segment identified"
    assert "hargpt_bus_window" in out.stages_run
    assert "hargpt_bus_rewrite" in out.stages_run
    assert "hargpt_bus_infer" in out.stages_run
    assert "hargpt_bus_parse" in out.stages_run


def test_hargpt_paper_records_budget_metadata() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What activity is user 1600 doing based on these IMU readings?"
    r = _result("HARGPT_PAPER", query)

    with patch.object(
        hargpt_paper,
        "_invoke_rewritten",
        return_value="Final answer: Walking",
    ):
        out = hargpt_paper.run_hargpt_paper(query, _df(), _client(), r)

    assert out.execution_attempts
    attempt = out.execution_attempts[-1]
    assert attempt["budget_tokens_est"] > 0
    assert attempt["est_prompt_tokens"] > 0
    assert 0 < attempt["context_pct_window"] <= 100
    assert 0 < attempt["context_pct_budget"] <= 100
    assert attempt["prefilter_rows"] >= attempt["rows_used"]
    assert isinstance(attempt["prefilter_applied"], bool)


def test_hargpt_paper_prefilter_applies_on_large_input() -> None:
    from flashfusion.baselines import hargpt_paper

    query = "What activity is user 1600 doing based on these IMU readings?"
    r = _result("HARGPT_PAPER", query)
    large_df = pd.DataFrame(
        {
            "subject_id": [1600] * 30000,
            "activity_label": ["A"] * 30000,
            "timestamp": list(range(30000)),
            "x": [0.1] * 30000,
            "y": [0.2] * 30000,
            "z": [0.3] * 30000,
            "magnitude": [0.374] * 30000,
            "activity_name": ["Walking"] * 30000,
        }
    )

    with patch.object(
        hargpt_paper,
        "_invoke_rewritten",
        return_value="Final answer: Walking",
    ):
        out = hargpt_paper.run_hargpt_paper(query, large_df, _client(), r)

    attempt = out.execution_attempts[-1]
    assert attempt["prefilter_applied"] is True
    assert attempt["rows_used"] < len(large_df)
    assert attempt["prefilter_rows"] < len(large_df)
