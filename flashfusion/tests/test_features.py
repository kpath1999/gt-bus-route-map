from __future__ import annotations

import pandas as pd

from flashfusion.pipeline.features import resolve_grounded_features


def test_resolves_vector_magnitude_only_when_grounded() -> None:
    df = pd.DataFrame({"x": [3.0], "y": [4.0], "z": [12.0]})

    unresolved, unresolved_added = resolve_grounded_features(
        df, "MAPPINGS:\nUNMAPPABLE: NONE"
    )
    resolved, added = resolve_grounded_features(
        df,
        "MAPPINGS:\n  acceleration magnitude \u2192 VECTOR_MAGNITUDE(x, y, z)\nUNMAPPABLE: NONE",
    )

    assert unresolved is df
    assert unresolved_added == []
    assert "acceleration_magnitude" not in df.columns
    assert added == ["acceleration_magnitude"]
    assert resolved.loc[0, "acceleration_magnitude"] == 13.0


def test_ignores_vector_magnitude_with_missing_component_column() -> None:
    df = pd.DataFrame({"x": [3.0], "y": [4.0]})

    resolved, added = resolve_grounded_features(
        df,
        "MAPPINGS:\n  magnitude \u2192 VECTOR_MAGNITUDE(x, y, z)\nUNMAPPABLE: NONE",
    )

    assert resolved is df
    assert added == []