"""
playground.py — Flash-Fusion skeleton
======================================
Unified codebase: build B4 (full Flash-Fusion) first, then ablate downward
to derive every baseline.  Zero divergent infrastructure.

Ablation ladder (each is a strict prefix-subtraction of B4):
  B0 — Raw Prompt          : raw CSV sample + question → LLM
  B1 — Schema-Aware        : column metadata + question → LLM
  B2 — + Concept Extraction: B1 + Stage 1 (DATA/REASONING split)
  B3 — + Schema Grounding  : B2 + Stage 2 (mappings) → single LLM call
  B4 — Full Flash-Fusion   : B3 + Stage 3 (sub-queries) + Tavily
  B4a — No Tavily           : B4 with Tavily disabled

Usage:
    export GROQ_API_KEY="..."
    export TAVILY_API_KEY="..."          # optional — enables Stage 1.5
    python src/playground/playground.py  # runs full eval matrix

──────────────────────────────────────────────────────────────────────────
TODOs — Critical issues surfaced by ECG B4 eval (2026-03-12)
──────────────────────────────────────────────────────────────────────────

TODO(P0-loop-1): Agent parse-loop — "Missing 'Action:' after 'Thought:'"
    Models like groq/compound produce essay-style answers instead of the
    Thought/Action/Action Input ReAct format.  handle_parsing_errors=True
    feeds back a format reminder, but the model repeats the same essay for
    all max_iterations.
    FIX: Detect repeated identical parsing failures inside _try_execute()
    (or via a custom OutputParser subclass).  After 2 consecutive identical
    parse errors, extract the textual answer from the malformed output and
    return it directly as the agent result, skipping further iterations.
    Also consider adding an output_parser that falls back to "treat entire
    LLM output as final answer" after N consecutive parse failures.

TODO(P0-loop-2): Agent parse-loop — "both a final answer and a parse-able action"
    llama-4-scout produces Thought + Action + Final Answer in a single turn.
    LangChain's ReActSingleInputOutputParser rejects this as ambiguous,
    logging OUTPUT_PARSING_FAILURE.  The model then repeats the same
    combined output every retry.
    FIX: Subclass ReActSingleInputOutputParser — when both an Action and a
    Final Answer are detected, prefer the Action (let the code run).  If
    the action has already been tried and produced the same parse error,
    fall back to extracting the Final Answer text.

TODO(P0-loop-3): Agent syntax-error loop — multi-line Action Input
    llama-3.3-70b appends a second "Thought:" block after the Action Input
    code.  The parser concatenates the code with the stray text, producing
    a SyntaxError on execution.  The model retries identically 6 times.
    FIX: In the custom output parser or in a pre-processing step on the
    parsed Action Input, strip everything after the first blank line or
    after a line starting with "Thought:".  This sanitizes the code block
    before it reaches python_repl_ast.

TODO(P1-codebook-context): Inject categorical codebook/legend metadata
    Some datasets use compact symbolic labels whose semantics are not
    recoverable from raw values alone.  Without a codebook, the model may
    misinterpret labels and produce incorrect conclusions.
    FIX: Add an optional metadata-enrichment hook that injects label
    definitions into schema context when available.  Keep this as a
    pluggable adapter so the core pipeline remains domain-agnostic.

TODO(P1-derived-features): Support derived features through adapters
    Some user questions target quantities that are not raw columns but are
    computable from existing columns via deterministic transformations.
    Without a derivation layer, the agent may answer with invalid proxies.
    FIX: Add an optional derived-feature adapter stage (post-load and
    pre-prompt) that can materialize canonical computed fields, expose them
    in metadata, and mark provenance of each derived field.

TODO(P2-s1-groq-compound): Stage 1 returns empty concepts for groq/compound
    groq/compound returns {"DATA": [], "REASONING": []} for 3 of 4 queries,
    causing S2 to bypass structured grounding and produce essays.
    FIX: Add a validation step after S1 parsing:  if both DATA and
    REASONING are empty for a non-trivial query (len(query) > 20), retry
    S1 once with a more explicit prompt, or force a minimal concept set
    derived from keyword extraction on the query text.

TODO(P2-s2-structured): Enforce structured output in Stage 2 grounding
    Models emit free-form essays instead of the MAPPINGS:/UNMAPPABLE:
    format.  Downstream parsing silently returns no mappings.
    FIX: Add post-parse validation: if no MAPPINGS lines are found in S2
    output, retry with a shorter, stricter prompt that includes a concrete
    example.  Consider using structured output / JSON mode if the model
    supports it.

TODO(P2-synthesis-lossy): Synthesizer drops sub-answer details
    In some runs, the agent trace contains richer quantitative detail than
    the final synthesis, which can collapse nuanced findings into a single
    headline metric.
    FIX: Pass the raw compact sub-answer text verbatim into the synthesis
    prompt (already done via _compact_answer_text, but the synthesizer
    ignores multi-line data).  Add an explicit instruction in
    SYNTHESIS_PROMPT: "Include ALL quantitative findings from sub-answers;
    do not omit or summarize away numeric results."

TODO(P3-outlier-context): Contextualize statistical outlier counts
    Threshold-based anomaly counts can be misleading if presented without a
    baseline prevalence expectation.  This can overstate severity.
    FIX: In the prefix prompt or grounding context, add a note: "When using
    threshold rules, report expected baseline prevalence and compare observed
    counts against that baseline so users can judge significance."

TODO(P3-retry-dedup): Avoid wasting iterations on identical failures
    All three looping failures repeat the exact same LLM output 6 times.
    The retry mechanism in execute_single() only retries once externally,
    but the inner agent loop (max_iterations=6) has no dedup.
    FIX: Track the last N LLM raw outputs in ThinkingCaptureHandler or a
    wrapper.  If two consecutive outputs are identical (or near-identical),
    break the loop early and synthesize from whatever partial result
    is available.
──────────────────────────────────────────────────────────────────────────
"""

import os
import sys
import time
import json
import re
import random
import argparse
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_classic.agents import create_react_agent, AgentExecutor
from langchain_classic.agents.agent import RunnableAgent
from langchain_classic.agents.output_parsers import ReActSingleInputOutputParser
from langchain_experimental.tools import PythonAstREPLTool
from langchain_experimental.agents.agent_toolkits.pandas.base import _get_prompt
from pydantic import PrivateAttr

try:
    from langchain_tavily import TavilySearch
    _TAVILY_AVAILABLE = True
except ImportError:
    _TAVILY_AVAILABLE = False

try:
    from scipy.io.arff import loadarff as _loadarff
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

try:
    import wfdb as _wfdb
    _WFDB_AVAILABLE = True
except ImportError:
    _WFDB_AVAILABLE = False

# Allow imports from src/scripts/ when running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Default model pool for round-robin load spreading during long eval runs.
DEFAULT_MODEL_POOL = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "groq/compound",
]

# Approximate per-1M-token rates in USD. Can be overridden with env vars:
# MODEL_RATE_INPUT_<MODEL_KEY>, MODEL_RATE_OUTPUT_<MODEL_KEY>
MODEL_RATE_PER_1M_TOKENS = {
    # Strong larger model, long context
    "llama-3.3-70b-versatile": {
        "input": 0.59,   # keep your existing ratio unless you have true prices
        "output": 0.79,
    },
    # Multimodal, long context; Groq caps output at 8,192 tokens per call
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "input": 0.11,   # as in your original, or adjust from billing
        "output": 0.34,
    },
    # Very large OpenAI OSS model, 131k context / 65k max output
    "groq/compound": {
        "input": 0.15,
        "output": 0.60,
    },
}

# ════════════════════════════════════════════════════════════
# 1. SHARED FOUNDATION — used by every baseline and B4
# ════════════════════════════════════════════════════════════

# ── 1a. data_loader ──────────────────────────────────────────

def _check_lfs_pointer(path: str) -> None:
    """Raise early if *path* is a Git LFS pointer instead of real data."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            first_line = fh.readline(256)
        if first_line.startswith("version https://git-lfs.github.com/spec/"):
            raise RuntimeError(
                f"File looks like a Git LFS pointer (not real data): {path}. "
                "Run 'git lfs pull' to download the actual file content."
            )
    except (OSError, UnicodeDecodeError):
        pass  # binary/missing — let downstream loaders handle it


def load_data(path: str) -> tuple[pd.DataFrame, str]:
    """Format-agnostic loader.  Returns (df, format_tag).
    Supports CSV, JSON-lines, Parquet, Weka ARFF, WISDM raw .txt,
    and MIT-BIH ECG records (.hea).  Nobody calls format-specific
    readers directly anywhere else in this file."""
    _check_lfs_pointer(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path), "csv"
    elif ext in (".json", ".jsonl"):
        return pd.read_json(path, lines=True), "jsonl"
    elif ext in (".parquet", ".pq"):
        return pd.read_parquet(path), "parquet"
    elif ext == ".arff":
        return _load_arff(path), "arff"
    elif ext == ".txt":
        return _load_wisdm_txt(path), "wisdm-raw"
    elif ext == ".hea":
        return _load_ecg_record(path), "ecg-wfdb"
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def export_ecg_record_to_csv(ecg_dir: str, record_id: str) -> str:
    """Decode an ECG record bundle and persist it as a CSV inside tmp_csv/."""
    if not record_id.isdigit():
        raise ValueError("ECG record id must be numeric, e.g. 100")

    base_path = os.path.join(ecg_dir, record_id)
    required_paths = [f"{base_path}.hea", f"{base_path}.atr"]
    missing = [p for p in required_paths if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            "Missing ECG companion files for record "
            f"{record_id}: {', '.join(os.path.basename(p) for p in missing)}"
        )

    df = _load_ecg_record(f"{base_path}.hea")
    tmp_dir = os.path.join(ecg_dir, "tmp_csv")
    os.makedirs(tmp_dir, exist_ok=True)
    csv_path = os.path.join(tmp_dir, f"{record_id}.csv")
    df.to_csv(csv_path, index=False)
    return csv_path


def list_ecg_records(ecg_dir: str) -> list[str]:
    """Return sorted list of valid numeric ECG record IDs in *ecg_dir*.

    A record is considered valid when it has both a .hea and a .atr companion
    file with a purely numeric stem (e.g. '100', '117').  Hyphenated variants
    like '102-0' are skipped because they share signal data with record '102'.
    """
    records = []
    for fname in os.listdir(ecg_dir):
        stem, ext = os.path.splitext(fname)
        if ext != ".atr" or not stem.isdigit():
            continue
        hea_path = os.path.join(ecg_dir, f"{stem}.hea")
        if os.path.exists(hea_path):
            records.append(stem)
    return sorted(records, key=int)


def _load_arff(path: str) -> pd.DataFrame:
    """Load a Weka ARFF file into a DataFrame."""
    if not _SCIPY_AVAILABLE:
        raise ImportError("scipy is required for ARFF files: pip install scipy")
    data, _ = _loadarff(path)
    df = pd.DataFrame(data)
    # scipy returns string columns as bytes — decode them
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].str.decode("utf-8")
    return df


def _load_wisdm_txt(path: str) -> pd.DataFrame:
    """Load a WISDM raw activity file (headerless CSV with trailing semicolons)."""
    import numpy as np
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.rstrip("; \t\n").strip()
            if not ln:
                continue
            fields = [f.strip() for f in ln.split(",")]
            if len(fields) < 6:
                fields += [np.nan] * (6 - len(fields))
            elif len(fields) > 6:
                fields = fields[:6]
            records.append(fields)
    df = pd.DataFrame(records, columns=["user", "activity", "timestamp", "x_accel", "y_accel", "z_accel"])
    return df


def _load_ecg_record(path: str) -> pd.DataFrame:
    """Load a single MIT-BIH ECG record (.hea path) into a flat DataFrame using wfdb."""
    if not _WFDB_AVAILABLE:
        raise ImportError("wfdb is required for ECG files: pip install wfdb")
    record_base = os.path.splitext(path)[0]  # strip .hea
    signal, fields = _wfdb.rdsamp(record_base)
    fs = fields["fs"]
    sig_names = fields["sig_name"]
    n_samples = signal.shape[0]
    df = pd.DataFrame(signal, columns=sig_names)
    df.insert(0, "sample_idx", range(n_samples))
    df.insert(1, "time_s", df["sample_idx"] / fs)
    df["record_id"] = os.path.basename(record_base)
    # Merge beat-level annotation symbols into the signal DataFrame
    try:
        ann = _wfdb.rdann(record_base, "atr")
        ann_map = dict(zip(ann.sample, ann.symbol))
        df["annotation"] = df["sample_idx"].map(ann_map)
    except Exception:
        df["annotation"] = None
    return df


# ── 1b. column_metadata ─────────────────────────────────────

def build_column_metadata(df: pd.DataFrame) -> dict:
    """Pre-compute column-level statistics for schema grounding."""
    meta = {}
    for col in df.columns:
        series = df[col].dropna()
        entry = {"dtype": str(df[col].dtype), "n_unique": series.nunique()}
        if pd.api.types.is_numeric_dtype(series):
            entry.update({
                "min": series.min(),
                "max": series.max(),
                "mean": round(series.mean(), 4),
                "std": round(series.std(), 4),
            })
        else:
            entry["sample_values"] = series.head(3).tolist()
        meta[col] = entry
    return meta


def validate_column_refs(mappings: list[str], df: pd.DataFrame) -> list[str]:
    """Check every column name mentioned in Stage 2 mappings against df.columns.
    Returns a list of invalid column references (empty if all valid)."""
    valid_cols = set(df.columns)
    invalid: list[str] = []

    # Non-column identifiers that may appear in mapping expressions.
    reserved_identifiers = {
        "and", "or", "not", "by", "per", "over", "for", "from", "to", "vs",
        "filter", "aggregate", "groupby", "correlate", "window", "rank",
        "mean", "avg", "median", "std", "var", "variance", "sum", "count",
        "min", "max", "abs", "diff", "delta", "prev", "next", "rolling",
        "where", "when", "then", "if", "else", "between", "within",
        "ascending", "descending", "asc", "desc",
        "percentile", "quantile", "zscore", "normalize", "normalized",
        "sqrt", "log", "exp", "pow",
        # Common function parameters / programming identifiers
        "window_size", "size", "n", "k", "threshold", "period",
        "lag", "step", "shift", "offset", "axis", "level", "limit",
        "inplace", "center", "closed", "on", "method", "kind",
        "fill_value", "na_position", "keep", "bins", "labels",
        "columns", "index", "values", "rows", "row", "col",
        "result", "output", "input", "data", "value", "key",
        "left", "right", "inner", "outer", "how", "suffix",
        "i", "e", "g", "note", "use", "the", "this", "that",
        "each", "all", "any", "no", "yes", "true", "false",
        "calculate", "compute", "apply", "transform", "resample",
    }
    derived_alias_suffixes = (
        "_intensity", "_magnitude", "_variability", "_score", "_index", "_rate",
    )
    token_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    for mapping in mappings:
        # mappings look like: "concept → col1, col2 + operation"
        if "→" not in mapping:
            continue
        rhs = mapping.split("→", 1)[1]

        # Pre-compute parenthesis depth map to skip function arguments.
        paren_depth = []
        depth = 0
        for ch in rhs:
            if ch == "(":
                depth += 1
            paren_depth.append(depth)
            if ch == ")":
                depth = max(0, depth - 1)

        for match in token_pattern.finditer(rhs):
            token = match.group(0)
            token_l = token.lower()

            if token in valid_cols:
                continue

            # Treat identifiers followed by '(' as functions/operators, not columns.
            next_char = rhs[match.end():match.end() + 1]
            if next_char == "(":
                continue

            # Skip tokens that appear inside function call parentheses.
            if match.start() < len(paren_depth) and paren_depth[match.start()] > 0:
                continue

            if token_l in reserved_identifiers:
                continue

            # Allow common derived aliases that are not physical columns.
            if token_l.endswith(derived_alias_suffixes):
                continue

            # Keep validation strict for likely column-like hallucinations.
            if "_" in token:
                invalid.append(token)

    # Preserve order while de-duplicating.
    return list(dict.fromkeys(invalid))


def meta_to_str(column_metadata: dict) -> str:
    return "\n".join(
        f"- '{col}': {info}" for col, info in column_metadata.items()
    )


def detect_dataset_type(path: str) -> str:
    """Infer dataset domain from path extension and name.
    Returns one of: 'bus', 'imu', 'ecg'."""
    ext = os.path.splitext(path)[1].lower()
    lower = path.lower()
    if ext == ".hea" or "ecg" in lower or "mitdb" in lower:
        return "ecg"
    if ext in (".arff", ".txt") or "wisdm" in lower or "/imu/" in lower:
        return "imu"
    return "bus"


def resolve_input_data_path(data_path: str, ecg_record: str | None = None) -> tuple[str, str | None]:
    """Resolve CLI input into an analyzable file path.

    Returns (resolved_data_path, source_format_hint).
    """
    if ecg_record is None:
        return data_path, None

    # If the user passes a specific .hea file, honor it directly.
    if os.path.isfile(data_path):
        ext = os.path.splitext(data_path)[1].lower()
        if ext in (".hea", ".csv"):
            return data_path, None

    if not os.path.isdir(data_path):
        raise ValueError(
            "When using --ecg-record, --data must be an ECG directory or a direct .hea/.csv file"
        )

    csv_path = export_ecg_record_to_csv(data_path, ecg_record)
    return csv_path, "ecg-csv"


# ── 1c. llm_client ──────────────────────────────────────────

@dataclass
class LLMCallLog:
    """Single invocation record."""
    model: str = ""
    stage: str = ""
    prompt_preview: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    token_source: str = "estimated"


def _estimate_tokens_from_text(text: str) -> int:
    """Rough fallback estimate when provider token usage is unavailable."""
    if not text:
        return 0
    return max(1, int(round(len(text) / 4)))


def _model_rate_key(model_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in model_name.upper())


def _rate_for_model(model_name: str) -> dict[str, float]:
    base = MODEL_RATE_PER_1M_TOKENS.get(model_name, {"input": 0.0, "output": 0.0}).copy()
    key = _model_rate_key(model_name)
    env_in = os.getenv(f"MODEL_RATE_INPUT_{key}")
    env_out = os.getenv(f"MODEL_RATE_OUTPUT_{key}")
    if env_in:
        try:
            base["input"] = float(env_in)
        except ValueError:
            pass
    if env_out:
        try:
            base["output"] = float(env_out)
        except ValueError:
            pass
    return base


def _compute_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    rates = _rate_for_model(model_name)
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"],
        6,
    )


class LLMClient:
    """Thin wrapper around ChatGroq that logs every invocation."""

    # you can alternate between models to avoid quota limits
    """    
    allam-2-7b
    canopylabs/orpheus-arabic-saudi
    canopylabs/orpheus-v1-english
    groq/compound
    groq/compound-mini
    llama-3.1-8b-instant
    llama-3.3-70b-versatile
    meta-llama/llama-4-scout-17b-16e-instruct
    meta-llama/llama-guard-4-12b
    meta-llama/llama-prompt-guard-2-22m
    meta-llama/llama-prompt-guard-2-86m
    moonshotai/kimi-k2-instruct
    moonshotai/kimi-k2-instruct-0905
    openai/gpt-oss-120b
    openai/gpt-oss-20b
    openai/gpt-oss-safeguard-20b
    """
    def __init__(self, model: str = "meta-llama/llama-4-scout-17b-16e-instruct"):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("Missing GROQ_API_KEY")
        self.model_name = model
        self.llm = ChatGroq(
            groq_api_key=api_key,
            model_name=model,
            temperature=0.0,
        )
        self.call_log: list[LLMCallLog] = []

    @staticmethod
    def _extract_usage_counts(payload: Any) -> tuple[int, int, str]:
        """Try common usage metadata formats; fallback handled by caller."""
        if payload is None:
            return 0, 0, "estimated"

        metadata = getattr(payload, "response_metadata", None) or {}
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
        if usage:
            in_tok = (
                usage.get("prompt_tokens")
                or usage.get("input_tokens")
                or usage.get("prompt_token_count")
                or 0
            )
            out_tok = (
                usage.get("completion_tokens")
                or usage.get("output_tokens")
                or usage.get("completion_token_count")
                or 0
            )
            return int(in_tok), int(out_tok), "provider"

        if isinstance(payload, dict):
            usage = payload.get("token_usage") or payload.get("usage") or {}
            if usage:
                return (
                    int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                    int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                    "provider",
                )

        return 0, 0, "estimated"

    @staticmethod
    def _inputs_to_text(inputs: dict) -> str:
        try:
            return json.dumps(inputs, default=str, ensure_ascii=True)
        except Exception:
            return str(inputs)

    def invoke_chain(self, chain, inputs: dict, stage: str = "") -> str:
        """Invoke a LangChain chain, log metadata, return stripped string."""
        t0 = time.time()
        payload = chain.invoke(inputs)
        if isinstance(payload, str):
            result = payload.strip()
        else:
            result = str(getattr(payload, "content", payload)).strip()

        in_tok, out_tok, src = self._extract_usage_counts(payload)
        if in_tok == 0 and out_tok == 0:
            in_tok = _estimate_tokens_from_text(self._inputs_to_text(inputs))
            out_tok = _estimate_tokens_from_text(result)

        latency = time.time() - t0
        cost_usd = _compute_cost_usd(self.model_name, in_tok, out_tok)
        self.call_log.append(LLMCallLog(
            model=self.model_name,
            stage=stage,
            prompt_preview=str(inputs)[:200],
            latency_s=round(latency, 3),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost_usd,
            token_source=src,
        ))
        return result

    def record_estimated_usage(self, stage: str, prompt_text: str, output_text: str):
        """Manual usage entry for flows that bypass invoke_chain (e.g., agent executor)."""
        in_tok = _estimate_tokens_from_text(prompt_text)
        out_tok = _estimate_tokens_from_text(output_text)
        self.call_log.append(LLMCallLog(
            model=self.model_name,
            stage=stage,
            prompt_preview=prompt_text[:200],
            latency_s=0.0,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=_compute_cost_usd(self.model_name, in_tok, out_tok),
            token_source="estimated",
        ))

    def total_latency(self) -> float:
        return sum(e.latency_s for e in self.call_log)

    def total_input_tokens(self) -> int:
        return sum(e.input_tokens for e in self.call_log)

    def total_output_tokens(self) -> int:
        return sum(e.output_tokens for e in self.call_log)

    def total_tokens(self) -> int:
        return self.total_input_tokens() + self.total_output_tokens()

    def total_cost_usd(self) -> float:
        return round(sum(e.cost_usd for e in self.call_log), 6)


# ── 1d. ThinkingCaptureHandler ───────────────────────────────

class ThinkingCaptureHandler(BaseCallbackHandler):
    """Collects the agent's Thought / Action / Observation steps."""

    def __init__(self):
        self.steps: list[str] = []
        self.action_inputs: list[str] = []
        self.last_successful_action_input: str = ""

    def on_agent_action(self, action: AgentAction, **kwargs):
        self.steps.append(f"Thought + Action: {action.log.strip()}")
        tool_input = str(action.tool_input).strip()
        if tool_input:
            self.action_inputs.append(tool_input)

    def on_tool_end(self, output: str, **kwargs):
        out = str(output).strip()
        self.steps.append(f"Observation: {out}")
        if not self._looks_like_tool_error(out) and self.action_inputs:
            self.last_successful_action_input = self.action_inputs[-1]

    def on_agent_finish(self, finish: AgentFinish, **kwargs):
        self.steps.append(f"Final Answer: {finish.return_values.get('output', '').strip()}")

    def get_trace(self) -> str:
        return "\n".join(self.steps) if self.steps else "(no steps captured)"

    def get_execution_details(self) -> tuple[str, int]:
        """Return (final_successful_code, tries) for this agent run."""
        final_code = self.last_successful_action_input
        if not final_code and self.action_inputs:
            final_code = self.action_inputs[-1]
        return final_code, len(self.action_inputs)

    @staticmethod
    def _looks_like_tool_error(output: str) -> bool:
        lowered = output.lower()
        return (
            "traceback" in lowered
            or "exception" in lowered
            or "syntaxerror" in lowered
            or "nameerror" in lowered
            or "keyerror" in lowered
            or "valueerror" in lowered
            or "indexerror" in lowered
        )


@dataclass
class ExecutionDetails:
    final_code: str = ""
    tries: int = 0


# ── 1e. ResilientReActOutputParser ───────────────────────────

class ResilientReActOutputParser(ReActSingleInputOutputParser):
    """Drop-in ReAct parser hardened against three common LLM failure modes.

    P0-loop-1: Essay-style output with no Action/Final Answer.
               After MAX_PARSE_FAILURES consecutive parse errors the raw text
               is returned as a Final Answer to break the loop.
    P0-loop-2: Output contains *both* an Action block and a Final Answer.
               The standard parser raises; this subclass prefers the Action
               (lets the code run) and falls back to Final Answer on error.
    P0-loop-3: Stray "Thought:" line appended after Action Input code block.
               Sanitises the Action Input before returning the AgentAction.
    P3-retry-dedup: Consecutive identical LLM outputs are detected and the
                    loop is broken early by synthesising a Final Answer.
    """

    _last_output: str = PrivateAttr(default="")
    _identical_count: int = PrivateAttr(default=0)
    _consecutive_parse_failures: int = PrivateAttr(default=0)
    MAX_IDENTICAL: int = 2
    MAX_PARSE_FAILURES: int = 2

    def parse(self, text: str) -> AgentAction | AgentFinish:
        cleaned = text.strip()

        # ── P3-retry-dedup ──────────────────────────────────
        if cleaned == self._last_output:
            self._identical_count += 1
        else:
            self._identical_count = 1
        self._last_output = cleaned

        if self._identical_count >= self.MAX_IDENTICAL:
            return self._extract_best_answer(cleaned)

        # ── P0-loop-2: both Action and Final Answer ─────────
        includes_answer = "Final Answer:" in text
        action_re = r"Action\s*\d*\s*:[\s]*(.*?)[\s]*Action\s*\d*\s*Input\s*\d*\s*:[\s]*(.*)"
        action_match = re.search(action_re, text, re.DOTALL)

        if action_match and includes_answer:
            # Prefer the Action — let the code execute.
            action = action_match.group(1).strip()
            action_input = self._sanitize_action_input(action_match.group(2))
            self._consecutive_parse_failures = 0
            return AgentAction(action, action_input.strip(' "'), text)

        # ── Standard path: Action without Final Answer ──────
        if action_match:
            action = action_match.group(1).strip()
            action_input = self._sanitize_action_input(action_match.group(2))
            self._consecutive_parse_failures = 0
            return AgentAction(action, action_input.strip(' "'), text)

        # ── Standard path: Final Answer only ────────────────
        if includes_answer:
            self._consecutive_parse_failures = 0
            return AgentFinish(
                {"output": text.split("Final Answer:")[-1].strip()}, text
            )

        # ── P0-loop-1: no recognisable format ──────────────
        self._consecutive_parse_failures += 1
        if self._consecutive_parse_failures >= self.MAX_PARSE_FAILURES:
            return self._extract_best_answer(cleaned)

        # First failure — raise normally so handle_parsing_errors can
        # feed format instructions back to the LLM.
        if not re.search(r"Action\s*\d*\s*:", text, re.DOTALL):
            raise OutputParserException(
                f"Could not parse LLM output: `{text}`",
                observation="Invalid Format: Missing 'Action:' after 'Thought:'",
                llm_output=text,
                send_to_llm=True,
            )
        raise OutputParserException(f"Could not parse LLM output: `{text}`")

    # ── helpers ────────────────────────────────────────────
    @staticmethod
    def _sanitize_action_input(raw_input: str) -> str:
        """P0-loop-3: strip stray Thought:/Action: lines appended after code."""
        lines = raw_input.split("\n")
        sanitized: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Thought:") or stripped.startswith("Action:"):
                break
            sanitized.append(line)
        return "\n".join(sanitized)

    @staticmethod
    def _extract_best_answer(text: str) -> AgentFinish:
        """Pull the most useful content from a malformed LLM response."""
        if "Final Answer:" in text:
            answer = text.split("Final Answer:")[-1].strip()
        else:
            answer = text.strip()
        return AgentFinish(return_values={"output": answer}, log=text)


# ── 1f. DatasetAdapter — pluggable domain-specific enrichment ─

class DatasetAdapter:
    """Protocol for optional dataset-specific metadata enrichment.

    Subclass to provide codebook labels (P1-codebook-context) and derived
    features (P1-derived-features) without modifying the core pipeline.
    The base implementation is a no-op so the pipeline stays domain-agnostic.
    """

    def get_codebook(self, df: pd.DataFrame) -> dict[str, dict[str, str]]:
        """Return ``{column: {raw_value: human_label}}`` for categorical columns.

        Example return (not embedded in core)::

            {"activity": {"1": "Walking", "2": "Running"}}
        """
        return {}

    def get_derived_features(
        self, df: pd.DataFrame
    ) -> tuple[pd.DataFrame, dict[str, str]]:
        """Materialise computed columns and return (enriched_df, provenance_map).

        *provenance_map* is ``{new_col_name: derivation_description}``.
        The enriched DataFrame may contain additional columns.
        """
        return df, {}


# ════════════════════════════════════════════════════════════
# 2. PROMPT TEMPLATES — one canonical copy, shared across all
#    baselines that use that stage
# ════════════════════════════════════════════════════════════

# Stage 1: Concept Extraction
CONCEPT_EXTRACTION_PROMPT = """
You are a concept extraction specialist for IoT sensor data queries.

Given a user's natural language query, identify every distinct semantic concept
and classify each as one of:
  DATA     — refers to a measurable quantity that should map directly to a dataset
             column (e.g., "acceleration", "location", "time", "heart rate")
  REASONING — a qualitative, interpretive, or standards-based idea that requires
             deriving a proxy from one or more columns (e.g., "bumpy", "dangerous",
             "ISO 2631 discomfort weighting", "ST depression")

Output format (strict):
DATA: <comma-separated data concepts, or NONE>
REASONING: <comma-separated reasoning concepts, or NONE>
"""



# Stage 2: Schema Grounding
SCHEMA_GROUNDING_PROMPT = """
You are a schema grounding specialist for IoT sensor data.

You receive:
* Extracted concepts (DATA and REASONING) from a user query.
* Available dataset columns and their metadata:
{column_metadata}
{enriched_definitions}

Your task:
1. For each DATA concept, find the best matching column(s).
2. For each REASONING concept, define a concrete proxy — which column(s) and what
   operation(s) approximate that concept.
   Where external definitions are provided above, use them to construct a
   more precise proxy.

If a DATA concept cannot map to any column, mark it UNMAPPABLE.

Output format (strict):
MAPPINGS:
  <concept> → <column(s) and operation>
  ...
UNMAPPABLE: <comma-separated unmappable concepts, or NONE>
"""

# Stage 3: Sub-query Generation
SUBQUERY_GENERATION_PROMPT = """
You are a query decomposition specialist for IoT sensor data analysis.

You receive:
* The user's original abstract query.
* Schema grounding mappings (concept → column/operation).
* Dataset column metadata:
{column_metadata}

Decompose the original query into 2-4 concrete, column-grounded sub-questions.
Each sub-question must:
  - Reference exact column names from the dataset.
  - Specify a single analytical operation: one of FILTER, AGGREGATE, GROUPBY,
    CORRELATE, WINDOW, or RANK.
    - Be independently answerable by a Pandas DataFrame agent.
    - Request compact summary outputs only (scalar values or short metric lists),
        never raw full Series/DataFrame dumps.

For WINDOW operations, always ask for summary metrics over windows, for example:
    - min/max/mean/std of the windowed metric
    - top 3 highest or lowest windows with their time ranges
    - trend direction over time

For FILTER/GROUPBY/RANK operations, ask for compact aggregates such as counts,
rates, percentages, top-k categories, or min/max examples with timestamps.

Avoid ambiguous instructions like "show", "print", or "list all rows".

Output format (strict):
SUB_Q1: [OPERATION] <concrete sub-question>
SUB_Q2: [OPERATION] <concrete sub-question>
[SUB_Q3: [OPERATION] <optional>]
[SUB_Q4: [OPERATION] <optional>]
SYNTHESIS_HINT: <one-line guidance on combining sub-answers>
"""

# Guardrail
GUARDRAIL_PROMPT = """
You are a schema gatekeeper for a tabular dataset queried by a Pandas agent.

Dataset columns (and dtypes/examples):
{schema}

Decision policy:
1. PROCEED if the query can be answered — even approximately — using
   statistics, aggregations, filtering, or windowed operations on the
   provided columns.
2. PROCEED if the query asks about patterns, variability, quality,
   summaries, or trends that can be approximated via basic column
   operations (e.g., value_counts, rolling std, annotation counts).
3. REJECT ONLY if the query requires data from entirely different
   sources or live external APIs that cannot be approximated from any column.

When in doubt, PROCEED — the Pandas agent will determine executability.

Output (single line):
- PROCEED
- REJECT: <short reason>
"""

# Synthesizer
SYNTHESIS_PROMPT = """
You are a data analyst assistant. The user asked an open-ended question about
sensor data. You have sub-answers to concrete analytical sub-questions.

Synthesize them into a single, coherent, natural-language response.
Prioritize evidence over style:
- Lead with the direct answer in the first sentence.
- Include ALL quantitative findings from sub-answers — do not omit or
  summarize away numeric results (counts, ranges, correlations, trends).
- If evidence is weak/incomplete, state that explicitly and explain why.
- Keep it concise (about 4-8 sentences) and avoid filler language.
- Do NOT mention sub-question IDs or implementation details.
- When reporting threshold-based counts or anomalies, note the expected
  baseline prevalence so users can judge significance.
"""

# ════════════════════════════════════════════════════════════
# 3. PIPELINE STAGES — composable building blocks
# ════════════════════════════════════════════════════════════

class Stage1_ConceptExtraction:
    """query → {"DATA": [...], "REASONING": [...]}"""

    def __init__(self, client: LLMClient):
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", CONCEPT_EXTRACTION_PROMPT),
                ("human", "Query: {query}"),
            ])
            | client.llm
            | StrOutputParser()
        )
        self._client = client

    def run(self, query: str) -> dict:
        raw = self._client.invoke_chain(self._chain, {"query": query}, stage="S1-concepts")
        concepts = self._parse(raw)

        # P2-s1: retry once when both concept lists are empty for a real query.
        if (
            not concepts["DATA"]
            and not concepts["REASONING"]
            and len(query.strip()) > 20
        ):
            retry_prompt = (
                "The previous attempt returned no concepts.  Please re-read the "
                "query carefully and extract at least one DATA or REASONING concept.\n\n"
                f"Query: {query}"
            )
            retry_chain = (
                ChatPromptTemplate.from_messages([
                    ("system", CONCEPT_EXTRACTION_PROMPT),
                    ("human", retry_prompt),
                ])
                | self._client.llm
                | StrOutputParser()
            )
            raw2 = self._client.invoke_chain(
                retry_chain, {"query": query}, stage="S1-concepts-retry"
            )
            retried = self._parse(raw2)
            if retried["DATA"] or retried["REASONING"]:
                return retried

            # Last resort: extract keywords from the query text.
            keywords = [w for w in re.findall(r'\b[a-zA-Z]{3,}\b', query)
                        if w.lower() not in {
                            "the", "and", "for", "are", "was", "were", "this",
                            "that", "how", "what", "when", "where", "which",
                            "does", "did", "any", "give", "there", "have",
                            "has", "been", "with", "from", "about",
                        }]
            if keywords:
                concepts["DATA"] = keywords[:3]

        return concepts

    @staticmethod
    def _parse(response: str) -> dict:
        concepts = {"DATA": [], "REASONING": []}
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("DATA:"):
                val = line.split("DATA:", 1)[1].strip()
                if val.upper() != "NONE":
                    concepts["DATA"] = [c.strip() for c in val.split(",")]
            elif line.startswith("REASONING:"):
                val = line.split("REASONING:", 1)[1].strip()
                if val.upper() != "NONE":
                    concepts["REASONING"] = [c.strip() for c in val.split(",")]
        return concepts


class Stage2_SchemaGrounding:
    """concepts + metadata + definitions → {"mappings": [...], "unmappable": [...]}"""

    def __init__(self, client: LLMClient):
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", SCHEMA_GROUNDING_PROMPT),
                ("human", "Concepts:\n{concepts}\n\nQuery context: {query}"),
            ])
            | client.llm
            | StrOutputParser()
        )
        self._client = client

    def run(
        self,
        concepts: dict,
        query: str,
        meta_str: str,
        enriched_defs: dict[str, str],
        df: pd.DataFrame,
    ) -> dict:
        defs_block = ""
        if enriched_defs:
            defs_block = (
                "\nExternal definitions:\n"
                + "\n".join(f"  {t}: {d}" for t, d in enriched_defs.items())
            )

        concept_summary = (
            f"DATA: {', '.join(concepts['DATA']) or 'NONE'}\n"
            f"REASONING: {', '.join(concepts['REASONING']) or 'NONE'}"
        )

        raw = self._client.invoke_chain(self._chain, {
            "concepts": concept_summary,
            "query": query,
            "column_metadata": meta_str,
            "enriched_definitions": defs_block,
        }, stage="S2-grounding")

        mappings, unmappable = self._parse(raw)

        # P2-s2: retry once if no structured MAPPINGS lines were found.
        if not mappings and (concepts["DATA"] or concepts["REASONING"]):
            retry_prompt = (
                "Your previous response did not contain the required MAPPINGS: "
                "section.  Respond using EXACTLY this format:\n\n"
                "MAPPINGS:\n"
                "  <concept> → <column(s) and operation>\n"
                "UNMAPPABLE: <concept list, or NONE>\n\n"
                f"Concepts:\n{concept_summary}\n\nQuery context: {query}"
            )
            retry_chain = (
                ChatPromptTemplate.from_messages([
                    ("system", SCHEMA_GROUNDING_PROMPT),
                    ("human", retry_prompt),
                ])
                | self._client.llm
                | StrOutputParser()
            )
            raw2 = self._client.invoke_chain(retry_chain, {
                "concepts": concept_summary,
                "query": query,
                "column_metadata": meta_str,
                "enriched_definitions": defs_block,
            }, stage="S2-grounding-retry")
            mappings2, unmappable2 = self._parse(raw2)
            if mappings2:
                mappings, unmappable, raw = mappings2, unmappable2, raw2

        # Post-parse validation: flag hallucinated column names
        invalid_refs = validate_column_refs(mappings, df)
        if invalid_refs:
            print(f"[Stage2] WARNING — invalid column refs: {invalid_refs}")
            # Mark each invalid ref as INVALID rather than passing it downstream
            for ref in invalid_refs:
                unmappable.append(f"INVALID:{ref}")

        return {
            "mappings": mappings,
            "unmappable": unmappable,
            "raw_grounding": raw,
        }

    @staticmethod
    def _parse(response: str) -> tuple[list[str], list[str]]:
        mappings, unmappable = [], []
        in_mappings = False
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("MAPPINGS:"):
                in_mappings = True
                continue
            elif line.startswith("UNMAPPABLE:"):
                in_mappings = False
                val = line.split("UNMAPPABLE:", 1)[1].strip()
                if val.upper() != "NONE":
                    unmappable = [c.strip() for c in val.split(",")]
            elif in_mappings and "→" in line:
                mappings.append(line.strip())
        return mappings, unmappable


class Stage3_SubqueryGeneration:
    """query + grounding + metadata → {"sub_queries": [...], "synthesis_hint": str}"""

    VALID_OPS = {"FILTER", "AGGREGATE", "GROUPBY", "CORRELATE", "WINDOW", "RANK"}

    def __init__(self, client: LLMClient):
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", SUBQUERY_GENERATION_PROMPT),
                ("human", "Original query: {query}\n\nGrounding:\n{grounding}"),
            ])
            | client.llm
            | StrOutputParser()
        )
        self._client = client

    def run(self, query: str, grounding_raw: str, meta_str: str) -> dict:
        raw = self._client.invoke_chain(self._chain, {
            "query": query,
            "grounding": grounding_raw,
            "column_metadata": meta_str,
        }, stage="S3-subqueries")

        sub_queries, synthesis_hint = self._parse(raw)
        return {"sub_queries": sub_queries, "synthesis_hint": synthesis_hint, "raw_subqueries": raw}

    @staticmethod
    def _parse(response: str) -> tuple[list[dict], str]:
        sub_queries = []
        synthesis_hint = ""
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("SUB_Q"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    text = parts[1].strip()
                    # Try to extract the operation tag [OPERATION]
                    op = "UNKNOWN"
                    if text.startswith("["):
                        close = text.find("]")
                        if close != -1:
                            op = text[1:close].strip().upper()
                            text = text[close + 1:].strip()
                    sub_queries.append({"operation": op, "question": text})
            elif line.startswith("SYNTHESIS_HINT:"):
                synthesis_hint = line.split("SYNTHESIS_HINT:", 1)[1].strip()
        return sub_queries, synthesis_hint


# ════════════════════════════════════════════════════════════
# 4. EXECUTION LAYER — Pandas agent + guardrail + synthesis
# ════════════════════════════════════════════════════════════

class ExecutionLayer:
    """Wraps the Pandas DataFrame agent, guardrail, and synthesizer."""

    def __init__(self, client: LLMClient, df: pd.DataFrame, source_path: str | None = None):
        self._client = client
        self._original_df = df.copy()
        self._df = df
        self._source_path = source_path or ""

        schema = self._build_schema_summary(df)

        # Pre-format the schema into the guardrail prompt
        self._guardrail_chain = (
            ChatPromptTemplate.from_messages([
                ("system", GUARDRAIL_PROMPT.replace("{schema}", schema)),
                ("human", "Query: {query}"),
            ])
            | client.llm
            | StrOutputParser()
        )

        self._synthesizer_chain = (
            ChatPromptTemplate.from_messages([
                ("system", SYNTHESIS_PROMPT),
                ("human",
                 "Original question: {question}\n\n"
                 "Sub-answers:\n{sub_answers}\n\n"
                 "Synthesis guidance: {synthesis_hint}"),
            ])
            | client.llm
            | StrOutputParser()
        )

        self._prefix_prompt = self._build_prefix(df, self._source_path)
        self._agent = self._create_agent(df)

    def _build_prefix(self, df: pd.DataFrame, source_path: str) -> str:
        col_list = ", ".join(df.columns)
        prefix = (
            f"You are a data analyst. The dataset has columns: {col_list}.\n"
            f"Total rows: {len(df)}.\n\n"
            f"Dataset source path (context only): {source_path or 'N/A'}\n"
            "IMPORTANT:\n"
            "- The DataFrame is already loaded in memory as variable `df`.\n"
            "- Never load files from disk (no pd.read_csv/read_parquet/read_json or path-based loading).\n"
            "- Never reference 'data.csv'. Work only with `df`.\n"
            "- NEVER modify `df` in-place. Do not use inplace=True, df.set_index(..., inplace=True), "
            "or any other in-place mutation. Always create new variables for transformations.\n\n"
        )

        col_set = set(c.lower() for c in df.columns)

        prefix += (
            "TOOL USAGE — you MUST follow this exact format:\n"
            "Thought: <your reasoning>\n"
            "Action: python_repl_ast\n"
            "Action Input: <valid pandas code as a single string>\n\n"
            "Example:\n"
            "Thought: I need to count the rows\n"
            "Action: python_repl_ast\n"
            "Action Input: print(len(df))\n\n"
            "WORKFLOW:\n"
            "1. Think about what calculation is needed\n"
            "2. Execute ONE python_repl_ast action\n"
            "3. Return Final Answer: <result>\n"
            "\n"
            "OUTPUT STYLE:\n"
            "- Return compact summaries only (scalars or short metric lists).\n"
            "- Do not print full DataFrames/Series unless explicitly asked.\n"
            "- For window questions, report summary stats (min/max/mean/std, top windows, trend).\n"
            "\n"
            "STATISTICAL REPORTING:\n"
            "- When using threshold rules to flag outliers or anomalies, report the\n"
            "  expected baseline prevalence and compare the observed count against\n"
            "  that baseline so users can judge significance.\n"
        )
        return prefix

    @staticmethod
    def _compact_answer_text(text: str, max_lines: int = 6) -> str:
        """Keep sub-answer payload compact before synthesis.

        This guards synthesis from huge raw dumps (full Series/DataFrames) and
        keeps only the most informative leading lines.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned

        lines = [ln.rstrip() for ln in cleaned.splitlines() if ln.strip()]
        if len(lines) <= max_lines:
            return "\n".join(lines)

        head = lines[:max_lines]
        omitted = len(lines) - max_lines
        head.append(f"... ({omitted} more lines omitted)")
        return "\n".join(head)

    def _create_agent(self, df: pd.DataFrame):
        # Build the agent manually so we can inject ResilientReActOutputParser
        # (create_pandas_dataframe_agent does not expose output_parser).
        tools = [PythonAstREPLTool(locals={"df": df})]
        prompt = _get_prompt(df, prefix=self._prefix_prompt)
        react_runnable = create_react_agent(
            self._client.llm,
            tools,
            prompt,
            output_parser=ResilientReActOutputParser(),
        )
        agent = RunnableAgent(
            runnable=react_runnable,
            input_keys_arg=["input"],
            return_keys_arg=["output"],
        )
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=False,
            max_iterations=6,
            early_stopping_method="generate",
            handle_parsing_errors=True,
        )

    def reset_agent(self):
        """Rebuild the agent with a fresh copy of the original DataFrame.

        This prevents in-place mutations from one sub-query corrupting
        the DataFrame for subsequent sub-queries.
        """
        fresh_df = self._original_df.copy()
        self._agent = self._create_agent(fresh_df)

    @staticmethod
    def _build_schema_summary(df: pd.DataFrame) -> str:
        lines = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else "N/A"
            lines.append(f"- '{col}' (dtype: {dtype}, e.g. {sample})")
        return "\n".join(lines)

    def guardrail(self, query: str) -> tuple[bool, str]:
        """Returns (proceed: bool, reason: str)."""
        decision = self._client.invoke_chain(
            self._guardrail_chain, {"query": query}, stage="guardrail"
        )
        verdict = decision.strip()
        if verdict.upper() == "PROCEED":
            return True, ""

        # Only treat explicit REJECT as blocking.
        # Non-standard replies like "safe" should not halt analysis.
        if verdict.upper().startswith("REJECT"):
            reason = verdict.split("REJECT:", 1)[1].strip() if "REJECT:" in verdict else verdict
            return False, reason

        return True, ""

    def execute_single(self, query: str) -> tuple[str, str, ExecutionDetails]:
        """Run a single query through the Pandas agent.
        Returns (raw_answer, trace).
        Retries once with explicit format hints if the first attempt fails."""
        raw_answer, trace, details = self._try_execute(query)

        # Detect agent parse failures: empty trace or error output.
        is_failure = (
            trace == "(no steps captured)"
            or "is not a valid tool" in trace
            or "Invalid Format" in trace
            or "[ERROR]" in raw_answer
        )

        if is_failure:
            # Retry with explicit format reminder prepended to the query.
            retry_query = (
                "IMPORTANT: You MUST respond using EXACTLY this format:\n"
                "Thought: <your reasoning>\n"
                "Action: python_repl_ast\n"
                "Action Input: <pandas code as plain text, not in backticks>\n\n"
                "Do NOT wrap code in ```python blocks. "
                "Write the code directly after 'Action Input: '.\n\n"
                f"Question: {query}"
            )
            self.reset_agent()
            raw_answer_retry, trace_retry, details_retry = self._try_execute(retry_query)
            # Use retry result if it's better.
            retry_failed = (
                trace_retry == "(no steps captured)"
                or "is not a valid tool" in trace_retry
                or "Invalid Format" in trace_retry
            )
            total_tries = details.tries + details_retry.tries
            if not retry_failed:
                return raw_answer_retry, trace_retry, ExecutionDetails(
                    final_code=details_retry.final_code,
                    tries=total_tries,
                )
            # Both attempts failed — return the original.
            return raw_answer, trace, ExecutionDetails(
                final_code=details.final_code,
                tries=total_tries,
            )

        return raw_answer, trace, details

    def _try_execute(self, query: str) -> tuple[str, str, ExecutionDetails]:
        """Single attempt to run a query through the Pandas agent."""
        handler = ThinkingCaptureHandler()
        try:
            result = self._agent.invoke(query, config={"callbacks": [handler]})
            output = result["output"]
            self._client.record_estimated_usage("agent-exec", query, output)
            final_code, tries = handler.get_execution_details()
            return output, handler.get_trace(), ExecutionDetails(final_code=final_code, tries=tries)
        except Exception as e:
            err = f"[ERROR] {e}"
            self._client.record_estimated_usage("agent-exec", query, err)
            final_code, tries = handler.get_execution_details()
            return err, handler.get_trace(), ExecutionDetails(final_code=final_code, tries=tries)

    def synthesize(self, question: str, sub_answers: str, synthesis_hint: str) -> str:
        return self._client.invoke_chain(
            self._synthesizer_chain,
            {"question": question, "sub_answers": sub_answers, "synthesis_hint": synthesis_hint},
            stage="synthesize",
        )


# ════════════════════════════════════════════════════════════
# 5. BASELINE RUNNER — single class, mode flag, zero divergence
# ════════════════════════════════════════════════════════════

BASELINE_MODES = ["B0", "B1", "B2", "B3", "B4", "B4a"]

@dataclass
class RunResult:
    """Uniform output from every baseline."""
    baseline: str = ""
    model: str = ""
    query: str = ""
    answer: str = ""
    trace: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    executed: bool = True       # did the Pandas agent succeed without error?
    stages_run: list[str] = field(default_factory=list)
    llm_calls: list[LLMCallLog] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    final_executed_code: str = ""
    agent_tries: int = 0


class BaselineRunner:
    """
    Build B4 completely, then short-circuit for each ablation.

    B0 — raw CSV sample + question → LLM (no agent, no stages)
    B1 — column metadata + question → LLM (no agent, no stages)
    B2 — B1 + Stage 1 concept extraction → LLM
    B3 — B2 + Stage 2 schema grounding → single LLM call (no sub-queries)
    B4 — B3 + Stage 3 sub-queries → agent per sub-query → synthesize (complex queries only)
    B4a — B4 alias (Tavily stage removed)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        mode: str = "B4",
        model: str = DEFAULT_MODEL_POOL[0],
        source_path: str | None = None,
        adapter: DatasetAdapter | None = None,
    ):
        if mode not in BASELINE_MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from {BASELINE_MODES}")
        self.mode = mode
        self.model = model
        self.source_path = source_path or ""
        self.adapter = adapter

        # P1-derived-features: materialise computed columns if an adapter
        # is provided, then expose their provenance in metadata.
        self._derived_provenance: dict[str, str] = {}
        if adapter:
            df, self._derived_provenance = adapter.get_derived_features(df)

        self.df = df
        self.client = LLMClient(model=model)
        self.col_meta = build_column_metadata(df)

        # P1-codebook-context: inject label definitions into the metadata
        # string so the LLM can interpret categorical values correctly.
        codebook = adapter.get_codebook(df) if adapter else {}
        self._codebook_block = ""
        if codebook:
            lines = []
            for col, labels in codebook.items():
                pairs = ", ".join(f"'{k}'={v}" for k, v in labels.items())
                lines.append(f"  {col}: {pairs}")
            self._codebook_block = "\nCategorical codebook:\n" + "\n".join(lines)

        provenance_block = ""
        if self._derived_provenance:
            provenance_block = "\nDerived columns:\n" + "\n".join(
                f"  {col}: {desc}" for col, desc in self._derived_provenance.items()
            )

        self._meta_str = meta_to_str(self.col_meta) + self._codebook_block + provenance_block

        # Build all stages — short-circuiting happens in run()
        self.stage1 = Stage1_ConceptExtraction(self.client)
        self.stage2 = Stage2_SchemaGrounding(self.client)
        self.stage3 = Stage3_SubqueryGeneration(self.client)
        self.executor = ExecutionLayer(self.client, df, source_path=self.source_path)

    @staticmethod
    def _is_complex_query(query: str, concepts: dict | None = None) -> bool:
        """Domain-agnostic gate for deciding whether S3 decomposition is needed.

        Complexity is inferred from query structure, not topic vocabulary:
        - Multi-intent conjunctions/comparisons
        - Temporal segmentation and ranking language
        - Explicit requests for multiple metrics
        - Number of extracted concepts from Stage 1
        """
        q = query.strip().lower()
        score = 0

        # Structural conjunctions usually imply multi-step reasoning.
        if re.search(r"\b(and|or|while|whereas|versus|vs|compared to|compare)\b", q):
            score += 1

        # Time/ranking/distribution language often needs decomposition.
        if re.search(r"\b(over time|during|trend|window|top\s*\d+|most|least|distribution)\b", q):
            score += 1

        # Multiple quantitative asks in one question (count, mean, std, etc.).
        metric_hits = re.findall(
            r"\b(count|mean|average|median|std|variance|min|max|range|correlation|rate|ratio|percent(?:age)?)\b",
            q,
        )
        if len(set(metric_hits)) >= 2:
            score += 1

        # Stage-1 concept breadth is a strong topic-agnostic proxy for complexity.
        if concepts:
            n_data = len(concepts.get("DATA", []))
            n_reason = len(concepts.get("REASONING", []))
            if (n_data + n_reason) >= 3 or (n_data >= 2 and n_reason >= 1):
                score += 1

        # Questions that ask for a single direct fact stay on the cheap path.
        if re.search(r"\b(how many|what is|which is|give me)\b", q) and score == 0:
            return False

        return score >= 2

    def run(self, query: str) -> RunResult:
        t0 = time.time()
        result = RunResult(baseline=self.mode, model=self.model, query=query)

        if self.mode == "B0":
            result = self._run_b0(query, result)
        elif self.mode == "B1":
            result = self._run_b1(query, result)
        elif self.mode == "B2":
            result = self._run_b2(query, result)
        elif self.mode == "B3":
            result = self._run_b3(query, result)
        elif self.mode in ("B4", "B4a"):
            result = self._run_b4(query, result)

        result.latency_s = round(time.time() - t0, 3)
        result.input_tokens = self.client.total_input_tokens()
        result.output_tokens = self.client.total_output_tokens()
        result.total_tokens = self.client.total_tokens()
        result.cost_usd = self.client.total_cost_usd()
        result.llm_calls = list(self.client.call_log)
        return result

    def _run_best_effort_fallback(
        self,
        query: str,
        r: RunResult,
        reason: str,
        grounding_raw: str = "",
    ) -> RunResult:
        """Generic fallback when strict gating rejects a query.

        Keeps behavior generalizable across datasets by avoiding domain-specific
        assumptions and asking for the closest answer from available columns.
        """
        r.stages_run.append("fallback-best-effort")

        fallback_query = (
            "The original query may not be directly answerable from the dataset columns. "
            "You should do your best with the given dataset columns. "
            "Provide the closest possible analysis to the original query, clearly label any assumptions/proxies, "
            "and avoid refusing unless execution itself fails.\n\n"
            "Use only the in-memory dataframe `df`; do not load files from disk.\n"
            f"Original question: {query}\n"
            f"Strict gate reason: {reason}\n"
        )
        if grounding_raw:
            fallback_query += f"\nGrounding context:\n{grounding_raw}\n"

        raw_answer, trace, exec_details = self.executor.execute_single(fallback_query)
        self._record_exec_details(r, exec_details)
        r.trace += f"\n[FALLBACK] Triggered: {reason}\n{trace}\n"

        # Keep the explanation brief and stable for user-facing responses.
        reason_clean = reason.replace("Guardrail reject:", "").replace("Unmappable concepts:", "").strip()
        if len(reason_clean) > 220:
            reason_clean = reason_clean[:217].rstrip() + "..."

        # Briefly summarize which columns/operations were used from grounding.
        op_keywords = [
            "filter", "aggregate", "groupby", "correlate", "window", "rank",
            "mean", "std", "sum", "count", "min", "max", "rolling", "diff",
        ]
        used_cols: list[str] = []
        used_ops: list[str] = []
        if grounding_raw:
            col_set = set(self.df.columns)
            for line in grounding_raw.splitlines():
                if "→" not in line:
                    continue
                rhs = line.split("→", 1)[1]
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rhs):
                    if token in col_set and token not in used_cols:
                        used_cols.append(token)
                    t = token.lower()
                    if t in op_keywords and t not in used_ops:
                        used_ops.append(t)

        cols_brief = ", ".join(used_cols[:4]) if used_cols else "available dataset columns"
        ops_brief = ", ".join(used_ops[:3]) if used_ops else "proxy calculations"

        direct_answer_note = (
            "I could not directly answer this from the dataset columns as-is "
            f"because... {reason_clean}. "
            f"I used columns ({cols_brief}) with operations ({ops_brief}) to provide the closest possible answer."
        )

        if "[ERROR]" not in raw_answer:
            r.executed = True
            compact = self.executor._compact_answer_text(raw_answer)
            best_effort_answer = self.executor.synthesize(
                query,
                f"best-effort result: {compact}",
                "Answer directly and include only key quantitative findings.",
            )
            r.answer = f"{direct_answer_note}\n\n{best_effort_answer}"
        else:
            r.executed = False
            r.answer = direct_answer_note

        return r

    # ── B0: raw prompt ───────────────────────────────────────

    def _run_b0(self, query: str, r: RunResult) -> RunResult:
        """Serialize truncated DataFrame + question → single LLM call."""
        r.stages_run.append("B0-raw")
        sample = self.df.head(50).to_csv(index=False)
        prompt = (
            f"Here is a sample of the dataset (first 50 rows):\n{sample}\n\n"
            f"Question: {query}\n\nAnswer concisely."
        )
        chain = (
            ChatPromptTemplate.from_messages([("human", "{prompt}")])
            | self.client.llm
            | StrOutputParser()
        )
        r.answer = self.client.invoke_chain(chain, {"prompt": prompt}, stage="B0-raw")
        return r

    # ── B1: schema-aware ─────────────────────────────────────

    def _run_b1(self, query: str, r: RunResult) -> RunResult:
        """Column metadata + question → LLM."""
        r.stages_run.append("B1-schema")
        prompt = (
            f"Dataset column metadata:\n{self._meta_str}\n\n"
            f"Question: {query}\n\nAnswer concisely using only the columns above."
        )
        chain = (
            ChatPromptTemplate.from_messages([("human", "{prompt}")])
            | self.client.llm
            | StrOutputParser()
        )
        r.answer = self.client.invoke_chain(chain, {"prompt": prompt}, stage="B1-schema")
        return r

    # ── B2: + concept extraction ─────────────────────────────

    def _run_b2(self, query: str, r: RunResult) -> RunResult:
        """B1 + Stage 1 concepts → LLM."""
        r.stages_run.append("S1-concepts")
        concepts = self.stage1.run(query)
        r.artifacts["S1-concepts"] = json.dumps(concepts, ensure_ascii=True)

        r.stages_run.append("B2-concepts+schema")
        prompt = (
            f"Dataset column metadata:\n{self._meta_str}\n\n"
            f"Extracted concepts from user query:\n"
            f"  DATA: {', '.join(concepts['DATA']) or 'NONE'}\n"
            f"  REASONING: {', '.join(concepts['REASONING']) or 'NONE'}\n\n"
            f"Question: {query}\n\nAnswer concisely."
        )
        chain = (
            ChatPromptTemplate.from_messages([("human", "{prompt}")])
            | self.client.llm
            | StrOutputParser()
        )
        r.answer = self.client.invoke_chain(chain, {"prompt": prompt}, stage="B2-answer")
        return r

    # ── B3: + schema grounding (no sub-queries) ──────────────

    def _run_b3(self, query: str, r: RunResult) -> RunResult:
        """B2 + Stage 2 grounding → single LLM call."""
        r.stages_run.append("S1-concepts")
        concepts = self.stage1.run(query)
        r.artifacts["S1-concepts"] = json.dumps(concepts, ensure_ascii=True)

        r.stages_run.append("S2-grounding")
        grounding = self.stage2.run(
            concepts, query, self._meta_str, {}, self.df
        )
        r.artifacts["S2-grounding"] = grounding["raw_grounding"]

        if grounding["unmappable"]:
            return self._run_best_effort_fallback(
                query,
                r,
                reason=f"Unmappable concepts: {grounding['unmappable']}",
                grounding_raw=grounding.get("raw_grounding", ""),
            )

        # guardrail
        proceed, reason = self.executor.guardrail(query)
        if not proceed:
            return self._run_best_effort_fallback(
                query,
                r,
                reason=f"Guardrail reject: {reason}",
                grounding_raw=grounding.get("raw_grounding", ""),
            )

        # Pass grounding context into agent as a single query
        r.stages_run.append("B3-grounded-exec")
        grounded_prompt = (
            f"Grounding context:\n{grounding['raw_grounding']}\n\n"
            f"Question: {query}"
        )
        raw_answer, trace, exec_details = self.executor.execute_single(grounded_prompt)
        self._record_exec_details(r, exec_details)
        r.trace = trace
        r.executed = "[ERROR]" not in raw_answer
        compact = self.executor._compact_answer_text(raw_answer)
        r.stages_run.append("synthesize")
        r.answer = self.executor.synthesize(
            query,
            f"single-pass result: {compact}",
            "Answer directly and include key numeric evidence.",
        )
        return r

    # ── B4 / B4a: full Flash-Fusion ──────────────────────────

    def _run_b4(self, query: str, r: RunResult) -> RunResult:
        """Hybrid pipeline: cheap single-pass for simple queries, S3 decomposition for complex ones."""

        # Stage 1
        r.stages_run.append("S1-concepts")
        concepts = self.stage1.run(query)
        r.artifacts["S1-concepts"] = json.dumps(concepts, ensure_ascii=True)

        enriched_defs: dict[str, str] = {}

        # Stage 2
        r.stages_run.append("S2-grounding")
        grounding = self.stage2.run(
            concepts, query, self._meta_str, enriched_defs, self.df
        )
        r.artifacts["S2-grounding"] = grounding["raw_grounding"]

        if grounding["unmappable"]:
            return self._run_best_effort_fallback(
                query,
                r,
                reason=f"Unmappable concepts: {grounding['unmappable']}",
                grounding_raw=grounding.get("raw_grounding", ""),
            )

        # Guardrail
        proceed, reason = self.executor.guardrail(query)
        if not proceed:
            return self._run_best_effort_fallback(
                query,
                r,
                reason=f"Guardrail reject: {reason}",
                grounding_raw=grounding.get("raw_grounding", ""),
            )

        # Skip S3 for non-complex prompts: execute once with grounding context.
        if not self._is_complex_query(query, concepts):
            r.stages_run.append("direct-exec")
            grounded_prompt = (
                f"Grounding context:\n{grounding['raw_grounding']}\n\n"
                f"Question: {query}\n"
                "Return a compact quantitative answer."
            )
            raw_answer, trace, exec_details = self.executor.execute_single(grounded_prompt)
            self._record_exec_details(r, exec_details)
            r.trace += trace
            r.executed = "[ERROR]" not in raw_answer
            compact = self.executor._compact_answer_text(raw_answer)
            r.stages_run.append("synthesize")
            r.answer = self.executor.synthesize(
                query,
                f"single-pass result: {compact}",
                "Answer directly with the strongest numeric evidence.",
            )
            return r

        # Stage 3
        r.stages_run.append("S3-subqueries")
        decomposition = self.stage3.run(query, grounding["raw_grounding"], self._meta_str)
        r.artifacts["S3-subqueries"] = decomposition.get("raw_subqueries", "")

        sub_queries = decomposition["sub_queries"]
        synthesis_hint = decomposition["synthesis_hint"]

        if not sub_queries:
            # Fallback: execute as a single query
            raw_answer, trace, exec_details = self.executor.execute_single(query)
            self._record_exec_details(r, exec_details)
            r.trace += trace
            r.executed = "[ERROR]" not in raw_answer
            compact = self.executor._compact_answer_text(raw_answer)
            r.stages_run.append("synthesize")
            r.answer = self.executor.synthesize(
                query,
                f"single-pass result: {compact}",
                "Answer directly with key numeric evidence.",
            )
            return r

        # Execute each sub-query
        sub_answers = []
        for i, sq in enumerate(sub_queries, 1):
            r.stages_run.append(f"exec-SQ{i}")
            # Reset agent with a fresh DataFrame copy to prevent in-place
            # mutations from one sub-query corrupting the next.
            self.executor.reset_agent()
            sq_query = (
                f"{sq['question']}\n"
                "Return only a compact summary with key numeric results. "
                "Do not print full tables or full series."
            )
            raw_answer, trace, exec_details = self.executor.execute_single(sq_query)
            self._record_exec_details(r, exec_details)
            r.trace += f"\n--- SQ{i} [{sq['operation']}] ---\n{trace}\n"
            compact = self.executor._compact_answer_text(raw_answer)
            sub_answers.append(f"SQ{i} [{sq['operation']}] {sq['question']}: {compact}")
            r.executed = r.executed and "[ERROR]" not in raw_answer

            if i < len(sub_queries):
                time.sleep(1.5)  # rate-limit

        # Synthesize
        r.stages_run.append("synthesize")
        r.answer = self.executor.synthesize(
            query,
            "\n".join(sub_answers),
            synthesis_hint or "Combine the sub-answers into a coherent response.",
        )
        return r

    @staticmethod
    def _record_exec_details(r: RunResult, details: ExecutionDetails) -> None:
        r.agent_tries += details.tries
        if details.final_code:
            r.final_executed_code = details.final_code


# ════════════════════════════════════════════════════════════
# 6. EVAL HARNESS — run_baseline() wrapper + scoring
# ════════════════════════════════════════════════════════════

@dataclass
class EvalRow:
    """One cell in the eval matrix."""
    dataset: str
    baseline: str
    model: str
    query_num: int
    query: str
    answer: str
    ground_truth: str
    latency_s: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    executed: bool
    stages: list[str]
    trace: str = ""
    llm_calls: list[LLMCallLog] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


def run_baseline(
    baseline_mode: str,
    query: str,
    df: pd.DataFrame,
    ground_truth: str = "",
    model: str = DEFAULT_MODEL_POOL[0],
    dataset: str = "unknown",
    query_num: int = 0,
    source_path: str | None = None,
    adapter: DatasetAdapter | None = None,
) -> EvalRow:
    """Uniform entry point: any baseline × any query → EvalRow."""
    runner = BaselineRunner(
        df, mode=baseline_mode, model=model,
        source_path=source_path, adapter=adapter,
    )
    result = runner.run(query)
    return EvalRow(
        dataset=dataset,
        baseline=baseline_mode,
        model=result.model,
        query_num=query_num,
        query=query,
        answer=result.answer,
        ground_truth=ground_truth,
        latency_s=result.latency_s,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
        cost_usd=result.cost_usd,
        executed=result.executed,
        stages=result.stages_run,
        trace=result.trace,
        llm_calls=result.llm_calls,
        artifacts=result.artifacts,
    )


def parse_model_pool(models_arg: str | None) -> list[str]:
    """Parse comma-separated model ids. Falls back to DEFAULT_MODEL_POOL."""
    if not models_arg:
        return DEFAULT_MODEL_POOL.copy()
    models = [m.strip() for m in models_arg.split(",") if m.strip()]
    return models or DEFAULT_MODEL_POOL.copy()


def run_eval_matrix(
    queries: list[tuple[str, str]],  # (query, ground_truth)
    df: pd.DataFrame,
    baselines: list[str] | None = None,
    model_pool: list[str] | None = None,
    dataset_type: str = "unknown",
    source_path: str | None = None,
    adapter: DatasetAdapter | None = None,
) -> list[EvalRow]:
    """Run every baseline against every query.  Returns flat list of EvalRows."""
    baselines = baselines or BASELINE_MODES
    model_pool = model_pool or DEFAULT_MODEL_POOL
    if not model_pool:
        raise ValueError("model_pool must contain at least one model")

    rows: list[EvalRow] = []
    rr_idx = 0

    for mode in baselines:
        print(f"\n{'═' * 60}")
        print(f"  BASELINE: {mode}")
        print(f"{'═' * 60}")
        for i, (q, gt) in enumerate(queries, 1):
            model = model_pool[rr_idx % len(model_pool)]
            rr_idx += 1
            print(f"  [{mode}] Q{i}: {q[:60]}...")
            print(f"    model={model}")
            row = run_baseline(
                mode,
                q,
                df,
                ground_truth=gt,
                model=model,
                dataset=dataset_type,
                query_num=i,
                source_path=source_path,
                adapter=adapter,
            )
            rows.append(row)
            print(
                "    → "
                f"latency={row.latency_s}s "
                f"tokens={row.total_tokens} "
                f"cost=${row.cost_usd:.6f} "
                f"executed={row.executed}"
            )
            time.sleep(2.0)  # rate-limit between queries

    return rows


def _log_eval_row_markdown(row: EvalRow):
    """Write one markdown file per row using dataset_baseline_querynum naming.
    Stored in a sub-folder named after the dataset type.
    """
    dataset = (row.dataset or "unknown").lower()
    baseline = (row.baseline or "unknown").lower()
    qnum = row.query_num if row.query_num > 0 else 0

    # P3-subfolder: Store .md files based on dataset type (imu, bus, ecg)
    dataset_sub_dir = os.path.join(OUTPUT_DIR, dataset)
    os.makedirs(dataset_sub_dir, exist_ok=True)

    per_row_path = os.path.join(dataset_sub_dir, f"{dataset}_{baseline}_{qnum:02d}.md")

    with open(per_row_path, "w", encoding="utf-8") as f:
        f.write(f"# Eval Result: {dataset}/{row.baseline}/Q{qnum:02d}\n\n")
        f.write(f"**Dataset:** {row.dataset}\n\n")
        f.write(f"**Baseline:** {row.baseline}\n\n")
        f.write(f"**Query #**: {qnum}\n\n")
        f.write(f"**Model:** {row.model}\n\n")
        f.write(f"**Query:** {row.query}\n\n")
        f.write(f"**Answer:** {row.answer}\n\n")
        if row.ground_truth:
            f.write(f"**Ground Truth:** {row.ground_truth}\n\n")
        f.write(
            f"**Latency:** {row.latency_s}s "
            f"| **Input Tokens:** {row.input_tokens} "
            f"| **Output Tokens:** {row.output_tokens} "
            f"| **Total Tokens:** {row.total_tokens} "
            f"| **Cost:** ${row.cost_usd:.6f} "
            f"| **Executed:** {row.executed}\n\n"
        )
        f.write(f"**Stages:** {' → '.join(row.stages)}\n\n")

        if row.trace:
            f.write("<details><summary>Agent trace</summary>\n\n")
            f.write(f"```\n{row.trace}\n```\n\n")
            f.write("</details>\n\n")

        if row.llm_calls:
            f.write("<details><summary>LLM call breakdown</summary>\n\n")
            f.write("| # | Stage | Model | Latency (s) | In Tok | Out Tok | Cost (USD) | Token Source |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for idx, call in enumerate(row.llm_calls, 1):
                stage = (call.stage or "-").replace("|", "\\|")
                model = (call.model or "-").replace("|", "\\|")
                f.write(
                    f"| {idx} | {stage} | {model} | {call.latency_s:.3f} "
                    f"| {call.input_tokens} | {call.output_tokens} | {call.cost_usd:.6f} "
                    f"| {call.token_source} |\n"
                )
            f.write("\n</details>\n\n")

        if row.artifacts:
            f.write("<details><summary>Stage artifacts</summary>\n\n")
            for name, content in row.artifacts.items():
                safe_name = name.replace("|", "\\|")
                f.write(f"#### {safe_name}\n\n")
                f.write(f"```\n{content}\n```\n\n")
            f.write("</details>\n")


def log_eval_matrix(rows: list[EvalRow], path: str | None = None):
    """Write the eval matrix to a markdown file."""
    path = path or os.path.join(OUTPUT_DIR, "eval_matrix.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Flash-Fusion Eval Matrix [{timestamp}]\n\n")
        f.write("| Baseline | Model | Query | Executed | Latency (s) | Tokens | Cost (USD) | Stages |\n")
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            short_q = r.query[:50].replace("|", "\\|")
            stages = " → ".join(r.stages)
            f.write(
                f"| {r.baseline} | {r.model} | {short_q} | {r.executed} | {r.latency_s} "
                f"| {r.total_tokens} | {r.cost_usd:.6f} | {stages} |\n"
            )

        # Summary table by baseline
        f.write("\n## Summary by baseline\n\n")
        f.write("| Baseline | Avg Latency (s) | Avg Tokens | Avg Cost (USD) | Executability | Queries |\n")
        f.write("|---|---|---|---|---|---|\n")
        for mode in BASELINE_MODES:
            mode_rows = [r for r in rows if r.baseline == mode]
            if not mode_rows:
                continue
            avg_lat = sum(r.latency_s for r in mode_rows) / len(mode_rows)
            avg_tokens = sum(r.total_tokens for r in mode_rows) / len(mode_rows)
            avg_cost = sum(r.cost_usd for r in mode_rows) / len(mode_rows)
            exec_rate = sum(r.executed for r in mode_rows) / len(mode_rows)
            f.write(
                f"| {mode} | {avg_lat:.2f} | {avg_tokens:.0f} | {avg_cost:.6f} "
                f"| {exec_rate:.0%} | {len(mode_rows)} |\n"
            )

        # Per-query details
        f.write("\n## Detailed Results\n\n")
        for r in rows:
            f.write(f"### [{r.baseline}] {r.query}\n\n")
            f.write(f"**Model:** {r.model}\n\n")
            f.write(f"**Answer:** {r.answer}\n\n")
            if r.ground_truth:
                f.write(f"**Ground Truth:** {r.ground_truth}\n\n")
            f.write(
                f"**Latency:** {r.latency_s}s "
                f"| **Input Tokens:** {r.input_tokens} "
                f"| **Output Tokens:** {r.output_tokens} "
                f"| **Total Tokens:** {r.total_tokens} "
                f"| **Cost:** ${r.cost_usd:.6f} "
                f"| **Executed:** {r.executed}\n\n"
            )
            if r.trace:
                f.write(f"<details><summary>Agent trace</summary>\n\n```\n{r.trace}\n```\n</details>\n\n")

            if r.llm_calls:
                f.write("<details><summary>LLM call breakdown</summary>\n\n")
                f.write("| # | Stage | Model | Latency (s) | In Tok | Out Tok | Cost (USD) | Token Source |\n")
                f.write("|---|---|---|---|---|---|---|---|\n")
                for idx, call in enumerate(r.llm_calls, 1):
                    stage = (call.stage or "-").replace("|", "\\|")
                    model = (call.model or "-").replace("|", "\\|")
                    f.write(
                        f"| {idx} | {stage} | {model} | {call.latency_s:.3f} "
                        f"| {call.input_tokens} | {call.output_tokens} | {call.cost_usd:.6f} "
                        f"| {call.token_source} |\n"
                    )
                f.write("\n</details>\n\n")

            if r.artifacts:
                f.write("<details><summary>Stage artifacts</summary>\n\n")
                for name, content in r.artifacts.items():
                    safe_name = name.replace("|", "\\|")
                    f.write(f"#### {safe_name}\n\n")
                    f.write(f"```\n{content}\n```\n\n")
                f.write("</details>\n\n")
            f.write("---\n\n")

    for r in rows:
        _log_eval_row_markdown(r)

    print(f"\nEval matrix written → {path}")


# ════════════════════════════════════════════════════════════
# 7. MAIN — default: full eval matrix across all baselines
# ════════════════════════════════════════════════════════════

# ── Bus / ride-quality queries ────────────────────────────────────────────────
BUS_EVAL_QUERIES: list[tuple[str, str]] = [
    ("Where are the roughest stretches of road?",                          "qualitative-rough-location"),
    ("Were there any dangerous driving moments?",                          "qualitative-danger"),
    ("Which parts of the route need road maintenance?",                    "qualitative-maintenance"),
    ("Was it a bumpy ride?",                                               "qualitative-bumpy"),
    ("What's the overall vibe of this ride?",                              "qualitative-vibe"),
    ("When during the trip was it most uncomfortable?",                    "qualitative-timing"),
    ("Was this a particularly rough route compared to what's normal?",     "qualitative-comparison"),
    ("Was anything unusual about this trip?",                              "qualitative-anomaly"),
    ("Give me a quick summary of this trip.",                              "qualitative-summary"),
    ("Did the ride get worse over time?",                                  "qualitative-trend"),
    ("How comfortable was this trip for passengers?",                      "qualitative-comfort"),
    ("How's the driving quality on this route?",                           "qualitative-quality"),
    ("Did the bus hit any big bumps or potholes?",                         "qualitative-bumps"),
    ("Is the driver driving aggressively?",                                "qualitative-aggression"),
    ("What can you tell me about the acceleration patterns on this route?", "qualitative-accel-patterns"),
]

# ── IMU / activity-recognition queries ───────────────────────────────────────
IMU_EVAL_QUERIES: list[tuple[str, str]] = [
    ("What activities did this person perform?",                           "factual-activities"),
    ("How much time was spent jogging versus walking?",                    "factual-activity-split"),
    ("Which activity shows the highest acceleration intensity?",           "factual-intensity"),
    ("Were there any sudden or abrupt changes in movement?",               "qualitative-transitions"),
    ("How active was this person overall during the session?",             "qualitative-activity-level"),
    ("What is the dominant activity in this dataset?",                     "factual-dominant"),
    ("Are there any unusual movement patterns worth flagging?",            "qualitative-anomaly"),
    ("How does walking compare to jogging in terms of variability?",       "qualitative-comparison"),
    ("What can you tell me about the acceleration patterns for each activity?", "qualitative-accel-patterns"),
    ("Which users show the most energetic movement?",                      "qualitative-users"),
    ("Give me a summary of the activity distribution.",                    "qualitative-summary"),
    ("Were there any periods of unusually high movement intensity?",       "qualitative-peaks"),
]

# ── ECG / cardiac-signal queries ──────────────────────────────────────────────
ECG_EVAL_QUERIES: list[tuple[str, str]] = [
    ("Are there any irregular heartbeat patterns in this recording?",      "qualitative-arrhythmia"),
    ("What is the average heart rate across this recording?",              "factual-heart-rate"),
    ("Were there any periods of abnormal cardiac activity?",               "qualitative-abnormal"),
    ("How does the cardiac signal vary over time?",                        "qualitative-hr-variability"),
    ("Were there any significant annotation events in this recording?",    "factual-annotations"),
    ("What is the overall quality of the ECG signal?",                     "qualitative-signal-quality"),
    ("Are there any concerning patterns in the cardiac data?",             "qualitative-concern"),
    ("When during the recording was cardiac activity most irregular?",     "qualitative-timing"),
    ("How many beats were annotated as abnormal?",                         "factual-abnormal-count"),
    ("Give me a summary of this ECG recording.",                           "qualitative-summary"),
    ("What beat types appear most frequently in this recording?",          "factual-beat-types"),
    ("Is there any evidence of ST-segment changes in the signal?",         "qualitative-st-changes"),
]

# ── ViSig / sports-related queries ──────────────────────────────────────────────
VISIG_EVAL_QUERIES: list[tuple[str, str]] = [
    ("", ""),
]


def get_eval_queries(dataset_type: str) -> list[tuple[str, str]]:
    """Return the evaluation query set for the detected dataset type."""
    if dataset_type == "ecg":
        return ECG_EVAL_QUERIES
    if dataset_type == "imu":
        return IMU_EVAL_QUERIES
    return BUS_EVAL_QUERIES


def _run_single_dataset_eval(
    dataset_type: str,
    data_arg: str | None,
    ecg_record: str,
    mode: str | None,
    query: str | None,
    query_num: int | None,
    all_baselines: bool,
    model_pool: list[str],
) -> list[EvalRow]:
    """Run eval for one dataset type and return rows."""
    # ── Determine whether ECG random-record mode is active ───────────────
    ecg_random = (dataset_type == "ecg" and ecg_record == "random")

    if dataset_type == "bus":
        input_path = data_arg or os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data.csv")
        data_path, resolved_hint = resolve_input_data_path(input_path, None)
    elif dataset_type == "imu":
        input_path = data_arg or os.path.join(BASE_DIR, "data", "AutoIOT_dataset", "IMU", "WISDM_ar_v1.1_raw.txt")
        data_path, resolved_hint = resolve_input_data_path(input_path, None)
    else:
        input_path = data_arg or os.path.join(BASE_DIR, "data", "AutoIOT_dataset", "ECG.0")
        if not ecg_random:
            data_path, resolved_hint = resolve_input_data_path(input_path, ecg_record)
        else:
            # Defer per-query loading; provide a placeholder for early prints.
            data_path, resolved_hint = input_path, None

    # ── For non-random ECG (and non-ECG datasets), load df once ──────────
    if not ecg_random:
        df, fmt = load_data(data_path)
        if resolved_hint == "ecg-csv":
            fmt = resolved_hint
            print(f"Decoded ECG record {ecg_record} into CSV: {data_path}")
        print(f"Loaded {fmt}: {data_path}  ({len(df):,} rows × {len(df.columns)} cols)")
        print(f"Dataset type detected: {dataset_type}")
    else:
        available_records = list_ecg_records(input_path)
        if not available_records:
            raise FileNotFoundError(f"No valid ECG records found in {input_path}")
        print(f"ECG random mode: {len(available_records)} records available → {available_records}")
        print(f"Dataset type detected: {dataset_type}")
        df = None  # loaded per-query below

    # ── Resolve a single query string from --query-num if given ──────────
    if query_num is not None:
        eval_queries_all = get_eval_queries(dataset_type)
        if not (1 <= query_num <= len(eval_queries_all)):
            raise ValueError(
                f"Query number {query_num} is out of range. "
                f"Dataset '{dataset_type}' has {len(eval_queries_all)} queries (1-{len(eval_queries_all)})"
            )
        query, _ = eval_queries_all[query_num - 1]
        print(f"Selected query #{query_num}: {query}")

    # ── Single-query branch ───────────────────────────────────────────────
    if query:
        run_mode = mode or "B4"
        model = model_pool[0]
        if ecg_random:
            chosen = random.choice(available_records)
            csv_path = export_ecg_record_to_csv(input_path, chosen)
            df, _ = load_data(csv_path)
            print(f"[random] Single query using ECG record {chosen}: {csv_path}")
            data_path = csv_path
        runner = BaselineRunner(df, mode=run_mode, model=model, source_path=data_path)
        result = runner.run(query)
        print(f"\n[{dataset_type.upper()}][{run_mode}] {result.query}")
        print(f"Model: {result.model}")
        print(f"Answer: {result.answer}")
        print(
            f"Latency: {result.latency_s}s "
            f"| Tokens: {result.total_tokens} "
            f"| Cost: ${result.cost_usd:.6f} "
            f"| Executed: {result.executed}"
        )
        print(f"Stages: {' → '.join(result.stages_run)}")
        return [
            EvalRow(
                dataset=dataset_type,
                baseline=result.baseline,
                model=result.model,
                query_num=query_num or 1,
                query=result.query,
                answer=result.answer,
                ground_truth="",
                latency_s=result.latency_s,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
                cost_usd=result.cost_usd,
                executed=result.executed,
                stages=result.stages_run,
                trace=result.trace,
                llm_calls=result.llm_calls,
                artifacts=result.artifacts,
            )
        ]

    # ── Multi-query branch ────────────────────────────────────────────────
    eval_queries = get_eval_queries(dataset_type)
    baselines_to_run = [mode] if mode else (BASELINE_MODES if all_baselines else ["B0", "B1", "B4"])

    if not ecg_random:
        rows = run_eval_matrix(
            eval_queries,
            df,
            baselines=baselines_to_run,
            model_pool=model_pool,
            dataset_type=dataset_type,
            source_path=data_path,
        )
    else:
        # Random mode: load a fresh, randomly chosen ECG record for every query.
        # Shuffle the record list upfront so queries across baselines stay varied
        # but are reproducibly spread (same shuffle seed per run unless you want
        # full independence — pass random.choice for that).
        shuffled = available_records.copy()
        random.shuffle(shuffled)
        rows: list[EvalRow] = []
        rr_idx = 0
        for bmode in baselines_to_run:
            print(f"\n{'═' * 60}")
            print(f"  BASELINE: {bmode}")
            print(f"{'═' * 60}")
            for i, (q, gt) in enumerate(eval_queries, 1):
                chosen = shuffled[i % len(shuffled)]  # deterministic spread
                csv_path = export_ecg_record_to_csv(input_path, chosen)
                q_df, _ = load_data(csv_path)
                model = model_pool[rr_idx % len(model_pool)]
                rr_idx += 1
                print(f"  [{bmode}] Q{i}: {q[:60]}...")
                print(f"    record={chosen}  model={model}")
                row = run_baseline(
                    bmode,
                    q,
                    q_df,
                    ground_truth=gt,
                    model=model,
                    dataset=dataset_type,
                    query_num=i,
                    source_path=csv_path,
                )
                rows.append(row)
                print(
                    f"    → latency={row.latency_s}s "
                    f"tokens={row.total_tokens} "
                    f"cost=${row.cost_usd:.6f} "
                    f"executed={row.executed}"
                )
                time.sleep(2.0)
    return rows


"""
USAGE:

python src/playground/playground.py --data data/AutoIOT_dataset/IMU/WISDM_ar_v1.1_raw.txt
python src/playground/playground.py --data data/AutoIOT_dataset/ECG.0/100.hea
python src/playground/playground.py --data data/bus/raw/bus_data.csv
"""

def main():
    parser = argparse.ArgumentParser(description="Flash-Fusion playground — build & ablate.")
    parser.add_argument(
        "--data", type=str, default=None,
        help="Path to dataset (CSV, JSON, Parquet, Weka ARFF, WISDM .txt, or ECG .hea).",
    )
    parser.add_argument(
        "--ecg-record", type=str, default="100",
        help=(
            "ECG record id to decode from an ECG directory, e.g. --ecg-record 100. "
            "Pass 'random' to assign a different randomly-shuffled record to each query."
        ),
    )
    parser.add_argument("--bus", action="store_true", help="Run using default bus dataset/query set.")
    parser.add_argument("--imu", action="store_true", help="Run using default IMU dataset/query set.")
    parser.add_argument("--ecg", action="store_true", help="Run using default ECG dataset/query set.")
    parser.add_argument("-all", "--all-datasets", dest="all_datasets", action="store_true", help="Run bus + IMU + ECG datasets in one invocation.")
    parser.add_argument("--mode", type=str, default=None,
                        choices=BASELINE_MODES, help="Run a single baseline only.")
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help=(
            "Comma-separated model ids used in round-robin across eval rows, "
            "e.g. 'meta-llama/llama-4-scout-17b-16e-instruct,llama-3.3-70b-versatile'"
        ),
    )
    parser.add_argument("--query", type=str, default=None, help="Single query to test.")
    parser.add_argument("--query-num", type=int, default=None, help="Query number from predefined list (1-based index). Overrides --query if specified.")
    parser.add_argument("--all-baselines", action="store_true", help="Run all baselines (B0..B4a) for selected dataset(s).")
    args = parser.parse_args()

    model_pool = parse_model_pool(args.models)

    selected_flags = [args.bus, args.imu, args.ecg]
    if sum(bool(x) for x in selected_flags) > 1 and not args.all_datasets:
        raise ValueError("Choose only one of --bus/--imu/--ecg, or use -all/--all-datasets")

    if args.all_datasets:
        datasets = ["bus", "imu", "ecg"]
    elif args.bus:
        datasets = ["bus"]
    elif args.imu:
        datasets = ["imu"]
    elif args.ecg:
        datasets = ["ecg"]
    else:
        # Backward-compatible fallback: infer from --data when explicit dataset flags are absent.
        inferred = detect_dataset_type(args.data or os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data.csv"))
        datasets = [inferred]

    all_rows: list[EvalRow] = []
    for ds in datasets:
        print(f"\n{'#' * 72}")
        print(f"DATASET RUN: {ds.upper()}")
        print(f"{'#' * 72}")
        rows = _run_single_dataset_eval(
            dataset_type=ds,
            data_arg=args.data,
            ecg_record=args.ecg_record,
            mode=args.mode,
            query=args.query,
            query_num=args.query_num,
            all_baselines=args.all_baselines,
            model_pool=model_pool,
        )
        all_rows.extend(rows)

        mode_tag = args.mode or "bAll"
        if args.query_num is not None:
            qnum_tag = f"q{args.query_num:02d}"
        elif args.query:
            qnum_tag = "q00"  # free-text query has no assigned number
        else:
            qnum_tag = "qAll"
        per_ds_path = os.path.join(OUTPUT_DIR, f"eval_{ds}_{mode_tag}_{qnum_tag}.md")
        log_eval_matrix(rows, path=per_ds_path)

        if args.query or args.query_num is not None:
            # Single-query mode already prints details; no need for combined matrix.
            continue

    if len(datasets) > 1 and not (args.query or args.query_num is not None):
        combined_path = os.path.join(OUTPUT_DIR, "eval_matrix_all_datasets.md")
        log_eval_matrix(all_rows, path=combined_path)


if __name__ == "__main__":
    main()