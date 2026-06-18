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
import urllib.error
import urllib.request
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

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
For each step, include practical algorithm choices and validation checks.
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


def _invoke(client: LLMClient, stage: str, system_prompt: str, user_input: str) -> str:
    chain = (
        ChatPromptTemplate.from_messages(
            [("system", system_prompt), ("human", "{input}")]
        )
        | client.llm
        | StrOutputParser()
    )
    return client.invoke_chain(chain, {"input": user_input}, stage=stage)


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
        return []
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return []
    results = parsed.get("results", [])
    return results if isinstance(results, list) else []


def _generate_search_queries(
    client: LLMClient,
    query: str,
    terms: list[str],
) -> dict[str, list[str]]:
    by_term: dict[str, list[str]] = {}
    for term in terms:
        raw = _invoke(
            client,
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
        client,
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
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if AUTOIOT_PAPER_REQUIRE_TAVILY and not tavily_key:
        raise RuntimeError(
            "AUTOIOT_PAPER requires TAVILY_API_KEY. Export it before running this baseline."
        )

    schema = _schema_hint(df)
    domain = _domain_context(df)
    terms_raw = _invoke(
        client,
        "autoiot_terms",
        _TERMS_PROMPT,
        f"Query: {query}\n\nDomain: {domain}\n\nSchema:\n{schema}",
    )
    terms = _extract_terms(terms_raw, AUTOIOT_PAPER_MAX_TERMS)
    if not terms:
        terms = _fallback_terms(query, AUTOIOT_PAPER_MAX_TERMS)
    r.stages_run.append("autoiot_terms")

    search_queries = _generate_search_queries(client, query, terms)
    r.stages_run.append("autoiot_search_queries")

    context_text, context_urls, retrieval_provenance = _retrieve_context(search_queries, tavily_key)
    r.stages_run.append("autoiot_retrieval")

    high_level = _invoke(
        client,
        "autoiot_design_high",
        _HIGH_LEVEL_PROMPT,
        f"Query: {query}\n\nDomain: {domain}\n\nSchema:\n{schema}\n\nContext:\n{context_text}",
    )
    detailed = _invoke(
        client,
        "autoiot_design_detail",
        _DETAIL_PROMPT,
        f"Query: {query}\n\nHigh-level plan:\n{high_level}\n\nContext:\n{context_text}",
    )
    module_steps = _parse_detailed_steps(detailed)
    module_codes: list[str] = []
    for idx, step in enumerate(module_steps, start=1):
        module_code_raw = _invoke(
            client,
            f"autoiot_module_gen_{idx}",
            _MODULE_PROMPT,
            f"Query: {query}\n\nSchema:\n{schema}\n\nDetailed step:\n{step}\n\nContext:\n{context_text}",
        )
        module_codes.append(_clean_code_block(module_code_raw))
    r.stages_run.extend(["autoiot_design_high", "autoiot_design_detail", "autoiot_module_gen"])

    integrated_raw = _invoke(
        client,
        "autoiot_code_integration",
        _INTEGRATE_PROMPT,
        (
            f"Query: {query}\n\nSchema:\n{schema}\n\n"
            f"Module code snippets:\n\n" + "\n\n".join(module_codes)
        ),
    )
    working_code = _clean_code_block(integrated_raw)
    r.stages_run.append("autoiot_code_integration")

    executor = ExecutionLayer(df, client)
    records: list[dict[str, Any]] = []
    iter_query = query
    latest_trace = ""

    for i in range(1, AUTOIOT_PAPER_ITERATIONS + 1):
        augmented_query = (
            f"{iter_query}\n\n"
            f"Relevant context:\n{context_text[:2000]}\n\n"
            f"Current implementation sketch:\n{working_code[:2000]}"
        )
        try:
            answer, trace, details = executor.execute_single(augmented_query)
            final_code = getattr(details, "final_code", "") or ""
            tries = int(getattr(details, "tries", 0) or 0)
            attempts = list(getattr(details, "attempts", []) or [])
        except Exception as exc:
            answer = f"Execution error in round {i}: {type(exc).__name__}: {exc}"
            trace = ""
            final_code = ""
            tries = 0
            attempts = []

        feedback = _collect_execution_feedback(answer=answer, trace=trace, attempts=attempts)
        corrected_code = ""
        if feedback["status"] == "failure":
            corrected_raw = _invoke(
                client,
                f"autoiot_correct_{i}",
                _CORRECT_PROMPT,
                (
                    f"Query: {query}\n\nCurrent code:\n{working_code}\n\n"
                    f"Execution stderr:\n{feedback['stderr']}\n\n"
                    f"Execution stdout:\n{feedback['stdout']}"
                ),
            )
            corrected_code = _clean_code_block(corrected_raw)
            if corrected_code:
                working_code = corrected_code

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
            improvement = _invoke(
                client,
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
            iter_query = f"{query}\n\nRefinement guidance:\n{improvement}"

    best_idx = _select_best_version(client, records)
    best = records[best_idx - 1]

    r.answer = str(best.get("answer", ""))
    r.trace = str(best.get("trace", "") or latest_trace)
    r.executed = True
    r.rejected = False
    r.final_code = str(best.get("final_code", "") or working_code)
    r.agent_tries = int(sum(int(rec.get("tries", 0)) for rec in records))
    r.execution_attempts = records
    r.stages_run.extend(["autoiot_agent_loop", "autoiot_select"])
    return r
