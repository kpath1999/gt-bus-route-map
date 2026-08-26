from __future__ import annotations

import pandas as pd

from flashfusion.viz.latencydebug import (
    STAGE_COLUMNS,
    _apply_cache_outcome_normalization,
    cache_vs_planner_speedup_summary,
    find_hit_slower_than_planner,
    reconcile_segments,
)


def _row_with_all_stages(**overrides):
    row = {
        "dataset": "bus",
        "run_id": 1,
        "query_id": 1,
        "latency_s": 0.0,
        "cache_outcome": "miss",
        "execution_path": "typed_operator",
    }
    for col in STAGE_COLUMNS:
        row[col] = 0.0
    row.update(overrides)
    return row


def test_reconcile_segments_prefers_guardrail_path_over_hit_label():
    df = pd.DataFrame(
        [
            _row_with_all_stages(
                cache_outcome="hit",
                execution_path="guardrail_reject",
                latency_s=2.1,
                cache_lookup_latency_s=0.1,
                cache_rejection_latency_s=2.0,
            )
        ]
    )

    out = reconcile_segments(df).iloc[0]

    assert "cache_rejection_latency_s" in out["expected_stage_set"]
    assert "cache_grounding_latency_s" not in out["expected_stage_set"]
    assert float(out["stray_stage_latency_s"]) == 0.0


def test_find_hit_slower_than_planner_ignores_non_typed_hits():
    cache_df = pd.DataFrame(
        [
            _row_with_all_stages(
                cache_outcome="hit",
                execution_path="guardrail_reject",
                latency_s=5.0,
            )
        ]
    )
    planner_df = pd.DataFrame(
        [
            _row_with_all_stages(
                cache_outcome="not_applicable",
                execution_path="typed_operator",
                latency_s=1.0,
            )
        ]
    )

    flagged = find_hit_slower_than_planner(cache_df, planner_df)

    assert flagged.empty


def test_speedup_summary_tracks_hit_rejected_separately():
    cache_df = pd.DataFrame(
        [
            _row_with_all_stages(cache_outcome="hit", execution_path="typed_operator_cache", latency_s=2.0),
            _row_with_all_stages(cache_outcome="hit_rejected", execution_path="guardrail_reject", latency_s=4.0),
            _row_with_all_stages(cache_outcome="miss", execution_path="typed_operator", latency_s=3.0),
        ]
    )
    planner_df = pd.DataFrame(
        [
            _row_with_all_stages(cache_outcome="not_applicable", execution_path="typed_operator", latency_s=6.0)
        ]
    )

    summary = cache_vs_planner_speedup_summary(cache_df, planner_df)
    row = summary.iloc[0]

    assert int(row["n_hit"]) == 1
    assert int(row["n_hit_rejected"]) == 1
    assert float(row["hit_rejected_mean_latency_s"]) == 4.0
    assert float(row["hit_speedup_vs_planner_x"]) == 3.0


def test_speedup_summary_excludes_cache_retry_overhead_from_fair_hit_latency():
    cache_df = pd.DataFrame(
        [
            _row_with_all_stages(
                cache_outcome="hit",
                execution_path="typed_operator_cache",
                latency_s=10.0,
                cache_retry_overhead_s=6.0,
            )
        ]
    )
    planner_df = pd.DataFrame(
        [
            _row_with_all_stages(
                cache_outcome="not_applicable",
                execution_path="typed_operator",
                latency_s=8.0,
            )
        ]
    )

    summary = cache_vs_planner_speedup_summary(cache_df, planner_df)
    row = summary.iloc[0]

    assert float(row["hit_mean_latency_s_raw"]) == 10.0
    assert float(row["hit_mean_latency_s"]) == 4.0
    assert float(row["hit_speedup_vs_planner_x"]) == 2.0


def test_normalization_relabels_stale_guardrail_hit_as_hit_rejected():
    df = pd.DataFrame(
        [
            {
                "baseline": "FLASH_FUSION_CACHE",
                "cache_outcome": "hit",
                "execution_path": "guardrail_reject",
                "plan_source": "exact_query_cache_out_of_scope",
            }
        ]
    )

    out = _apply_cache_outcome_normalization(df).iloc[0]

    assert out["cache_outcome_raw"] == "hit"
    assert out["cache_outcome"] == "hit_rejected"
    assert bool(out["cache_outcome_mismatch"]) is True


def test_normalization_preserves_non_cache_baseline_outcome():
    df = pd.DataFrame(
        [
            {
                "baseline": "FLASH_FUSION",
                "cache_outcome": "not_applicable",
                "execution_path": "typed_operator",
                "plan_source": "llm",
            }
        ]
    )

    out = _apply_cache_outcome_normalization(df).iloc[0]

    assert out["cache_outcome"] == "not_applicable"
    assert bool(out["cache_outcome_mismatch"]) is False
