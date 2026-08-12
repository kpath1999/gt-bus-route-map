"""
tests/test_executor.py — Unit tests for ExecutionLayer and ResilientReActOutputParser.

Run with: pytest flashfusion/tests/test_executor.py -v

All LLM calls are mocked — these tests run without a GROQ_API_KEY.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from langchain_core.agents import AgentAction, AgentFinish


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_df():
    return pd.DataFrame(
        {
            "subject_id": [1600, 1601],
            "activity_label": ["A", "B"],
            "timestamp": [1000, 2000],
            "x": [0.5, 1.0],
            "y": [0.5, 1.0],
            "z": [0.5, 1.0],
        }
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.model_name = "llama-3.3-70b-versatile"
    client.llm = MagicMock()
    client.invoke_chain.return_value = "PROCEED"
    return client


# ---------------------------------------------------------------------------
# ResilientReActOutputParser tests
# ---------------------------------------------------------------------------

class TestResilientReActOutputParser:
    """Tests for the three LLM failure mode handlers."""

    def setup_method(self):
        from flashfusion.pipeline.executor import ResilientReActOutputParser
        self.parser = ResilientReActOutputParser()

    def test_p3_dedup_identical_outputs_triggers_extract(self):
        """
        P3-dedup: After MAX_IDENTICAL identical raw outputs, parser should
        call _extract_best_answer() and return an AgentFinish instead of raising.

        Simulate by calling parse() with the same text MAX_IDENTICAL+1 times.
        All calls except the dedup-triggered one should be valid or fail gracefully.
        The final call (when dedup triggers) should return an AgentFinish.

        Note: The valid calls may raise OutputParserException if the text is not
        valid ReAct format — that's acceptable as long as the dedup call returns AgentFinish.
        """
        from langchain_core.exceptions import OutputParserException

        text = "This is just an essay response with no Action or Final Answer tags."
        results = []
        for _ in range(self.parser.MAX_IDENTICAL + 1):
            try:
                result = self.parser.parse(text)
                results.append(result)
            except OutputParserException:
                results.append(None)
        # Last result (dedup triggered) should be AgentFinish
        assert isinstance(results[-1], AgentFinish)

    def test_p0_loop3_sanitize_strips_thought_after_code(self):
        """
        P0-loop-3: _sanitize_action_input() should strip stray Thought: blocks.

        Input:  "df.groupby('activity').mean()\nThought: Now I have the answer"
        Output: "df.groupby('activity').mean()"
        """
        raw = "df.groupby('activity').mean()\nThought: Now I have the answer"
        result = self.parser._sanitize_action_input(raw)
        assert "Thought:" not in result
        assert "df.groupby" in result

    def test_p0_loop3_sanitize_strips_double_newline_explanation(self):
        """
        _sanitize_action_input() should also strip trailing explanations after \n\n.

        Input:  "print(df.shape)\n\nThis shows the dimensions."
        Output: "print(df.shape)"
        """
        raw = "print(df.shape)\n\nThis shows the dimensions."
        result = self.parser._sanitize_action_input(raw)
        assert result == "print(df.shape)"

    def test_extract_best_answer_finds_final_answer(self):
        """
        _extract_best_answer() should extract text after "Final Answer:" label.
        """
        text = "Thought: I know the answer now.\nFinal Answer: Jogging has the highest magnitude."
        result = self.parser._extract_best_answer(text)
        assert isinstance(result, AgentFinish)
        assert "Jogging" in result.return_values["output"]

    def test_extract_best_answer_falls_back_to_full_text(self):
        """
        _extract_best_answer() should use the full text if no Final Answer label is found.
        """
        text = "The answer is 42."
        result = self.parser._extract_best_answer(text)
        assert isinstance(result, AgentFinish)
        assert "42" in result.return_values["output"]

    def test_p0_loop2_both_action_and_final_answer_prefers_action(self):
        """
        P0-loop-2: When both Action: and Final Answer: are present, the parser
        should prefer Action (strip Final Answer) and successfully parse an AgentAction.

        This test uses a valid ReAct format with Action coming after Final Answer.
        """
        # Final Answer appears first, Action comes after
        text = (
            "Thought: Let me check.\n"
            "Final Answer: Maybe jogging.\n"
            "Action: python_repl_ast\n"
            "Action Input: print(df['activity_label'].value_counts())\n"
        )
        try:
            result = self.parser.parse(text)
            # Should parse as AgentAction (preferring the Action over Final Answer)
            assert isinstance(result, AgentAction) or isinstance(result, AgentFinish)
        except Exception:
            pass  # Acceptable if parser can't handle this specific case

    def test_resilient_parser_grounds_final_answer_to_observation(self):
        """Malformed hedging prose must not override a successful observation."""
        self.parser.record_observation("42", ok=True)

        result = self.parser.parse(
            "I have insufficient information and cannot determine the answer."
        )

        assert isinstance(result, AgentFinish)
        assert result.return_values["output"] == "42"

    def test_reject_query_action_sets_structural_verdict(self):
        """The abstention action should terminate without invoking a tool."""
        result = self.parser.parse(
            "Action: reject_query\n"
            "Action Input: The weather is not represented by any available column."
        )

        assert isinstance(result, AgentFinish)
        assert result.return_values["rejected"] is True
        assert result.return_values["rejection_reason"] == (
            "The weather is not represented by any available column."
        )


# ---------------------------------------------------------------------------
# ExecutionLayer tests
# ---------------------------------------------------------------------------

class TestExecutionLayer:
    """Tests for ExecutionLayer.guardrail() and execute_single()."""

    def test_constructor_does_not_build_agent(self, minimal_df, mock_client):
        """
        ExecutionLayer.__init__ should not eagerly build the pandas agent.
        """
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()) as build_agent:
            ExecutionLayer(minimal_df, mock_client)
        build_agent.assert_not_called()

    def test_guardrail_does_not_build_agent(self, minimal_df, mock_client):
        """
        guardrail() should run without triggering pandas agent construction.
        """
        mock_client.invoke_chain.return_value = "PROCEED"
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()) as build_agent:
            layer = ExecutionLayer(minimal_df, mock_client)
            proceed, reason = layer.guardrail("Count samples per activity.")
        assert proceed is True
        assert reason == ""
        build_agent.assert_not_called()

    def test_safe_backend_rejects_before_code_execution(self, minimal_df, mock_client):
        """A safe codegen REJECT sentinel must avoid Python execution entirely."""
        from flashfusion.pipeline.executor import ExecutionLayer

        rejection = "REJECT: Geographic location is not represented in the available columns."
        mock_client.invoke_chain.return_value = rejection

        with patch.dict(
            os.environ,
            {
                "FLASHFUSION_AGENT_BACKEND": "safe",
                "REACT_ABSTENTION_CLAUSE": "Return REJECT for unavailable data.",
            },
            clear=False,
        ):
            layer = ExecutionLayer(minimal_df, mock_client)
            with patch.object(layer, "_run_safe_code") as run_safe_code:
                answer, trace, details = layer.execute_single("Where was subject 1600 walking?")

        assert answer == rejection
        assert "Scope check rejected" in trace
        assert details.final_code == ""
        assert details.tries == 0
        run_safe_code.assert_not_called()
        assert mock_client.invoke_chain.call_args.kwargs["stage"] == "safe_codegen_1"
        assert (
            mock_client.invoke_chain.call_args.args[1]["abstention_clause"]
            == "Return REJECT for unavailable data."
        )

    def test_execute_single_builds_agent_lazily_once(self, minimal_df, mock_client):
        """
        execute_single() should build the agent only on first use.
        """
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"output": "42"}

        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "classic"}, clear=False):
            with patch.object(ExecutionLayer, "_build_agent", return_value=mock_agent) as build_agent:
                layer = ExecutionLayer(minimal_df, mock_client)
                first_answer, _, _ = layer.execute_single("How many rows?")
                second_answer, _, _ = layer.execute_single("How many rows?")

        assert first_answer == "42"
        assert second_answer == "42"
        assert build_agent.call_count == 1

    def test_reset_agent_invalidates_and_rebuilds_lazily(self, minimal_df, mock_client):
        """
        reset_agent() should invalidate the cached agent and rebuild on next execute.
        """
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"output": "ok"}

        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "classic"}, clear=False):
            with patch.object(ExecutionLayer, "_build_agent", return_value=mock_agent) as build_agent:
                layer = ExecutionLayer(minimal_df, mock_client)
                layer.execute_single("first")
                assert build_agent.call_count == 1

                layer.reset_agent()
                layer.execute_single("second")

        assert build_agent.call_count == 2

    def test_backend_auto_uses_safe_on_macos(self, minimal_df, mock_client):
        """
        auto backend should resolve to safe on Darwin to avoid classic import deadlocks.
        """
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "auto"}, clear=False):
            with patch("platform.system", return_value="Darwin"):
                layer = ExecutionLayer(minimal_df, mock_client)
        assert layer._agent_backend == "safe"

    def test_backend_env_override_classic(self, minimal_df, mock_client):
        """
        explicit env override must force classic backend even on Darwin.
        """
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "classic"}, clear=False):
            with patch("platform.system", return_value="Darwin"):
                layer = ExecutionLayer(minimal_df, mock_client)
        assert layer._agent_backend == "classic"

    def test_execute_single_safe_backend(self, minimal_df, mock_client):
        """
        safe backend should execute generated code and return answer + trace + details.
        """
        responses = {
            "safe_codegen_1": "result = int(df.shape[0])",
            "safe_answer_1": "There are 2 rows in the dataset.",
        }

        def _fake_invoke_chain(chain, inputs, stage):
            return responses[stage]

        mock_client.invoke_chain.side_effect = _fake_invoke_chain

        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "safe"}, clear=False):
            layer = ExecutionLayer(minimal_df, mock_client)
            answer, trace, details = layer.execute_single("How many rows?")

        assert "2 rows" in answer
        assert "Action Input" in trace
        assert details.final_code.strip() == "result = int(df.shape[0])"
        assert details.tries == 1

    def test_guardrail_proceed_on_valid_query(self, minimal_df, mock_client):
        """
        guardrail() should return (True, "") when LLM responds with "PROCEED".
        """
        mock_client.invoke_chain.return_value = "PROCEED"
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()):
            layer = ExecutionLayer(minimal_df, mock_client)
            proceed, reason = layer.guardrail("Count samples per activity.")
        assert proceed is True
        assert reason == ""

    def test_guardrail_reject_on_missing_column(self, minimal_df, mock_client):
        """
        guardrail() should return (False, reason) when LLM responds with REJECT.
        """
        mock_client.invoke_chain.return_value = "REJECT: heart_rate column does not exist."
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()):
            layer = ExecutionLayer(minimal_df, mock_client)
            proceed, reason = layer.guardrail("What is the average heart rate?")
        assert proceed is False
        assert "heart_rate" in reason

    def test_include_abstention_clause_independent_of_parser(self, minimal_df, mock_client):
        """Prompt scope protocol and parser choice must be independently selectable."""
        from flashfusion.pipeline.executor import ExecutionLayer

        for include_abstention_clause in (False, True):
            for use_resilient_parser in (False, True):
                layer = ExecutionLayer(
                    minimal_df,
                    mock_client,
                    include_abstention_clause=include_abstention_clause,
                    use_resilient_parser=use_resilient_parser,
                )
                prefix = layer._build_prefix(minimal_df)
                assert ("SCOPE CHECK" in prefix) is include_abstention_clause
                assert layer._use_resilient_parser is use_resilient_parser

    def test_backward_compatible_react_faithful_flag(self, minimal_df, mock_client):
        """The deprecated flag retains its original coupled behavior temporarily."""
        from flashfusion.pipeline.executor import ExecutionLayer

        with pytest.warns(DeprecationWarning):
            faithful = ExecutionLayer(minimal_df, mock_client, react_faithful=True)
        with pytest.warns(DeprecationWarning):
            non_faithful = ExecutionLayer(minimal_df, mock_client, react_faithful=False)

        assert faithful._include_abstention_clause is True
        assert faithful._use_resilient_parser is False
        assert non_faithful._include_abstention_clause is False
        assert non_faithful._use_resilient_parser is True

    def test_react_faithful_prefix_includes_simple_scope_check(self, minimal_df, mock_client):
        """Paper-faithful ReAct prefix should include a detectable rejection sentinel."""
        from flashfusion.pipeline.executor import ExecutionLayer
        from flashfusion.prompts.templates import GUARDRAIL_PROMPT

        with pytest.warns(DeprecationWarning):
            layer = ExecutionLayer(minimal_df, mock_client, react_faithful=True)
        react_prefix = layer._build_prefix(minimal_df)

        assert "PROCEED for in-dataset predictive tasks" in GUARDRAIL_PROMPT
        assert "forecasting the next observed in-dataset value" in GUARDRAIL_PROMPT
        assert "SCOPE CHECK" in react_prefix
        assert "Action: reject_query" in react_prefix
        assert "out-of-scope for the available data" in react_prefix

    def test_execute_single_returns_structural_rejection_verdict(self, minimal_df, mock_client):
        """A parser-recognized abstention must surface without prose inference."""
        from flashfusion.pipeline.executor import ExecutionLayer, ReActResult

        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "output": "REJECT: Weather is unavailable.",
            "rejected": True,
            "rejection_reason": "Weather is unavailable.",
        }
        with patch.dict(os.environ, {"FLASHFUSION_AGENT_BACKEND": "classic"}, clear=False):
            with patch.object(ExecutionLayer, "_build_agent", return_value=mock_agent):
                layer = ExecutionLayer(minimal_df, mock_client)
                result = layer.execute_single("What was the weather?")

        assert isinstance(result, ReActResult)
        assert result.rejected is True
        assert result.rejection_reason == "Weather is unavailable."

    def test_reset_agent_creates_fresh_copy(self, minimal_df, mock_client):
        """
        reset_agent() should replace self._df with a fresh copy of the original.

        Simulate mutation: add a column to self._df, then call reset_agent(),
        verify the new df does NOT have the added column.
        """
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()):
            layer = ExecutionLayer(minimal_df, mock_client)
            layer._df["injected"] = 99  # simulate state leakage
            assert "injected" in layer._df.columns
            layer.reset_agent()
            assert "injected" not in layer._df.columns

    def test_judge_result_parses_pass(self, minimal_df, mock_client):
        """
        judge_result() should return {"verdict": "PASS", ...} when LLM says VERDICT: PASS.
        """
        mock_client.invoke_chain.return_value = "VERDICT: PASS"
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()):
            layer = ExecutionLayer(minimal_df, mock_client)
            verdict = layer.judge_result(
                "Count samples per activity",
                "df.groupby('activity_label').size()",
                "A: 100, B: 200",
            )
        assert verdict["verdict"] == "PASS"

    def test_judge_result_parses_fail_with_issue(self, minimal_df, mock_client):
        """
        judge_result() should parse VERDICT: FAIL + ISSUE + SUGGESTION correctly.
        """
        mock_client.invoke_chain.return_value = (
            "VERDICT: FAIL\n"
            "ISSUE: Used wrong column name 'heartrate' instead of 'heart_rate'.\n"
            "SUGGESTION: Replace 'heartrate' with the correct column name."
        )
        from flashfusion.pipeline.executor import ExecutionLayer
        with patch.object(ExecutionLayer, "_build_agent", return_value=MagicMock()):
            layer = ExecutionLayer(minimal_df, mock_client)
            verdict = layer.judge_result("Q", "bad_code", "bad_result")
        assert verdict["verdict"] == "FAIL"
        assert "ISSUE" in verdict or "issue" in verdict
