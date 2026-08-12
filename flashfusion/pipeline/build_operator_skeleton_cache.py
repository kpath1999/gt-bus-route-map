"""Build an exact-match cache registry for typed operator plans.

The project stores benchmark runs under ``flashfusion/results`` and the actual
artifact schema is slightly different from the original rough sketch:

- metrics.csv rows are under ``run_*`` directories and include per-query
  ``query_id`` / ``execution_path`` / ``operators_used`` / ``gt_score``.
- raw_results.jsonl rows contain the exact question in ``query`` and the full
  typed plan in ``typed_plan`` but do not include a stable query_id.
- literal query text for each query is most reliably recovered from the
  ground-truth / llm_judgment CSVs and matched back to raw results by exact
  question text.

This script scans the current result directories, builds a dataset-scoped cache
entry for each exact query text, and only marks an entry as reusable when every
observed run for that query is typed_operator, all pass the gt threshold, and the
operator skeleton agrees across runs. It also stamps every entry with the live
operator contract hash from the typed operator vocabulary.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

csv.field_size_limit(max(csv.field_size_limit(), 10_000_000))

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flashfusion.pipeline.operators import PLANNER_PREFIX_SHA256

RESULT_ROOT_CANDIDATES = [
    # REPO_ROOT / "flashfusion" / "results" / "ff_react_operators" / "FLASH_FUSION",
    REPO_ROOT / "flashfusion" / "results" / "ff_and_react_qwen" / "FLASH_FUSION",
    # REPO_ROOT / "results" / "ff_react_operators" / "FLASH_FUSION",
    REPO_ROOT / "results" / "ff_and_react_qwen" / "FLASH_FUSION",
]
OUTPUT_DIR = REPO_ROOT / "flashfusion" / "eval" / "cache"
SUCCESS_THRESHOLD = 1.0
TYPED_EXECUTION_PATH = "typed_operator"
QUERY_TEXT_KEYS = ("query_text", "query", "guardrail_input", "question", "nl_query", "prompt")
TYPED_PLAN_KEYS = ("typed_plan", "guardrail_plan", "final_plan", "plan")


@dataclass
class RunObservation:
    run_id: str
    execution_path: str | None
    plan_source: str | None
    operators_used: tuple[str, ...]
    gt_score: float | None
    judge_verdict: str | None
    rejected: bool | None = None


def is_out_of_scope(observation: RunObservation) -> bool:
    if observation.rejected is not None:
        return bool(observation.rejected)
    if observation.execution_path and "reject" in observation.execution_path.lower():
        return True
    return False


@dataclass
class SkeletonEntry:
    dataset: str
    query_id: str
    query_text: str | None
    query_text_source: str
    operator_skeleton: list[str]
    field_level_skeleton: list[dict[str, Any]] | None
    plan_source_modes: list[str]
    n_runs_observed: int
    n_runs_agreeing: int
    status: str
    reasons: list[str]
    example_run_ids: list[str]
    operator_contract_hash: str | None = None

    def cache_key(self) -> str:
        basis = self.query_text if self.query_text else f"__query_id__:{self.query_id}"
        return hashlib.sha256(f"{self.dataset}::{basis}".encode("utf-8")).hexdigest()


def canonical_dataset_dir_name(name: str) -> str:
    aliases = {"mit_ecg": "ecg", "ecg": "ecg", "bus": "bus", "wisdm": "wisdm"}
    return aliases.get(name, name)


def resolve_dataset_dirs() -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for root in RESULT_ROOT_CANDIDATES:
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            dataset = canonical_dataset_dir_name(child.name)
            if dataset not in resolved:
                resolved[dataset] = child
    return resolved


DATASET_DIRS = resolve_dataset_dirs()
if not DATASET_DIRS:
    DATASET_DIRS = {
        "bus": REPO_ROOT / "flashfusion" / "results" / "ff_and_react_qwen" / "FLASH_FUSION" / "bus",
        "ecg": REPO_ROOT / "flashfusion" / "results" / "ff_and_react_qwen" / "FLASH_FUSION" / "mit_ecg",
        "wisdm": REPO_ROOT / "flashfusion" / "results" / "ff_and_react_qwen" / "FLASH_FUSION" / "wisdm",
    }


def find_metrics_files(dataset_dir: Path) -> list[Path]:
    found: list[Path] = []
    top_level = dataset_dir / "metrics.csv"
    if top_level.exists():
        found.append(top_level)
    if dataset_dir.exists():
        for run_dir in sorted(dataset_dir.glob("run_*")):
            candidate = run_dir / "metrics.csv"
            if candidate.exists():
                found.append(candidate)
    return found


def find_raw_results_files(dataset_dir: Path) -> list[Path]:
    found: list[Path] = []
    top_level = dataset_dir / "raw_results.jsonl"
    if top_level.exists():
        found.append(top_level)
    if dataset_dir.exists():
        for run_dir in sorted(dataset_dir.glob("run_*")):
            candidate = run_dir / "raw_results.jsonl"
            if candidate.exists():
                found.append(candidate)
    return found


def parse_operators_used(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return tuple()
    if isinstance(raw, (list, tuple)):
        values = []
        for item in raw:
            if isinstance(item, str):
                values.append(item.strip())
        return tuple(value for value in values if value)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return tuple()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return parse_operators_used(parsed)
            except json.JSONDecodeError:
                pass
        return tuple(op.strip() for op in text.split(",") if op.strip())
    return tuple()


def find_query_text_lookup(dataset_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in dataset_dir.rglob("*.csv"):
        if "llm_judgments" not in path.name and "ground_truth" not in path.name:
            continue
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    qid = str(row.get("query_id") or "").strip()
                    text = None
                    for key in ("query_text", "guardrail_input", "question", "query"):
                        value = row.get(key)
                        if isinstance(value, str) and value.strip():
                            text = value.strip()
                            break
                    if qid and text:
                        mapping[qid] = text
        except OSError:
            continue
    return mapping


def load_metrics(metrics_path: Path) -> list[tuple[str, RunObservation]]:
    run_id = metrics_path.parent.name if metrics_path.parent.name.startswith("run_") else "single_run"
    observations: list[tuple[str, RunObservation]] = []
    try:
        with metrics_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if (row.get("baseline") or "").strip() not in {"FLASH_FUSION", "FLASH-FUSION"}:
                    continue
                qid = str(row.get("query_id") or "").strip()
                if not qid:
                    continue
                gt_score_raw = row.get("gt_score", "")
                try:
                    gt_score = float(gt_score_raw) if gt_score_raw not in ("", None) else None
                except ValueError:
                    gt_score = None
                rejected_raw = row.get("rejected", "")
                rejected = None
                if rejected_raw not in ("", None):
                    if isinstance(rejected_raw, str):
                        rejected = rejected_raw.strip().lower() in {"true", "1", "yes", "y"}
                    else:
                        rejected = bool(rejected_raw)
                observations.append(
                    (
                        qid,
                        RunObservation(
                            run_id=run_id,
                            execution_path=(row.get("execution_path") or "").strip() or None,
                            plan_source=(row.get("plan_source") or "").strip() or None,
                            operators_used=parse_operators_used(row.get("operators_used", "")),
                            gt_score=gt_score,
                            judge_verdict=(row.get("judge_verdict") or "").strip() or None,
                            rejected=rejected,
                        ),
                    )
                )
    except OSError:
        return []
    return observations


def try_extract_query_text_and_plan(raw_results_paths: list[Path], query_text_lookup: dict[str, str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    qid_by_text = {text: qid for qid, text in query_text_lookup.items()}

    for path in raw_results_paths:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        continue

                    qid = str(row.get("query_id") or "").strip()
                    if not qid:
                        qtext = None
                        for key in QUERY_TEXT_KEYS:
                            value = row.get(key)
                            if isinstance(value, str) and value.strip():
                                qtext = value.strip()
                                break
                        if qtext:
                            qid = qid_by_text.get(qtext)
                    if not qid:
                        continue

                    entry = out.setdefault(qid, {})
                    if "query_text" not in entry:
                        for key in QUERY_TEXT_KEYS:
                            value = row.get(key)
                            if isinstance(value, str) and value.strip():
                                entry["query_text"] = value.strip()
                                break
                    if "typed_plan" not in entry:
                        for key in TYPED_PLAN_KEYS:
                            value = row.get(key)
                            if value:
                                entry["typed_plan"] = value
                                break
        except OSError:
            continue
    return out


def field_level_skeleton_from_plan(typed_plan: Any) -> list[dict[str, Any]] | None:
    if typed_plan is None:
        return None
    if isinstance(typed_plan, str):
        try:
            typed_plan = json.loads(typed_plan)
        except json.JSONDecodeError:
            return None
    if isinstance(typed_plan, dict):
        if "steps" in typed_plan and isinstance(typed_plan["steps"], list):
            typed_plan = typed_plan["steps"]
        else:
            return None
    if not isinstance(typed_plan, list):
        return None

    skeleton: list[dict[str, Any]] = []
    for step in typed_plan:
        if not isinstance(step, dict) or "op" not in step:
            return None
        skeleton.append({"op": step["op"], "slots": sorted(key for key in step.keys() if key != "op")})
    return skeleton


def build_dataset_entries(dataset: str, dataset_dir: Path) -> list[SkeletonEntry]:
    metrics_files = find_metrics_files(dataset_dir)
    if not metrics_files:
        return []

    query_text_lookup = find_query_text_lookup(dataset_dir)
    by_query: dict[str, list[RunObservation]] = defaultdict(list)
    for path in metrics_files:
        for qid, obs in load_metrics(path):
            by_query[qid].append(obs)

    raw_lookup = try_extract_query_text_and_plan(find_raw_results_files(dataset_dir), query_text_lookup)

    entries: list[SkeletonEntry] = []
    for query_id, observations in sorted(by_query.items(), key=lambda item: item[0]):
        gt_1_runs = [obs for obs in observations if obs.gt_score is not None and obs.gt_score >= SUCCESS_THRESHOLD]
        decision_observations = gt_1_runs if gt_1_runs else observations

        skeletons = [obs.operators_used for obs in decision_observations]
        distinct_skeletons = set(skeletons)
        majority_skeleton = max(distinct_skeletons, key=skeletons.count) if distinct_skeletons else tuple()
        n_agreeing = sum(1 for item in skeletons if item == majority_skeleton)
        out_of_scope_flags = [is_out_of_scope(obs) for obs in decision_observations]
        all_oos = bool(decision_observations) and all(out_of_scope_flags)
        all_oos_agree = bool(decision_observations) and len(set(out_of_scope_flags)) == 1

        reasons: list[str] = []
        if not (all_oos and all_oos_agree):
            if not all(obs.execution_path == TYPED_EXECUTION_PATH for obs in decision_observations):
                reasons.append("not_all_runs_typed_operator")
            if not all(obs.gt_score is not None and obs.gt_score >= SUCCESS_THRESHOLD for obs in decision_observations):
                reasons.append("not_all_runs_passed_gt_threshold")
        if len(distinct_skeletons) != 1:
            reasons.append("operator_skeleton_disagrees_across_runs")
        if not majority_skeleton and not (all_oos and all_oos_agree):
            reasons.append("empty_operator_skeleton")

        status = "reusable" if not reasons else "needs_review"

        raw_info = raw_lookup.get(query_id, {})
        query_text = raw_info.get("query_text") or query_text_lookup.get(query_id)
        query_text_source = "raw_results" if raw_info.get("query_text") else ("llm_judgments" if query_text_lookup.get(query_id) else "query_id_fallback")
        field_skeleton = field_level_skeleton_from_plan(raw_info.get("typed_plan"))

        entries.append(
            SkeletonEntry(
                dataset=dataset,
                query_id=query_id,
                query_text=query_text,
                query_text_source=query_text_source,
                operator_skeleton=list(majority_skeleton),
                field_level_skeleton=field_skeleton,
                plan_source_modes=sorted({obs.plan_source for obs in observations if obs.plan_source}),
                n_runs_observed=len(observations),
                n_runs_agreeing=n_agreeing,
                status=status,
                reasons=reasons,
                example_run_ids=[obs.run_id for obs in observations],
                operator_contract_hash=PLANNER_PREFIX_SHA256,
            )
        )
    return entries


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    registry: dict[str, dict[str, Any]] = {}
    all_entries: list[SkeletonEntry] = []

    for dataset, dataset_dir in sorted(DATASET_DIRS.items()):
        entries = build_dataset_entries(dataset, dataset_dir)
        all_entries.extend(entries)
        for entry in entries:
            registry[entry.cache_key()] = asdict(entry)

    registry_path = OUTPUT_DIR / "cache_registry.json"
    report_path = OUTPUT_DIR / "cache_registry_report.md"

    with registry_path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, indent=2, sort_keys=True)

    reusable = [entry for entry in all_entries if entry.status == "reusable"]
    needs_review = [entry for entry in all_entries if entry.status == "needs_review"]
    missing_text = [entry for entry in all_entries if entry.query_text_source == "query_id_fallback"]

    lines = [
        "# Operator Skeleton Cache Registry",
        "",
        f"Total entries: {len(all_entries)}",
        f"Reusable (exact-match cache candidates): {len(reusable)}",
        f"Needs review (not cached): {len(needs_review)}",
        f"Missing literal query text (keyed by query_id only): {len(missing_text)}",
        "",
        "## Needs Review",
        "",
    ]

    if needs_review:
        for entry in needs_review:
            lines.append(
                f"- dataset={entry.dataset} query_id={entry.query_id} "
                f"skeleton={entry.operator_skeleton} reasons={entry.reasons}"
            )
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Missing Literal Query Text",
        "",
        "These entries could not be keyed by exact query text because the result artifacts did not expose a matchable literal question. They are keyed by query_id as a fallback, which is not safe for exact-match reuse across a real query stream.",
        "",
    ])

    if missing_text:
        for entry in missing_text:
            lines.append(f"- dataset={entry.dataset} query_id={entry.query_id}")
    else:
        lines.append("- None")

    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print(f"Wrote {registry_path} with {len(registry)} entries.")
    print(f"Wrote {report_path} ({len(reusable)} reusable, {len(needs_review)} needs_review).")


if __name__ == "__main__":
    main()