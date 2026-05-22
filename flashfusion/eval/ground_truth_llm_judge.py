"""
eval/ground_truth_llm_judge.py — LLM-based judging against benchmark ground truth.

This module supports two workflows:
1) Standalone: judge existing `raw_results.jsonl` files under eval_results/*_all.
2) Programmatic: judge in-memory RunResult rows emitted by benchmark.py.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.config import DEFAULT_MODEL
from flashfusion.eval.ground_truth import GroundTruthEntry, load_ground_truth
from flashfusion.eval.ground_truth_builder import build_ground_truth
from flashfusion.eval.queries import DATASET_WISDM, SUPPORTED_DATASETS, get_queries
from flashfusion.eval.semantic_scorer import SemanticScorer
from flashfusion.pipeline.loader import load_dataset_by_name
from flashfusion.pipeline.runner import LLMClient, RunResult


def _query_lookup(dataset: str) -> dict[str, int]:
    return {q["text"]: q["id"] for q in get_queries(dataset)}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " ...[truncated]"


def _normalize_answer(text: str) -> str:
    """
    Normalize numeric answers for robust comparison.
    
    - Strips trailing sentence-ending periods from numbers
    - Rounds floats to 3 decimal places for consistent formatting
    - Preserves original text structure otherwise
    """
    import re
    
    text = (text or "").strip()
    
    # Pattern: number (possibly negative, with decimals) followed by period at word boundary
    # Replace trailing period only when it's sentence punctuation, not part of number
    def normalize_number(match):
        num_str = match.group(1)
        try:
            # Parse as float and round to 3 decimals
            num = float(num_str)
            # Format with up to 3 decimals, stripping unnecessary trailing zeros
            formatted = f"{num:.3f}".rstrip('0').rstrip('.')
            return formatted
        except (ValueError, OverflowError):
            return num_str
    
    # Match numbers (integer or float, possibly negative) followed by optional period
    # Capture number, normalize it, and remove trailing period if present
    text = re.sub(r'(-?\d+\.?\d*)(?:\.(?=\s|$))?', normalize_number, text)
    
    return text


def _load_jsonl_rows(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSONL at {path}:{lineno}: {e}") from e
            if isinstance(obj, dict):
                obj["_source_file"] = path
                rows.append(obj)
    return rows


def _rows_from_run_results(results: list[RunResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in results:
        row = dataclasses.asdict(r)
        row["_source_file"] = "[in_memory_benchmark_run]"
        out.append(row)
    return out


def _extract_first_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _resolve_candidate_code(row: dict[str, Any]) -> str:
    """Resolve best available generated code from a result row."""
    final_code = str(row.get("final_code", "")).strip()
    if final_code:
        return final_code

    attempts = row.get("execution_attempts", [])
    if isinstance(attempts, list):
        for attempt in reversed(attempts):
            if not isinstance(attempt, dict):
                continue
            for key in ("code", "generated_code", "final_code", "candidate_code", "action_input"):
                value = str(attempt.get(key, "")).strip()
                if value:
                    return value

    trace = str(row.get("trace", ""))
    if "Action Input:" in trace:
        action_inputs = [
            ln.split("Action Input:", 1)[1].strip()
            for ln in trace.splitlines()
            if "Action Input:" in ln
        ]
        action_inputs = [x for x in action_inputs if x]
        if action_inputs:
            return action_inputs[-1]

    return ""


def build_ground_truth_sanity(
    ground_truth_by_id: dict[int, GroundTruthEntry],
    data_path: str | None,
    dataset: str,
) -> pd.DataFrame:
    """
    Compare current ground truth against deterministic dataset-derived references.

    Returns a row per query with a semantic similarity score and mismatch flags.
    """
    rows: list[dict[str, Any]] = []
    scorer = SemanticScorer()

    rebuilt_by_id: dict[int, dict[str, Any]] = {}
    if data_path:
        df = load_dataset_by_name(data_path, dataset)
        rebuilt = build_ground_truth(df, dataset)
        rebuilt_by_id = {int(x["query_id"]): x for x in rebuilt}

    for qid in sorted(ground_truth_by_id):
        gt = ground_truth_by_id[qid]
        rebuilt = rebuilt_by_id.get(qid)
        rebuilt_answer = ""
        rebuilt_expected_rejection = None
        sim = None
        rejection_match = None
        if rebuilt:
            rebuilt_answer = str(rebuilt.get("reference_answer", ""))
            rebuilt_expected_rejection = bool(rebuilt.get("expected_rejection", False))
            sim = scorer.score(gt.reference_answer, rebuilt_answer)
            rejection_match = gt.expected_rejection == rebuilt_expected_rejection

        rows.append(
            {
                "query_id": qid,
                "query_text": gt.query_text,
                "gt_expected_rejection": gt.expected_rejection,
                "rebuilt_expected_rejection": rebuilt_expected_rejection,
                "expected_rejection_match": rejection_match,
                "semantic_similarity": sim,
                "gt_reference_answer": gt.reference_answer,
                "rebuilt_reference_answer": rebuilt_answer,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    def _flag(row: pd.Series) -> str:
        sim_val = row.get("semantic_similarity")
        rem = row.get("expected_rejection_match")
        if rem is False:
            return "mismatch_expected_rejection"
        if sim_val is None or pd.isna(sim_val):
            return "no_dataset_sanity"
        if float(sim_val) < 0.55:
            return "low_similarity"
        return "ok"

    out["sanity_flag"] = out.apply(_flag, axis=1)
    return out


def judge_rows_with_llm(
    rows: list[dict[str, Any]],
    ground_truth_by_id: dict[int, GroundTruthEntry],
    dataset: str,
    model_name: str,
    api_key: str,
    sanity_df: pd.DataFrame | None = None,
    max_answer_chars: int = 1800,
    max_code_chars: int = 1400,
) -> pd.DataFrame:
    """Run LLM-based per-row judgment against ground truth."""
    q_lookup = _query_lookup(dataset)
    sanity_by_id: dict[int, dict[str, Any]] = {}
    if sanity_df is not None and not sanity_df.empty:
        sanity_by_id = {
            int(r["query_id"]): {
                "sanity_flag": str(r.get("sanity_flag", "")),
                "rebuilt_reference_answer": str(r.get("rebuilt_reference_answer", "")),
            }
            for r in sanity_df.to_dict(orient="records")
        }

    client = LLMClient(model_name=model_name, api_key=api_key)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You evaluate benchmark answers against ground truth. "
                "Be strict, concise, and prefer factual correctness over phrasing. "
                "For numeric answers: treat values as equivalent if they differ by less than 0.01 or represent the same number with different trailing zeros. "
                "Ignore formatting differences like trailing periods after numbers. "
                "Return JSON only.",
            ),
            (
                "user",
                """
Judge whether candidate answer matches ground truth.

Return JSON with keys:
- verdict: PASS | FAIL
- reason: one short sentence
- ground_truth_sanity: SOUND | POSSIBLY_WRONG | WRONG
- ground_truth_note: short note

Rules:
- PASS only if the candidate answer is factually correct. FAIL otherwise — there is no middle ground.
- For numeric values: -3.175 and -3.1750 are equivalent; ignore trailing zeros and minor rounding differences (< 0.01).
- If expected_rejection=true, PASS only if candidate clearly rejects for the same scope/schema reason.
- If expected_rejection=false and candidate rejects, verdict must be FAIL.
- Use generated code and deterministic hint only as supporting evidence.

Query:
{query_text}

Expected rejection:
{expected_rejection}

Ground truth answer:
{ground_truth_answer}

Deterministic sanity hint (may be empty):
{deterministic_hint}

Deterministic sanity flag:
{deterministic_flag}

Candidate baseline:
{baseline}

Candidate executed:
{executed}

Candidate rejected:
{rejected}

Candidate answer:
{candidate_answer}

Candidate generated code:
{candidate_code}
""",
            ),
        ]
    )
    chain = prompt | client.llm | StrOutputParser()

    judged_rows: list[dict[str, Any]] = []
    for row in rows:
        query_text = str(row.get("query", ""))
        baseline = str(row.get("baseline", "UNKNOWN"))
        qid = q_lookup.get(query_text)
        if qid is None or qid not in ground_truth_by_id:
            continue

        gt = ground_truth_by_id[qid]
        sanity = sanity_by_id.get(qid, {})
        candidate_code = _resolve_candidate_code(row)
        
        # Normalize answers for robust numeric comparison
        normalized_gt_answer = _normalize_answer(gt.reference_answer)
        normalized_candidate_answer = _normalize_answer(str(row.get("answer", "")))
        
        llm_raw = client.invoke_chain(
            chain,
            {
                "query_text": gt.query_text,
                "expected_rejection": str(gt.expected_rejection),
                "ground_truth_answer": normalized_gt_answer,
                "deterministic_hint": _clip(
                    _normalize_answer(str(sanity.get("rebuilt_reference_answer", ""))),
                    max_answer_chars,
                ),
                "deterministic_flag": str(sanity.get("sanity_flag", "")),
                "baseline": baseline,
                "executed": str(bool(row.get("executed", False))),
                "rejected": str(bool(row.get("rejected", False))),
                "candidate_answer": _clip(normalized_candidate_answer, max_answer_chars),
                "candidate_code": _clip(candidate_code, max_code_chars),
            },
            stage="gt_llm_judge",
        )

        parsed = _extract_first_json(llm_raw)
        verdict = str(parsed.get("verdict", "FAIL")).upper()
        if verdict not in {"PASS", "FAIL"}:
            verdict = "FAIL"

        score = 1.0 if verdict == "PASS" else 0.0

        judged_rows.append(
            {
                "source_file": str(row.get("_source_file", "")),
                "baseline": baseline,
                "query_id": qid,
                "query_text": gt.query_text,
                "expected_rejection": gt.expected_rejection,
                "gt_reference_answer": gt.reference_answer,
                "candidate_answer": str(row.get("answer", "")),
                "candidate_rejected": bool(row.get("rejected", False)),
                "candidate_executed": bool(row.get("executed", False)),
                "candidate_code": candidate_code,
                "llm_verdict": verdict,
                "llm_score": score,
                "llm_reason": str(parsed.get("reason", "")),
                "gt_sanity": str(parsed.get("ground_truth_sanity", "")),
                "gt_sanity_note": str(parsed.get("ground_truth_note", "")),
                "llm_raw": llm_raw,
            }
        )

    df = pd.DataFrame(judged_rows)
    if not df.empty:
        df = df.sort_values(["baseline", "query_id"]).reset_index(drop=True)
    return df


def summarize_judgments(judgments_df: pd.DataFrame) -> pd.DataFrame:
    if judgments_df.empty:
        return pd.DataFrame(
            columns=[
                "baseline",
                "pass_rate",
                "fail_rate",
                "count",
            ]
        )

    tmp = judgments_df.copy()
    tmp["is_pass"] = (tmp["llm_verdict"] == "PASS").astype(int)
    tmp["is_fail"] = (tmp["llm_verdict"] == "FAIL").astype(int)

    summary = (
        tmp.groupby("baseline")
        .agg(
            pass_rate=("is_pass", "mean"),
            fail_rate=("is_fail", "mean"),
            count=("query_id", "count"),
        )
        .reset_index()
        .sort_values("pass_rate", ascending=False)
    )
    return summary


def run_llm_ground_truth_judge(
    *,
    rows: list[dict[str, Any]],
    ground_truth_by_id: dict[int, GroundTruthEntry],
    output_dir: str,
    model_name: str,
    api_key: str,
    data_path: str | None = None,
    dataset: str = DATASET_WISDM,
    max_answer_chars: int = 1800,
    max_code_chars: int = 1400,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run full LLM judging workflow and write output artifacts."""
    os.makedirs(output_dir, exist_ok=True)

    sanity_df = build_ground_truth_sanity(
        ground_truth_by_id=ground_truth_by_id,
        data_path=data_path,
        dataset=dataset,
    )
    judgments_df = judge_rows_with_llm(
        rows=rows,
        ground_truth_by_id=ground_truth_by_id,
        dataset=dataset,
        model_name=model_name,
        api_key=api_key,
        sanity_df=sanity_df,
        max_answer_chars=max_answer_chars,
        max_code_chars=max_code_chars,
    )
    summary_df = summarize_judgments(judgments_df)

    judgments_path = os.path.join(output_dir, "llm_judgments.csv")
    summary_path = os.path.join(output_dir, "llm_judgments_summary.csv")
    sanity_path = os.path.join(output_dir, "ground_truth_sanity.csv")

    judgments_df.to_csv(judgments_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    sanity_df.to_csv(sanity_path, index=False)

    with open(os.path.join(output_dir, "llm_judgments.jsonl"), "w", encoding="utf-8") as fh:
        for row in judgments_df.to_dict(orient="records"):
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")

    return judgments_df, summary_df, sanity_df


def _collect_rows(results_files: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in results_files:
        rows.extend(_load_jsonl_rows(p))
    return rows


def _resolve_results_files(results: str | None, results_glob: str | None) -> list[str]:
    files: list[str] = []
    if results:
        for raw in results.split(","):
            p = raw.strip()
            if p:
                files.append(p)
    if results_glob:
        files.extend(glob.glob(results_glob))
    files = sorted({str(Path(p)) for p in files})
    return [p for p in files if Path(p).exists()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "LLM ground-truth judge for benchmark raw_results.jsonl files. "
            "Compares candidate answers against flashfusion/eval/ground_truth_wisdm.json."
        )
    )
    parser.add_argument(
        "--results",
        default=None,
        help="Comma-separated raw_results.jsonl files",
    )
    parser.add_argument(
        "--results-glob",
        default="flashfusion/eval_results/*_all/raw_results.jsonl",
        help="Glob pattern for raw_results.jsonl files",
    )
    parser.add_argument(
        "--ground-truth",
        default="flashfusion/eval/ground_truth_wisdm.json",
        help="Path to ground truth JSON",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Optional dataset path for deterministic sanity checks",
    )
    parser.add_argument(
        "--dataset",
        default=DATASET_WISDM,
        choices=list(SUPPORTED_DATASETS),
        help="Dataset profile for query lookup and deterministic sanity checks",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LLM model for judging (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output",
        default="flashfusion/eval_results/ground_truth_llm_judge",
        help="Output directory for judge artifacts",
    )
    parser.add_argument(
        "--max-answer-chars",
        type=int,
        default=1800,
        help="Max answer chars sent to judge model",
    )
    parser.add_argument(
        "--max-code-chars",
        type=int,
        default=1400,
        help="Max generated-code chars sent to judge model",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("Error: GROQ_API_KEY environment variable not set")

    ground_truth_by_id = load_ground_truth(args.ground_truth)
    files = _resolve_results_files(args.results, args.results_glob)
    if not files:
        raise SystemExit("No results files found. Check --results or --results-glob")

    rows = _collect_rows(files)
    judgments_df, summary_df, sanity_df = run_llm_ground_truth_judge(
        rows=rows,
        ground_truth_by_id=ground_truth_by_id,
        output_dir=args.output,
        model_name=args.model,
        api_key=api_key,
        data_path=args.data,
        dataset=args.dataset,
        max_answer_chars=args.max_answer_chars,
        max_code_chars=args.max_code_chars,
    )

    print(f"Judged {len(judgments_df)} rows from {len(files)} file(s)")
    if not summary_df.empty:
        print("\nLLM Judge Summary:")
        print(summary_df.to_string(index=False))

    if not sanity_df.empty:
        flagged = sanity_df[sanity_df["sanity_flag"] != "ok"]
        print(
            f"\nGround-truth sanity: {len(flagged)} flagged / {len(sanity_df)} total "
            f"(see {os.path.join(args.output, 'ground_truth_sanity.csv')})"
        )


if __name__ == "__main__":
    main()
