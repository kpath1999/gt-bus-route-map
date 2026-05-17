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

import re
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
    SYNTHESIS_PROMPT,
)
from flashfusion.config import (
    RESILIENT_PARSER_MAX_IDENTICAL,
    RESILIENT_PARSER_MAX_FAILURES,
)

if TYPE_CHECKING:
    from flashfusion.pipeline.runner import LLMClient

# ---------------------------------------------------------------------------
# ExecutionDetails
# ---------------------------------------------------------------------------

@dataclass
class ExecutionDetails:
    """Captures per-run agent execution metrics."""
    final_code: str = ""
    tries: int = 0
    attempts: list[dict] = field(default_factory=list)


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
    """

    def __init__(self, df: pd.DataFrame, client: "LLMClient") -> None:
        """
        Initialise the execution layer with a DataFrame and LLM client.

        Args:
            df:     The WISDM DataFrame (enriched with derived features if adapter applied).
            client: LLMClient wrapping ChatGroq.

        Sets up:
            self._original_df — immutable reference copy for reset_agent()
            self._df          — working copy passed to the pandas agent
            self._agent_executor — AgentExecutor with ResilientReActOutputParser
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

        self._agent_executor = self._build_agent()

    def _build_agent(self) -> Any:
        """
        Construct the ReAct pandas DataFrame agent with the resilient parser.

        See CLAUDE.md §ExecutionLayer._build_agent for construction details.
        Reference: chat/playground/playground.py ~line 750.
        """
        from langchain_classic.agents import AgentExecutor, create_react_agent
        from langchain_experimental.tools import PythonAstREPLTool

        try:
            from langchain_experimental.agents.agent_toolkits.pandas.base import _get_prompt
        except ImportError:
            _get_prompt = None

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

        react_agent = create_react_agent(self._client.llm, [tool], prompt)
        try:
            react_agent.output_parser = ResilientReActOutputParser()
        except Exception:
            pass

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
        handler = ThinkingCaptureHandler()
        try:
            result = self._agent_executor.invoke(
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
            return {"verdict": "FAIL", "issue": "Execution error", "suggestion": ""}
        try:
            response = self._client.invoke_chain(
                self._judge_chain,
                {"question": question, "code": code or "(no code captured)", "result": result},
                stage="judge",
            )
        except Exception:
            return {"verdict": "UNKNOWN", "issue": "", "suggestion": ""}

        verdict = "UNKNOWN"
        issue = ""
        suggestion = ""
        for line in response.splitlines():
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("VERDICT:"):
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

        return {"verdict": verdict, "issue": issue, "suggestion": suggestion}

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
        Reset the DataFrame to its original state and rebuild the agent.

        Call between sub-query executions in Flash-Fusion to prevent
        PythonAstREPLTool state leakage (variables defined in one sub-query
        would otherwise persist into the next).
        """
        self._df = self._original_df.copy()
        self._agent_executor = self._build_agent()
