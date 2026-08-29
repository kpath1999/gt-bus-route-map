from __future__ import annotations

import pandas as pd

from flashfusion.eval.trace_hybrid_cache import (
    DEFAULT_CONFIG_PATH,
    ContractExtractor,
    HybridMatcher,
    load_config,
)
from flashfusion.viz.measure import CACHE_HIT_BASELINE, split_cache_baseline_rows


def _make_matcher() -> HybridMatcher:
    entries = [
        {
            "status": "reusable",
            "dataset": "bus",
            "query_id": "4",
            "query_text": "How many data samples show an accel_variance strictly greater than 0.20?",
            "semantic_signature": {
                "aggregate": "count",
                "fields": ["accel_variance"],
                "predicate_ops": {"accel_variance": ">"},
                "filter_values": {"accel_variance": "0.20"},
                "output_shape": "scalar",
                "predictive": {"model": None, "target_column": None},
            },
            "operator_skeleton": ["FILTER_COMPARE", "COUNT_ROWS"],
        }
    ]
    config = load_config(DEFAULT_CONFIG_PATH)
    return HybridMatcher(
        entries=entries,
        config=config,
        dataset="bus",
        schema_columns=["accel_variance", "timestamp", "behavior"],
        schema_fingerprint=None,
        device="cpu",
        no_warmup=True,
        mode="fuzzy",
        dense_top_k_override=1,
        lexical_top_k_override=1,
    )


def test_contract_extractor_parses_phrase_comparison_and_binding() -> None:
    extractor = ContractExtractor(schema_columns=["accel_variance", "timestamp"])
    contract = extractor.extract("How many data samples show an accel_variance strictly greater than 0.20?")

    assert contract.aggregate == "count"
    assert "accel_variance" in contract.fields
    assert ("accel_variance", "gt") in contract.predicate_ops
    assert ("accel_variance", "0.20") in contract.filter_values
    assert contract.output_shape == "scalar"


def test_contract_extractor_normalizes_confidence_for_complete_sparse_evidence() -> None:
    extractor = ContractExtractor(schema_columns=["accel_variance", "timestamp"])

    contract = extractor.extract("What is the maximum accel_variance?")

    assert contract.applicable_evidence_count == 2
    assert contract.matched_evidence_count == 2
    assert contract.confidence == 1.0


def test_contract_extractor_does_not_treat_ordinal_statistics_as_filter_literals() -> None:
    extractor = ContractExtractor(schema_columns=["accel_variance"])

    contract = extractor.extract("What is the maximum accel_variance at the 99th percentile?")

    assert contract.applicable_evidence_count == 2
    assert contract.matched_evidence_count == 2
    assert contract.confidence == 1.0


def test_contract_extractor_penalizes_unmatched_applicable_aggregate_cue() -> None:
    extractor = ContractExtractor(schema_columns=["accel_variance"])
    extractor._extract_aggregate = lambda _query: None  # type: ignore[method-assign]

    contract = extractor.extract("What is the maximum accel_variance?")

    assert contract.applicable_evidence_count == 2
    assert contract.matched_evidence_count == 1
    assert contract.confidence == 0.5


def test_component_scores_exclude_unknown_dimensions() -> None:
    matcher = _make_matcher()
    live = {
        "admissibility": "in_scope",
        "aggregate": "count",
        "fields": ["accel_variance"],
        "predicate_ops": {"accel_variance": "gt"},
        "filter_values": {"accel_variance": "0.20"},
        "output_shape": "scalar",
        "predictive": {},
        "operator_skeleton": None,
    }
    cand = {
        "aggregate": "count",
        "fields": ["accel_variance"],
        "predicate_ops": {"accel_variance": "gt"},
        "filter_values": {"accel_variance": "0.20"},
        "output_shape": "scalar",
        "predictive": {"model": None, "target_column": None},
        "operator_skeleton": ["FILTER_COMPARE", "COUNT_ROWS"],
    }

    scores, contract_score = matcher._component_scores(live, cand)

    assert set(scores) == {"aggregate", "fields", "predicate_ops", "filter_values", "output_shape"}
    assert contract_score == 1.0


def test_out_of_scope_empty_skeleton_is_a_high_contract_match() -> None:
    matcher = _make_matcher()
    live = {"admissibility": "out_of_scope", "operator_skeleton": None}
    cand = {"operator_skeleton": []}

    scores, contract_score = matcher._component_scores(live, cand)
    ok, failures = matcher._safety_critical_agreement(live, cand)

    assert scores == {"out_of_scope_match": 1.0}
    assert contract_score == 1.0
    assert ok
    assert failures == []


def test_in_scope_query_rejects_empty_skeleton_candidate() -> None:
    matcher = _make_matcher()
    live = {"admissibility": "in_scope", "operator_skeleton": None}
    cand = {"operator_skeleton": []}

    ok, failures = matcher._safety_critical_agreement(live, cand)

    assert not ok
    assert failures == ["out_of_scope_candidate_for_in_scope_query"]


def test_unkeyed_live_filter_values_do_not_hard_fail() -> None:
    matcher = _make_matcher()
    live = {
        "aggregate": "count",
        "fields": ["accel_variance"],
        "predicate_ops": {"accel_variance": "gt"},
        "filter_values": {"*": "0.20"},
        "output_shape": "scalar",
        "predictive": {},
        "operator_skeleton": None,
    }
    cand = {
        "aggregate": "count",
        "fields": ["accel_variance"],
        "predicate_ops": {"accel_variance": "gt"},
        "filter_values": {"accel_variance": "0.20"},
        "output_shape": "scalar",
        "predictive": {},
        "operator_skeleton": ["FILTER_COMPARE", "COUNT_ROWS"],
    }

    ok, failures = matcher._safety_critical_agreement(live, cand)

    assert ok
    assert "filter_value_mismatch" not in failures


def test_hit_rejected_is_classified_as_cache_hit() -> None:
    rows = pd.DataFrame(
        [{"baseline": "FLASH_FUSION_CACHE", "cache_outcome": "hit_rejected"}]
    )

    split = split_cache_baseline_rows(rows)

    assert CACHE_HIT_BASELINE in split["baseline"].tolist()
