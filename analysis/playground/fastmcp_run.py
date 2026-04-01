"""fastmcp_run.py
=================
Capability-boundary MCP server for Flash-Fusion datasets.

This server exposes deterministic, inspectable data capabilities and avoids
hardcoded domain semantics. It reuses core loading/metadata helpers from
``playground.py`` and keeps the interface domain-agnostic.

Run (stdio transport by default):
    python analysis/playground/fastmcp_run.py

Run with explicit transport:
    python analysis/playground/fastmcp_run.py --transport stdio
"""

# source .venv/bin/activate
# python playground.py --help
# python fastmcp_run.py --transport stdio

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

import click
import pandas as pd

from fastmcp import FastMCP

from playground import BASE_DIR, build_column_metadata, load_data


LOGGER = logging.getLogger("flashfusion.fastmcp")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    stream=sys.stderr,
)


@dataclass
class DatasetContext:
    dataset_id: str
    source_path: str
    format_tag: str
    df: pd.DataFrame
    column_metadata: dict[str, Any]
    codebook: dict[str, dict[str, str]]
    derived_features: dict[str, str]


class DatasetRegistry:
    def __init__(self):
        self._datasets: dict[str, DatasetContext] = {}

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ds in self._datasets.values():
            rows.append(
                {
                    "dataset_id": ds.dataset_id,
                    "format": ds.format_tag,
                    "source_path": ds.source_path,
                    "rows": len(ds.df),
                    "columns": list(ds.df.columns),
                    "derived_features": list(ds.derived_features.keys()),
                }
            )
        return rows

    def get(self, dataset_id: str) -> DatasetContext:
        if dataset_id not in self._datasets:
            known = ", ".join(sorted(self._datasets.keys())) or "(none loaded)"
            raise ValueError(f"Unknown dataset_id '{dataset_id}'. Loaded: {known}")
        return self._datasets[dataset_id]

    def load(
        self,
        data_path: str,
        dataset_id: str | None = None,
        codebook_json: str | None = None,
        derived_features_json: str | None = None,
    ) -> DatasetContext:
        df, format_tag = load_data(data_path)
        df_enriched, derived_features = self._apply_derived_features(df, derived_features_json)
        metadata = build_column_metadata(df_enriched)
        codebook = self._build_codebook(df_enriched, codebook_json)

        inferred_id = dataset_id or self._infer_dataset_id(data_path)
        ctx = DatasetContext(
            dataset_id=inferred_id,
            source_path=data_path,
            format_tag=format_tag,
            df=df_enriched,
            column_metadata=metadata,
            codebook=codebook,
            derived_features=derived_features,
        )
        self._datasets[inferred_id] = ctx
        return ctx

    @staticmethod
    def _infer_dataset_id(path: str) -> str:
        stem = os.path.splitext(os.path.basename(path))[0]
        return stem or "dataset"

    @staticmethod
    def _build_codebook(df: pd.DataFrame, codebook_json: str | None) -> dict[str, dict[str, str]]:
        """Return categorical legends.

        Priority:
        1) explicit user-provided codebook JSON
        2) generic identity legend inferred from low-cardinality categorical columns
        """
        if codebook_json:
            provided = json.loads(codebook_json)
            if not isinstance(provided, dict):
                raise ValueError("codebook_json must be a JSON object: {column: {raw: label}}")
            return {
                str(col): {str(k): str(v) for k, v in labels.items()}
                for col, labels in provided.items()
                if isinstance(labels, dict)
            }

        inferred: dict[str, dict[str, str]] = {}
        for col in df.columns:
            s = df[col]
            if pd.api.types.is_numeric_dtype(s):
                continue
            unique_vals = [v for v in s.dropna().unique().tolist()]
            if 0 < len(unique_vals) <= 30:
                inferred[col] = {str(v): str(v) for v in unique_vals}
        return inferred

    @staticmethod
    def _apply_derived_features(
        df: pd.DataFrame,
        derived_features_json: str | None,
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        """Apply optional deterministic derived-feature expressions.

        Expected JSON shape:
        {
          "new_col": {
            "formula": "col_a + col_b",
            "description": "sum of a and b"
          }
        }

        Formulas are evaluated with pandas.eval on existing columns only.
        """
        out = df.copy()
        derived: dict[str, str] = {}
        if not derived_features_json:
            return out, derived

        spec = json.loads(derived_features_json)
        if not isinstance(spec, dict):
            raise ValueError("derived_features_json must be a JSON object")

        for new_col, conf in spec.items():
            if not isinstance(conf, dict) or "formula" not in conf:
                raise ValueError(f"Derived feature '{new_col}' must define a 'formula'")
            formula = str(conf["formula"]).strip()
            if not formula:
                raise ValueError(f"Derived feature '{new_col}' formula is empty")
            out[new_col] = out.eval(formula)
            desc = str(conf.get("description") or formula)
            derived[str(new_col)] = desc

        return out, derived


registry = DatasetRegistry()
mcp = FastMCP("flash-fusion-capabilities")


def _apply_filters(df: pd.DataFrame, filters_json: str | None) -> pd.DataFrame:
    if not filters_json:
        return df

    filters = json.loads(filters_json)
    filtered = df
    for item in filters:
        col = item.get("column")
        op = item.get("op", "==")
        value = item.get("value")
        if col not in filtered.columns:
            raise ValueError(f"Filter column '{col}' not found")

        series = filtered[col]
        if op == "==":
            filtered = filtered[series == value]
        elif op == "!=":
            filtered = filtered[series != value]
        elif op == ">":
            filtered = filtered[pd.to_numeric(series, errors="coerce") > float(value)]
        elif op == ">=":
            filtered = filtered[pd.to_numeric(series, errors="coerce") >= float(value)]
        elif op == "<":
            filtered = filtered[pd.to_numeric(series, errors="coerce") < float(value)]
        elif op == "<=":
            filtered = filtered[pd.to_numeric(series, errors="coerce") <= float(value)]
        elif op == "in":
            vals = value if isinstance(value, list) else [value]
            filtered = filtered[series.isin(vals)]
        else:
            raise ValueError(f"Unsupported filter op '{op}'")
    return filtered


def _safe_serialize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _safe_serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_serialize(v) for v in value]
    if isinstance(value, tuple):
        return [_safe_serialize(v) for v in value]
    if isinstance(value, pd.Series):
        return _safe_serialize(value.to_dict())
    if isinstance(value, pd.DataFrame):
        return _safe_serialize(value.to_dict(orient="records"))
    return str(value)


@mcp.tool
def list_loaded_datasets() -> dict[str, Any]:
    """List datasets currently loaded in server memory."""
    return {"datasets": registry.list()}


@mcp.tool
def discover_data_files(
    root_path: str | None = None,
    include_pattern: str = r".*\.(csv|json|jsonl|parquet|pq|arff|txt|hea)$",
    max_results: int = 200,
) -> dict[str, Any]:
    """Discover candidate data files under a root path.

    Args:
        root_path: Directory to scan recursively. Defaults to repo data directory.
        include_pattern: Regex pattern for candidate file paths.
        max_results: Hard cap for returned file paths.
    """
    root = root_path or os.path.join(BASE_DIR, "data")
    if not os.path.isdir(root):
        return {"root_path": root, "files": [], "message": "root_path does not exist"}

    rx = re.compile(include_pattern, re.IGNORECASE)
    matches: list[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, BASE_DIR)
            if rx.match(rel):
                matches.append(full)
            if len(matches) >= max(1, max_results):
                break
        if len(matches) >= max(1, max_results):
            break

    return {"root_path": root, "count": len(matches), "files": matches}


@mcp.tool
def load_dataset(
    data_path: str,
    dataset_id: str | None = None,
    codebook_json: str | None = None,
    derived_features_json: str | None = None,
) -> dict[str, Any]:
    """Load a dataset into memory for subsequent deterministic tool calls.

    Args:
        data_path: Path to an input dataset file.
        dataset_id: Optional ID used by subsequent tool calls.
        codebook_json: Optional JSON object mapping raw categorical values to labels.
        derived_features_json: Optional JSON object specifying derived feature formulas.
    """
    ctx = registry.load(
        data_path=data_path,
        dataset_id=dataset_id,
        codebook_json=codebook_json,
        derived_features_json=derived_features_json,
    )
    return {
        "dataset_id": ctx.dataset_id,
        "format": ctx.format_tag,
        "source_path": ctx.source_path,
        "rows": len(ctx.df),
        "columns": list(ctx.df.columns),
        "derived_features": ctx.derived_features,
    }


@mcp.tool
def describe_dataset(dataset_id: str) -> dict[str, Any]:
    """Return schema and column-level metadata for a loaded dataset."""
    ctx = registry.get(dataset_id)
    return {
        "dataset_id": ctx.dataset_id,
        "source_path": ctx.source_path,
        "rows": len(ctx.df),
        "columns": list(ctx.df.columns),
        "column_metadata": _safe_serialize(ctx.column_metadata),
    }


@mcp.tool
def get_codebook(dataset_id: str) -> dict[str, Any]:
    """Return categorical legends (user-provided or generic inferred)."""
    ctx = registry.get(dataset_id)
    return {
        "dataset_id": dataset_id,
        "codebook": ctx.codebook,
    }


@mcp.tool
def get_derived_features(dataset_id: str) -> dict[str, Any]:
    """Return derived features and provenance descriptions."""
    ctx = registry.get(dataset_id)
    return {
        "dataset_id": dataset_id,
        "derived_features": ctx.derived_features,
    }


@mcp.tool
def run_aggregate(
    dataset_id: str,
    column: str,
    operation: str,
    filters_json: str | None = None,
) -> dict[str, Any]:
    """Run deterministic aggregate operation on a single column."""
    ctx = registry.get(dataset_id)
    if column not in ctx.df.columns:
        raise ValueError(f"Column '{column}' not found")

    df = _apply_filters(ctx.df, filters_json)
    s = df[column]
    op = operation.strip().lower()
    if op == "count":
        result = int(s.count())
    elif op == "nunique":
        result = int(s.nunique(dropna=True))
    elif op == "mean":
        result = float(pd.to_numeric(s, errors="coerce").mean())
    elif op == "median":
        result = float(pd.to_numeric(s, errors="coerce").median())
    elif op == "min":
        result = _safe_serialize(s.min())
    elif op == "max":
        result = _safe_serialize(s.max())
    elif op == "std":
        result = float(pd.to_numeric(s, errors="coerce").std())
    elif op == "sum":
        result = float(pd.to_numeric(s, errors="coerce").sum())
    else:
        raise ValueError(f"Unsupported operation '{operation}'")

    return {
        "dataset_id": dataset_id,
        "operation": op,
        "column": column,
        "rows_after_filter": len(df),
        "result": result,
    }


@mcp.tool
def run_groupby(
    dataset_id: str,
    group_by: str,
    target_column: str,
    agg: str = "count",
    top_k: int = 10,
    descending: bool = True,
    filters_json: str | None = None,
) -> dict[str, Any]:
    """Run deterministic groupby aggregation and return top-K groups."""
    ctx = registry.get(dataset_id)
    df = _apply_filters(ctx.df, filters_json)

    if group_by not in df.columns:
        raise ValueError(f"group_by column '{group_by}' not found")
    if target_column not in df.columns:
        raise ValueError(f"target_column '{target_column}' not found")

    agg_l = agg.lower().strip()
    grouped = df.groupby(group_by)[target_column]
    if agg_l == "count":
        res = grouped.count()
    elif agg_l == "nunique":
        res = grouped.nunique()
    elif agg_l in {"mean", "median", "min", "max", "std", "sum"}:
        num = pd.to_numeric(df[target_column], errors="coerce")
        res = pd.DataFrame({group_by: df[group_by], "__num__": num}).groupby(group_by)["__num__"].agg(agg_l)
    else:
        raise ValueError(f"Unsupported agg '{agg}'")

    res = res.sort_values(ascending=not descending).head(max(1, top_k))
    out = [{group_by: k, f"{agg_l}_{target_column}": _safe_serialize(v)} for k, v in res.items()]
    return {
        "dataset_id": dataset_id,
        "group_by": group_by,
        "target_column": target_column,
        "agg": agg_l,
        "rows_after_filter": len(df),
        "top_k": out,
    }


@mcp.tool
def run_window_metrics(
    dataset_id: str,
    column: str,
    window_size: int,
    metric: str = "mean",
    top_k: int = 3,
    highest: bool = True,
) -> dict[str, Any]:
    """Run rolling window metrics on a numeric column."""
    ctx = registry.get(dataset_id)
    if column not in ctx.df.columns:
        raise ValueError(f"Column '{column}' not found")
    if window_size < 2:
        raise ValueError("window_size must be >= 2")

    s = pd.to_numeric(ctx.df[column], errors="coerce")
    roll = s.rolling(window=window_size)
    metric_l = metric.lower().strip()
    if metric_l == "mean":
        vals = roll.mean()
    elif metric_l == "std":
        vals = roll.std()
    elif metric_l == "min":
        vals = roll.min()
    elif metric_l == "max":
        vals = roll.max()
    elif metric_l == "sum":
        vals = roll.sum()
    else:
        raise ValueError(f"Unsupported metric '{metric}'")

    valid = vals.dropna()
    if valid.empty:
        return {
            "dataset_id": dataset_id,
            "column": column,
            "window_size": window_size,
            "metric": metric_l,
            "summary": "No valid windows available",
            "top_windows": [],
        }

    ranked = valid.sort_values(ascending=not highest).head(max(1, top_k))
    top_windows = []
    for idx, value in ranked.items():
        end_idx = int(idx)
        start_idx = int(idx - window_size + 1)
        top_windows.append(
            {
                "start_row": start_idx,
                "end_row": end_idx,
                "value": float(value),
            }
        )

    return {
        "dataset_id": dataset_id,
        "column": column,
        "window_size": window_size,
        "metric": metric_l,
        "direction": "highest" if highest else "lowest",
        "global_min": float(valid.min()),
        "global_max": float(valid.max()),
        "global_mean": float(valid.mean()),
        "global_std": float(valid.std()),
        "top_windows": top_windows,
    }


@mcp.tool
def run_value_counts(
    dataset_id: str,
    column: str,
    top_k: int = 20,
    dropna: bool = True,
) -> dict[str, Any]:
    """Return value-count summary for any column (generic symbol/category summary)."""
    ctx = registry.get(dataset_id)
    if column not in ctx.df.columns:
        raise ValueError(f"Column '{column}' not found")

    vc = ctx.df[column].astype(str).value_counts(dropna=dropna).head(max(1, top_k))
    rows = [{"value": k, "count": int(v)} for k, v in vc.items()]
    legend = ctx.codebook.get(column, {})
    if legend:
        for row in rows:
            row["label"] = legend.get(row["value"], row["value"])

    return {
        "dataset_id": dataset_id,
        "column": column,
        "top_k": rows,
    }


@mcp.tool
def get_cluster_summary(
    dataset_id: str,
    regex_pattern: str = r"cluster|segment|zone",
) -> dict[str, Any]:
    """Return summaries for columns matching a cluster-like regex pattern."""
    ctx = registry.get(dataset_id)
    rx = re.compile(regex_pattern, re.IGNORECASE)
    candidates = [c for c in ctx.df.columns if rx.search(c)]
    if not candidates:
        return {
            "dataset_id": dataset_id,
            "available": False,
            "message": f"No columns matched pattern '{regex_pattern}'",
        }

    out: dict[str, Any] = {"dataset_id": dataset_id, "available": True, "columns": {}}
    for col in candidates:
        top = ctx.df[col].value_counts(dropna=False).head(10)
        out["columns"][col] = [
            {"value": _safe_serialize(k), "count": int(v)} for k, v in top.items()
        ]
    return out


@mcp.tool
def lookup_external_definition(term: str, glossary_json: str | None = None) -> dict[str, Any]:
    """Lookup term definitions from a caller-provided glossary.

    Args:
        term: Term to lookup.
        glossary_json: Optional JSON object {term: definition}. No hardcoded domains.
    """
    glossary: dict[str, str] = {}
    if glossary_json:
        parsed = json.loads(glossary_json)
        if not isinstance(parsed, dict):
            raise ValueError("glossary_json must be a JSON object")
        glossary = {str(k).strip().lower(): str(v) for k, v in parsed.items()}

    key = term.strip().lower()
    found = glossary.get(key)
    return {
        "term": term,
        "definition": found or "No definition found in provided glossary.",
        "source": "provided_glossary" if glossary else "none",
    }


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="MCP transport mode.",
)
def main(transport: str):
    LOGGER.info("Starting Flash-Fusion MCP server with transport=%s", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
