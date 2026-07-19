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

    def test_parses_three_buckets(self, mock_client):
        """
        Given a well-formed LLM response, run() should parse COLUMN, DERIVED_STAT,
        and PROXY lists.

        Mock invoke_chain to return:
            "COLUMN: activity_label, x, y, z\nDERIVED_STAT: NONE\nPROXY: magnitude, sedentary"

        Assert:
            - result["COLUMN"] == ["activity_label", "x", "y", "z"]
            - result["PROXY"] == ["magnitude", "sedentary"]
        """
        mock_client.invoke_chain.return_value = (
            "COLUMN: activity_label, x, y, z\nDERIVED_STAT: NONE\nPROXY: magnitude, sedentary"
        )
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("Which activities have the highest magnitude?")
        assert "activity_label" in result["COLUMN"]
        assert "magnitude" in result["PROXY"]
        assert result["DERIVED_STAT"] == []

    def test_parses_derived_stat_bucket(self, mock_client):
        """DERIVED_STAT concepts (median, average, threshold splits) parse separately."""
        mock_client.invoke_chain.return_value = (
            "COLUMN: latitude, acceleration variance\n"
            "DERIVED_STAT: median, average, northern half, southern half\n"
            "PROXY: NONE"
        )
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run(
            "Is the northern half of the route rougher than the southern half, "
            "based on average acceleration variance?"
        )
        assert "median" not in result["COLUMN"]
        assert "median" in result["DERIVED_STAT"]
        assert "average" in result["DERIVED_STAT"]
        assert "northern half" in result["DERIVED_STAT"]

    def test_filters_none_values(self, mock_client):
        """
        If LLM returns "DERIVED_STAT: NONE" / "PROXY: NONE", those lists should be empty.

        Mock response: "COLUMN: subject_id\nDERIVED_STAT: NONE\nPROXY: NONE"
        Assert: result["DERIVED_STAT"] == [] and result["PROXY"] == []
        """
        mock_client.invoke_chain.return_value = (
            "COLUMN: subject_id\nDERIVED_STAT: NONE\nPROXY: NONE"
        )
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("How many samples per subject?")
        assert result["DERIVED_STAT"] == []
        assert result["PROXY"] == []
        assert "subject_id" in result["COLUMN"]

    def test_keyword_fallback_on_empty_response(self, mock_client):
        """
        If LLM returns empty lists for all three buckets, the stage should
        fall back to keyword extraction from the query.

        Mock invoke_chain to return all-NONE for all calls.

        Assert: result["COLUMN"] is non-empty (keyword fallback activated).
        """
        mock_client.invoke_chain.return_value = (
            "COLUMN: NONE\nDERIVED_STAT: NONE\nPROXY: NONE"
        )
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("What is the average acceleration during jogging activities?")
        # Keyword fallback should populate COLUMN with tokens from the query
        assert len(result["COLUMN"]) > 0

    def test_retry_on_all_empty(self, mock_client):
        """
        If first call returns empty and query is > 20 chars, should retry once.

        Mock invoke_chain to return empty first, then valid second.
        Assert invoke_chain called exactly twice.
        """
        mock_client.invoke_chain.side_effect = [
            "COLUMN: NONE\nDERIVED_STAT: NONE\nPROXY: NONE",
            "COLUMN: x, y\nDERIVED_STAT: NONE\nPROXY: NONE",
        ]
        stage = Stage1_ConceptExtraction(mock_client)
        result = stage.run("Compare acceleration across activities for each subject")
        assert mock_client.invoke_chain.call_count == 2
        assert "x" in result["COLUMN"]


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
        concepts = {"COLUMN": ["activity"], "DERIVED_STAT": [], "PROXY": []}
        result = stage.run(concepts, "heart rate during activity", meta_str, minimal_df)
        assert any("activity_label" in m for m in result["mappings"])
        assert "heart_rate" in result["unmappable"]

    def test_runs_without_codebook_injection(self, mock_client, minimal_df):
        """Stage2 should run successfully without any adapter/codebook state."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
        mock_client.invoke_chain.return_value = "MAPPINGS:\n  walking → activity_label == 'Walking'\nUNMAPPABLE: NONE"
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"COLUMN": ["walking"], "DERIVED_STAT": [], "PROXY": []}
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
        concepts = {"COLUMN": ["subject"], "DERIVED_STAT": [], "PROXY": []}
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
            "COLUMN": ["identifier", "accel_variance", "timestamp", "acceleration"],
            "DERIVED_STAT": [],
            "PROXY": [],
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
        concepts = {"COLUMN": ["identifier"], "DERIVED_STAT": [], "PROXY": []}

        stage.run(concepts, "How many rows are there?", meta_str, minimal_df)

        sent_input = mock_client.invoke_chain.call_args_list[0].args[1]["input"]
        assert "COLUMN concepts: identifier" in sent_input

    def test_derived_stat_grounds_to_operation_not_bare_column(self, mock_client, minimal_df):
        """DERIVED_STAT concepts (e.g. 'median') should ground to OPERATION(column),
        never to a bare unrelated column such as a percentile column."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str

        mock_client.invoke_chain.return_value = (
            "MAPPINGS:\n"
            "  latitude → latitude\n"
            "  median → MEDIAN(latitude)\n"
            "UNMAPPABLE: NONE"
        )
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"COLUMN": ["latitude"], "DERIVED_STAT": ["median"], "PROXY": []}
        result = stage.run(
            concepts, "latitude above the median", meta_str, minimal_df
        )
        assert any("MEDIAN(latitude)" in m for m in result["mappings"])
        # "MEDIAN" itself must never be flagged as an invalid/unknown column.
        assert not any("INVALID(MEDIAN" in m for m in result["mappings"])

    def test_repair_derived_stat_column_drift(self):
        """Regression test for the accel_variance -> accel_stats_y_p90 drift bug:
        a DERIVED_STAT mapping whose concept text overlaps an already-grounded
        COLUMN concept must be forced to reuse that same column."""
        valid_cols = {"latitude", "accel_variance", "accel_stats_y_p90"}
        mappings = [
            "acceleration variance → accel_variance",
            "average acceleration variance → MEAN(accel_stats_y_p90)",
        ]
        repaired = Stage2_SchemaGrounding._repair_derived_stat_column_drift(mappings, valid_cols)
        assert "average acceleration variance → MEAN(accel_variance)" in repaired
        assert not any("accel_stats_y_p90" in m for m in repaired)

    def test_repair_unresolved_column_reference_fuzzy_matches_real_column(self):
        """Regression test: the LLM inventing 'acceleration_variance' (a plausible
        but non-existent column) instead of the real abbreviated column
        'accel_variance' must be auto-corrected before invalid-column validation."""
        valid_cols = {"latitude", "accel_variance"}
        mappings = ["acceleration variance → acceleration_variance"]
        repaired = Stage2_SchemaGrounding._repair_unresolved_column_reference(mappings, valid_cols)
        assert repaired == ["acceleration variance → accel_variance"]

    def test_run_repairs_fuzzy_column_before_invalid_check(self, mock_client, minimal_df):
        """End-to-end: run() should repair a near-miss invented column name so it
        never gets flagged INVALID, using a df that has an abbreviated column."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str

        df = minimal_df.copy()
        df["accel_variance"] = [0.1, 0.2, 0.3, 0.4, 0.5]
        mock_client.invoke_chain.return_value = (
            "MAPPINGS:\n"
            "  acceleration variance → acceleration_variance\n"
            "UNMAPPABLE: NONE"
        )
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(df))
        concepts = {"COLUMN": ["acceleration variance"], "DERIVED_STAT": [], "PROXY": []}
        result = stage.run(concepts, "acceleration variance", meta_str, df)
        assert any("accel_variance" in m and "INVALID" not in m for m in result["mappings"])
        assert not any("acceleration_variance" in m for m in result["mappings"])

    def test_run_retries_when_derived_stat_left_unmappable(self, mock_client, minimal_df):
        """If DERIVED_STAT concepts are dumped into UNMAPPABLE despite a grounded
        COLUMN concept existing, S2 should retry once with a stricter instruction."""
        from flashfusion.pipeline.loader import build_column_metadata, meta_to_str

        mock_client.invoke_chain.side_effect = [
            "MAPPINGS:\n  latitude → latitude\nUNMAPPABLE: median",
            "MAPPINGS:\n  latitude → latitude\n  median → MEDIAN(latitude)\nUNMAPPABLE: NONE",
        ]
        stage = Stage2_SchemaGrounding(mock_client)
        meta_str = meta_to_str(build_column_metadata(minimal_df))
        concepts = {"COLUMN": ["latitude"], "DERIVED_STAT": ["median"], "PROXY": []}
        result = stage.run(concepts, "latitude above the median", meta_str, minimal_df)
        assert mock_client.invoke_chain.call_count == 2
        assert any("MEDIAN(latitude)" in m for m in result["mappings"])
        assert "median" not in result["unmappable"]


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

    def test_compiles_group_median_split_plan(self, mock_client):
        """Regression test for bus query 5: a median-split group comparison should
        be compiled deterministically into typed sub-queries (no ReAct fallback),
        reusing the exact column named in the grounding (accel_variance), never
        an unrelated percentile column."""
        mock_client.invoke_chain.return_value = (
            "SUB_Q1: [FILTER] irrelevant free-text plan\nSYNTHESIS_HINT: n/a"
        )
        stage = Stage3_SubqueryGeneration(mock_client)
        grounding_raw = (
            "MAPPINGS:\n"
            "  latitude → latitude\n"
            "  acceleration variance → accel_variance\n"
            "  median → MEDIAN(latitude)\n"
            "  northern half → latitude > MEDIAN(latitude)\n"
            "  southern half → latitude <= MEDIAN(latitude)\n"
            "  average acceleration variance → MEAN(accel_variance)\n"
            "UNMAPPABLE: NONE"
        )
        meta_str = "latitude (float64): min=0 max=1\naccel_variance (float64): min=0 max=1"
        result = stage.run(
            "Is the northern half of the route (latitude above median) rougher "
            "than the southern half, based on average acceleration variance?",
            grounding_raw=grounding_raw,
            meta_str=meta_str,
        )
        assert result["compiled_plan"] is True
        typed = result["typed_sub_queries"]
        ops = [step["op"] for step in typed]
        assert ops == [
            "SPLIT_BY_THRESHOLD",
            "SPLIT_BY_THRESHOLD",
            "GROUP_AGGREGATE",
            "COMPARE_GROUPS",
        ]
        split_steps = [s for s in typed if s["op"] == "SPLIT_BY_THRESHOLD"]
        assert all(s["column"] == "latitude" for s in split_steps)
        group_agg = next(s for s in typed if s["op"] == "GROUP_AGGREGATE")
        assert group_agg["column"] == "accel_variance"
