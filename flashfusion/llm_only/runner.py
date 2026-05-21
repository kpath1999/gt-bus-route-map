"""
flashfusion.llm_only.runner — trial execution with real Groq-billed token capture.

The default LLMClient in flashfusion.pipeline.runner estimates tokens with
len(text.split()) * 1.3, which under-counts CSV-heavy prompts by 20–40%. For a
cost-and-scaling study that is fatal, so this module talks to ChatGroq directly
and reads `response_metadata['token_usage']` to get billed counts.

Single API call per LLM_ONLY trial. FF trials delegate to the existing
BaselineRunner so we reuse the published pipeline.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from flashfusion.config import MODEL_RATE_PER_1M_TOKENS


CONTEXT_TOKEN_BUDGET = 128_000          # llama-3.3-70b-versatile context window
RESPONSE_TOKEN_RESERVE = 4_096          # leave room for the model's reply
QUESTION_TOKEN_RESERVE = 512            # max question + system prompt headroom
PROMPT_TOKEN_BUDGET = (
    CONTEXT_TOKEN_BUDGET - RESPONSE_TOKEN_RESERVE - QUESTION_TOKEN_RESERVE
)

# Llama 3.3 averages ~3.6 chars/token on CSV-dense text. We start with a safe
# 3.0 chars/token estimate when pre-truncating, then verify with the real
# billed count after the call.
EST_CHARS_PER_TOKEN = 3.0


_SYSTEM_PROMPT = (
    "You are a data analyst. Answer the user's question using ONLY the dataset "
    "provided below. Be direct and concise. If the dataset is incomplete or "
    "truncated, say so explicitly. Do not invent values that are not in the data."
)


@dataclass
class TrialResult:
    """One LLM-Only trial: a single API call on one (dataset, n_rows, question, rep)."""

    baseline: str
    dataset: str
    n_rows: int
    question_id: int
    question: str
    rep: int

    # Real Groq-billed token counts
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Pre-call estimates (for divergence reporting)
    prompt_tokens_estimated: int = 0

    latency_s: float = 0.0
    cost_usd: float = 0.0

    truncation_pct: float = 0.0
    prompt_sha256: str = ""
    answer: str = ""
    api_error: str = ""
    model: str = ""


def _estimate_tokens_from_chars(text: str) -> int:
    """Cheap pre-call estimate. Real count comes from the API response."""
    if not text:
        return 0
    return max(1, int(len(text) / EST_CHARS_PER_TOKEN))


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_RATE_PER_1M_TOKENS.get(model, {"input": 0.0, "output": 0.0})
    return (
        prompt_tokens * rates.get("input", 0.0)
        + completion_tokens * rates.get("output", 0.0)
    ) / 1_000_000


def serialize_with_truncation(
    df: pd.DataFrame,
    n_rows: int,
    question: str,
    seed: int = 42,
) -> tuple[str, float, int]:
    """
    Sample n_rows from df, serialize to CSV, char-truncate to fit context budget.

    Returns (csv_text, truncation_pct, estimated_full_tokens).
      truncation_pct = (full_tokens − sent_tokens) / full_tokens, in [0, 1].
    """
    if n_rows >= len(df):
        sample = df
    else:
        sample = df.sample(n=n_rows, random_state=seed)

    full_csv = sample.to_csv(index=False)
    full_tokens = _estimate_tokens_from_chars(full_csv)

    # Reserve a few hundred tokens for the question + system prompt scaffolding
    question_tokens = _estimate_tokens_from_chars(question) + 64
    max_csv_chars = int((PROMPT_TOKEN_BUDGET - question_tokens) * EST_CHARS_PER_TOKEN)

    if len(full_csv) <= max_csv_chars:
        return full_csv, 0.0, full_tokens

    truncated = full_csv[:max_csv_chars]
    # snap to last complete row
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[: last_newline + 1]
    sent_tokens = _estimate_tokens_from_chars(truncated)
    truncation_pct = max(0.0, 1.0 - sent_tokens / max(1, full_tokens))
    return truncated, truncation_pct, full_tokens


def build_user_prompt(csv_text: str, question: str, truncation_pct: float) -> str:
    header = "Dataset (CSV):"
    if truncation_pct > 0:
        header += (
            f"  [NOTE: the dataset was truncated to fit the model's context window. "
            f"Approximately {truncation_pct*100:.1f}% of rows were dropped.]"
        )
    return f"{header}\n{csv_text}\n\nQuestion: {question}\n\nAnswer directly and concisely."


def run_llm_only_trial(
    *,
    dataset: str,
    df: pd.DataFrame,
    n_rows: int,
    question_id: int,
    question: str,
    rep: int,
    model: str,
    api_key: str,
    raw_response_dir: Path | None = None,
) -> TrialResult:
    """Execute one LLM-Only trial and return the populated TrialResult."""
    result = TrialResult(
        baseline="LLM_ONLY",
        dataset=dataset,
        n_rows=min(n_rows, len(df)),
        question_id=question_id,
        question=question,
        rep=rep,
        model=model,
    )

    try:
        csv_text, truncation_pct, full_tokens_est = serialize_with_truncation(
            df, n_rows, question
        )
        user_prompt = build_user_prompt(csv_text, question, truncation_pct)
        result.truncation_pct = truncation_pct
        result.prompt_tokens_estimated = _estimate_tokens_from_chars(
            _SYSTEM_PROMPT + user_prompt
        )
        result.prompt_sha256 = sha256(user_prompt.encode("utf-8")).hexdigest()[:16]

        llm = ChatGroq(model=model, groq_api_key=api_key, temperature=0)
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        t0 = time.time()
        response = llm.invoke(messages)
        result.latency_s = time.time() - t0

        # Real billed token counts — the whole reason this module exists.
        usage = (response.response_metadata or {}).get("token_usage", {})
        result.prompt_tokens = int(usage.get("prompt_tokens", 0))
        result.completion_tokens = int(usage.get("completion_tokens", 0))
        result.total_tokens = int(
            usage.get("total_tokens", result.prompt_tokens + result.completion_tokens)
        )
        result.cost_usd = _compute_cost(
            model, result.prompt_tokens, result.completion_tokens
        )
        result.answer = str(getattr(response, "content", "") or "")
    except Exception as exc:  # noqa: BLE001 — record and continue
        result.api_error = f"{type(exc).__name__}: {exc}"

    if raw_response_dir is not None:
        raw_response_dir.mkdir(parents=True, exist_ok=True)
        archive = {
            **asdict(result),
            "system_prompt": _SYSTEM_PROMPT,
            # only first 4k chars of the user prompt — full one is reconstructible
            # from (dataset, n_rows, question, seed)
            "user_prompt_preview": (user_prompt[:4000] if 'user_prompt' in locals() else ""),
            "user_prompt_chars": len(user_prompt) if 'user_prompt' in locals() else 0,
        }
        path = raw_response_dir / (
            f"{dataset}_n{n_rows}_q{question_id}_r{rep}.json"
        )
        path.write_text(json.dumps(archive, indent=2))

    return result


# ---------------------------------------------------------------------------
# FF delegation — reuses the standard BaselineRunner so the comparison is
# apples-to-apples with the published WISDM benchmark.
# ---------------------------------------------------------------------------

def run_flash_fusion_trial(
    *,
    dataset: str,
    df: pd.DataFrame,
    question_id: int,
    question: str,
    rep: int,
    model: str,
    api_key: str,
    adapter: Any | None = None,
) -> TrialResult:
    """
    Invoke the standard Flash-Fusion pipeline via BaselineRunner. FF does not
    have a 'n_rows' axis — it always operates on the full dataframe.
    """
    from flashfusion.pipeline.runner import BaselineRunner, LLMClient

    result = TrialResult(
        baseline="FLASH_FUSION",
        dataset=dataset,
        n_rows=len(df),
        question_id=question_id,
        question=question,
        rep=rep,
        model=model,
    )
    try:
        client = LLMClient(model_name=model, api_key=api_key)
        runner = BaselineRunner(
            mode="FLASH_FUSION", df=df.copy(), client=client, adapter=adapter
        )
        t0 = time.time()
        run_result = runner.run(question)
        result.latency_s = time.time() - t0
        # LLMClient estimates tokens via word-count×1.3 — we accept that here
        # since FF makes many internal calls and a unified billed count would
        # require rewriting LLMClient. The cost is still computed via the same
        # rate card and is comparable in *order of magnitude* to LLM_ONLY.
        result.prompt_tokens = client.total_input_tokens()
        result.completion_tokens = client.total_output_tokens()
        result.total_tokens = client.total_tokens()
        result.cost_usd = client.total_cost_usd()
        result.answer = run_result.answer or ""
    except Exception as exc:  # noqa: BLE001
        result.api_error = f"{type(exc).__name__}: {exc}"

    return result
