"""
tests/test_stages.py — Unit tests for pipeline stages S1, S2, S3.

Run with: pytest flashfusion/tests/test_stages.py -v

All LLM calls are mocked — these tests run without a GROQ_API_KEY.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from flashfusion.pipeline.stages import (
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    Stage3_SubqueryGeneration,
)
from flashfusion.prompts.templates import CONCEPT_EXTRACTION_PROMPT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client():
    """Return a mock LLMClient."""
    client = MagicMock()
    client.llm = MagicMock()
    return client


@pytest.fixture
def minimal_df():
    """Return a minimal 5-row WISDM-like DataFrame."""
    return pd.DataFrame(
        {
            "subject_id": [1600, 1600, 1601, 1601, 1602],
            "activity_label": ["A", "B", "C", "D", "E"],
            "timestamp": [1000, 2000, 3000, 4000, 5000],
            "x": [0.1, 0.2, 0.3, 0.4, 0.5],
            "y": [0.1, 0.2, 0.3, 0.4, 0.5],
            "z": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )


# ---------------------------------------------------------------------------
# Stage 1 tests
# ---------------------------------------------------------------------------

class TestStage1ConceptExtraction:
    """Tests for Stage1_ConceptExtraction.run()."""

    def test_prompt_enforces_minimality(self):
        """S1 prompt should explicitly require minimal, non-invented concepts."""
        assert "Extract only concepts that are strictly required" in CONCEPT_EXTRACTION_PROMPT
        assert "Do not invent structural or auxiliary concepts" in CONCEPT_EXTRACTION_PROMPT

    def test_parses_data_and_reasoning(self, mock_client):
        """
        Given a well-formed LLM response, run() should parse DATA and REASONING lists.

        Mock invoke_chain to return:
            "DATA: activity_label, x, y, z\nREASONING: magnitude, sedentary"

        Assert:
            - result["DATA"] == ["activity_label", "x", "y", "z"]
            - result["REASONING"] == ["magnitude", "sedentary"]
        """
        mock_client.invoke_chain.return_value = (
            "DATA: activity_label, x, y, z\nREASONING: magnitude, sedentary"
        )
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("Which activities have the highest magnitude?")
        assert "activity_label" in result["DATA"]
        assert "magnitude" in result["REASONING"]

    def test_filters_none_values(self, mock_client):
        """
        If LLM returns "REASONING: NONE", that list should be empty.

        Mock response: "DATA: subject_id\nREASONING: NONE"
        Assert: result["REASONING"] == []
        """
        mock_client.invoke_chain.return_value = "DATA: subject_id\nREASONING: NONE"
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("How many samples per subject?")
        assert result["REASONING"] == []
        assert "subject_id" in result["DATA"]

    def test_keyword_fallback_on_empty_response(self, mock_client):
        """
        If LLM returns empty lists for both DATA and REASONING, the stage should
        fall back to keyword extraction from the query.

        Mock invoke_chain to return "DATA: NONE\nREASONING: NONE" for all calls.

        Assert: result["DATA"] is non-empty (keyword fallback activated).
        """
        mock_client.invoke_chain.return_value = "DATA: NONE\nREASONING: NONE"
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("What is the average acceleration during jogging activities?")
        # Keyword fallback should populate DATA with tokens from the query
        assert len(result["DATA"]) > 0

    def test_retry_on_both_empty(self, mock_client):
        """
        If first call returns empty and query is > 20 chars, should retry once.

        Mock invoke_chain to return empty first, then valid second.
        Assert invoke_chain called exactly twice.
        """
        mock_client.invoke_chain.side_effect = [
            "DATA: NONE\nREASONING: NONE",
            "DATA: x, y\nREASONING: NONE",
        ]
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("Compare acceleration across activities for each subject")
        assert mock_client.invoke_chain.call_count == 2
        assert "x" in result["DATA"]


# ---------------------------------------------------------------------------
# Stage 2 tests
# ---------------------------------------------------------------------------

class TestStage2SchemaGrounding:
    """Tests for Stage2_SchemaGrounding.run()."""

    def test_parses_mappings_and_unmappable(self, mock_client, minimal_df):
        """
        Given a well-formed LLM response, run() should parse MAPPINGS and UNMAPPABLE.

        Mock response:
            "MAPPINGS:\n  activity → activity_label\nUNMAPPABLE: heart_rate"

        Assert:
            - grounding["mappings"] contains "activity → activity_label"
            - "heart_rate" in grounding["unmappable"]
        """
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
        mock_client.invoke_chain.return_value = (
            "MAPPINGS:\n  activity → activity_label\nUNMAPPABLE: heart_rate"
        )
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"DATA": ["activity"], "REASONING": []}
        result = stage.run(concepts, "heart rate during activity", meta_str, minimal_df)
        assert any("activity_label" in m for m in result["mappings"])
        assert "heart_rate" in result["unmappable"]

    def test_runs_without_codebook_injection(self, mock_client, minimal_df):
        """Stage2 should run successfully without any adapter/codebook state."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
        mock_client.invoke_chain.return_value = "MAPPINGS:\n  walking → activity_label == 'Walking'\nUNMAPPABLE: NONE"
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"DATA": ["walking"], "REASONING": []}
        result = stage.run(concepts, "walking stats", meta_str, minimal_df)
        assert any("activity_label" in m for m in result["mappings"])
        assert mock_client.invoke_chain.call_count >= 1

    def test_retry_on_empty_mappings(self, mock_client, minimal_df):
        """
        If first call returns no mappings, should retry once with stricter instructions.

        Mock: first call → "MAPPINGS:\nUNMAPPABLE: NONE", second → valid MAPPINGS.
        Assert invoke_chain called twice.
        """
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
        mock_client.invoke_chain.side_effect = [
            "MAPPINGS:\nUNMAPPABLE: NONE",
            "MAPPINGS:\n  subject → subject_id\nUNMAPPABLE: NONE",
        ]
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"DATA": ["subject"], "REASONING": []}
        stage.run(concepts, "samples per subject", meta_str, minimal_df)
        assert mock_client.invoke_chain.call_count == 2

    def test_filters_non_query_critical_concepts(self, mock_client, minimal_df):
        """S2 should drop concepts with no lexical evidence in the query."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str

        mock_client.invoke_chain.return_value = (
            "MAPPINGS:\n  accel_variance → accel_variance\nUNMAPPABLE: NONE"
        )
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {
            "DATA": ["identifier", "accel_variance", "timestamp", "acceleration"],
            "REASONING": [],
        }

        stage.run(
            concepts,
            "What is the maximum accel_variance observed in this dataset?",
            meta_str,
            minimal_df,
        )

        sent_input = mock_client.invoke_chain.call_args_list[0].args[1]["input"]
        assert "accel_variance" in sent_input
        assert "identifier" not in sent_input
        assert "timestamp" not in sent_input
        assert "acceleration" not in sent_input

    def test_filter_fallback_preserves_original_when_all_dropped(self, mock_client, minimal_df):
        """If filtering removes everything, S2 should keep original concepts."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str

        mock_client.invoke_chain.return_value = "MAPPINGS:\n  identifier → subject_id\nUNMAPPABLE: NONE"
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"DATA": ["identifier"], "REASONING": []}

        stage.run(concepts, "How many rows are there?", meta_str, minimal_df)

        sent_input = mock_client.invoke_chain.call_args_list[0].args[1]["input"]
        assert "DATA concepts: identifier" in sent_input


# ---------------------------------------------------------------------------
# Stage 3 tests
# ---------------------------------------------------------------------------

class TestStage3SubqueryGeneration:
    """Tests for Stage3_SubqueryGeneration.run()."""

    def test_parses_sub_queries_and_hint(self, mock_client):
        """
        Given a well-formed LLM response, run() should extract sub-queries and hint.

        Mock response:
            "SUB_Q1: [FILTER] Filter rows where activity_label == 'B'\\n"
            "SUB_Q2: [AGGREGATE] Compute mean(x) per subject_id\\n"
            "SYNTHESIS_HINT: Compare means across subjects."

        Assert:
            - len(sub_result["sub_queries"]) == 2
            - "FILTER" in sub_result["sub_queries"][0]
            - sub_result["synthesis_hint"] starts with "Compare"
        """
        mock_client.invoke_chain.return_value = (
            "SUB_Q1: [FILTER] Filter rows where activity_label == 'B'\n"
            "SUB_Q2: [AGGREGATE] Compute mean(x) per subject_id\n"
            "SYNTHESIS_HINT: Compare means across subjects."
        )
        stage = Stage3_SubqueryGeneration(mock_client)
        result = stage.run(
            "Compare mean x-axis acceleration during jogging per subject",
            grounding_raw="jogging → activity_label == 'B'; x → x column",
            meta_str="x (float64): min=-20 max=20 mean=0",
        )
        assert len(result["sub_queries"]) == 2
        assert "FILTER" in result["sub_queries"][0]
        assert result["synthesis_hint"].startswith("Compare")

    def test_returns_raw_subqueries(self, mock_client):
        """
        run() should preserve the raw LLM output in result["raw_subqueries"].
        """
        raw = "SUB_Q1: [AGGREGATE] Count rows per activity_label\nSYNTHESIS_HINT: Present counts."
        mock_client.invoke_chain.return_value = raw
        stage = Stage3_SubqueryGeneration(mock_client)
        result = stage.run("Count samples per activity", "", "")
        assert result["raw_subqueries"] == raw
