"""
pipeline/runner.py — LLM client, result dataclass, and baseline runner.

Classes:
  LLMCallLog    — dataclass recording per-LLM-call usage metrics
  LLMClient     — wraps ChatGroq, accumulates call logs, computes totals
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
from langchain_groq import ChatGroq

from flashfusion.config import MODEL_RATE_PER_1M_TOKENS, TOKEN_ESTIMATE_MULTIPLIER


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
    Wraps ChatGroq with call logging and cost estimation.

    One LLMClient instance per (baseline, query) benchmark run so that
    call_log and totals are isolated.

    Usage:
        client = LLMClient(model_name="llama-3.3-70b-versatile", api_key=os.environ["GROQ_API_KEY"])
        result = client.invoke_chain(chain, {"input": "..."}, stage="S1")
        print(client.total_cost_usd())
    """

    def __init__(self, model_name: str, api_key: str) -> None:
        """
        Args:
            model_name: Groq model identifier (must be a key in config.MODEL_RATE_PER_1M_TOKENS).
            api_key:    Groq API key (from GROQ_API_KEY environment variable).
        """
        self.model_name = model_name
        self.llm = ChatGroq(model=model_name, groq_api_key=api_key, temperature=0)
        self.call_log: list[LLMCallLog] = []

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
        t0 = time.time()
        result = chain.invoke(inputs)
        latency = time.time() - t0
        if isinstance(result, str):
            output_text = result
        else:
            output_text = str(getattr(result, "content", result))
        in_tok = self._estimate_tokens(str(inputs))
        out_tok = self._estimate_tokens(output_text)
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

    def _estimate_tokens(self, text: str) -> int:
        """
        Rough token estimation: word count × TOKEN_ESTIMATE_MULTIPLIER.
        """
        if not text:
            return 0
        return max(1, int(len(text.split()) * TOKEN_ESTIMATE_MULTIPLIER))

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


# ---------------------------------------------------------------------------
# BaselineRunner
# ---------------------------------------------------------------------------

class BaselineRunner:
    """
    Dispatches a query to the correct baseline implementation.

    Supported modes (self.MODES):
        "LLM_ONLY"     — B0: raw 20-row CSV + query → single LLM call
        "WELLMAX_ONLY"  — B3: S1 + S2 + S3 → grounded query → pandas agent
        "AUTOIOT_ONLY"  — Agent: raw query → pandas agent
        "FLASH_FUSION"  — B4: S1 + S2 + S3 + guardrail + agent + judge (+ retry)

    For rewriting baselines (WellMax/Flash-Fusion), derived features are applied
    internally before dispatch: magnitude and activity_name.
    """

    MODES: frozenset = frozenset(
        {"LLM_ONLY", "WELLMAX_ONLY", "AUTOIOT_ONLY", "FLASH_FUSION"}
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

        if not self._enrichment_applied and self.mode in {"WELLMAX_ONLY", "FLASH_FUSION"}:
            self.df, _ = self._apply_default_enrichment(self.df)
            self._enrichment_applied = True

        from flashfusion.baselines.autoiot_only import run_autoiot_only
        from flashfusion.baselines.flash_fusion import run_flash_fusion
        from flashfusion.baselines.llm_only import run_llm_only
        from flashfusion.baselines.wellmax_only import run_wellmax_only

        if self.mode == "LLM_ONLY":
            run_llm_only(query, self.df, self.client, r)
        elif self.mode == "WELLMAX_ONLY":
            run_wellmax_only(query, self.df, self.client, r)
        elif self.mode == "AUTOIOT_ONLY":
            run_autoiot_only(query, self.df, self.client, r)
        elif self.mode == "FLASH_FUSION":
            run_flash_fusion(query, self.df, self.client, r)

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

    def _run_autoiot_only(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.autoiot_only.run_autoiot_only."""
        from flashfusion.baselines.autoiot_only import run_autoiot_only
        return run_autoiot_only(query, self.df, self.client, r)

    def _run_flash_fusion(self, query: str, r: RunResult) -> RunResult:
        """Delegates to baselines.flash_fusion.run_flash_fusion."""
        from flashfusion.baselines.flash_fusion import run_flash_fusion
        return run_flash_fusion(query, self.df, self.client, r)
