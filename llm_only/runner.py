"""
flashfusion.llm_only.runner — trial execution with provider-billed token capture.

The default LLMClient in flashfusion.pipeline.runner estimates tokens with
len(text.split()) * 1.3, which under-counts CSV-heavy prompts by 20–40%. For a
cost-and-scaling study that is fatal, so this module talks to the chat provider
directly and reads response token usage for billed counts.

Single API call per LLM_ONLY trial. FF trials delegate to the existing
BaselineRunner so we reuse the published pipeline.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter

from flashfusion.config import MODEL_RATE_PER_1M_TOKENS


CONTEXT_TOKEN_BUDGET = 128_000
RESPONSE_TOKEN_RESERVE = 4_096
QUESTION_TOKEN_RESERVE = 512
PROMPT_TOKEN_BUDGET = (
    CONTEXT_TOKEN_BUDGET - RESPONSE_TOKEN_RESERVE - QUESTION_TOKEN_RESERVE
)

EST_CHARS_PER_TOKEN = 3.0


_SYSTEM_PROMPT = (
    "You are a data analyst. Answer the user's question using ONLY the dataset "
    "provided below. Be direct and concise. If the dataset is incomplete or "
    "truncated, say so explicitly. Do not invent values that are not in the data."
)


@dataclass
class TrialResult:
    """One LLM-only trial on one (dataset, n_rows, question, rep)."""

    baseline: str
    dataset: str
    n_rows: int
    question_id: int
    question: str
    rep: int

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_estimated: int = 0

    latency_s: float = 0.0
    cost_usd: float = 0.0

    truncation_pct: float = 0.0
    prompt_sha256: str = ""
    answer: str = ""
    api_error: str = ""
    model: str = ""


def _estimate_tokens_from_chars(text: str) -> int:
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
    """Sample n_rows and truncate CSV serialization to fit prompt budget."""
    sample = df if n_rows >= len(df) else df.sample(n=n_rows, random_state=seed)

    full_csv = sample.to_csv(index=False)
    full_tokens = _estimate_tokens_from_chars(full_csv)

    question_tokens = _estimate_tokens_from_chars(question) + 64
    max_csv_chars = int((PROMPT_TOKEN_BUDGET - question_tokens) * EST_CHARS_PER_TOKEN)

    if len(full_csv) <= max_csv_chars:
        return full_csv, 0.0, full_tokens

    truncated = full_csv[:max_csv_chars]
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
            f"  [NOTE: the dataset was truncated to fit the model context window. "
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
    """Execute one LLM-only trial and return the populated TrialResult."""
    result = TrialResult(
        baseline="LLM_ONLY",
        dataset=dataset,
        n_rows=min(n_rows, len(df)),
        question_id=question_id,
        question=question,
        rep=rep,
        model=model,
    )

    user_prompt = ""
    try:
        csv_text, truncation_pct, _full_tokens_est = serialize_with_truncation(
            df, n_rows, question
        )
        user_prompt = build_user_prompt(csv_text, question, truncation_pct)
        result.truncation_pct = truncation_pct
        result.prompt_tokens_estimated = _estimate_tokens_from_chars(
            _SYSTEM_PROMPT + user_prompt
        )
        result.prompt_sha256 = sha256(user_prompt.encode("utf-8")).hexdigest()[:16]

        llm = ChatOpenRouter(
            model=model,
            api_key=api_key,
            temperature=0,
            max_retries=2,
        )
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        t0 = time.time()
        response = llm.invoke(messages)
        result.latency_s = time.time() - t0

        usage = getattr(response, "usage_metadata", None) or {}
        result.prompt_tokens = int(usage.get("input_tokens", 0))
        result.completion_tokens = int(usage.get("output_tokens", 0))
        result.total_tokens = int(
            usage.get("total_tokens", result.prompt_tokens + result.completion_tokens)
        )
        result.cost_usd = _compute_cost(
            model, result.prompt_tokens, result.completion_tokens
        )
        result.answer = str(getattr(response, "content", "") or "")
    except Exception as exc:  # noqa: BLE001
        result.api_error = f"{type(exc).__name__}: {exc}"

    if raw_response_dir is not None:
        raw_response_dir.mkdir(parents=True, exist_ok=True)
        archive = {
            **asdict(result),
            "system_prompt": _SYSTEM_PROMPT,
            "user_prompt_preview": user_prompt[:4000],
            "user_prompt_chars": len(user_prompt),
        }
        path = raw_response_dir / f"{dataset}_n{n_rows}_q{question_id}_r{rep}.json"
        path.write_text(json.dumps(archive, indent=2))

    return result


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
    """Invoke the standard Flash-Fusion pipeline via BaselineRunner."""
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
        result.prompt_tokens = client.total_input_tokens()
        result.completion_tokens = client.total_output_tokens()
        result.total_tokens = client.total_tokens()
        result.cost_usd = client.total_cost_usd()
        result.answer = run_result.answer or ""
    except Exception as exc:  # noqa: BLE001
        result.api_error = f"{type(exc).__name__}: {exc}"

    return result
