import csv
import json
from pathlib import Path

from flashfusion.pipeline import build_operator_skeleton_cache as cache


def _write_metrics(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "baseline",
                "query_id",
                "gt_score",
                "execution_path",
                "plan_source",
                "operators_used",
                "judge_verdict",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "baseline": "FLASH_FUSION",
                "query_id": "1",
                "gt_score": "1.0",
                "execution_path": "typed_operator",
                "plan_source": "llm",
                "operators_used": "FILTER_IN,AGGREGATE_COLUMN",
                "judge_verdict": "N/A",
            }
        )


def test_build_dataset_entries_extracts_real_query_text_and_typed_plan(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "wisdm"
    run_dir = dataset_dir / "run_1"
    run_dir.mkdir(parents=True)

    _write_metrics(run_dir / "metrics.csv")

    raw_payload = {
        "query": "What is the maximum recorded x-acceleration for user 15?",
        "execution_path": "typed_operator",
        "typed_plan": {
            "version": "1",
            "steps": [
                {"op": "FILTER_IN", "column": "subject_id", "values": [15]},
                {"op": "AGGREGATE_COLUMN", "column": "x", "aggregate": "max"},
            ],
        },
    }
    with (run_dir / "raw_results.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps(raw_payload) + "\n")

    llm_dir = run_dir / "ground_truth_llm_judge"
    llm_dir.mkdir(parents=True)
    with (llm_dir / "llm_judgments.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["query_id", "query_text"])
        writer.writeheader()
        writer.writerow({"query_id": "1", "query_text": "What is the maximum recorded x-acceleration for user 15?"})

    entries = cache.build_dataset_entries("wisdm", dataset_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.query_text == "What is the maximum recorded x-acceleration for user 15?"
    assert entry.status == "reusable"
    assert entry.operator_skeleton == ["FILTER_IN", "AGGREGATE_COLUMN"]
    assert entry.field_level_skeleton == [
        {"op": "FILTER_IN", "slots": ["column", "values"]},
        {"op": "AGGREGATE_COLUMN", "slots": ["aggregate", "column"]},
    ]


def test_build_dataset_entries_accepts_empty_operator_skeleton_for_out_of_scope_queries(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "wisdm"
    run_dir = dataset_dir / "run_1"
    run_dir.mkdir(parents=True)

    with (run_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "baseline",
                "query_id",
                "gt_score",
                "rejected",
                "execution_path",
                "plan_source",
                "operators_used",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "baseline": "FLASH_FUSION",
                "query_id": "9",
                "gt_score": "1.0",
                "rejected": "True",
                "execution_path": "guardrail_reject",
                "plan_source": "llm",
                "operators_used": "",
            }
        )

    with (run_dir / "raw_results.jsonl").open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "query": "How does the average walking speed in miles per hour correlate with the age of the users?",
                    "rejected": True,
                    "execution_path": "guardrail_reject",
                    "typed_plan": {},
                }
            )
            + "\n"
        )

    llm_dir = run_dir / "ground_truth_llm_judge"
    llm_dir.mkdir(parents=True)
    with (llm_dir / "llm_judgments.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["query_id", "query_text"])
        writer.writeheader()
        writer.writerow({"query_id": "9", "query_text": "How does the average walking speed in miles per hour correlate with the age of the users?"})

    entries = cache.build_dataset_entries("wisdm", dataset_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == "reusable"
    assert entry.operator_skeleton == []
    assert entry.reasons == []


def test_build_dataset_entries_prefers_gt_1_run_when_other_runs_fail_threshold(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "bus"
    run_dir = dataset_dir / "run_1"
    run_dir.mkdir(parents=True)

    with (run_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "baseline",
                "query_id",
                "gt_score",
                "execution_path",
                "plan_source",
                "operators_used",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "baseline": "FLASH_FUSION",
                "query_id": "6",
                "gt_score": "0.0",
                "execution_path": "typed_operator",
                "plan_source": "llm",
                "operators_used": "PARALLEL_AGGREGATE,DERIVE_BINARY,RANK_ROWS",
            }
        )

    another_run_dir = dataset_dir / "run_2"
    another_run_dir.mkdir(parents=True)
    with (another_run_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "baseline",
                "query_id",
                "gt_score",
                "execution_path",
                "plan_source",
                "operators_used",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "baseline": "FLASH_FUSION",
                "query_id": "6",
                "gt_score": "1.0",
                "execution_path": "typed_operator",
                "plan_source": "llm",
                "operators_used": "DERIVE_BINARY,RANK_ROWS",
            }
        )

    raw_path = another_run_dir / "raw_results.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "query": "Which bus route has the highest incidence of sudden braking?",
                    "execution_path": "typed_operator",
                    "typed_plan": {
                        "steps": [
                            {"op": "DERIVE_BINARY", "column": "braking"},
                            {"op": "RANK_ROWS", "column": "braking"},
                        ]
                    },
                }
            )
            + "\n"
        )

    llm_dir = dataset_dir / "ground_truth_llm_judge"
    llm_dir.mkdir(parents=True)
    with (llm_dir / "llm_judgments.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["query_id", "query_text"])
        writer.writeheader()
        writer.writerow({"query_id": "6", "query_text": "Which bus route has the highest incidence of sudden braking?"})

    entries = cache.build_dataset_entries("bus", dataset_dir)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == "reusable"
    assert entry.operator_skeleton == ["DERIVE_BINARY", "RANK_ROWS"]
    assert "not_all_runs_passed_gt_threshold" not in entry.reasons
