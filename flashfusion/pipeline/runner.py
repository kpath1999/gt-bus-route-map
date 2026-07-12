"""
pipeline/runner.py — LLM client, result dataclass, and baseline runner.

Classes:
  LLMCallLog    — dataclass recording per-LLM-call usage metrics
    LLMClient     — wraps a chat model client, accumulates call logs, computes totals
  RunResult     — dataclass capturing the full output of one (baseline, query) run
  BaselineRunner — dispatches to the correct baseline implementation

See CLAUDE.md §pipeline/runner.py for full implementation specifications.
Reference: chat/playground/playground.py ~lines 474–900.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import pandas as pd
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openrouter import ChatOpenRouter

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from flashfusion.config import MODEL_RATE_PER_1M_TOKENS


# ---------------------------------------------------------------------------
# _UsageCapture — callback to extract real token counts from API responses
# ---------------------------------------------------------------------------

class _UsageCapture(BaseCallbackHandler):
    """LangChain callback that reads token counts from the OpenRouter API response.

    OpenRouter always returns native-tokenizer usage in every response via
    ``AIMessage.usage_metadata`` (populated by langchain_openrouter).  No
    heuristics or estimation are used.
    """

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs: object) -> None:
        for gen_list in response.generations:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg is not None else None
                if um:
                    self.input_tokens += int(um.get("input_tokens", 0))
                    self.output_tokens += int(um.get("output_tokens", 0))


# ---------------------------------------------------------------------------
# LLMCallLog
# ---------------------------------------------------------------------------

@dataclass
class LLMCallLog:
    """Records usage metrics for a single LLM invocation."""
    model: str
    stage: str
    input_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Wraps a chat model with call logging and cost estimation.

    One LLMClient instance per (baseline, query) benchmark run so that
    call_log and totals are isolated.

    Usage:
        client = LLMClient(model_name="meta-llama/llama-3.1-8b-instruct", api_key=os.environ["OPENROUTER_API_KEY"])
        result = client.invoke_chain(chain, {"input": "..."}, stage="S1")
        print(client.total_cost_usd())
    """

    def __init__(
        self,
        model_name: str,
        api_key: str,
        light_model_name: str | None = None,
        _shared_call_log: list["LLMCallLog"] | None = None,
    ) -> None:
        """
        Args:
            model_name:       Primary model identifier (must be a key in
                              config.MODEL_RATE_PER_1M_TOKENS).
            api_key:          Provider API key (OPENROUTER_API_KEY preferred,
                              GROQ_API_KEY fallback).
            light_model_name: Optional lighter model used for cheap early stages
                              (e.g. Flash-Fusion S1/S2). When set and different
                              from model_name, ``self.light`` is a sibling
                              LLMClient bound to that model whose calls are logged
                              into this client's call_log, so cost/latency/token
                              totals remain aggregated on the primary client.
            _shared_call_log: Internal — when provided, this instance is a light
                              sibling that shares the primary client's call_log.
        """
        self.model_name = model_name
        self.llm = ChatOpenRouter(
            model=model_name,
            api_key=api_key,
            temperature=0,
            max_retries=2,
            timeout=480_000,  # 480 s in ms; ChatOpenRouter native param (not request_timeout)
        )
        # A light sibling shares the primary's call_log so totals aggregate once.
        self.call_log: list[LLMCallLog] = (
            _shared_call_log if _shared_call_log is not None else []
        )
        if _shared_call_log is not None:
            # This instance IS the light sibling; route .light to itself.
            self.light: "LLMClient" = self
        elif light_model_name and light_model_name != model_name:
            self.light = LLMClient(
                model_name=light_model_name,
                api_key=api_key,
                _shared_call_log=self.call_log,
            )
        else:
            self.light = self

    def invoke_chain(self, chain, inputs: dict, stage: str) -> str:
        """
        Invoke a LangChain chain, record usage, and return the string output.

        Args:
            chain:  Any LangChain Runnable (e.g. prompt | llm | StrOutputParser()).
            inputs: Dict of template variables for the chain.
            stage:  Human-readable label for this call (e.g. "S1", "guardrail").

        Returns:
            String output from the chain.

        Implementation:
            1. t0 = time.time()
            2. result = chain.invoke(inputs)
            3. latency = time.time() - t0
            4. Estimate tokens from inputs and result strings
            5. Compute cost via _compute_cost()
            6. Append LLMCallLog to self.call_log
            7. Return result if str, else str(result)
        """
        import sys as _sys
        import httpx as _httpx
        _max_attempts = 3
        _last_exc: Exception | None = None
        capture = _UsageCapture()
        for _attempt in range(_max_attempts):
            try:
                t0 = time.time()
                result = chain.invoke(inputs, config={"callbacks": [capture]})
                latency = time.time() - t0
                break
            except _httpx.ReadTimeout as _exc:
                _last_exc = _exc
                _sys.stdout.write(
                    f"  [WARN invoke_chain] ReadTimeout on attempt {_attempt + 1}/{_max_attempts}"
                    f" (stage={stage!r}); retrying…\n"
                )
                _sys.stdout.flush()
        else:
            raise _last_exc  # type: ignore[misc]
        if isinstance(result, str):
            output_text = result
        else:
            output_text = str(getattr(result, "content", result))
        in_tok = capture.input_tokens
        out_tok = capture.output_tokens
        cost = self._compute_cost(in_tok, out_tok)
        self.call_log.append(
            LLMCallLog(
                model=self.model_name,
                stage=stage,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_s=latency,
                cost_usd=cost,
            )
        )
        return output_text

    def _compute_cost(self, in_tok: int, out_tok: int) -> float:
        """
        Compute USD cost from token counts using config.MODEL_RATE_PER_1M_TOKENS.
        """
        rates = MODEL_RATE_PER_1M_TOKENS.get(
            self.model_name, {"input": 0.0, "output": 0.0}
        )
        return (
            in_tok * rates.get("input", 0.0) + out_tok * rates.get("output", 0.0)
        ) / 1_000_000

    def total_latency(self) -> float:
        return sum(c.latency_s for c in self.call_log)

    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.call_log)

    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.call_log)

    def total_tokens(self) -> int:
        return self.total_input_tokens() + self.total_output_tokens()

    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.call_log)


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """
    Captures the full output of one (baseline, query) benchmark run.

    Fields set by BaselineRunner.run() — do not set manually from outside.
    """
    baseline: str                                    # e.g. "LLM_ONLY"
    model: str                                       # e.g. "llama-3.3-70b-versatile"
    query: str                                       # original query text

    # Outputs
    answer: str = ""                                 # final natural-language answer
    trace: str = ""                                  # agent ReAct trace (if executed)

    # Metrics (populated after run completes)
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    # Execution state
    executed: bool = False                           # True if pandas agent ran code
    stages_run: list = field(default_factory=list)  # e.g. ["S1","S2","S3","guardrail","agent","judge"]
    judge_verdict: dict = field(default_factory=dict)  # {"verdict": "PASS"|"FAIL", "issue": str, ...}
    alignment_explanation: str = ""                   # user-facing alignment/rejection rationale
    rejected: bool = False                           # True if guardrail or S2 rejected query
    rejection_reason: str = ""

    # Agent details
    final_code: str = ""                             # last successfully executed pandas code
    agent_tries: int = 0                             # total agent iterations across sub-queries
    execution_attempts: list = field(default_factory=list)  # per-attempt stats

    # Pipeline stage intermediates (populated by rewriting baselines: WELLMAX_ONLY, FLASH_FUSION)
    s1_concepts: dict = field(default_factory=dict)      # Stage 1 output: {"DATA": [...], "REASONING": [...]}
    s2_grounding: str = ""                               # Stage 2 raw LLM grounding text
    s3_sub_queries: list = field(default_factory=list)   # Stage 3 concrete sub-questions
    s3_synthesis_hint: str = ""                          # Stage 3 synthesis guidance string

    # Stage latency telemetry (seconds)
    stage_latency_s: dict = field(default_factory=dict)   # canonical keys: s1,s2,s3,guardrail,agent


# ---------------------------------------------------------------------------
# BaselineRunner
# ---------------------------------------------------------------------------

class BaselineRunner:
    """
    Dispatches a query to the correct baseline implementation.

    Supported modes (self.MODES):
        "LLM_ONLY"     — B0: raw 20-row CSV + query → single LLM call
        "WELLMAX_ONLY"  — B3: S1 + S2 + S3 → grounded query → pandas agent
        "REACT_ONLY"  — ReAct: raw query → pandas agent (paper-faithful ReAct)
        "LLMSENSE_PAPER" — narration/summarization + reasoning over narrative text
        "FLASH_FUSION"  — B4: S1 + S2 + S3 + guardrail + agent + judge (+ retry)

    For rewriting baselines (WellMax/Flash-Fusion), derived features are applied
    internally before dispatch: magnitude and activity_name.
    """

    MODES: frozenset = frozenset(
        {
            "LLM_ONLY",
            "WELLMAX_ONLY",
            "REACT_ONLY",
            "AUTOIOT_PAPER",
            "FLASH_FUSION",
            "HARGPT_PAPER",
            "LLMSENSE_PAPER",
        }
    )

    def __init__(
        self,
        mode: str,
        df: pd.DataFrame,
        client: LLMClient,
        data_path: str = "WISDM",
    ) -> None:
        """
        Args:
            mode:       One of self.MODES.
            df:         WISDM DataFrame.
            client:     LLMClient for this run.
            data_path:  Descriptive label injected into agent prefix (not a file path).

        Raises:
            ValueError: If mode is not in self.MODES.
        """
        # DEBUG: Check what df we receive
        import sys
        # print(f"[RUNNER INIT DEBUG] mode={mode}, df len={len(df)}, df cols={list(df.columns) if hasattr(df, 'columns') else 'N/A'}", file=sys.stderr, flush=True)
        # if len(df) > 0:
        #     print(f"[RUNNER INIT DEBUG] df.head(3):\n{df.head(3)}", file=sys.stderr, flush=True)
        
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}, got {mode!r}")
        self.mode = mode
        self.df = df.copy()
        self.client = client
        self.data_path = data_path
        self._enrichment_applied = False

    @staticmethod
    def _apply_default_enrichment(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
        """
        Add deterministic derived columns used by rewriting baselines.

        Returns:
            (enriched_df, provenance)
        """
        enriched = df.copy()
        provenance: dict[str, str] = {}

        if all(col in enriched.columns for col in ("x", "y", "z")) and "magnitude" not in enriched.columns:
            enriched["magnitude"] = (enriched["x"] ** 2 + enriched["y"] ** 2 + enriched["z"] ** 2) ** 0.5
            provenance["magnitude"] = "sqrt(x^2 + y^2 + z^2)"

        # dt_s: inter-sample recording time in seconds for each row.
        # Computed as the consecutive timestamp difference within each subject,
        # clipped to 0 (removes session-boundary negatives), then converted from
        # nanoseconds to seconds.  Use dt_s to measure activity duration by summing
        # rows for a given activity group — never use max-min timestamp span.
        if "timestamp" in enriched.columns and "subject_id" in enriched.columns and "dt_s" not in enriched.columns:
            _sorted = enriched.sort_values(["subject_id", "timestamp"])
            _diffs = (
                _sorted.groupby("subject_id")["timestamp"]
                .diff()
                .clip(lower=0)
                .fillna(0)
                / 1e9
            )
            enriched["dt_s"] = _diffs.reindex(enriched.index).fillna(0)
            provenance["dt_s"] = "inter-sample time in seconds: diff(timestamp).clip(0).fillna(0)/1e9 within subject_id"

        if "activity_label" in enriched.columns and "activity_name" not in enriched.columns:
            labels = enriched["activity_label"]
            if pd.api.types.is_string_dtype(labels) or labels.dtype == object:
                labels = labels.astype(str).str.strip()
            enriched["activity_name"] = labels
            provenance["activity_name"] = "copied from activity_label"

        return enriched, provenance

    def run(self, query: str) -> RunResult:
        """
        Execute the full baseline pipeline for one query.

        Args:
            query: Raw natural language query string.

        Returns:
            Populated RunResult. latency_s, input_tokens, output_tokens,
            and cost_usd are set from client.call_log after execution.

        Implementation:
            1. r = RunResult(baseline=self.mode, model=self.client.model_name, query=query)
            2. t0 = time.time()
            3. Apply deterministic enrichment once for rewriting baselines.
            4. Dispatch to _run_<mode>(query, r)
            5. r.latency_s = time.time() - t0
            6. r.input_tokens  = self.client.total_input_tokens()
            7. r.output_tokens = self.client.total_output_tokens()
            8. r.cost_usd      = self.client.total_cost_usd()
            9. return r
        """
        r = RunResult(
            baseline=self.mode, model=self.client.model_name, query=query
        )
        t0 = time.time()

        if not self._enrichment_applied and self.mode in {"WELLMAX_ONLY", "FLASH_FUSION", "LLMSENSE_PAPER"}:
            self.df, _ = self._apply_default_enrichment(self.df)
            self._enrichment_applied = True

        from flashfusion.baselines.react_only import run_react_only
        from flashfusion.baselines.autoiot_paper import run_autoiot_paper
        from flashfusion.baselines.flash_fusion import run_flash_fusion
        from flashfusion.baselines.hargpt_paper import run_hargpt_paper
        from flashfusion.baselines.llmsense_paper import run_llmsense_paper
        from flashfusion.baselines.llm_only import run_llm_only
        from flashfusion.baselines.wellmax_only import run_wellmax_only

        if self.mode == "LLM_ONLY":
            run_llm_only(query, self.df, self.client, r)
        elif self.mode == "WELLMAX_ONLY":
            run_wellmax_only(query, self.df, self.client, r)
        elif self.mode == "REACT_ONLY":
            run_react_only(query, self.df, self.client, r)
        elif self.mode == "AUTOIOT_PAPER":
            run_autoiot_paper(query, self.df, self.client, r)
        elif self.mode == "FLASH_FUSION":
            run_flash_fusion(query, self.df, self.client, r)
        elif self.mode == "HARGPT_PAPER":
            # DEBUG: Check dataframe before calling HARGPT
            import sys
            print(f"[RUNNER DEBUG] Before HARGPT call - df len={len(self.df)}, cols={list(self.df.columns) if hasattr(self.df, 'columns') else 'N/A'}", file=sys.stderr, flush=True)
            if len(self.df) > 0:
                print(f"[RUNNER DEBUG] df.head(3):\n{self.df.head(3)}", file=sys.stderr, flush=True)
            run_hargpt_paper(query, self.df, self.client, r)
        elif self.mode == "LLMSENSE_PAPER":
            run_llmsense_paper(query, self.df, self.client, r)

        r.latency_s = time.time() - t0
        r.input_tokens = self.client.total_input_tokens()
        r.output_tokens = self.client.total_output_tokens()
        r.cost_usd = self.client.total_cost_usd()
        return r

    def _run_llm_only(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.llm_only.run_llm_only."""
        from flashfusion.baselines.llm_only import run_llm_only
        return run_llm_only(query, self.df, self.client, r)

    def _run_wellmax_only(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.wellmax_only.run_wellmax_only."""
        from flashfusion.baselines.wellmax_only import run_wellmax_only
        return run_wellmax_only(query, self.df, self.client, r)

    def _run_react_only(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.react_only.run_react_only."""
        from flashfusion.baselines.react_only import run_react_only
        return run_react_only(query, self.df, self.client, r)

    def _run_flash_fusion(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.flash_fusion.run_flash_fusion."""
        from flashfusion.baselines.flash_fusion import run_flash_fusion
        return run_flash_fusion(query, self.df, self.client, r)
