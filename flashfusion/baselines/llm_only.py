"""
baselines/llm_only.py — LLM-Only baseline (B0).

Feeds a raw 20-row CSV sample of the DataFrame plus the user question
directly to the LLM in a single call. No schema grounding, no code
execution, no guardrail.

Intended use: import run_llm_only and call it from BaselineRunner._run_llm_only().
Alternatively, BaselineRunner can inline the logic — this module exists to keep
each baseline's logic isolated and independently testable.

Expected benchmark behaviour:
  - executed  = False  (no pandas agent runs)
  - rejected  = False  (no guardrail)
  - answer    = LLM's raw text (may be fabricated / hallucinated)
  - stages_run = ["llm_only"]

See CLAUDE.md §_run_llm_only for the full algorithm.
"""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.pipeline.runner import LLMClient, RunResult


_SYSTEM_PROMPT = (
    "You are a data analyst. Answer the question using the dataset sample provided. "
    "Be direct and concise. If you are not certain, make your best estimate."
)

_HUMAN_TEMPLATE = """\
Dataset sample (first 20 rows):
{sample_csv}

Question: {query}

Answer directly and concisely.\
"""


def run_llm_only(query: str, df, client: LLMClient, r: RunResult) -> RunResult:
    """
    Execute the LLM-Only baseline.

    Args:
        query:  Raw natural language query.
        df:     WISDM DataFrame (only first 20 rows are used).
        client: LLMClient instance for this run.
        r:      RunResult to populate (baseline, model, query already set by caller).

    Returns:
        Populated RunResult with answer, executed=False, rejected=False.

    Implementation steps:
        1. sample_csv = df.head(20).to_csv(index=False)
        2. prompt = ChatPromptTemplate.from_messages([
               ("system", _SYSTEM_PROMPT),
               ("human", _HUMAN_TEMPLATE),
           ])
        3. chain = prompt | client.llm | StrOutputParser()
        4. r.answer = client.invoke_chain(
               chain,
               {"sample_csv": sample_csv, "query": query},
               stage="llm_only"
           )
        5. r.executed = False
        6. r.rejected = False
        7. r.stages_run = ["llm_only"]
        8. return r
    """
    sample_csv = df.head(20).to_csv(index=False)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _SYSTEM_PROMPT),
            ("human", _HUMAN_TEMPLATE),
        ]
    )
    chain = prompt | client.llm | StrOutputParser()
    r.answer = client.invoke_chain(
        chain,
        {"sample_csv": sample_csv, "query": query},
        stage="llm_only",
    )
    r.executed = False
    r.rejected = False
    r.stages_run = ["llm_only"]
    return r
