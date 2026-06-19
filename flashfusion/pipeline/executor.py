"""
pipeline/executor.py — Pandas agent execution layer with resilient parsing.

Classes:
  ExecutionDetails         — dataclass capturing per-run agent metrics
  ThinkingCaptureHandler   — LangChain callback handler that records agent trace
  ResilientReActOutputParser — hardened ReAct parser (handles 3 LLM failure modes)
  ExecutionLayer           — orchestrates guardrail, agent execution, judge, synthesis

See CLAUDE.md §pipeline/executor.py for full implementation specifications.
Reference: chat/playground/playground.py ~lines 660–900.
"""

from __future__ import annotations

import contextlib
import io
import multiprocessing as mp
import os
import platform
import re
import time
from queue import Empty
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import pandas as pd
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.pipeline.loader import build_column_metadata, meta_to_str
from flashfusion.prompts.templates import (
    GUARDRAIL_PROMPT,
    JUDGE_PROMPT,
    PLAN_JUDGE_PROMPT,
    SYNTHESIS_PROMPT,
)
from flashfusion.config import (
    AGENT_SAFE_MAX_ATTEMPTS,
    AGENT_SAFE_CODE_TIMEOUT_S,
    EXECUTION_AGENT_BACKEND_DEFAULT,
    RESILIENT_PARSER_MAX_IDENTICAL,
    RESILIENT_PARSER_MAX_FAILURES,
)

if TYPE_CHECKING:
    from flashfusion.pipeline.runner import LLMClient


def _safe_exec_worker(code: str, df: pd.DataFrame, output_queue) -> None:
    """Execute generated code in an isolated process and return (ok, output)."""
    local_ns: dict[str, Any] = {"df": df.copy(), "pd": pd, "result": None}
    stdout_buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buffer):
            exec(code, {"__builtins__": __builtins__}, local_ns)
        result_obj = local_ns.get("result")
        std_out = stdout_buffer.getvalue().strip()
        if result_obj is None:
            if std_out:
                output_queue.put((True, std_out))
                return
            output_queue.put((True, "(no result produced)"))
            return
        output_queue.put((True, str(result_obj)))
    except Exception as exc:
        output_queue.put((False, f"{type(exc).__name__}: {exc}"))

# ---------------------------------------------------------------------------
# ExecutionDetails
# ---------------------------------------------------------------------------

@dataclass
class ExecutionDetails:
    """Captures per-run agent execution metrics."""
    final_code: str = ""
    tries: int = 0
    attempts: list[dict] = field(default_factory=list)
    safe_execution_s: float = 0.0


# ---------------------------------------------------------------------------
# ThinkingCaptureHandler
# ---------------------------------------------------------------------------

class ThinkingCaptureHandler(BaseCallbackHandler):
    """
    LangChain callback handler that captures the full agent ReAct trace.

    Records each Thought/Action/Observation cycle so the ExecutionLayer
    can extract the final executed code and the number of agent iterations.

    Implementation: see CLAUDE.md §pipeline/executor.py::ThinkingCaptureHandler.
    The full class body is specified there — implement exactly as described.
    """

    def __init__(self) -> None:
        super().__init__()
        self.steps: list[str] = []
        self.action_inputs: list[str] = []
        self.final_output: str = ""
        self.last_successful_action_input: str = ""

    def on_agent_action(self, action: AgentAction, **kwargs) -> None:
        """Record a Thought + Action cycle."""
        self.steps.append(f"Thought: {action.log.strip()}")
        tool_input = str(action.tool_input).strip()
        if tool_input:
            self.action_inputs.append(tool_input)

    def on_tool_end(self, output: str, **kwargs) -> None:
        """Record an Observation (tool result)."""
        out = str(output)
        self.steps.append(f"Observation: {out}")
        if not self._looks_like_tool_error(out) and self.action_inputs:
            self.last_successful_action_input = self.action_inputs[-1]

    def on_agent_finish(self, finish: AgentFinish, **kwargs) -> None:
        """Record the agent's final answer."""
        self.final_output = finish.return_values.get("output", "")
        self.steps.append(f"Final Answer: {self.final_output}")

    def get_trace(self) -> str:
        """Return full trace as newline-joined string."""
        return "\n".join(self.steps) if self.steps else "(no steps captured)"

    def get_execution_details(self) -> tuple[str, int]:
        """Return (last_successful_action_input, n_action_inputs)."""
        final_code = self.last_successful_action_input
        if not final_code and self.action_inputs:
            final_code = self.action_inputs[-1]
        return final_code, len(self.action_inputs)

    def _looks_like_tool_error(self, output: str) -> bool:
        """
        Return True if the tool output looks like a Python exception/error.

        Error keywords: Error, Traceback, Exception, SyntaxError, NameError,
                        ValueError, KeyError, AttributeError, TypeError
        """
        error_keywords = (
            "Traceback", "Exception", "SyntaxError", "NameError",
            "ValueError", "KeyError", "AttributeError", "TypeError",
            "IndexError",
        )
        return any(k in output for k in error_keywords)


# ---------------------------------------------------------------------------
# ResilientReActOutputParser
# ---------------------------------------------------------------------------

class ResilientReActOutputParser:
    """
    Hardened ReAct output parser that handles three documented LLM failure modes.

    Failure modes (see CLAUDE.md §ResilientReActOutputParser):
      P0-loop-1: Essay output (no Action / no Final Answer)
                 → parse failures counted; after MAX_PARSE_FAILURES → extract best answer
      P0-loop-2: Both "Action:" and "Final Answer:" present
                 → prefer the Action; strip Final Answer section; fallback if still fails
      P0-loop-3: Stray "Thought:" lines appended after Action Input code block
                 → _sanitize_action_input strips everything after the first \nThought:
      P3-dedup:  Consecutive identical raw outputs
                 → detect via _output_history; after MAX_IDENTICAL → extract best answer

    Uses Pydantic PrivateAttr for mutable state (required because this class
    inherits from a Pydantic model via LangChain).
    """

    MAX_IDENTICAL: ClassVar[int] = RESILIENT_PARSER_MAX_IDENTICAL
    MAX_PARSE_FAILURES: ClassVar[int] = RESILIENT_PARSER_MAX_FAILURES

    def __init__(self) -> None:
        self._output_history: list[str] = []
        self._parse_failure_count: int = 0

    def parse(self, text: str) -> AgentAction | AgentFinish:
        """
        Parse LLM output into AgentAction or AgentFinish with failure-mode handling.

        Order of checks:
          1. Dedup detection (P3): if last MAX_IDENTICAL outputs are identical → extract answer
          2. Append text to _output_history
          3. P0-loop-2: both Action + Final Answer present → strip Final Answer, try super()
          4. Try super().parse(text) on the (possibly sanitized) text
          5. On success: sanitize tool_input via _sanitize_action_input (P0-loop-3)
          6. On OutputParserException (P0-loop-1): increment counter; if >= MAX → extract answer
        """
        cleaned = text.strip()

        self._output_history.append(cleaned)
        if len(self._output_history) > self.MAX_IDENTICAL:
            self._output_history = self._output_history[-self.MAX_IDENTICAL:]
        if (
            len(self._output_history) >= self.MAX_IDENTICAL
            and len(set(self._output_history)) == 1
        ):
            self._parse_failure_count = 0
            return self._extract_best_answer(cleaned)

        # P0-loop-2: both Action and Final Answer present — prefer Action
        includes_answer = "Final Answer:" in text
        action_re = (
            r"Action\s*\d*\s*:[\s]*(.*?)[\s]*Action\s*\d*\s*Input\s*\d*\s*:[\s]*(.*)"
        )
        action_match = re.search(action_re, text, re.DOTALL)

        if action_match and includes_answer:
            action = action_match.group(1).strip()
            action_input = self._sanitize_action_input(action_match.group(2))
            self._parse_failure_count = 0
            return AgentAction(action, action_input.strip(' "'), text)

        if action_match:
            action = action_match.group(1).strip()
            action_input = self._sanitize_action_input(action_match.group(2))
            self._parse_failure_count = 0
            return AgentAction(action, action_input.strip(' "'), text)

        if includes_answer:
            self._parse_failure_count = 0
            answer = text.split("Final Answer:")[-1].strip()
            return AgentFinish({"output": answer}, text)

        # P0-loop-1: no recognisable format
        self._parse_failure_count += 1
        if self._parse_failure_count >= self.MAX_PARSE_FAILURES:
            self._parse_failure_count = 0
            return self._extract_best_answer(cleaned)

        # Return a soft fallback instead of raising to avoid parser deadlocks in some runtimes.
        return self._extract_best_answer(cleaned)

    def _sanitize_action_input(self, raw: str) -> str:
        """
        Strip stray Thought blocks and trailing explanations from Action Input.

        Handles P0-loop-3: model appends a second "Thought:" block after code.

        Steps:
          1. If "\nThought:" in raw: take raw.split("\nThought:")[0]
          2. If "\n\n" in raw: take first segment
          3. Return stripped result
        """
        cleaned = raw
        if "\nThought:" in cleaned:
            cleaned = cleaned.split("\nThought:", 1)[0]
        if "\nAction:" in cleaned:
            cleaned = cleaned.split("\nAction:", 1)[0]
        if "\n\n" in cleaned:
            cleaned = cleaned.split("\n\n", 1)[0]
        return cleaned.strip()

    def _extract_best_answer(self, text: str) -> AgentFinish:
        """
        Extract the best available answer from a malformed LLM output (P0-loop-1 fallback).

        Search priority:
          1. "Final Answer:" (case-insensitive) → everything after it
          2. "Answer:" → everything after it
          3. Last non-empty paragraph (split on \n\n)
          4. Entire text as fallback

        Returns:
            AgentFinish with return_values={"output": answer.strip()}, log=text
        """
        lower = text.lower()
        idx = lower.find("final answer:")
        if idx >= 0:
            answer = text[idx + len("final answer:"):].strip()
        else:
            idx = lower.find("answer:")
            if idx >= 0:
                answer = text[idx + len("answer:"):].strip()
            else:
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                answer = paragraphs[-1] if paragraphs else text.strip()
        return AgentFinish(return_values={"output": answer.strip()}, log=text)


# ---------------------------------------------------------------------------
# ExecutionLayer
# ---------------------------------------------------------------------------

class ExecutionLayer:
    """
    Orchestrates guardrail checking, agent execution, judge verification, and synthesis.

    The pandas agent uses ResilientReActOutputParser to handle LLM failure modes.
    Always call reset_agent() between sub-query executions in Flash-Fusion to
    prevent DataFrame state leakage across agent runs.

    Important: agent construction is lazy. Non-executing paths (guardrail-only
    or rejected queries) should not import or construct the pandas agent stack.
    """

    def __init__(self, df: pd.DataFrame, client: "LLMClient") -> None:
        """
        Initialise the execution layer with a DataFrame and LLM client.

        Args:
            df:     The WISDM DataFrame (enriched with derived features if adapter applied).
            client: LLMClient wrapping a chat model client.

        Sets up:
            self._original_df — immutable reference copy for reset_agent()
            self._df          — working copy passed to the pandas agent
            self._agent_executor — lazily created AgentExecutor (or None)
            Prompt chains for guardrail, synthesis, and judge.

        Agent construction notes (see CLAUDE.md §ExecutionLayer):
            - Tool: PythonAstREPLTool(locals={"df": self._df})
            - Parser: ResilientReActOutputParser() (replace default parser)
            - Wrap in AgentExecutor(max_iterations=6, handle_parsing_errors=True,
                                    return_intermediate_steps=True, verbose=False)
        """
        self._client = client
        self._original_df = df.copy()
        self._df = df.copy()

        self._guardrail_chain_template = ChatPromptTemplate.from_messages(
            [("system", "{system}"), ("human", "{query}")]
        )

        self._synthesizer_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", SYNTHESIS_PROMPT),
                    (
                        "human",
                        "Original question: {question}\n\n"
                        "Sub-answers:\n{sub_answers}\n\n"
                        "Synthesis guidance: {synthesis_hint}",
                    ),
                ]
            )
            | client.llm
            | StrOutputParser()
        )

        self._judge_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", JUDGE_PROMPT),
                    (
                        "human",
                        "Question: {question}\n\n"
                        "Code executed:\n{code}\n\n"
                        "Result:\n{result}",
                    ),
                ]
            )
            | client.llm
            | StrOutputParser()
        )

        self._plan_judge_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", PLAN_JUDGE_PROMPT),
                    (
                        "human",
                        "Question: {question}\n\n"
                        "Schema grounding:\n{grounding}\n\n"
                        "Sub-queries:\n{sub_queries}\n\n"
                        "Synthesis hint:\n{synthesis_hint}",
                    ),
                ]
            )
            | client.llm
            | StrOutputParser()
        )

        self._safe_codegen_chain = (
            ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You write Python code that runs against an in-memory pandas DataFrame named df. "
                        "Return only Python code. Do not include markdown fences. "
                        "Assign the final answer to a variable named result.",
                    ),
                    (
                        "human",
                        "Question: {question}\n\n"
                        "Columns:\n{column_metadata}\n\n"
                        "Previous error (if any):\n{last_error}",
                    ),
                ]
            )
            | client.llm
            | StrOutputParser()
        )

        self._safe_answer_chain = (
            ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a concise data analyst. Provide a direct answer to the question using the execution output.",
                    ),
                    (
                        "human",
                        "Question: {question}\n\nExecution output:\n{execution_output}",
                    ),
                ]
            )
            | client.llm
            | StrOutputParser()
        )

        self._agent_executor: Any | None = None
        self._agent_backend = self._resolve_agent_backend()
        debug_raw = os.getenv("FLASHFUSION_DEBUG_AGENT_INIT", "")
        self._debug_agent_init = debug_raw.lower() in {"1", "true", "yes", "on"}

    def _resolve_agent_backend(self) -> str:
        """
        Resolve agent backend from environment.

        Allowed values: auto, classic, safe.
        auto chooses safe on macOS (Darwin), classic elsewhere.
        """
        raw = os.getenv("FLASHFUSION_AGENT_BACKEND", EXECUTION_AGENT_BACKEND_DEFAULT)
        mode = (raw or "auto").strip().lower()
        if mode not in {"auto", "classic", "safe"}:
            mode = "auto"
        if mode == "auto":
            return "safe" if platform.system() == "Darwin" else "classic"
        return mode

    def _debug(self, msg: str) -> None:
        """Emit debug logs for agent init/execution when enabled."""
        if self._debug_agent_init:
            print(f"[ExecutionLayer] {msg}")

    def _ensure_agent(self) -> Any:
        """
        Build and cache the pandas agent executor on first execution use.

        This keeps non-executing paths free of agent-stack imports.
        """
        if self._agent_backend != "classic":
            return None
        if self._agent_executor is None:
            self._debug("building classic agent executor")
            self._agent_executor = self._build_agent()
        return self._agent_executor

    def _build_agent(self) -> Any:
        """
        Construct the ReAct pandas DataFrame agent with the resilient parser.

        See CLAUDE.md §ExecutionLayer._build_agent for construction details.
        Reference: chat/playground/playground.py ~line 750.
        """
        if self._agent_backend == "safe":
            self._debug("safe backend selected; skipping classic agent construction")
            return None

        self._debug("importing langchain_classic and langchain_experimental modules")
        from langchain_classic.agents import AgentExecutor, create_react_agent
        from langchain_experimental.tools import PythonAstREPLTool

        try:
            from langchain_experimental.agents.agent_toolkits.pandas.base import _get_prompt
        except ImportError:
            _get_prompt = None

        self._debug("constructing PythonAstREPLTool")
        tool = PythonAstREPLTool(locals={"df": self._df})
        prefix = self._build_prefix(self._df)

        if _get_prompt is not None:
            try:
                prompt = _get_prompt(self._df, prefix=prefix)
            except TypeError:
                prompt = _get_prompt(self._df)
        else:
            from langchain_core.prompts import PromptTemplate
            prompt = PromptTemplate.from_template(
                prefix
                + "\n\n{tools}\n\nUse the following format:\n"
                "Question: the input question\n"
                "Thought: think step by step\n"
                "Action: the action to take, should be one of [{tool_names}]\n"
                "Action Input: the input to the action\n"
                "Observation: the result of the action\n"
                "... (this Thought/Action/Action Input/Observation can repeat)\n"
                "Thought: I now know the final answer\n"
                "Final Answer: the final answer\n\n"
                "Begin!\n\nQuestion: {input}\n{agent_scratchpad}"
            )

        self._debug("creating ReAct agent")
        react_agent = create_react_agent(self._client.llm, [tool], prompt)
        try:
            react_agent.output_parser = ResilientReActOutputParser()
        except Exception:
            pass

        self._debug("wrapping ReAct agent in AgentExecutor")
        return AgentExecutor(
            agent=react_agent,
            tools=[tool],
            max_iterations=6,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
            verbose=False,
        )

    def _build_prefix(self, df: pd.DataFrame) -> str:
        """
        Build the agent system prompt prefix injected into the ReAct template.
        """
        col_descriptions = ", ".join(
            f"{c} ({df[c].dtype})" for c in df.columns
        )
        return (
            "You are a data analyst working with a pandas DataFrame named `df`.\n"
            f"Columns: {col_descriptions}\n"
            f"Total rows: {len(df)}\n\n"
            "IMPORTANT:\n"
            "- The DataFrame is already loaded in memory as `df`.\n"
            "- Never load files from disk. Work only with `df`.\n"
            "- Use the python_repl_ast tool to write and execute Python code.\n"
            "- Do NOT import external libraries not already available.\n"
            "- Return only the final computed value, not intermediate steps.\n"
            "- Never modify `df` in-place.\n"
            "- For ranking/argmax operations, always set `result` as a dict containing\n"
            "  the identifier key and the metric key separately\n"
            "  Never return a bare numeric ID — it is indistinguishable from a measurement value.\n"
        )

    def guardrail(self, query: str) -> tuple[bool, str]:
        """
        Check whether the query is feasible given the available schema.

        Args:
            query: The (possibly rewritten) query to check.

        Returns:
            (proceed, reason)
            proceed == True  → query is feasible
            proceed == False → reason explains the rejection

        Implementation:
            1. meta_str = meta_to_str(build_column_metadata(self._df))
            2. system = GUARDRAIL_PROMPT.format(column_metadata=meta_str)
            3. Build chain with formatted system prompt + query as human message
            4. Invoke (stage="guardrail")
            5. Parse: starts with "PROCEED" → (True, "")
                      starts with "REJECT"  → (False, text after "REJECT: ")
        """
        meta_str = meta_to_str(build_column_metadata(self._df))
        system_prompt = GUARDRAIL_PROMPT.format(column_metadata=meta_str)
        chain = (
            ChatPromptTemplate.from_messages(
                [("system", system_prompt), ("human", "{query}")]
            )
            | self._client.llm
            | StrOutputParser()
        )
        response = self._client.invoke_chain(
            chain, {"query": query}, stage="guardrail"
        )
        verdict = response.strip()
        upper = verdict.upper()
        if upper.startswith("PROCEED"):
            return True, ""
        if upper.startswith("REJECT"):
            reason = (
                verdict.split(":", 1)[1].strip()
                if ":" in verdict
                else verdict
            )
            return False, reason
        return True, ""

    def execute_single(self, query: str) -> tuple[str, str, ExecutionDetails]:
        """
        Execute a single query against the pandas DataFrame agent.

        Args:
            query: Concrete, column-grounded query for the agent.

        Returns:
            (raw_answer, trace, details)
            raw_answer — agent's text output
            trace      — full Thought/Action/Observation trace string
            details    — ExecutionDetails with final_code and tries

        Implementation:
            1. handler = ThinkingCaptureHandler()
            2. result = self._agent_executor.invoke(
                   {"input": query},
                   config={"callbacks": [handler]}
               )
            3. raw_answer = result.get("output", "")
            4. trace = handler.get_trace()
            5. final_code, tries = handler.get_execution_details()
            6. return raw_answer, trace, ExecutionDetails(final_code, tries, [])
        """
        if self._agent_backend == "safe":
            self._debug("executing query with safe backend")
            return self._execute_single_safe(query)

        handler = ThinkingCaptureHandler()
        agent_executor = self._ensure_agent()
        try:
            result = agent_executor.invoke(
                {"input": query}, config={"callbacks": [handler]}
            )
            raw_answer = result.get("output", "") if isinstance(result, dict) else str(result)
        except Exception as e:
            raw_answer = f"[ERROR] {e}"
        trace = handler.get_trace()
        final_code, tries = handler.get_execution_details()
        return raw_answer, trace, ExecutionDetails(
            final_code=final_code, tries=tries, attempts=[]
        )

    def _execute_single_safe(self, query: str) -> tuple[str, str, ExecutionDetails]:
        """Run codegen+execution without langchain_classic agent dependencies."""
        attempts: list[dict] = []
        trace_steps: list[str] = []
        last_error = "(none)"
        last_code = ""
        total_safe_exec_s = 0.0

        for i in range(1, AGENT_SAFE_MAX_ATTEMPTS + 1):
            code_text = self._client.invoke_chain(
                self._safe_codegen_chain,
                {
                    "question": query,
                    "column_metadata": meta_to_str(build_column_metadata(self._df)),
                    "last_error": last_error,
                },
                stage=f"safe_codegen_{i}",
            )
            code = self._extract_python_code(code_text)
            last_code = code

            trace_steps.append(f"Thought: Attempt {i}: generate executable pandas code")
            trace_steps.append("Action: python_exec")
            trace_steps.append(f"Action Input: {code}")

            exec_t0 = time.time()
            ok, exec_out = self._run_safe_code(code)
            exec_latency_s = time.time() - exec_t0
            total_safe_exec_s += exec_latency_s
            attempts.append(
                {
                    "attempt": i,
                    "code": code,
                    "ok": ok,
                    "output": exec_out,
                    "exec_latency_s": exec_latency_s,
                }
            )
            trace_steps.append(f"Observation: {exec_out}")

            if ok:
                answer = self._client.invoke_chain(
                    self._safe_answer_chain,
                    {"question": query, "execution_output": exec_out},
                    stage=f"safe_answer_{i}",
                ).strip()
                trace_steps.append(f"Final Answer: {answer}")
                return answer, "\n".join(trace_steps), ExecutionDetails(
                    final_code=code,
                    tries=i,
                    attempts=attempts,
                    safe_execution_s=total_safe_exec_s,
                )

            last_error = exec_out

        error_answer = f"[ERROR] Safe backend failed after {AGENT_SAFE_MAX_ATTEMPTS} attempts: {last_error}"
        trace_steps.append(f"Final Answer: {error_answer}")
        return error_answer, "\n".join(trace_steps), ExecutionDetails(
            final_code=last_code,
            tries=AGENT_SAFE_MAX_ATTEMPTS,
            attempts=attempts,
            safe_execution_s=total_safe_exec_s,
        )

    def _extract_python_code(self, text: str) -> str:
        """Extract python code from raw model output (supports fenced responses)."""
        match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _run_safe_code(self, code: str) -> tuple[bool, str]:
        """
        Execute generated code in a restricted local scope.

        Convention: generated code should assign the final answer to `result`.
        """
        ctx = mp.get_context("spawn")
        output_queue = ctx.Queue(maxsize=1)
        proc = ctx.Process(
            target=_safe_exec_worker,
            args=(code, self._df, output_queue),
            daemon=True,
        )

        try:
            proc.start()
            proc.join(timeout=AGENT_SAFE_CODE_TIMEOUT_S)

            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1.0)
                return (
                    False,
                    (
                        "TimeoutError: Safe code execution exceeded "
                        f"{AGENT_SAFE_CODE_TIMEOUT_S:.1f}s and was terminated"
                    ),
                )

            try:
                ok, out = output_queue.get_nowait()
                return bool(ok), str(out)
            except Empty:
                return (
                    False,
                    (
                        "RuntimeError: Safe execution process exited without output "
                        f"(exit_code={proc.exitcode})"
                    ),
                )
        finally:
            output_queue.close()

    def judge_result(self, question: str, code: str, result: str) -> dict:
        """
        Evaluate whether the agent result correctly answers the original question.

        Args:
            question: Original user question.
            code:     Python code executed by the agent (from ExecutionDetails.final_code).
            result:   Agent's raw answer or synthesised text.

        Returns:
            dict with keys: verdict ("PASS" | "FAIL" | "UNKNOWN"), issue (str), suggestion (str)

        Implementation:
            Build human message: "Question: {question}\n\nCode executed:\n{code}\n\nResult:\n{result}"
            Parse response for "VERDICT: PASS" or "VERDICT: FAIL".
            Also parse ISSUE: and SUGGESTION: lines.
            See CLAUDE.md §ExecutionLayer.judge_result for full parsing logic.
        """
        if not result or "[ERROR]" in str(result):
            return {
                "verdict": "FAIL",
                "issue": "Execution failed or produced an error instead of a valid answer.",
                "suggestion": "Revise the query grounding and rerun with valid dataframe operations.",
            }
        try:
            response = self._client.invoke_chain(
                self._judge_chain,
                {"question": question, "code": code or "(no code captured)", "result": result},
                stage="judge",
            )
        except Exception:
            return {
                "verdict": "UNKNOWN",
                "issue": "Judge invocation failed.",
                "suggestion": "Treat this answer as unverified and review manually.",
            }

        verdict = self._parse_judge_response(response)
        if verdict["verdict"] == "PASS" and not verdict["issue"]:
            verdict["issue"] = "No alignment issues detected against the original question."
        if verdict["verdict"] == "FAIL" and not verdict["issue"]:
            verdict["issue"] = "Answer appears misaligned with the question intent or dataset constraints."
        if verdict["verdict"] == "FAIL" and not verdict["suggestion"]:
            verdict["suggestion"] = "Adjust the computation to match the requested metric and valid schema fields."
        return verdict

    def judge_plan(
        self,
        question: str,
        grounding: str,
        sub_queries: list[str],
        synthesis_hint: str,
    ) -> dict:
        """
        Evaluate whether the Stage-3 plan is adequate before execution.

        Args:
            question: Original user question.
            grounding: Stage-2 raw grounding text.
            sub_queries: Stage-3 generated sub-queries.
            synthesis_hint: Stage-3 synthesis hint.

        Returns:
            dict with keys: verdict ("PASS" | "FAIL" | "UNKNOWN"), issue, suggestion.
        """
        sub_query_text = "\n".join(f"- {sq}" for sq in sub_queries) if sub_queries else "(none)"
        try:
            response = self._client.invoke_chain(
                self._plan_judge_chain,
                {
                    "question": question,
                    "grounding": grounding or "(none)",
                    "sub_queries": sub_query_text,
                    "synthesis_hint": synthesis_hint or "(none)",
                },
                stage="judge_plan",
            )
        except Exception:
            return {
                "verdict": "FAIL",
                "issue": "Plan judge invocation failed.",
                "suggestion": "Regenerate Stage-3 sub-queries to explicitly cover each requested analysis step.",
            }

        verdict = self._parse_judge_response(response)
        if verdict["verdict"] == "UNKNOWN":
            verdict["verdict"] = "FAIL"
        if not verdict["issue"]:
            verdict["issue"] = "Plan quality could not be verified for complete intent coverage."
        if verdict["verdict"] == "FAIL" and not verdict["suggestion"]:
            verdict["suggestion"] = (
                "Regenerate Stage-3 with ordered, executable sub-queries that fully cover the original question intent."
            )
        return verdict

    def _parse_judge_response(self, response: str) -> dict:
        """Parse strict VERDICT/ISSUE/SUGGESTION/CHECKLIST response format used by judge chains."""

        verdict = "UNKNOWN"
        issue = ""
        suggestion = ""
        checklist = []
        in_checklist = False
        for line in response.splitlines():
            stripped = line.strip()
            upper = stripped.upper()
            
            if upper.startswith("CHECKLIST:"):
                in_checklist = True
                continue
            elif in_checklist and (upper.startswith("VERDICT:") or upper.startswith("ISSUE:") or upper.startswith("SUGGESTION:")):
                in_checklist = False
                
            if in_checklist:
                if stripped:
                    checklist.append(stripped)
            elif upper.startswith("VERDICT:"):
                v = stripped.split(":", 1)[1].strip().upper()
                if "PASS" in v:
                    verdict = "PASS"
                elif "FAIL" in v:
                    verdict = "FAIL"
            elif upper.startswith("ISSUE:"):
                issue = stripped.split(":", 1)[1].strip()
            elif upper.startswith("SUGGESTION:"):
                suggestion = stripped.split(":", 1)[1].strip()

        if verdict == "UNKNOWN":
            if "VERDICT: PASS" in response.upper():
                verdict = "PASS"
            elif "VERDICT: FAIL" in response.upper():
                verdict = "FAIL"
        
        result = {"verdict": verdict, "issue": issue, "suggestion": suggestion}
        if checklist:
            result["checklist"] = checklist
        return result

    def explain_alignment(self, question: str, verdict: dict) -> str:
        """
        Build a concise user-facing alignment explanation from judge output.

        Args:
            question: Original user question.
            verdict:  Dict from judge_result().

        Returns:
            One-line explanation suitable for reports.
        """
        status = (verdict.get("verdict") or "UNKNOWN").upper()
        issue = (verdict.get("issue") or "").strip()
        suggestion = (verdict.get("suggestion") or "").strip()

        if status == "PASS":
            return (
                "Judge sanity check: PASS. "
                f"The generated answer is aligned with the intent of: \"{question}\"."
            )
        if status == "FAIL":
            parts = ["Judge sanity check: FAIL."]
            if issue:
                parts.append(f"Issue: {issue}")
            if suggestion:
                parts.append(f"Suggested fix: {suggestion}")
            return " ".join(parts)
        return (
            "Judge sanity check: UNKNOWN. "
            "Alignment could not be confirmed automatically."
        )

    def synthesize(self, question: str, sub_answers: list[str], hint: str) -> str:
        """
        Combine sub-query answers into a single natural-language response.

        Args:
            question:    Original user question.
            sub_answers: List of raw answers from individual sub-query executions.
            hint:        Synthesis hint from Stage 3.

        Returns:
            Concise natural-language string (1–4 sentences).

        Implementation:
            sub_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(sub_answers))
            human = f"Question: {question}\n\nSub-answers:\n{sub_text}\n\nSynthesis guidance: {hint}"
            Invoke synthesizer chain (stage="synthesis").
        """
        sub_text = "\n".join(
            f"{i + 1}. {a}" for i, a in enumerate(sub_answers) if a
        )
        if not sub_text:
            sub_text = "(no sub-answers produced)"
        response = self._client.invoke_chain(
            self._synthesizer_chain,
            {
                "question": question,
                "sub_answers": sub_text,
                "synthesis_hint": hint,
            },
            stage="synthesis",
        )
        return response.strip()

    def reset_agent(self) -> None:
        """
        Reset the DataFrame to its original state and invalidate the agent.

        Call between sub-query executions in Flash-Fusion to prevent
        PythonAstREPLTool state leakage (variables defined in one sub-query
        would otherwise persist into the next).
        """
        self._df = self._original_df.copy()
        self._agent_executor = None
