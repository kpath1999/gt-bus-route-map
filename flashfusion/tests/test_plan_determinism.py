"""Two runs of the same question must produce the same plan digest.

Determinism is claimed at the level of the *plan*, not the prose: the executor
is pure pandas, so once the typed plan is fixed the answer is fixed. This is the
harness that would catch a regression where sampling variance leaks back into
planning — an unsorted list, a case-varying enum, an optional field the model
sometimes fills with null.

It runs against a stub planner, so it is deterministic and free. Point
``FF_DETERMINISM_LIVE=1`` at a real client to sample the actual model.
"""

from __future__ import annotations

import pandas as pd

from flashfusion.baselines.flash_fusion import typed_plan_digest
from flashfusion.pipeline.operators import normalize_raw_plan, structural_validate

#: The same intent, written the way a sampler actually varies it across draws.
SAMPLED_VARIANTS: list[dict] = [
    {
        "version": "1",
        "steps": [
            {"op": "FILTER_IN", "column": "record_id", "values": [106, 101]},
            {"op": "AGGREGATE_COLUMN", "column": "MLII", "aggregate": "mean"},
        ],
    },
    {
        "version": "1",
        "steps": [
            # reordered set membership
            {"op": "FILTER_IN", "column": "record_id", "values": [101, 106]},
            {"op": "AGGREGATE_COLUMN", "column": "MLII", "aggregate": "mean"},
        ],
    },
    {
        "version": "1",
        "steps": [
            # shouted enums and a lowercase op
            {"op": "filter_in", "column": "record_id", "values": [106, 101]},
            {"op": "AGGREGATE_COLUMN", "column": "MLII", "aggregate": "MEAN"},
        ],
    },
    {
        "version": "1",
        "steps": [
            # optional fields explicitly nulled
            {"op": "FILTER_IN", "column": "record_id", "values": [101, 106]},
            {
                "op": "AGGREGATE_COLUMN",
                "column": "MLII",
                "aggregate": "mean",
                "group_by": None,
            },
        ],
    },
]


def _digest(raw: dict) -> str:
    normalized, _ = normalize_raw_plan(raw)
    return typed_plan_digest(structural_validate(normalized))


def test_sampling_variants_collapse_to_one_plan_digest() -> None:
    digests = {_digest(variant) for variant in SAMPLED_VARIANTS}
    assert len(digests) == 1, f"plan digest varied across samplings: {digests}"


def test_digest_changes_when_the_plan_actually_differs() -> None:
    """The digest must not be so lossy that it hides a real semantic change."""
    baseline = _digest(SAMPLED_VARIANTS[0])
    different = _digest(
        {
            "version": "1",
            "steps": [
                {"op": "FILTER_IN", "column": "record_id", "values": [106, 101]},
                {"op": "AGGREGATE_COLUMN", "column": "MLII", "aggregate": "max"},
            ],
        }
    )
    assert baseline != different


def test_identical_plans_produce_identical_answers() -> None:
    """The other half of the claim: a fixed plan is a fixed number."""
    from flashfusion.pipeline.operators import execute_plan

    df = pd.DataFrame(
        {"record_id": [101, 101, 106, 106, 200], "MLII": [1.0, 3.0, 5.0, 7.0, 99.0]}
    )
    values = set()
    for variant in SAMPLED_VARIANTS:
        normalized, _ = normalize_raw_plan(variant)
        values.add(execute_plan(df, structural_validate(normalized)).value)
    assert values == {4.0}
