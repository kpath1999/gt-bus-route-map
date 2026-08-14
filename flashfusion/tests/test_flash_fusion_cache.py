"""Tests for the exact-match operator-skeleton cache baseline.

Covered gates: exact hit, cross-dataset miss, skeleton mutation rejection,
invalid grounding fallback, and successful typed execution. Every non-hit path
must delegate to the normal Flash-Fusion planner rather than answer from cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from flashfusion.baselines import flash_fusion_cache as ffc
from flashfusion.pipeline.runner import RunResult

SKELETON = ["FILTER_COMPARE", "COUNT_ROWS"]

GOOD_PLAN = {
    "version": "1",
    "steps": [
        {"op": "FILTER_COMPARE", "column": "accel_variance", "comparator": "gt", "value": 0.20},
        {"op": "COUNT_ROWS"},
    ],
}

QUERY = "How many data samples show an accel_variance strictly greater than 0.20?"


# ---------------------------------------------------------------------------
# Fixtures / doubles
# ---------------------------------------------------------------------------


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "accel_variance": [0.05, 0.25, 0.30, 0.20, 0.9],
            "behavior": ["calm", "rough", "rough", "calm", "rough"],
        }
    )


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "cache_registry.json"
    payload = {
        "bus_key": {
            "dataset": "bus",
            "query_text": QUERY,
            "status": "reusable",
            "operator_skeleton": SKELETON,
            "operator_contract_hash": "contract-1",
            "query_id": "4",
            "n_runs_agreeing": 6,
            "n_runs_observed": 6,
        },
        "ecg_key": {
            "dataset": "ecg",
            "query_text": "What is the minimum MLII value recorded for record_id 101?",
            "status": "reusable",
            "operator_skeleton": ["FILTER_COMPARE", "AGGREGATE_COLUMN"],
            "operator_contract_hash": "contract-1",
        },
        "oos_key": {
            "dataset": "bus",
            "query_text": "Did rainy weather cause the roughest segments in this route?",
            "status": "reusable",
            "operator_skeleton": [],
            "operator_contract_hash": "contract-1",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _FakeLight:
    """Stands in for ``client.light``: returns one canned grounding response."""

    def __init__(self, response: str) -> None:
        self.llm = object()
        self.response = response
        self.prompts: list[str] = []

    def invoke_messages(self, messages: list, stage: str) -> str:
        self.prompts.append(messages[-1].content)
        return self.response


class _FakeClient:
    def __init__(self, response: str = "") -> None:
        self.model_name = "test/model"
        self.light = _FakeLight(response)


@pytest.fixture
def no_fallback(monkeypatch):
    """Make any delegation to the full planner an explicit test failure."""

    def _boom(*args, **kwargs):
        raise AssertionError("run_flash_fusion should not have been called")

    monkeypatch.setattr(ffc, "run_flash_fusion", _boom)


@pytest.fixture
def fallback_spy(monkeypatch):
    """Record delegation to the full planner instead of making a real call."""
    calls: list[dict] = []

    def _fake_run_flash_fusion(query, df, client, r=None, **kwargs):
        calls.append({"query": query, "r": r, "kwargs": kwargs})
        if r is not None:
            r.answer = "fallback-answer"
            r.execution_path = "react_fallback"
            return r
        return RunResult(baseline="FLASH_FUSION", model="", query=query)

    monkeypatch.setattr(ffc, "run_flash_fusion", _fake_run_flash_fusion)
    return calls


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_exact_hit_matches_dataset_and_literal_query(registry: Path) -> None:
    entries = ffc._load_entries(registry)
    entry, status = ffc._find_exact_entry(entries, QUERY, "bus")
    assert status == "exact_cache_hit"
    assert entry is not None
    assert entry["operator_skeleton"] == SKELETON


def test_exact_hit_canonicalises_mit_ecg_to_ecg(registry: Path) -> None:
    entries = ffc._load_entries(registry)
    entry, status = ffc._find_exact_entry(
        entries, "What is the minimum MLII value recorded for record_id 101?", "mit_ecg"
    )
    assert status == "exact_cache_hit"
    assert entry is not None and entry["dataset"] == "ecg"


def test_cross_dataset_lookup_is_a_miss(registry: Path) -> None:
    entries = ffc._load_entries(registry)
    # Same literal query text, wrong dataset — reuse must not leak across datasets.
    entry, status = ffc._find_exact_entry(entries, QUERY, "wisdm")
    assert entry is None
    assert status == "exact_query_miss"


def test_empty_skeleton_entry_is_out_of_scope_hit(registry: Path) -> None:
    entries = ffc._load_entries(registry)
    entry, status = ffc._find_exact_entry(
        entries, "Did rainy weather cause the roughest segments in this route?", "bus"
    )
    assert entry is not None
    assert status == "exact_cache_hit_out_of_scope"
    assert entry["operator_skeleton"] == []


def test_whitespace_only_differences_still_hit(registry: Path) -> None:
    entries = ffc._load_entries(registry)
    entry, status = ffc._find_exact_entry(entries, f"  {QUERY}\n", "bus")
    assert status == "exact_cache_hit" and entry is not None


# ---------------------------------------------------------------------------
# End-to-end cache path
# ---------------------------------------------------------------------------


def test_successful_typed_execution_on_cache_hit(df, registry, no_fallback) -> None:
    client = _FakeClient(json.dumps(GOOD_PLAN))
    trace = ffc.CacheGroundingTrace()

    result = ffc.run_flash_fusion_cache(
        QUERY, df, client, dataset="bus", cache_path=registry, trace=trace
    )

    assert result.executed is True
    assert result.rejected is False
    assert result.execution_path == ffc.PATH_TYPED_OPERATOR_CACHE
    assert result.operators_used == SKELETON
    assert result.plan_source == "exact_query_cache_light_grounded"
    # 0.25, 0.30, 0.9 are strictly greater than 0.20.
    assert result.raw_answer == "3"
    assert "3" in result.answer
    assert result.typed_plan["steps"][0]["column"] == "accel_variance"
    assert result.final_code
    assert "df = df[df['accel_variance'] > 0.2]" in result.final_code
    assert "result = len(df)" in result.final_code
    assert result.stage_latency_s["cache_grounding"] >= 0.0
    assert result.stage_latency_s["typed_exec"] >= 0.0

    assert trace.hit is True
    assert trace.operator_skeleton == SKELETON
    # The light model sees the live schema, never stored values or an answer.
    assert "accel_variance" in trace.prompt
    assert "LIVE DATASET SCHEMA" in trace.prompt


def test_code_fenced_light_output_is_accepted(df, registry, no_fallback) -> None:
    client = _FakeClient("```json\n" + json.dumps(GOOD_PLAN) + "\n```")
    result = ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=registry)
    assert result.execution_path == ffc.PATH_TYPED_OPERATOR_CACHE


def test_out_of_scope_cache_hit_rejects_without_full_planner(df, registry, no_fallback) -> None:
    """An empty skeleton means the query is out-of-scope; infer the reason from the light model."""
    oos_query = "Did rainy weather cause the roughest segments in this route?"
    reason = "The dataset does not contain any column indicating weather conditions."
    client = _FakeClient(json.dumps({"rejection_reason": reason}))
    trace = ffc.CacheGroundingTrace()

    result = ffc.run_flash_fusion_cache(
        oos_query, df, client, dataset="bus", cache_path=registry, trace=trace
    )

    assert result.rejected is True
    assert result.executed is False
    assert result.execution_path == "guardrail_reject"
    assert result.plan_source == "exact_query_cache_out_of_scope"
    assert result.answer == f"Query rejected. Reason: {reason}"
    assert "Rejected by the guardrail because" in result.trace
    assert reason in result.trace
    # One cheap light-model call for the reason, no planner fallback.
    assert len(client.light.prompts) == 1
    assert "LIVE DATASET SCHEMA" in client.light.prompts[0]
    assert oos_query in client.light.prompts[0]


def test_cross_dataset_miss_falls_back_to_full_planner(df, registry, fallback_spy) -> None:
    client = _FakeClient(json.dumps(GOOD_PLAN))
    r = RunResult(baseline=ffc.BASELINE_NAME, model="test/model", query=QUERY)

    result = ffc.run_flash_fusion_cache(
        QUERY, df, client, r, dataset="wisdm", cache_path=registry
    )

    assert len(fallback_spy) == 1
    assert fallback_spy[0]["r"] is r
    assert result.answer == "fallback-answer"
    assert "exact_query_miss" in result.deterministic_fallback_reason
    # No light-model call is worth making on a miss.
    assert client.light.prompts == []


def test_cache_grounding_timing_survives_fallback(df, registry, fallback_spy) -> None:
    client = _FakeClient(json.dumps({"cache_grounding_failed": True, "reason": "no column"}))
    result = ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=registry)

    assert len(fallback_spy) == 1
    assert result.stage_latency_s["cache_grounding"] >= 0.0


def test_skeleton_mutation_is_rejected(df, registry, fallback_spy) -> None:
    mutated = {
        "version": "1",
        "steps": [
            {"op": "FILTER_COMPARE", "column": "accel_variance", "comparator": "gt", "value": 0.20},
            {"op": "AGGREGATE_COLUMN", "column": "accel_variance", "aggregate": "mean"},
        ],
    }
    client = _FakeClient(json.dumps(mutated))
    trace = ffc.CacheGroundingTrace()

    result = ffc.run_flash_fusion_cache(
        QUERY, df, client, dataset="bus", cache_path=registry, trace=trace
    )

    assert len(fallback_spy) == 1
    assert "changed the cached operator skeleton" in result.deterministic_fallback_reason
    assert trace.hit is False and trace.fell_back is True
    assert trace.validated_plan is None
    assert result.typed_plan == {}


def test_invalid_grounding_falls_back(df, registry, fallback_spy) -> None:
    """A column the dataset does not have must not reach execution."""
    hallucinated = {
        "version": "1",
        "steps": [
            {"op": "FILTER_COMPARE", "column": "rainfall_mm", "comparator": "gt", "value": 0.20},
            {"op": "COUNT_ROWS"},
        ],
    }
    client = _FakeClient(json.dumps(hallucinated))

    result = ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=registry)

    assert len(fallback_spy) == 1
    assert result.executed is False
    assert "rainfall_mm" in result.deterministic_fallback_reason


def test_declined_grounding_falls_back(df, registry, fallback_spy) -> None:
    client = _FakeClient(json.dumps({"cache_grounding_failed": True, "reason": "no such column"}))
    result = ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=registry)
    assert len(fallback_spy) == 1
    assert "declined grounding" in result.deterministic_fallback_reason


def test_non_json_light_output_falls_back(df, registry, fallback_spy) -> None:
    client = _FakeClient("Sure! Here is the plan you asked for.")
    ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=registry)
    assert len(fallback_spy) == 1


def test_contract_hash_mismatch_falls_back(df, registry, fallback_spy) -> None:
    client = _FakeClient(json.dumps(GOOD_PLAN))
    result = ffc.run_flash_fusion_cache(
        QUERY,
        df,
        client,
        dataset="bus",
        cache_path=registry,
        expected_operator_contract_hash="contract-2",
    )
    assert len(fallback_spy) == 1
    assert "operator_contract_hash_mismatch" in result.deterministic_fallback_reason


def test_schema_fingerprint_mismatch_falls_back(df, tmp_path, fallback_spy) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "dataset": "bus",
                        "query_text": QUERY,
                        "status": "reusable",
                        "operator_skeleton": SKELETON,
                        "schema_fingerprint": "deadbeefdeadbeef",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = _FakeClient(json.dumps(GOOD_PLAN))
    result = ffc.run_flash_fusion_cache(QUERY, df, client, dataset="bus", cache_path=path)
    assert len(fallback_spy) == 1
    assert "schema_fingerprint_mismatch" in result.deterministic_fallback_reason


def test_missing_registry_file_falls_back(df, tmp_path, fallback_spy) -> None:
    client = _FakeClient(json.dumps(GOOD_PLAN))
    ffc.run_flash_fusion_cache(
        QUERY, df, client, dataset="bus", cache_path=tmp_path / "nope.json"
    )
    assert len(fallback_spy) == 1


def test_shipped_registry_entries_are_wellformed() -> None:
    """The checked-in registry must stay loadable and dataset-scoped."""
    entries = ffc._load_entries(ffc.DEFAULT_CACHE_PATH)
    assert entries
    for entry in entries:
        assert isinstance(entry.get("query_text"), str)
        assert ffc.canonical_dataset(entry.get("dataset")) in {"bus", "ecg", "wisdm"}
    reusable = [
        (ffc.canonical_dataset(e["dataset"]), e["query_text"].strip())
        for e in entries
        if e.get("status") == "reusable" and e.get("operator_skeleton")
    ]
    assert len(reusable) == len(set(reusable)), "duplicate (dataset, query_text) cache keys"


@pytest.fixture
def semantic_registry(tmp_path: Path) -> Path:
    path = tmp_path / "semantic_registry.json"
    payload = {
        "semantic_1": {
            "template_id": "tmpl-bus-count-gt",
            "dataset": "bus",
            "status": "reusable",
            "operator_skeleton": SKELETON,
            "operator_contract_hash": "contract-1",
            "semantic_signature": {
                "aggregate": "count",
                "fields": ["accel_variance"],
                "predicate_ops": {"accel_variance": "gt"},
                "output_shape": "scalar",
            },
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_semantic_hit_on_exact_miss_uses_existing_grounding_path(
    df, registry, semantic_registry, no_fallback
) -> None:
    client = _FakeClient(json.dumps(GOOD_PLAN))
    paraphrase = "Count rows with accel_variance > 0.20"
    trace = ffc.CacheGroundingTrace()

    result = ffc.run_flash_fusion_cache(
        paraphrase,
        df,
        client,
        dataset="bus",
        cache_path=registry,
        semantic_cache_path=semantic_registry,
        trace=trace,
    )

    assert result.executed is True
    assert result.execution_path == ffc.PATH_TYPED_OPERATOR_CACHE
    assert result.plan_source == "semantic_cache_light_grounded"
    assert trace.lookup_status == "semantic_cache_hit"
    assert trace.semantic_match_evidence is not None
    assert trace.semantic_match_evidence["candidate_ids_considered"]
    assert trace.semantic_match_evidence["abstention_reason"] == ""


def test_semantic_aggregate_mismatch_abstains_before_light_call(
    df, registry, tmp_path, fallback_spy
) -> None:
    semantic_path = tmp_path / "semantic_registry_min.json"
    semantic_path.write_text(
        json.dumps(
            {
                "entry": {
                    "template_id": "tmpl-bus-min",
                    "dataset": "bus",
                    "status": "reusable",
                    "operator_skeleton": ["FILTER_COMPARE", "AGGREGATE_COLUMN"],
                    "operator_contract_hash": "contract-1",
                    "semantic_signature": {
                        "aggregate": "min",
                        "fields": ["accel_variance"],
                        "predicate_ops": {"accel_variance": "gt"},
                        "output_shape": "scalar",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    # If semantic gates fail early, no light-model prompt should be sent.
    client = _FakeClient(json.dumps(GOOD_PLAN))
    query = "What is the median accel_variance where accel_variance > 0.20?"
    trace = ffc.CacheGroundingTrace()

    result = ffc.run_flash_fusion_cache(
        query,
        df,
        client,
        dataset="bus",
        cache_path=registry,
        semantic_cache_path=semantic_path,
        trace=trace,
    )

    assert len(fallback_spy) == 1
    assert "semantic:" in result.deterministic_fallback_reason
    assert trace.semantic_match_evidence is not None
    assert trace.semantic_match_evidence["abstention_reason"] == "semantic_gate_reject_all"
    # No prompt means we abstained before invoking light grounding.
    assert client.light.prompts == []


def test_exact_hit_still_short_circuits_semantic_extraction(
    df, registry, semantic_registry, no_fallback, monkeypatch
) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("semantic extractor should not run on exact cache hit")

    monkeypatch.setattr(ffc, "_extract_semantic_signature", _boom)
    client = _FakeClient(json.dumps(GOOD_PLAN))

    result = ffc.run_flash_fusion_cache(
        QUERY,
        df,
        client,
        dataset="bus",
        cache_path=registry,
        semantic_cache_path=semantic_registry,
    )

    assert result.execution_path == ffc.PATH_TYPED_OPERATOR_CACHE
    assert result.plan_source == "exact_query_cache_light_grounded"
