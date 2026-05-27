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


_HIGH_LEVEL_PROMPT = """You are designing a data-analysis approach.
Given the query and external context, output a short step-by-step high-level plan.
Keep to 4-7 steps and stay tied to the provided schema and user goal."""


_DETAIL_PROMPT = """Expand the high-level plan into implementation-focused detail.
For each step, include practical algorithm choices and validation checks.
Do not write runnable code yet."""


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


def _extract_terms(raw: str, max_terms: int) -> list[str]:
    chunks = re.split(r"[,;\n]", raw)
    terms: list[str] = []
    for chunk in chunks:
        term = chunk.strip().strip("-*")
        if not term:
            continue
        if term.lower().startswith("terms:"):
            term = term.split(":", 1)[1].strip()
        if term and term not in terms:
            terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


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


def _retrieve_context(query: str, terms: list[str], tavily_key: str) -> tuple[str, list[str]]:
    snippets: list[str] = []
    urls: list[str] = []
    for term in terms:
        search_q = f"{term} for this analytics task: {query}"
        results = _tavily_search(
            api_key=tavily_key,
            query=search_q,
            max_results=AUTOIOT_PAPER_MAX_URLS_PER_TERM,
            timeout_s=AUTOIOT_PAPER_HTTP_TIMEOUT_S,
        )
        for item in results:
            url = str(item.get("url", "")).strip()
            content = str(item.get("content", "")).strip()
            if url:
                urls.append(url)
            if content:
                snippets.append(content[:400])
    dedup_urls = list(dict.fromkeys(urls))
    context_text = "\n\n".join(snippets[:10]).strip()
    return context_text, dedup_urls


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
    terms_raw = _invoke(
        client,
        "autoiot_terms",
        _TERMS_PROMPT,
        f"Query: {query}\n\nSchema:\n{schema}",
    )
    terms = _extract_terms(terms_raw, AUTOIOT_PAPER_MAX_TERMS)
    if not terms:
        terms = _fallback_terms(query, AUTOIOT_PAPER_MAX_TERMS)
    r.stages_run.append("autoiot_terms")

    context_text, context_urls = _retrieve_context(query, terms, tavily_key)
    r.stages_run.append("autoiot_retrieval")

    high_level = _invoke(
        client,
        "autoiot_design_high",
        _HIGH_LEVEL_PROMPT,
        f"Query: {query}\n\nSchema:\n{schema}\n\nContext:\n{context_text}",
    )
    detailed = _invoke(
        client,
        "autoiot_design_detail",
        _DETAIL_PROMPT,
        f"Query: {query}\n\nHigh-level plan:\n{high_level}\n\nContext:\n{context_text}",
    )
    draft_code = _invoke(
        client,
        "autoiot_code_draft",
        "Write a complete Python solution skeleton matching the detailed plan.",
        f"Query: {query}\n\nDetailed plan:\n{detailed}\n\nSchema:\n{schema}",
    )
    working_code = _clean_code_block(draft_code)
    r.stages_run.extend(["autoiot_design_high", "autoiot_design_detail", "autoiot_code_draft"])

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

        record = {
            "version": i,
            "query": augmented_query,
            "answer": answer,
            "trace": trace,
            "final_code": final_code,
            "tries": tries,
            "attempts": attempts,
            "context_urls": context_urls,
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
                f"Query: {query}\n\nCurrent plan:\n{detailed}\n\nLatest output:\n{answer}",
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
