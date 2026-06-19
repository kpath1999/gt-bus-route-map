"""chat_server.py
=================
Chat interface for Flash-Fusion data exploration.

Serves a web chat UI and uses OpenRouter-backed LLM tool-calling to answer
data questions through the Flash-Fusion MCP tool layer.

Usage:
    python chat_server.py                   # default port 8000
    python chat_server.py --port 8080       # custom port
"""

from __future__ import annotations

import json
import inspect
import logging
import os
import sys
import uuid
from typing import Any

import pandas as pd

import click
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except ImportError:
    pass

# Import the MCP registry and tool functions directly
from fastmcp_run import (
    registry,
    discover_data_files,
    list_loaded_datasets,
    load_dataset,
    describe_dataset,
    get_codebook,
    get_derived_features,
    run_aggregate,
    run_groupby,
    run_window_metrics,
    run_value_counts,
    get_cluster_summary,
    lookup_external_definition,
    _apply_filters,
    _safe_serialize,
)

# Import B4 pipeline components from the playground for full-quality analysis
from playground import (
    BaselineRunner,
    load_data,
    DEFAULT_MODEL_POOL,
    BASE_DIR,
    LLMClient,
    Stage1_ConceptExtraction,
    Stage2_SchemaGrounding,
    build_column_metadata,
    meta_to_str,
)

LOGGER = logging.getLogger("flashfusion.chat")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    stream=sys.stderr,
)

app = FastAPI(title="Flash-Fusion Chat")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHAT_PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "chat_public")

# ── OpenRouter client ───────────────────────────────────────

def _get_openrouter_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY (or GROQ_API_KEY for transition) is not set")
    return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")


# ── Tool definitions for OpenRouter function-calling ─────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "discover_data_files",
            "description": "Discover candidate data files under a root path. Defaults to the repo data directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root_path": {"type": "string", "description": "Directory to scan. Defaults to repo data directory."},
                    "include_pattern": {"type": "string", "description": "Regex for file paths."},
                    "max_results": {"type": "integer", "description": "Max files to return."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_loaded_datasets",
            "description": "List datasets currently loaded in server memory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_dataset",
            "description": "Load a dataset file into memory. Supports CSV, JSON, Parquet, ARFF, WISDM txt, ECG .hea. Returns schema overview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_path": {"type": "string", "description": "Absolute path to the dataset file."},
                    "dataset_id": {"type": "string", "description": "Optional short ID for this dataset."},
                    "codebook_json": {"type": "string", "description": "Optional JSON mapping raw categorical values to labels."},
                    "derived_features_json": {"type": "string", "description": "Optional JSON specifying derived feature formulas."},
                },
                "required": ["data_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_dataset",
            "description": "Return schema and column-level metadata (dtype, nulls, unique count, sample values) for a loaded dataset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                },
                "required": ["dataset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_codebook",
            "description": "Return categorical legends / codebook for a loaded dataset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                },
                "required": ["dataset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_aggregate",
            "description": "Run a deterministic aggregate (count, nunique, mean, median, min, max, std, sum) on a single column, with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                    "column": {"type": "string", "description": "Column name."},
                    "operation": {"type": "string", "description": "One of: count, nunique, mean, median, min, max, std, sum."},
                    "filters_json": {"type": "string", "description": "Optional JSON array of filter objects [{column, op, value}]."},
                },
                "required": ["dataset_id", "column", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_groupby",
            "description": "Run a groupby aggregation on a dataset and return top-K groups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                    "group_by": {"type": "string", "description": "Column to group by."},
                    "target_column": {"type": "string", "description": "Column to aggregate."},
                    "agg": {"type": "string", "description": "Aggregation: count, nunique, mean, median, min, max, std, sum."},
                    "top_k": {"type": "integer", "description": "Number of top groups to return."},
                    "descending": {"type": "boolean", "description": "Sort descending (default true)."},
                    "filters_json": {"type": "string", "description": "Optional JSON array of filter objects."},
                },
                "required": ["dataset_id", "group_by", "target_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_window_metrics",
            "description": "Run rolling window metrics (mean, std, min, max, sum) on a numeric column.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                    "column": {"type": "string", "description": "Numeric column name."},
                    "window_size": {"type": "integer", "description": "Rolling window size (>= 2)."},
                    "metric": {"type": "string", "description": "One of: mean, std, min, max, sum."},
                    "top_k": {"type": "integer", "description": "Top windows to return."},
                    "highest": {"type": "boolean", "description": "Return highest (true) or lowest (false)."},
                },
                "required": ["dataset_id", "column", "window_size"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_value_counts",
            "description": "Return value-count summary for a column (category frequency).",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                    "column": {"type": "string", "description": "Column name."},
                    "top_k": {"type": "integer", "description": "Max values to return."},
                },
                "required": ["dataset_id", "column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cluster_summary",
            "description": "Return summaries for columns matching a cluster/segment pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID of the loaded dataset."},
                    "regex_pattern": {"type": "string", "description": "Regex to match cluster-like column names."},
                },
                "required": ["dataset_id"],
            },
        },
    },
]

# (RESOLVED) Zero-shot domain classification: _llm_classify_domain() below
# handles this — passes recent conversation history to the LLM so ambiguous
# follow-ups resolve correctly without sticky state.  Called as fallback when
# keyword detection returns nothing.
#
# (RESOLVED) Previous-answer context: the /api/chat endpoint passes the full
# conversation history (conversations[conv_id]) to both _llm_classify_domain()
# and _chat_completion(), so every turn has access to prior context.
# ── Domain detection & B4 pipeline integration ─────────────

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "ecg": [
        "ecg", "heart", "cardiac", "heartbeat", "rhythm", "arrhythmia",
        "beat", "annotation", "st segment", "wfdb", "recording", "pulse",
        "p-wave", "qrs", "t-wave", "r-peak", "mitdb", "bradycardia", "tachycardia",
    ],
    "imu": [
        "imu", "accelerometer", "activity", "walking", "jogging", "running",
        "wisdm", "gyroscope", "motion", "movement", "exercise", "accel",
        "downstairs", "upstairs", "sitting", "standing",
    ],
    "bus": [
        "bus", "ride", "pothole", "bump", "road", "passenger",
        "driving", "vehicle", "comfort", "maintenance", "bumpy", "trip",
        "driver", "route",
    ],
}

DEFAULT_DATA_PATHS: dict[str, str] = {
    "ecg": os.path.join(BASE_DIR, "data", "Agent_dataset", "ECG.0", "100.hea"),
    "imu": os.path.join(BASE_DIR, "data", "Agent_dataset", "IMU", "WISDM_ar_v1.1_raw.txt"),
    "bus": os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data.csv"),
}

_df_cache: dict[str, pd.DataFrame] = {}  # data_path → DataFrame (loaded once)


def _load_domain_df(domain: str, data_path: str | None = None) -> tuple[pd.DataFrame, str]:
    """Load (and cache) the DataFrame for a domain. Thread-safe for read-after-write."""
    resolved = data_path or DEFAULT_DATA_PATHS.get(domain)
    if resolved is None:
        raise ValueError(f"No data path known for domain '{domain}'")
    if resolved not in _df_cache:
        df, _ = load_data(resolved)
        _df_cache[resolved] = df
        LOGGER.info("Cached dataset '%s': %s (%d rows)", domain, resolved, len(df))
    return _df_cache[resolved], resolved


def detect_domain_from_query(query: str) -> str | None:
    """Keyword-based domain detection. Returns 'ecg', 'imu', 'bus', or None."""
    q = query.lower()
    scores = {d: sum(1 for kw in kws if kw in q) for d, kws in DOMAIN_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _llm_classify_domain(
    query: str,
    client: OpenAI,
    model: str,
    recent_history: list[dict[str, Any]] | None = None,
) -> str | None:
    """Zero-shot domain classification with optional conversation-history context.

    Passes the last few turns to the model so ambiguous follow-up messages
    (e.g., "What about the acceleration?") can be resolved without hard-coding
    sticky domain state.  Returns 'ecg', 'imu', 'bus', or None.
    """
    try:
        system = (
            "Classify the user query as exactly one of: ecg, imu, bus, unknown.\n"
            "- ecg: heart rate, cardiac signals, ECG, arrhythmia, beats, wfdb\n"
            "- imu: activities, accelerometer data, walking/jogging/running, WISDM\n"
            "- bus: bus rides, road quality, driving comfort, route/pothole analysis\n"
            "- unknown: general questions not clearly about a specific dataset\n"
            "If the current query is ambiguous, use the conversation history for context.\n"
            "Reply with exactly one word."
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        # Include up to the last 4 turns (capped to 500 chars each) for context
        if recent_history:
            for msg in recent_history[-4:]:
                if msg["role"] in ("user", "assistant"):
                    messages.append({"role": msg["role"], "content": str(msg.get("content", ""))[:500]})
        messages.append({"role": "user", "content": query})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=5,
            temperature=0,
        )
        result = resp.choices[0].message.content.strip().lower()
        return result if result in ("ecg", "imu", "bus") else None
    except Exception:
        return None


def _run_b4_pipeline(
    query: str,
    domain: str,
    model: str,
    data_path: str | None = None,
) -> str:
    """Run the full B4 Flash-Fusion pipeline (concept extraction → schema grounding
    → sub-query decomposition → Pandas agent → synthesis). Synchronous."""
    df, resolved_path = _load_domain_df(domain, data_path)
    runner = BaselineRunner(df, mode="B4", model=model, source_path=resolved_path)
    result = runner.run(query)
    LOGGER.info(
        "B4 pipeline: domain=%s stages=%s latency=%.2fs tokens=%d cost=$%.6f executed=%s",
        domain, result.stages_run, result.latency_s, result.total_tokens,
        result.cost_usd, result.executed,
    )
    return result.answer


# ── Mappability gate (Stage1 + Stage2) ────────────────────


def _check_mappability_via_stages(
    query: str,
    domain: str | None,
    model: str,
) -> tuple[str, str]:
    """Determine mappability by running Stage 1 concept extraction + Stage 2
    schema grounding against the detected domain's actual dataset schema.

    Returns (status, message) where status ∈ {'DIRECT', 'PROXY', 'UNMAPPABLE'}.
    Falls back to 'DIRECT' on any error so normal pipeline flow continues.

    Decision logic:
      UNMAPPABLE — Stage 2 marked at least one concept as UNMAPPABLE (and those
                   are genuine schema misses, not hallucinated column names).
      PROXY      — Stage 1 produced REASONING concepts *and* Stage 2 mapped them
                   to proxy operations (e.g., roughness → acceleration variance).
      DIRECT     — All concepts mapped directly; no proxy required.
    """
    if not domain:
        return "DIRECT", ""

    try:
        df, _ = _load_domain_df(domain)
    except Exception:
        LOGGER.warning(
            "Could not load domain '%s' for mappability check; defaulting to DIRECT",
            domain, exc_info=True,
        )
        return "DIRECT", ""

    try:
        llm_client = LLMClient(model=model)

        # Stage 1: split query concepts into DATA vs REASONING
        s1 = Stage1_ConceptExtraction(llm_client)
        concepts = s1.run(query)

        # Stage 2: ground every concept onto the actual dataset schema
        meta = build_column_metadata(df)
        meta_str = meta_to_str(meta)
        s2 = Stage2_SchemaGrounding(llm_client)
        grounding = s2.run(concepts, query, meta_str, {}, df)

        mappings: list[str] = grounding["mappings"]
        # Filter INVALID: entries — those are hallucinated column names caught by
        # validate_column_refs, not true unmappable concepts.
        unmappable: list[str] = [
            u for u in grounding["unmappable"] if not u.startswith("INVALID:")
        ]
        reasoning_concepts: list[str] = concepts.get("REASONING", [])

        LOGGER.info(
            "Mappability via stages: domain=%s DATA=%s REASONING=%s "
            "mappings=%d unmappable=%s",
            domain,
            concepts.get("DATA", []),
            reasoning_concepts,
            len(mappings),
            unmappable,
        )

        # No usable mappings at all → UNMAPPABLE
        if not mappings and unmappable:
            return (
                "UNMAPPABLE",
                f"I don't have data that can answer that question "
                f"(unmappable concepts: {', '.join(unmappable)}).",
            )

        # REASONING concepts got proxied → PROXY; surface the proxy description
        if reasoning_concepts and mappings:
            reasoning_lower = {r.lower() for r in reasoning_concepts}
            proxy_lines = [
                m for m in mappings
                if any(r in m.lower() for r in reasoning_lower)
            ]
            if proxy_lines:
                proxy_desc = "; ".join(
                    m.split("→", 1)[1].strip() if "→" in m else m
                    for m in proxy_lines[:2]
                )
                concept_str = ", ".join(reasoning_concepts[:2])
                return (
                    "PROXY",
                    f"I don't have direct data for '{concept_str}', "
                    f"but I can proxy it using {proxy_desc}. "
                    "Would you like me to proceed?",
                )

        return "DIRECT", ""

    except Exception:
        LOGGER.warning(
            "Stage-based mappability check failed; defaulting to DIRECT", exc_info=True
        )
        return "DIRECT", ""


# Map tool names to the actual Python callables
TOOL_DISPATCH: dict[str, Any] = {
    "discover_data_files": discover_data_files,
    "list_loaded_datasets": list_loaded_datasets,
    "load_dataset": load_dataset,
    "describe_dataset": describe_dataset,
    "get_codebook": get_codebook,
    "get_derived_features": get_derived_features,
    "run_aggregate": run_aggregate,
    "run_groupby": run_groupby,
    "run_window_metrics": run_window_metrics,
    "run_value_counts": run_value_counts,
    "get_cluster_summary": get_cluster_summary,
    "lookup_external_definition": lookup_external_definition,
}

SYSTEM_PROMPT = """\
You are Flash-Fusion, a data exploration assistant. You help users explore and \
analyze datasets that are available in the Flash-Fusion server.

Your capabilities:
- Discover data files in the repository
- Load datasets (CSV, JSON, Parquet, ARFF, ECG, WISDM formats)
- Describe dataset schemas with column-level metadata
- Run aggregations (count, mean, median, min, max, std, sum, nunique)
- Run groupby analyses with top-K results
- Compute rolling window metrics on numeric columns
- Get value counts / frequency distributions
- Get cluster summaries

Workflow:
1. If no dataset is loaded yet, first discover available files, then load one.
2. After loading, describe the dataset to understand its structure.
3. Use the appropriate analysis tools to answer user questions.

Guidelines:
- Always use the tools to get real data — never fabricate numbers.
- When presenting results, be clear about what the numbers mean.
- If a question is ambiguous, ask the user to clarify.
- Keep responses concise but informative.
- When showing data results, format them clearly.\
"""

# ── Conversation store (in-memory) ─────────────────────────

conversations: dict[str, list[dict[str, Any]]] = {}

# Per-conversation routing context: active domain and data path
conversation_context: dict[str, dict[str, Any]] = {}
# Structure: {conv_id: {"domain": str | None, "data_path": str | None}}

MAX_TOOL_ROUNDS = 8  # safety limit on tool-calling loops


def _execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call a Flash-Fusion tool and return serialized result."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    # Groq may emit `null` for tools with no parameters. Normalize that to an
    # empty object so zero-argument tool calls execute cleanly.
    if arguments is None:
        arguments = {}
    elif not isinstance(arguments, dict):
        return json.dumps(
            {"error": f"Tool arguments for '{name}' must be a JSON object or null."}
        )

    try:
        signature = inspect.signature(fn)
        required_params = [
            param
            for param in signature.parameters.values()
            if param.default is inspect._empty
            and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ]
        if required_params and not arguments:
            missing = ", ".join(param.name for param in required_params)
            return json.dumps({"error": f"Tool '{name}' requires arguments: {missing}"})

        # The MCP tools decorated with @mcp.tool are still plain callables.
        result = fn(**arguments)
        return json.dumps(_safe_serialize(result), default=str)
    except Exception as exc:
        LOGGER.exception("Tool %s failed", name)
        return json.dumps({"error": str(exc)})


def _chat_completion(
    client: OpenAI,
    messages: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    """Run the OpenRouter chat-completion loop with tool calls."""
    for _round in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=2048,
        )
        msg = response.choices[0].message

        # If the model produced tool calls, execute them and loop
        if msg.tool_calls:
            # Append the assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            tool_log = []
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                if fn_args is None:
                    fn_args = {}

                LOGGER.info("Tool call: %s(%s)", fn_name, json.dumps(fn_args)[:200])
                result_str = _execute_tool(fn_name, fn_args)
                LOGGER.info("Tool result (truncated): %s", result_str[:300])

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
                tool_log.append({"tool": fn_name, "args": fn_args})

            continue  # next round — let the model see the tool results

        # No tool calls — we have a final answer
        return {
            "content": msg.content or "",
            "tool_calls_made": _round > 0,
        }

    # Exhausted rounds
    return {
        "content": msg.content or "I ran out of tool-calling rounds. Please try a simpler question.",
        "tool_calls_made": True,
    }


# ── Request / Response models ──────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    model: str = "llama-3.3-70b-versatile"
    domain: str | None = None      # explicit domain override: 'ecg', 'imu', 'bus'
    data_path: str | None = None   # explicit data file path override
    confirmed: bool = False        # set True to proceed after a PROXY clarification


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    tool_calls_made: bool
    domain: str | None = None          # which domain was used (if any)
    needs_confirmation: bool = False   # True when a PROXY prompt is awaiting user consent
    confirmation_prompt: str | None = None  # the clarification text shown to the user


# ── API routes ──────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(CHAT_PUBLIC_DIR, "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")

    conv_id = req.conversation_id or str(uuid.uuid4())

    if conv_id not in conversations:
        conversations[conv_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conv_id not in conversation_context:
        conversation_context[conv_id] = {"domain": None, "data_path": None}

    conversations[conv_id].append({"role": "user", "content": req.message})

    try:
        client = _get_openrouter_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Domain routing ──────────────────────────────────────
    # Domain is detected fresh for every message — no sticky fallback.
    # Priority: explicit request field > keyword match > zero-shot LLM classifier
    # (using recent history as context so follow-up questions stay coherent).
    domain: str | None = req.domain
    data_path: str | None = req.data_path

    if not domain:
        domain = detect_domain_from_query(req.message)

    if not domain:
        # Zero-shot classification; pass recent turns so the model can resolve
        # ambiguous follow-ups (e.g. "what about the roughness?") correctly.
        domain = _llm_classify_domain(
            req.message,
            client,
            req.model,
            recent_history=conversations[conv_id][1:],  # skip system prompt
        )

    # ── Mappability gate ──────────────────────────────────────
    # Uses Stage 1 (DATA/REASONING split) + Stage 2 (schema grounding) from
    # playground.py to determine whether the query maps to the available data.
    # Skip if the user has already confirmed they want to proceed with a proxy.
    if not req.confirmed:
        map_status, map_message = _check_mappability_via_stages(
            req.message, domain, req.model
        )

        if map_status == "UNMAPPABLE":
            reply = map_message or "I don't have data that can answer that question."
            conversations[conv_id].append({"role": "assistant", "content": reply})
            return ChatResponse(
                conversation_id=conv_id,
                reply=reply,
                tool_calls_made=False,
                domain=domain,
            )

        if map_status == "PROXY":
            # Halt and surface a clarification prompt; caller must re-send with confirmed=True
            prompt = map_message or (
                "I don't have direct data for that query, but I can approximate it "
                "with a proxy metric. Would you like me to proceed?"
            )
            conversations[conv_id].append({"role": "assistant", "content": prompt})
            return ChatResponse(
                conversation_id=conv_id,
                reply=prompt,
                tool_calls_made=False,
                domain=domain,
                needs_confirmation=True,
                confirmation_prompt=prompt,
            )
        # DIRECT — fall through to pipeline

    # ── B4 pipeline for data-analysis queries ───────────────
    if domain:
        # Persist an explicitly-provided data_path but NOT the domain — each
        # message detects its own domain so users can switch freely.
        if data_path:
            conversation_context[conv_id]["data_path"] = data_path
        try:
            answer = _run_b4_pipeline(req.message, domain, req.model, data_path)
            conversations[conv_id].append({"role": "assistant", "content": answer})
            return ChatResponse(
                conversation_id=conv_id,
                reply=answer,
                tool_calls_made=True,
                domain=domain,
            )
        except Exception as exc:
            LOGGER.exception(
                "B4 pipeline failed for domain '%s'; falling back to tool loop", domain
            )
            # Fall through to the Groq tool-calling loop

    # ── Fallback: Groq function-calling loop ─────────────────
    try:
        result = _chat_completion(client, conversations[conv_id], req.model)
    except Exception as exc:
        LOGGER.exception("Chat completion failed")
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}")

    # Store assistant reply in conversation history
    conversations[conv_id].append({"role": "assistant", "content": result["content"]})

    return ChatResponse(
        conversation_id=conv_id,
        reply=result["content"],
        tool_calls_made=result["tool_calls_made"],
        domain=domain,
    )


@app.get("/api/conversations")
async def list_conversations():
    return {
        cid: len([m for m in msgs if m["role"] == "user"])
        for cid, msgs in conversations.items()
    }


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    conversations.pop(conversation_id, None)
    conversation_context.pop(conversation_id, None)
    return {"status": "ok"}


@app.get("/api/datasets")
async def api_list_datasets():
    return list_loaded_datasets()


@app.get("/api/domains")
async def api_list_domains():
    """Return available data domains, their default file paths, and cache status."""
    return {
        "domains": {
            domain: {
                "default_path": path,
                "exists": os.path.exists(path),
                "cached": path in _df_cache,
            }
            for domain, path in DEFAULT_DATA_PATHS.items()
        }
    }


@app.post("/api/conversations/{conversation_id}/domain")
async def set_conversation_domain(
    conversation_id: str,
    domain: str,
    data_path: str | None = None,
):
    """Explicitly pin a domain (and optional data path) to a conversation.
    Useful when the user wants to switch datasets mid-conversation.
    """
    if domain not in DEFAULT_DATA_PATHS and data_path is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown domain '{domain}'. Known domains: {list(DEFAULT_DATA_PATHS)}",
        )
    if conversation_id not in conversation_context:
        conversation_context[conversation_id] = {}
    conversation_context[conversation_id]["domain"] = domain
    conversation_context[conversation_id]["data_path"] = data_path
    return {"conversation_id": conversation_id, "domain": domain, "data_path": data_path}


# Serve static files from chat_public/
app.mount("/static", StaticFiles(directory=CHAT_PUBLIC_DIR), name="static")


# ── CLI entry point ─────────────────────────────────────────

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8000, show_default=True, help="Port number.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def main(host: str, port: int, reload: bool):
    """Start the Flash-Fusion chat server."""
    LOGGER.info("Starting Flash-Fusion chat server on %s:%d", host, port)
    uvicorn.run(
        "chat_server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
