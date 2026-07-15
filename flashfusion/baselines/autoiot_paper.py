"""
baselines/autoiot_paper.py — AutoIOT paper-faithful baseline.

Implements a lightweight paper-aligned loop:
1) infer search terms from the query
2) retrieve background context from Tavily
3) draft and refine a solution design via LLM
4) run iterative execution rounds with feedback
5) select the best-performing round
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

try:
    from openrouter.errors.responsevalidationerror import ResponseValidationError as _ORResponseValidationError
except ImportError:
    _ORResponseValidationError = None  # type: ignore[assignment,misc]

from flashfusion.config import (
    AUTOIOT_PAPER_HTTP_TIMEOUT_S,
    AUTOIOT_PAPER_ITERATIONS,
    AUTOIOT_PAPER_MAX_TERMS,
    AUTOIOT_PAPER_MAX_URLS_PER_TERM,
    AUTOIOT_PAPER_REQUIRE_TAVILY,
)
from flashfusion.pipeline.executor import ExecutionLayer
from flashfusion.pipeline.runner import LLMClient, RunResult


_TERMS_PROMPT = """You extract concise search terms from an analytics query.
Return only a comma-separated list of terms that appear in or are implied by the query.
Do not include extra prose."""


_SEARCH_QUERIES_PROMPT = """You generate focused web-search queries for one concept.
Given the user task and a concept term, output 1-3 concise search queries.
Return only comma-separated queries, no prose."""


_HIGH_LEVEL_PROMPT = """You are designing a data-analysis approach.
Given the query and external context, output a short step-by-step high-level plan.
Keep to 4-7 steps and stay tied to the provided schema and user goal."""


_DETAIL_PROMPT = """Expand the high-level plan into implementation-focused detail.

Return exactly 2 or 3 numbered implementation modules:
1. data preparation and chronological split
2. model training and prediction
3. answer formatting and validation

For each module, include practical algorithm choices and validation checks.
Do not write runnable code yet."""


_MODULE_PROMPT = """Implement exactly one module as Python code.
You are given the full user task, schema summary, and one detailed step.
Output only a fenced python code block for this module."""


_INTEGRATE_PROMPT = """Integrate all module code snippets into one coherent executable Python script.
Requirements:
- keep complete function bodies
- define a clear entrypoint
- avoid placeholders
- ensure symbols are connected and runnable
Output only a fenced python code block."""


_CORRECT_PROMPT = """The code execution failed. Fix the code using the execution logs.
Return only a fenced python code block with corrected complete code."""


_IMPROVE_PROMPT = """You are improving an iterative data-analysis workflow.
Given the latest execution output and prior plan, provide concrete refinements for the next round.
Be specific and brief."""


_SELECT_PROMPT = """Choose the single best version number based on quality and query alignment.
Return only the version number as an integer."""


AUTOIOT_DEBUG = os.getenv("AUTOIOT_DEBUG", "").lower() in {"1", "true", "yes", "on"}
AUTOIOT_PROGRESS = os.getenv("AUTOIOT_PROGRESS", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
_INVOKE_RETRIES = 2


def _debug(msg: str) -> None:
    if AUTOIOT_DEBUG:
        print(f"[AUTOIOT_DEBUG] {msg}", file=sys.stderr, flush=True)


def _progress(message: str) -> None:
    """Print concise AutoIOT workflow progress to the benchmark terminal."""
    if AUTOIOT_PROGRESS:
        print(f"[AUTOIOT] {message}", file=sys.stderr, flush=True)


def _invoke(client: LLMClient, stage: str, system_prompt: str, user_input: str) -> str:
    chain = (
        ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{input}")]
        )
        | client.llm
        | StrOutputParser()
    )
    last_exc: Exception | None = None
    for attempt in range(_INVOKE_RETRIES + 1):
        t0 = time.time()
        _debug(
            f"LLM start stage={stage} attempt={attempt + 1}/{_INVOKE_RETRIES + 1} "
            f"input_chars={len(user_input)}"
        )
        try:
            output = client.invoke_chain(chain, {"input": user_input}, stage=stage)
            latency = time.time() - t0
            _debug(
                f"LLM done  stage={stage} attempt={attempt + 1}/{_INVOKE_RETRIES + 1} "
                f"latency={latency:.3f}s output_chars={len(output)}"
            )
            return output
        except Exception as exc:
            latency = time.time() - t0
            _debug(
                f"LLM error stage={stage} attempt={attempt + 1}/{_INVOKE_RETRIES + 1} "
                f"latency={latency:.3f}s error={type(exc).__name__}: {exc}"
            )
            is_validation_err = (
                _ORResponseValidationError is not None
                and isinstance(exc, _ORResponseValidationError)
            ) or "EOF while parsing" in str(exc)
            if is_validation_err and attempt < _INVOKE_RETRIES:
                last_exc = exc
                continue
            raise
    raise RuntimeError(f"_invoke failed after {_INVOKE_RETRIES} retries") from last_exc


def _clean_code_block(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def _extract_csv_list(raw: str, max_items: int) -> list[str]:
    chunks = re.split(r"[,;\n]", raw)
    items: list[str] = []
    for chunk in chunks:
        item = chunk.strip().strip("-*")
        if not item:
            continue
        if item and item not in items:
            items.append(item)
        if len(items) >= max_items:
            break
    return items


def _extract_terms(raw: str, max_terms: int) -> list[str]:
    terms = _extract_csv_list(raw, max_terms)
    cleaned: list[str] = []
    for term in terms:
        if term.lower().startswith("terms:"):
            term = term.split(":", 1)[1].strip()
        if term:
            cleaned.append(term)
    return cleaned


def _fallback_terms(query: str, max_terms: int) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", query.lower())
    stop = {
        "what",
        "which",
        "when",
        "where",
        "how",
        "is",
        "are",
        "the",
        "a",
        "an",
        "in",
        "on",
        "for",
        "to",
        "of",
        "and",
        "or",
    }
    uniq: list[str] = []
    for word in words:
        if len(word) <= 2 or word in stop:
            continue
        if word not in uniq:
            uniq.append(word)
        if len(uniq) >= max_terms:
            break
    return uniq


def _schema_hint(df) -> str:
    cols = ", ".join(str(c) for c in df.columns)
    sample = df.head(2).to_dict(orient="records")
    return f"Columns: {cols}\nSample rows: {sample}"


def _domain_context(df) -> str:
    cols = set(df.columns)
    if {"MLII", "V1", "record_id"}.issubset(cols):
        return (
            "ECG / cardiac electrophysiology signal analysis. "
            "Key topics: arrhythmia detection, heart rate estimation, QRS complex, "
            "R-peak annotation, MIT-BIH arrhythmia database, signal amplitude (mV)."
        )
    if {"latitude", "longitude", "accel_mean"}.issubset(cols):
        return (
            "Bus telematics, vehicle vibration analysis, road surface quality. "
            "Key topics: acceleration variance, road roughness index, GPS trajectory, "
            "percentile acceleration, transit telemetry, pothole detection, IoT vehicle monitoring."
        )
    return (
        "Human Activity Recognition (HAR) using inertial measurement unit (IMU) data. "
        "Key topics: accelerometer, wearable sensor, activity classification, WISDM dataset, "
        "acceleration magnitude, triaxial IMU, step detection."
    )


def _tavily_search(
    *,
    api_key: str,
    query: str,
    max_results: int,
    timeout_s: float,
) -> list[dict[str, Any]]:
    _debug(f"Tavily start query={query!r} max_results={max_results}")
    t0 = time.time()
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        _debug(f"Tavily error query={query!r} latency={time.time() - t0:.3f}s")
        return []
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        _debug(f"Tavily invalid JSON query={query!r} latency={time.time() - t0:.3f}s body_chars={len(body)}")
        return []
    results = parsed.get("results", [])
    results_list = results if isinstance(results, list) else []
    _debug(f"Tavily done  query={query!r} latency={time.time() - t0:.3f}s results={len(results_list)}")
    return results_list


def _generate_search_queries(
    client: LLMClient,
    query: str,
    terms: list[str],
) -> dict[str, list[str]]:
    by_term: dict[str, list[str]] = {}
    for term in terms:
        raw = _invoke(
            client.light,
            "autoiot_search_queries",
            _SEARCH_QUERIES_PROMPT,
            f"Task: {query}\n\nTerm: {term}",
        )
        generated = _extract_csv_list(raw, 3)
        if not generated:
            generated = [f"{term} {query}"]
        by_term[term] = generated
    return by_term


def _retrieve_context(
    search_queries: dict[str, list[str]],
    tavily_key: str,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    snippets: list[str] = []
    urls: list[str] = []
    provenance: list[dict[str, Any]] = []
    for term, queries in search_queries.items():
        for search_q in queries:
            results = _tavily_search(
                api_key=tavily_key,
                query=search_q,
                max_results=AUTOIOT_PAPER_MAX_URLS_PER_TERM,
                timeout_s=AUTOIOT_PAPER_HTTP_TIMEOUT_S,
            )
            hit_urls: list[str] = []
            for item in results:
                url = str(item.get("url", "")).strip()
                content = str(item.get("content", "")).strip()
                if url:
                    urls.append(url)
                    hit_urls.append(url)
                if content:
                    snippets.append(content[:600])
            provenance.append(
                {
                    "term": term,
                    "generated_query": search_q,
                    "urls": hit_urls,
                }
            )
    dedup_urls = list(dict.fromkeys(urls))
    context_text = "\n\n".join(snippets[:10]).strip()
    return context_text, dedup_urls, provenance


def _parse_detailed_steps(detailed: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", detailed) if b.strip()]
    if not blocks:
        return [detailed.strip()]
    return blocks


def _collect_execution_feedback(
    *,
    answer: str,
    trace: str,
    attempts: list[dict[str, Any]],
) -> dict[str, str]:
    if attempts:
        latest = attempts[-1]
        output = str(latest.get("output", "")).strip()
        ok = bool(latest.get("ok", False))
        return {
            "status": "success" if ok else "failure",
            "stdout": output if ok else "",
            "stderr": "" if ok else output,
        }

    observations = re.findall(r"Observation:\s*(.*)", trace)
    last_obs = observations[-1].strip() if observations else ""
    looks_error = "error" in last_obs.lower() or "exception" in last_obs.lower()
    if looks_error:
        return {"status": "failure", "stdout": "", "stderr": last_obs}

    return {
        "status": "success" if "[ERROR]" not in answer else "failure",
        "stdout": answer if "[ERROR]" not in answer else "",
        "stderr": answer if "[ERROR]" in answer else "",
    }


def _select_best_version(client: LLMClient, records: list[dict[str, Any]]) -> int:
    formatted = []
    for i, rec in enumerate(records, start=1):
        formatted.append(
            f"Version {i}:\n"
            f"Answer: {rec.get('answer', '')}\n"
            f"Tries: {rec.get('tries', 0)}"
        )
    choice_raw = _invoke(
        client.light,
        "autoiot_select",
        _SELECT_PROMPT,
        "\n\n".join(formatted),
    )
    m = re.search(r"(\d+)", choice_raw)
    if not m:
        return len(records)
    idx = int(m.group(1))
    if idx < 1 or idx > len(records):
        return len(records)
    return idx


def run_autoiot_paper(
    query: str,
    df,
    client: LLMClient,
    r: RunResult,
) -> RunResult:
    """Execute the AutoIOT paper baseline with retrieval-guided iterative refinement."""
    _debug(f"module={__file__}")
    _debug(f"run start query_chars={len(query)} df_shape={getattr(df, 'shape', None)}")
    started = time.time()
    stage_latency_s = {
        "s1": 0.0,
        "s2": 0.0,
        "s3": 0.0,
        "guardrail": 0.0,
        "agent": 0.0,
    }

    def record_stage(stage_key: str, operation: str, started_at: float) -> None:
        elapsed = max(0.0, time.perf_counter() - started_at)
        stage_latency_s[stage_key] += elapsed
        r.stage_latency_s = dict(stage_latency_s)
        r.stage_events.append(
            {
                "phase": {
                    "s1": "Grounding",
                    "s2": "Planning",
                    "s3": "Execution",
                    "guardrail": "Validation",
                    "agent": "Execution",
                }[stage_key],
                "canonical_key": stage_key,
                "operation": operation,
                "duration_s": elapsed,
            }
        )

    r.stage_latency_s = dict(stage_latency_s)
    _progress(
        f"START query={query[:80]!r} rows={len(df)} "
        f"columns={len(getattr(df, 'columns', []))}"
    )
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if AUTOIOT_PAPER_REQUIRE_TAVILY and not tavily_key:
        raise RuntimeError(
            "AUTOIOT_PAPER requires TAVILY_API_KEY. Export it before running this baseline."
        )

    schema = _schema_hint(df)
    domain = _domain_context(df)
    _progress("1/8 Extracting search terms")
    stage_started = time.time()
    timing_started = time.perf_counter()
    try:
        terms_raw = _invoke(
            client.light,
            "autoiot_terms",
            _TERMS_PROMPT,
            f"Query: {query}\n\nDomain: {domain}\n\nSchema:\n{schema}",
        )
    finally:
        record_stage("s1", "extract_terms", timing_started)
    terms = _extract_terms(terms_raw, AUTOIOT_PAPER_MAX_TERMS)
    if not terms:
        terms = _fallback_terms(query, AUTOIOT_PAPER_MAX_TERMS)
    r.stages_run.append("autoiot_terms")
    _debug(f"terms={terms}")
    _progress(f"    terms={terms!r} elapsed={time.time() - stage_started:.1f}s")

    _progress("2/8 Generating search queries")
    stage_started = time.time()
    timing_started = time.perf_counter()
    try:
        search_queries = _generate_search_queries(client, query, terms)
    finally:
        record_stage("s1", "generate_search_queries", timing_started)
    r.stages_run.append("autoiot_search_queries")
    _debug(f"search_queries={search_queries}")
    _progress(
        f"    queries={sum(len(v) for v in search_queries.values())} "
        f"elapsed={time.time() - stage_started:.1f}s"
    )

    _progress("3/8 Retrieving external context from Tavily")
    stage_started = time.time()
    timing_started = time.perf_counter()
    try:
        context_text, context_urls, retrieval_provenance = _retrieve_context(search_queries, tavily_key)
    finally:
        record_stage("s1", "retrieve_context", timing_started)
    r.stages_run.append("autoiot_retrieval")
    _debug(f"retrieval context_chars={len(context_text)} urls={len(context_urls)} provenance_items={len(retrieval_provenance)}")
    _progress(
        f"    urls={len(context_urls)} context_chars={len(context_text)} "
        f"elapsed={time.time() - stage_started:.1f}s"
    )

    _progress("4/8 Creating high-level plan")
    stage_started = time.time()
    timing_started = time.perf_counter()
    try:
        high_level = _invoke(
            client,
            "autoiot_design_high",
            _HIGH_LEVEL_PROMPT,
            f"Query: {query}\n\nDomain: {domain}\n\nSchema:\n{schema}\n\nContext:\n{context_text}",
        )
    finally:
        record_stage("s2", "design_high_level", timing_started)
    _progress(f"    elapsed={time.time() - stage_started:.1f}s")

    _progress("5/8 Creating detailed implementation plan")
    stage_started = time.time()
    timing_started = time.perf_counter()
    try:
        detailed = _invoke(
            client,
            "autoiot_design_detail",
            _DETAIL_PROMPT,
            f"Query: {query}\n\nHigh-level plan:\n{high_level}\n\nContext:\n{context_text}",
        )
    finally:
        record_stage("s2", "design_detail", timing_started)
    module_steps = _parse_detailed_steps(detailed)
    _debug(f"design high_chars={len(high_level)} detail_chars={len(detailed)} module_steps={len(module_steps)}")
    _progress(
        f"    modules={len(module_steps)} elapsed={time.time() - stage_started:.1f}s"
    )

    _progress(f"6/8 Generating {len(module_steps)} implementation module(s)")
    module_codes: list[str] = []
    for idx, step in enumerate(module_steps, start=1):
        module_started = time.time()
        _progress(f"    module {idx}/{len(module_steps)} started")
        _debug(f"module_gen start idx={idx} step_chars={len(step)}")
        timing_started = time.perf_counter()
        try:
            module_code_raw = _invoke(
                client,
                f"autoiot_module_gen_{idx}",
                _MODULE_PROMPT,
                f"Query: {query}\n\nSchema:\n{schema}\n\nDetailed step:\n{step}\n\nContext:\n{context_text}",
            )
        finally:
            record_stage("s3", f"generate_module_{idx}", timing_started)
        module_code = _clean_code_block(module_code_raw)
        module_codes.append(module_code)
        _debug(f"module_gen done  idx={idx} code_chars={len(module_code)}")
        _progress(
            f"    module {idx}/{len(module_steps)} completed "
            f"chars={len(module_code)} elapsed={time.time() - module_started:.1f}s"
        )
    r.stages_run.extend(["autoiot_design_high", "autoiot_design_detail", "autoiot_module_gen"])

    _progress("7/8 Integrating generated modules")
    stage_started = time.time()
    _debug(f"integration start modules={len(module_codes)} total_module_code_chars={sum(len(code) for code in module_codes)}")
    timing_started = time.perf_counter()
    try:
        integrated_raw = _invoke(
            client,
            "autoiot_code_integration",
            _INTEGRATE_PROMPT,
            (
                f"Query: {query}\n\nSchema:\n{schema}\n\n"
                f"Module code snippets:\n\n" + "\n\n".join(module_codes)
            ),
        )
    finally:
        record_stage("s3", "integrate_modules", timing_started)
    working_code = _clean_code_block(integrated_raw)
    r.stages_run.append("autoiot_code_integration")
    _debug(f"integration done working_code_chars={len(working_code)}")
    _progress(
        f"    integrated_code_chars={len(working_code)} "
        f"elapsed={time.time() - stage_started:.1f}s"
    )

    executor = ExecutionLayer(df, client)
    records: list[dict[str, Any]] = []
    iter_query = query
    latest_trace = ""

    _progress(f"8/8 Executing iterative workflow ({AUTOIOT_PAPER_ITERATIONS} round(s))")

    for i in range(1, AUTOIOT_PAPER_ITERATIONS + 1):
        round_started = time.time()
        augmented_query = (
            f"{iter_query}\n\n"
            f"Relevant context:\n{context_text[:2000]}\n\n"
            f"Current implementation sketch:\n{working_code[:2000]}"
        )
        _debug(f"agent_round start round={i}/{AUTOIOT_PAPER_ITERATIONS} augmented_query_chars={len(augmented_query)}")
        _progress(f"    execution round {i}/{AUTOIOT_PAPER_ITERATIONS} started")
        timing_started = time.perf_counter()
        try:
            answer, trace, details = executor.execute_single(augmented_query)
            final_code = getattr(details, "final_code", "") or ""
            tries = int(getattr(details, "tries", 0) or 0)
            attempts = list(getattr(details, "attempts", []) or [])
        except Exception as exc:
            _debug(f"agent_round exception round={i} error={type(exc).__name__}: {exc}")
            answer = f"Execution error in round {i}: {type(exc).__name__}: {exc}"
            trace = ""
            final_code = ""
            tries = 0
            attempts = []
        finally:
            record_stage("agent", f"execute_round_{i}", timing_started)

        timing_started = time.perf_counter()
        try:
            feedback = _collect_execution_feedback(answer=answer, trace=trace, attempts=attempts)
        finally:
            record_stage("guardrail", f"collect_feedback_round_{i}", timing_started)
        _debug(
            f"agent_round done  round={i} status={feedback['status']} tries={tries} "
            f"attempts={len(attempts)} answer_chars={len(answer)} trace_chars={len(trace)} final_code_chars={len(final_code)}"
        )
        _progress(
            f"    execution round {i}/{AUTOIOT_PAPER_ITERATIONS} "
            f"status={feedback['status']} tries={tries} attempts={len(attempts)} "
            f"elapsed={time.time() - round_started:.1f}s"
        )
        corrected_code = ""
        if feedback["status"] == "failure":
            correction_started = time.time()
            _progress(f"    correction for round {i} started")
            _debug(
                f"correction start round={i} stderr_chars={len(feedback['stderr'])} "
                f"stdout_chars={len(feedback['stdout'])} working_code_chars={len(working_code)}"
            )
            timing_started = time.perf_counter()
            try:
                corrected_raw = _invoke(
                    client.light,
                    f"autoiot_correct_{i}",
                    _CORRECT_PROMPT,
                    (
                        f"Query: {query}\n\nCurrent code:\n{working_code}\n\n"
                        f"Execution stderr:\n{feedback['stderr']}\n\n"
                        f"Execution stdout:\n{feedback['stdout']}"
                    ),
                )
            finally:
                record_stage("guardrail", f"correct_round_{i}", timing_started)
            corrected_code = _clean_code_block(corrected_raw)
            if corrected_code:
                working_code = corrected_code
            _debug(f"correction done  round={i} corrected_code_chars={len(corrected_code)}")
            _progress(
                f"    correction for round {i} completed "
                f"elapsed={time.time() - correction_started:.1f}s"
            )

        record = {
            "version": i,
            "query": augmented_query,
            "answer": answer,
            "trace": trace,
            "final_code": final_code,
            "tries": tries,
            "attempts": attempts,
            "context_urls": context_urls,
            "retrieval_provenance": retrieval_provenance,
            "search_queries": search_queries,
            "module_codes": module_codes,
            "execution_feedback": feedback,
            "corrected_code": corrected_code,
        }
        records.append(record)
        latest_trace = trace
        if final_code:
            working_code = final_code

        if i < AUTOIOT_PAPER_ITERATIONS:
            _progress(f"    generating refinement guidance after round {i}")
            _debug(
                f"improve start round={i} detail_chars={len(detailed)} stderr_chars={len(feedback['stderr'])} "
                f"stdout_chars={len(feedback['stdout'])} answer_chars={len(answer)}"
            )
            timing_started = time.perf_counter()
            try:
                improvement = _invoke(
                    client.light,
                    f"autoiot_improve_{i}",
                    _IMPROVE_PROMPT,
                    (
                        f"Query: {query}\n\nCurrent plan:\n{detailed}\n\n"
                        f"Execution status: {feedback['status']}\n"
                        f"Execution stderr:\n{feedback['stderr']}\n\n"
                        f"Execution stdout:\n{feedback['stdout']}\n\n"
                        f"Latest answer:\n{answer}"
                    ),
                )
            finally:
                record_stage("guardrail", f"refine_round_{i}", timing_started)
            iter_query = f"{query}\n\nRefinement guidance:\n{improvement}"
            _debug(f"improve done  round={i} improvement_chars={len(improvement)}")

    _debug(f"select start records={len(records)}")
    _progress("Selecting best execution version")
    timing_started = time.perf_counter()
    try:
        best_idx = _select_best_version(client, records)
    finally:
        record_stage("guardrail", "select_best_version", timing_started)
    best = records[best_idx - 1]
    _debug(f"select done  best_idx={best_idx}")

    r.answer = str(best.get("answer", ""))
    r.trace = str(best.get("trace", "") or latest_trace)
    r.executed = True
    r.rejected = False
    r.final_code = str(best.get("final_code", "") or working_code)
    r.agent_tries = int(sum(int(rec.get("tries", 0)) for rec in records))
    r.execution_attempts = records
    r.stages_run.extend(["autoiot_agent_loop", "autoiot_select"])
    _debug(
        f"run done executed={r.executed} rejected={r.rejected} latency={r.latency_s:.3f}s "
        f"input_tokens={r.input_tokens} output_tokens={r.output_tokens} cost={r.cost_usd:.6f}"
    )
    _progress(
        f"DONE selected_version={best_idx} total_elapsed={time.time() - started:.1f}s "
        f"answer_chars={len(r.answer)}"
    )
    return r
