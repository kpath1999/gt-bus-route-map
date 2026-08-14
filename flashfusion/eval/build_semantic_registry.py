"""Build a semantic cache registry offline from query definitions.

This tool generates intent signatures that can be compared against live
(online) extraction at runtime. It is intended to pair with:
  - flashfusion.eval.trace_query --cache --query-version v2|v3 --semantic-cache-path ...

Typical use:
  python -m flashfusion.eval.build_semantic_registry --dataset bus --query-version v1 \
    --output flashfusion/eval/cache/semantic_registry_bus_v1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from flashfusion.baselines.flash_fusion_cache import (
    DEFAULT_CACHE_PATH,
    canonical_dataset,
    extract_semantic_signature,
)
from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS
from flashfusion.eval import queries as queries_v1
from flashfusion.eval import queries_v2, queries_v3
from flashfusion.eval.queries import SUPPORTED_DATASETS
from flashfusion.pipeline.loader import load_dataset_by_name


def _get_queries(dataset: str, version: str) -> list[dict]:
    if version == "v1":
        return queries_v1.get_queries(dataset)
    if version == "v2":
        return queries_v2.get_queries(dataset)
    if version == "v3":
        return queries_v3.get_queries(dataset)
    raise ValueError(f"Unsupported query version {version!r}")


def _load_entries(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        candidates = raw["entries"]
    elif isinstance(raw, dict):
        candidates = list(raw.values())
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise ValueError("Unsupported cache shape")
    return [x for x in candidates if isinstance(x, dict)]


def build_semantic_registry(
    *,
    dataset: str,
    query_version: str,
    data_path: str,
    cache_path: Path,
) -> dict[str, Any]:
    df = load_dataset_by_name(data_path, dataset)
    queries = _get_queries(dataset, query_version)
    q_by_id = {str(q["id"]): q for q in queries}

    exact_entries = _load_entries(cache_path)
    target_dataset = canonical_dataset(dataset)

    out: dict[str, Any] = {}
    for idx, entry in enumerate(exact_entries, start=1):
        if entry.get("status") != "reusable":
            continue
        if canonical_dataset(entry.get("dataset")) != target_dataset:
            continue
        skeleton = entry.get("operator_skeleton")
        if not isinstance(skeleton, list) or not skeleton:
            continue

        qid = str(entry.get("query_id") or "")
        qdef = q_by_id.get(qid)
        text = str(qdef["text"]) if qdef is not None else str(entry.get("query_text") or "")
        if not text.strip():
            continue

        sem_sig = extract_semantic_signature(text, df)
        template_id = f"{target_dataset}:{qid or idx}:{query_version}"
        out[template_id] = {
            "template_id": template_id,
            "dataset": target_dataset,
            "status": "reusable",
            "query_id": qid,
            "query_text": text,
            "query_version": query_version,
            "operator_skeleton": skeleton,
            "operator_contract_hash": entry.get("operator_contract_hash"),
            "schema_fingerprint": entry.get("schema_fingerprint"),
            "semantic_signature": sem_sig,
            "source": "offline_semantic_registry_build",
        }

    return {
        "version": 1,
        "dataset": target_dataset,
        "query_version": query_version,
        "count": len(out),
        "entries": out,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, choices=SUPPORTED_DATASETS)
    p.add_argument("--query-version", default="v1", choices=("v1", "v2", "v3"))
    p.add_argument("--data", default=None, help="Override dataset path")
    p.add_argument(
        "--cache-path",
        default=str(DEFAULT_CACHE_PATH),
        help="Path to exact cache registry used as source of operator skeletons",
    )
    p.add_argument(
        "--output",
        required=True,
        help="Output semantic registry JSON path",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    data_path = args.data or DEFAULT_DATA_PATHS[args.dataset]
    cache_path = Path(args.cache_path)
    output = Path(args.output)

    payload = build_semantic_registry(
        dataset=args.dataset,
        query_version=args.query_version,
        data_path=data_path,
        cache_path=cache_path,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Wrote semantic registry: dataset={payload['dataset']} version={payload['query_version']} "
        f"entries={payload['count']} -> {output}"
    )


if __name__ == "__main__":
    main()
