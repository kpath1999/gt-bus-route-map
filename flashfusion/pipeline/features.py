"""Query-driven derived features for rewriting pipelines.

Stage 2 (schema grounding) is the single source of truth for *which* derived
feature a query actually needs. This module never precomputes features
speculatively — it only materializes a feature when a Stage 2 mapping line
explicitly names a formulaic expression (e.g. ``VECTOR_MAGNITUDE(x, y, z)``)
built entirely from columns that already exist in the DataFrame. The derived
column is named after the concept on the left-hand side of that mapping
line, so this generalizes across datasets/concepts instead of hardcoding a
fixed column name such as "magnitude".
"""

from __future__ import annotations

import re

import pandas as pd

_VECTOR_MAGNITUDE_RE = re.compile(
    r"(?P<lhs>[^\n\u2192]+?)\s*\u2192[^\n]*?\bVECTOR_MAGNITUDE\(\s*(?P<args>[^()]+?)\s*\)"
)
_INTER_SAMPLE_SECONDS_RE = re.compile(
    r"(?P<lhs>[^\n\u2192]+?)\s*\u2192[^\n]*?\bINTER_SAMPLE_SECONDS\("
    r"\s*(?P<time_col>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<group_col>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


def _sanitize_feature_name(concept: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", concept.strip().lower()).strip("_")
    return name or "derived_feature"


def resolve_grounded_features(
    df: pd.DataFrame, grounding: str
) -> tuple[pd.DataFrame, list[str]]:
    """Materialize only features explicitly derivable from Stage 2 grounding.

    Returns (possibly-copied) DataFrame and the list of newly added column
    names, so callers can refresh downstream column metadata only when
    something actually changed.
    """
    resolved = df
    added: list[str] = []

    for match in _VECTOR_MAGNITUDE_RE.finditer(grounding):
        args = [a.strip() for a in match.group("args").split(",") if a.strip()]
        if len(args) < 2 or not all(col in resolved.columns for col in args):
            continue
        feature_name = _sanitize_feature_name(match.group("lhs"))
        if feature_name in resolved.columns:
            continue
        if resolved is df:
            resolved = df.copy()
        resolved[feature_name] = sum(resolved[col] ** 2 for col in args) ** 0.5
        added.append(feature_name)

    for match in _INTER_SAMPLE_SECONDS_RE.finditer(grounding):
        time_col = match.group("time_col")
        group_col = match.group("group_col")
        if time_col not in resolved.columns or group_col not in resolved.columns:
            continue
        feature_name = _sanitize_feature_name(match.group("lhs"))
        if feature_name in resolved.columns:
            continue
        if resolved is df:
            resolved = df.copy()
        sorted_df = resolved.sort_values([group_col, time_col])
        intervals = (
            sorted_df.groupby(group_col)[time_col].diff().clip(lower=0).fillna(0) / 1e9
        )
        resolved[feature_name] = intervals.reindex(resolved.index).fillna(0)
        added.append(feature_name)

    return resolved, added