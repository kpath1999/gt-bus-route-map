"""
eval.py
-------
Single script: runs each test query through the Llama 4 Scout 17B pandas dataframe
agent, computes the pandas ground-truth answer, and prints a side-by-side
comparison.

Usage:
    # 0. Environment setup (run once before anything else)
    #    export GROQ_API_KEY="your_groq_api_key_here"
    #    export TAVILY_API_KEY="your_tavily_api_key_here"   # optional — enables Stage 1.5
    #    pip install -r env/requirements.txt

    # 1. Smoke-test the query rewriter in isolation first (no pandas agent calls)
    #    python src/scripts/query_rewrite.py "Was it a bumpy ride?"
    #    python src/scripts/query_rewrite.py  # runs all 4 built-in demo queries
    #    python src/scripts/query_rewrite.py "ISO 2631 discomfort weighting"  # exercises Tavily
    #
    #    A well-formed result has non-empty 'sub_queries', correct 'mappings',
    #    and 'tavily_enriched' populated for any jargon terms.
    #    Fix prompt templates here before burning agent API calls.

    # 2. Run the full abstract eval pipeline
    #    Single query — fastest iteration loop:
    python eval.py --query "Was it a bumpy ride?"
    python eval.py --query "Were there any dangerous driving moments?"
    python eval.py --query "Give me a summary of this trip."

    #    Full abstract suite (all ABSTRACT_QUERIES from queries.py):
    python eval.py --abstract

    #    Against the 100x enlarged dataset:
    python eval.py --abstract --enlarged

    # 3. Other eval modes
    #    Standard conversational queries:
    python eval.py

    #    Out-of-scope queries (should all be rejected by the guardrail):
    python eval.py --out_of_scope

    #    Point to any CSV:
    python eval.py --csv path/to/file.csv

    # 4. Inspect results
    #    tail -f src/scripts/output/eval_responses.md
    #    open src/scripts/output/eval_responses.md


DESCRIPTION:
i) Inferring analytical intent and query rewriting
When a user submits an ambiguous natural language query (e.g., "tell me where the bus movements were jerkiest"),
the system first resolves the user's intent against the actual schema of the dataset. It uses an LLM-based
query rewriter equipped with pre-computed metadata about the dataset's columns (data types, min/max values, unique
counts). The rewriter maps the loose human vocabulary in the query ti exact, available column names (e.g., mapping
"jerkiest" to the "accel_variance" column). This transforms the ambiguous prompt into a precise, column-grounded query.

ii) Integrated guardrail and data extraction
Focuses on feasibility, rejects queries that require external data (e.g., "what was the weather?") or speculative
derivations that the available sensors cannot support. Once approved, the query is handed to a Pandas DataFrame Agent.
Executes python code against the IoT CSV dataframe.

iii) Natural language contextualization
The raw data returned from the Pandas execution (e.g., a list of coordinates with high variance) is
transformed back into a human-readable format.
GOAL [will take care of this with tavily];
TODO (ignore for now)]: map the raw data points to meaningful real-world anchors, such as campus specific landmarks or timestamps
LLM synthesizes this contextualized data into a natural language response that matches the conversational style 
of the original user query, providing a direct answer without requiring the user to interpret technical coordinates
or statistics.
"""

import os
import sys
import time
import random
import argparse
import warnings
import pandas as pd
from datetime import datetime
from langchain_groq import ChatGroq
from langchain_experimental.agents import create_pandas_dataframe_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.agents import AgentAction, AgentFinish

from queries import (
    TEST_QUERIES, QUERY_INTENT, OUT_OF_SCOPE,
    GROUND_TRUTH_FNS, GT_OUT_OF_SCOPE,
    ABSTRACT_QUERIES, ABSTRACT_GROUND_TRUTH_FNS,
)

# --- Configuration ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

BASE_DIR     = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CSV_DEFAULT  = os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data.csv")
CSV_ENLARGED = os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data_enlarged.csv")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
LOG_FILE   = os.path.join(OUTPUT_DIR, "eval_responses.md")

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ====================================================
# Schema-aware query rewriter
# Runs BEFORE the guardrail:
# (i) takes an ambiguous natural language query
# (ii) inspects the actual column names + dtypes + stats
# (iii) rewrites it into a precise, column-grounded question
# ====================================================

def build_column_metadata(df):
    """
    Pre-compute once at load time. Returns a dict of column ->
    {dtype, min, max, n_unique, sample_values}.
    This is O(N) but done once; subsequent LLM calls use cached metadata.
    """
    meta = {}
    for col in df.columns:
        series = df[col].dropna()
        entry = {"dtype": str(df[col].dtype), "n_unique": series.nunique()}
        if pd.api.types.is_numeric_dtype(series):
            entry.update({"min": series.min(), "max": series.max(), "mean": round(series.mean(), 4)})
        else:
            entry["sample_values"] = series.head(3).tolist()
        meta[col] = entry
    return meta


# ====================================================
# Abstract query decomposer
# Sits BEFORE the rewriter for open-ended queries:
# (i)   identifies abstract concepts in the user query
# (ii)  decomposes them into concrete, schema-grounded sub-questions
# (iii) the sub-questions are each passed to the pandas agent
# (iv)  sub-answers are synthesized into a final NL response
# ====================================================

DECOMPOSER_SYSTEM = """
You are an expert query decomposer for IoT sensor data analysis.

You receive:
* An abstract, open-ended user query about sensor/vehicle/route data.
* The dataset's column metadata (names, types, statistics):
{column_metadata}

Your job is to break the abstract query into 2-4 concrete, column-grounded
sub-questions that, when answered together, let you compose a full answer
to the original query.

Step-by-step:
1. Identify the abstract concepts the user is asking about (e.g., "bumpy" → 
   high accel_variance, high z-axis p99 values).
2. For each concept, formulate a precise sub-question that references exact
   column names and specific operations (mean, max, count, filter, group-by).
3. If the query asks for a qualitative judgment (e.g., "smooth?"), include a
   sub-question that computes a relevant threshold or percentile so the
   synthesizer can make that judgment.
4. Do NOT include sub-questions that require columns not in the dataset.

Output format (strict):
CONCEPTS: <comma-separated abstract concepts identified>
SUB_Q1: <first concrete sub-question>
SUB_Q2: <second concrete sub-question>
[SUB_Q3: <optional third>]
[SUB_Q4: <optional fourth>]
SYNTHESIS_HINT: <one-line guidance on how to combine the sub-answers>
"""


REWRITER_SYSTEM = """
You are an expert semantic query rewriter for a structured tabular IoT sensor dataset.

You are given:
* A user query written in natural language.
* Available dataset columns and their metadata (names, descriptions, statistics):
{column_metadata}

Your task is to transform the user query into a schema-aligned, unambiguous version.

Step-by-step instructions:
1. Identify every distinct semantic concept in the user query. Concepts fall into two categories:
   - DATA concepts: entities, measurements, or attributes that must map to a column (e.g., "temperature", "device ID", "latitude").
   - OPERATION concepts: mathematical or statistical functions applied to columns (e.g., standard deviation, mean, variance,
     sum, count, max, min, percentile, correlation). Operations are NEVER column names and must NEVER be added to UNMAPPABLE.
2. Map each DATA concept to the closest semantically matching column. Use column descriptions and statistics to guide mapping.
Prefer exact semantic matches, standardized naming conventions, and units consistency (e.g., °C vs °F). If multiple columns
are similar, choose the most specific and least ambiguous match.
3. In terms of disambiguation, replace all vague or generic terms with exact column names. Preserve logical structure
(filters, aggregations, time constraints). Do NOT invent new columns. Do NOT assume mappings without reasonable semantic
similarity.
4. If a DATA concept has no plausible mapping to any available column, do NOT remove it silently. Add it to the UNMAPPABLE
list. OPERATION concepts (e.g., standard deviation, mean, variance, count) must NEVER appear in UNMAPPABLE — they are
always valid as long as the column they operate on is present.

Output rules:
REWRITTEN: <new precise query using exact column names>
UNMAPPABLE: <comma-separated list of unmappable concepts, or NONE>
"""


# ====================================================
# Callback handler to capture agent reasoning trace
# ====================================================

class ThinkingCaptureHandler(BaseCallbackHandler):
    """Collects the agent's Thought / Action / Observation steps into a list."""

    def __init__(self):
        self.steps: list[str] = []

    def on_agent_action(self, action: AgentAction, **kwargs) -> None:
        self.steps.append(f"Thought + Action: {action.log.strip()}")
        self.steps.append(f"Action Input: {action.tool_input}")

    def on_tool_end(self, output: str, **kwargs) -> None:
        self.steps.append(f"Observation: {str(output).strip()}")

    def on_agent_finish(self, finish: AgentFinish, **kwargs) -> None:
        self.steps.append(f"Final Answer: {finish.return_values.get('output', '').strip()}")

    def get_trace(self) -> str:
        return "\n".join(self.steps) if self.steps else "(no steps captured)"

def build_schema_summary(dataframe):
    """Auto-derive column summary from any dataframe — no hardcoding.
    Note: kept alongside build_column_metadata because the guardrail prompt
    needs a concise dtype+example view, while the rewriter needs richer stats."""
    lines = []
    for col in dataframe.columns:
        dtype = str(dataframe[col].dtype)
        sample = dataframe[col].dropna().iloc[0] if not dataframe[col].dropna().empty else "N/A"
        lines.append(f"- '{col}' (dtype: {dtype}, e.g. {sample})")
    return "\n".join(lines)


def init_llm_components(df):
    if not GROQ_API_KEY:
        raise ValueError("Missing GROQ_API_KEY. Set it before running eval.py")

    llm = ChatGroq(
        groq_api_key=GROQ_API_KEY,
        model_name="meta-llama/llama-4-scout-17b-16e-instruct",
        temperature=0.0,
    )

    # Pre-compute column metadata (min, max, unique counts) once at load time;
    # fed as context to the rewriter so it can map ambiguous terms to real columns.
    column_metadata = build_column_metadata(df)

    # Build the decomposer chain — breaks abstract queries into concrete sub-questions
    decomposer_chain = (
        ChatPromptTemplate.from_messages([
            ("system", DECOMPOSER_SYSTEM),
            ("human", "User query: {query}"),
        ])
        | llm
        | StrOutputParser()
    )

    # Build the rewriter chain (needs the live llm instance)
    rewriter_chain = (
        ChatPromptTemplate.from_messages([
            ("system", REWRITER_SYSTEM),
            ("human", "Original query: {query}"),
        ])
        | llm
        | StrOutputParser()
    )

    def rewrite_query(user_query):
        """
        Schema-aware query rewriter.
        Returns (rewritten_query: str, unmappable: list[str])
        """
        meta_str = "\n".join(
            f"- '{col}': {info}" for col, info in column_metadata.items()
        )
        response = rewriter_chain.invoke({
            "query": user_query,
            "column_metadata": meta_str,
        }).strip()

        rewritten = user_query      # fallback
        unmappable = []

        for line in response.splitlines():
            if line.startswith("REWRITTEN:"):
                rewritten = line.split("REWRITTEN:", 1)[1].strip()
            elif line.startswith("UNMAPPABLE:"):
                raw = line.split("UNMAPPABLE:", 1)[1].strip()
                if raw.upper() != "NONE":
                    unmappable = [c.strip() for c in raw.split(",")]

        return rewritten, unmappable

    # NL response contextualizer — converts raw agent output into a
    # human-readable natural language answer
    contextualizer_chain = (
        ChatPromptTemplate.from_messages([
            ("system",
             "You are a data analyst assistant. Given the user's original question "
             "and the raw analytical result, produce a clear, concise natural language "
             "response that directly answers the question. Do NOT include code or "
             "technical details — just the answer in plain English."),
            ("human",
             "Question: {question}\n\nRaw result: {raw_answer}"),
        ])
        | llm
        | StrOutputParser()
    )

    col_list     = ", ".join(df.columns)
    sample_rows  = df.head(2).to_dict(orient="records")
    total_rows   = len(df)

    """
    TODO [IGNORE] -- one idea...
    RAG for schema: creating a tiny vector store of column descriptions
    we search for relevant columns first; if none match the query keywords, REJECT
    but i feel build_schema_summary(df) doesn't take all that long anyway
    """

    schema = build_schema_summary(df)
    guardrail_context = f"""
You are a schema gatekeeper for a tabular dataset that will be queried by a Python Pandas agent.

Dataset columns (and dtypes/examples):
{schema}

Decision policy:
1. Return PROCEED if the query can be answered using the provided columns. The Pandas agent CAN perform operations like:
   - Filtering by exact values, thresholds, or text matches.
   - Mathematical and statistical calculations (e.g., standard deviation, variance, mean, max).
   - Grouping, counting, and sorting (e.g., finding the top 5 most frequent items).
2. Return REJECT ONLY if the query strictly requires:
   - Entirely missing base metrics/columns (e.g., asking for temperature when only acceleration exists).
   - External data sources or internet access.
   - Predictive forecasting or machine learning models.

Output contract (MUST follow exactly, single line):
- If answerable: PROCEED
- If not answerable: REJECT: <short reason>
"""

    guardrail_chain = (
        ChatPromptTemplate.from_messages(
            [
                ("system", guardrail_context),
                ("human", "Query: {query}"),
            ]
        )
        | llm
        | StrOutputParser()
    )

    # Escape curly braces in sample_rows to prevent LangChain template variable errors
    sample_rows_str = str(sample_rows).replace("{", "{{").replace("}", "}}")
    
    prefix_prompt = (
        f"You are a data analyst. The dataset has columns: {col_list}.\n"
        f"Total rows: {total_rows}.\n\n"
        "TOOL USAGE:\n"
        "- To execute Python: Action: python_repl_ast\n"
        "- Then provide Action Input with valid pandas code\n"
        "- Example: Action Input: df['column'].sum()\n\n"
        "WORKFLOW:\n"
        "1. Think about what calculation is needed\n"
        "2. Execute ONE python_repl_ast action with the necessary pandas code\n"
        "3. Return Final Answer: <result>\n\n"
        "Avoid multiple actions when one suffices. Be direct and concise.\n"
    )

    agent = create_pandas_dataframe_agent(
        llm,
        df,
        verbose=False,  # disable verbose to reduce I/O overhead
        allow_dangerous_code=True,
        agent_type="zero-shot-react-description",
        prefix=prefix_prompt,
        max_iterations=3,  # reduce from 5 to 3 for faster execution
        agent_executor_kwargs={
            "handle_parsing_errors": True,
        },
    )

    def ask_agent(user_query):
        t0 = time.time()

        # stage 0: rewrite query -> column-grounded version
        rewritten_query, unmappable = rewrite_query(user_query)

        # If the rewriter found unmappable concepts, reject early
        if unmappable:
            reason = f"Query requires concepts not present in dataset: {', '.join(unmappable)}"
            latency = time.time() - t0
            return (
                f"[REJECTED] {reason}",
                f"Unmappable concepts detected: {unmappable}",
                latency,
            )

        decision = guardrail_chain.invoke({"query": rewritten_query}).strip()

        if decision != "PROCEED":
            latency = time.time() - t0
            if decision.startswith("REJECT:"):
                reason = decision.split("REJECT:", 1)[1].strip()
                return f"[REJECTED] {reason}", f"Guardrail decision: {decision}", latency
            return f"[REJECTED] {decision}", f"Guardrail decision: {decision}", latency

        # Pass rewritten query — the rewriter already resolved typos / ambiguous
        # column references (e.g. 'accl variance' → 'accel_variance')
        handler = ThinkingCaptureHandler()
        try:
            result = agent.invoke(rewritten_query, config={"callbacks": [handler]})
            raw_answer = result["output"]

            # Contextualize: convert raw agent output to natural language
            nl_answer = contextualizer_chain.invoke({
                "question": user_query,
                "raw_answer": raw_answer,
            }).strip()

            latency = time.time() - t0
            return nl_answer, handler.get_trace(), latency
        except Exception as e:
            latency = time.time() - t0
            return f"[ERROR] {e}", handler.get_trace(), latency

    # ====================================================
    # Abstract query handler: decompose → execute sub-queries → synthesize
    # ====================================================

    def decompose_query(user_query):
        """
        Takes an abstract query, returns structured decomposition:
        (concepts: list[str], sub_queries: list[str], synthesis_hint: str)
        """
        meta_str = "\n".join(
            f"- '{col}': {info}" for col, info in column_metadata.items()
        )
        response = decomposer_chain.invoke({
            "query": user_query,
            "column_metadata": meta_str,
        }).strip()

        concepts = []
        sub_queries = []
        synthesis_hint = ""

        for line in response.splitlines():
            line = line.strip()
            if line.startswith("CONCEPTS:"):
                concepts = [c.strip() for c in line.split("CONCEPTS:", 1)[1].split(",")]
            elif line.startswith("SUB_Q"):
                # SUB_Q1: ..., SUB_Q2: ..., etc.
                parts = line.split(":", 1)
                if len(parts) == 2:
                    sub_queries.append(parts[1].strip())
            elif line.startswith("SYNTHESIS_HINT:"):
                synthesis_hint = line.split("SYNTHESIS_HINT:", 1)[1].strip()

        return concepts, sub_queries, synthesis_hint

    # Synthesis chain — combines sub-answers into a final NL response
    synthesizer_chain = (
        ChatPromptTemplate.from_messages([
            ("system",
             "You are a data analyst assistant. The user asked an open-ended "
             "question about sensor data. You have been given the sub-answers "
             "to several concrete analytical questions that together address "
             "the user's query.\n\n"
             "Your task: synthesize these sub-answers into a single, coherent, "
             "natural-language response that directly answers the original "
             "question. Be qualitative and conversational — the user is not "
             "a data scientist. Include specific numbers only when they help "
             "illustrate the point. Do NOT mention sub-questions, column names, "
             "or technical implementation details."),
            ("human",
             "Original question: {question}\n\n"
             "Sub-answers:\n{sub_answers}\n\n"
             "Synthesis guidance: {synthesis_hint}"),
        ])
        | llm
        | StrOutputParser()
    )

    def ask_agent_abstract(user_query):
        """
        Full pipeline for abstract / open-ended queries:
          1. Decompose into concrete sub-questions
          2. Rewrite + guardrail each sub-question
          3. Execute each sub-question via the pandas agent
          4. Synthesize sub-answers into a final NL response
        """
        t0 = time.time()
        all_traces = []

        # Stage 1: decompose
        concepts, sub_queries, synthesis_hint = decompose_query(user_query)
        all_traces.append(f"[Decomposer] Concepts: {concepts}")
        all_traces.append(f"[Decomposer] Sub-queries ({len(sub_queries)}):")
        for j, sq in enumerate(sub_queries, 1):
            all_traces.append(f"  SQ{j}: {sq}")

        if not sub_queries:
            # Fallback: treat as a regular query if decomposition fails
            all_traces.append("[Decomposer] No sub-queries produced — falling back to direct agent.")
            return ask_agent(user_query)

        # Stage 2 & 3: rewrite, guardrail, execute each sub-query
        sub_answers = []
        for j, sq in enumerate(sub_queries, 1):
            all_traces.append(f"\n--- Sub-query {j} ---")

            # Rewrite as usual
            rewritten_sq, unmappable = rewrite_query(sq)
            all_traces.append(f"[Rewriter] {sq} → {rewritten_sq}")

            if unmappable:
                sub_answers.append(f"SQ{j}: Could not answer — requires data not in dataset: {', '.join(unmappable)}")
                all_traces.append(f"[Rewriter] Unmappable: {unmappable}")
                continue

            decision = guardrail_chain.invoke({"query": rewritten_sq}).strip()
            if decision != "PROCEED":
                sub_answers.append(f"SQ{j}: Rejected by guardrail — {decision}")
                all_traces.append(f"[Guardrail] {decision}")
                continue

            handler = ThinkingCaptureHandler()
            try:
                result = agent.invoke(rewritten_sq, config={"callbacks": [handler]})
                raw_answer = result["output"]
                sub_answers.append(f"SQ{j} ({sq}): {raw_answer}")
                all_traces.append(handler.get_trace())
            except Exception as e:
                sub_answers.append(f"SQ{j}: Error — {e}")
                all_traces.append(f"[Error] {e}")

            # Rate-limit between sub-queries
            if j < len(sub_queries):
                time.sleep(1.5)

        # Stage 4: synthesize
        sub_answers_str = "\n".join(sub_answers)
        all_traces.append(f"\n[Synthesizer] Input sub-answers:\n{sub_answers_str}")

        try:
            final_answer = synthesizer_chain.invoke({
                "question": user_query,
                "sub_answers": sub_answers_str,
                "synthesis_hint": synthesis_hint or "Combine the sub-answers into a coherent response.",
            }).strip()
        except Exception as e:
            final_answer = f"[SYNTHESIS ERROR] {e}"

        latency = time.time() - t0
        return final_answer, "\n".join(all_traces), latency

    # think about it...this is our naive baseline (RAG, SQL, VocalDB)
    # IDEA: based on the query, the data is organized in a specific manner and then fed to the pandas agent
    return ask_agent, ask_agent_abstract

# ====================================================
# Logging
# ====================================================

def log_results(results, csv_path):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n# Eval Run [{timestamp}] — {csv_path}\n\n")
        for i, (query, gt, llm_ans, thinking, latency) in enumerate(results, 1):
            f.write(f"## Q{i}: {query}\n\n")
            f.write(f"| | Answer |\n|---|---|\n")
            f.write(f"| **Ground Truth** | {gt} |\n")
            f.write(f"| **LLM** | {llm_ans} |\n")
            f.write(f"| **Latency** | {latency:.2f}s |\n\n")
            f.write(f"**Agent Reasoning:**\n```\n{thinking}\n```\n\n")
            f.write("---\n")

        # Summary stats
        latencies = [r[4] for r in results]
        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        f.write(f"\n**Latency summary:** avg={avg_lat:.2f}s, "
                f"min={min(latencies):.2f}s, max={max(latencies):.2f}s\n")
    print(f"\nResults logged → {LOG_FILE}")


# ====================================================
# Main
# ====================================================

def run(csv_path, out_of_scope=False, abstract=False, custom_query=None):
    print(f"\nLoading: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"Rows: {len(df):,}  Columns: {len(df.columns)}")

    ask_agent, ask_agent_abstract = init_llm_components(df)

    results = []

    # Single custom query mode — skip predefined lists entirely
    if custom_query:
        queries = [custom_query]
        ground_truths = ["N/A (custom query)"]
        print(f"\nRunning custom query...")
    elif out_of_scope:
        queries = OUT_OF_SCOPE
        ground_truths = GT_OUT_OF_SCOPE
        print("\nEvaluating OUT-OF-SCOPE queries (should be rejected)...")
    elif abstract:
        queries = [q for q, _ in ABSTRACT_QUERIES]
        ground_truths = [gt_fn(df) for gt_fn in ABSTRACT_GROUND_TRUTH_FNS]
        print("\nEvaluating ABSTRACT / open-ended queries (decompose → execute → synthesize)...")
    else:
        # User requested to test rewriter with conversational queries instead of standard ones
        queries = QUERY_INTENT      # you can swap this out with TEST_QUERIES instead
        ground_truths = [gt_fn(df) for gt_fn in GROUND_TRUTH_FNS]

    # Pair queries with ground truths and shuffle (skip shuffle for single custom query)
    if len(queries) > 1:
        combined = list(zip(queries, ground_truths))
        random.shuffle(combined)
        queries, ground_truths = zip(*combined)

    # Choose the right handler
    handler_fn = ask_agent_abstract if abstract else ask_agent

    for i, (query, gt_answer) in enumerate(zip(queries, ground_truths), 1):
        print(f"\n{'─' * 60}")
        print(f"Q{i}: {query}")
        print(f"{'─' * 60}")

        llm_answer, thinking, latency = handler_fn(query)

        print(f"  GROUND TRUTH : {gt_answer}")
        print(f"  LLM ANSWER   : {llm_answer}")
        print(f"  LATENCY      : {latency:.2f}s")

        results.append((query, gt_answer, llm_answer, thinking, latency))

        # Add a delay between queries to avoid Groq rate limits (HTTP 429)
        if i < len(queries):
            time.sleep(2.0)

    log_results(results, csv_path)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run eval: LLM agent vs pandas ground truth.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enlarged", action="store_true",
                       help="Use the 100x enlarged dataset.")
    group.add_argument("--csv", type=str, default=None,
                       help="Path to a custom CSV file.")
    
    group.add_argument("--out_of_scope", action="store_true",
                       help="Evaluate out-of-scope queries that should be rejected.")
    group.add_argument("--abstract", action="store_true",
                       help="Evaluate abstract / open-ended queries using decompose-execute-synthesize.")

    parser.add_argument("--query", type=str, default=None,
                        help="Run a single custom natural language query against the dataset.")

    args = parser.parse_args()

    if args.csv:
        csv_path = args.csv
    elif args.enlarged:
        csv_path = CSV_ENLARGED
    else:
        csv_path = CSV_DEFAULT

    run(
        csv_path,
        out_of_scope=getattr(args, 'out_of_scope', False),
        abstract=getattr(args, 'abstract', False),
        custom_query=args.query,
    )
