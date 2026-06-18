"""
query_rewrite.py

Reviewer comment: how does Flash-Fusion construct a structured and high-signal prompt?

This module implements a multi-stage query rewriting pipeline that converts
abstract, open-ended user queries into structured, schema-grounded prompts
suitable for execution against a tabular IoT sensor dataset.

The pipeline has four stages, inspired by WellMax (SenSys'25) and TaskSense:

  Stage 1   — Concept Extraction
    Identify every abstract concept in the user query and classify it as
    either a DATA concept (must map to a dataset column) or a REASONING
    concept (a qualitative judgment the system must derive, e.g. "bumpy").

  Stage 1.5 — Tavily Enrichment  [optional — requires TAVILY_API_KEY]
    For each REASONING concept, an LLM novelty classifier decides whether
    it is COMMON (everyday language the LLM handles reliably, e.g. "bumpy")
    or JARGON (domain-specific term, technical standard, or clinical concept
    that needs a precise definition, e.g. "ISO 2631 discomfort weighting",
    "ST depression", "RMS jerk").  JARGON concepts are searched on the web
    via Tavily and the retrieved definitions are injected into Stage 2's
    grounding prompt so the LLM can produce an accurate column proxy.
    If TAVILY_API_KEY is not set, this stage is silently skipped.

  Stage 2   — Schema Grounding
    Map each DATA concept to the closest column(s) using pre-computed column
    metadata.  For REASONING concepts, determine which column(s) and
    aggregation(s) can serve as a proxy (e.g., "bumpy" → high accel_variance,
    high accel_stats_z_p99).  If Stage 1.5 ran, the fetched definitions are
    included so the proxy can be precise (e.g., ISO 2631 → weighted RMS of
    z-axis acceleration in the 1–80 Hz band → accel_stats_z_p90/p99).

  Stage 3   — Sub-query Generation
    Decompose the original query into 2-4 concrete, column-grounded
    sub-questions.  Each sub-question is a single analytical operation
    (filter, aggregate, group-by, correlation, etc.) that the downstream
    Pandas agent can execute directly.

The output is a structured dictionary that eval.py's abstract handler can
consume directly.
"""

# Agent + Tavily: narrowing in on definitions for abstract/jargon words (Stage 1.5).
# WellMax / TaskSense: getting every abstract query to be rewritten into a structured, high-signal prompt.

"""
Some ideas provided by the WellMax SenSys'25 paper on query rewriting:

The Query Rewriting Module (QRM) is designed to leverage the processed sensing data
to refine user queries so that the LLM agent's responses are both contextually aware
and closely aligned with the user's current physical and mental state. The QRM
operates through a process inspired by the Chain-of-Thought (CoT) prompting
technique. By guiding the model through intermediate reasoning steps, akin to human
problem-solving processes, this technique allows the LLM to break down complex tasks
into manageable sub-tasks, leading to more accurate and contextually relevant
responses. The inference process is applied as follows:

(i) Analyze User Data and Identify Key Factors: In the first step, WellMax prompts
GPT-4o to thoroughly examine the user's profile data, including key indicators such
as physical activity levels, sleep quality metrics, and stress levels. By analyzing
this data, WellMax gathers and synthesizes critical context, forming a comprehensive
understanding of the user's current state.

(ii) Determine the Refinement Goals: Once the user's profile data has been analyzed,
WellMax determines the primary query refinement goal. This step aligns the query
refinement with the user's specific context and needs. WellMax pre-defines a set of
potential goals based on common user needs and psychological principles: relieving
stress, promoting physical wellness, improving productivity, enhancing mental clarity,
and providing emotional support.

(iii) Rewrite the Query: Inspired by Conversational Query Rewrite, with the
refinement goal identified, WellMax proceeds to rewrite the user's original query.
This transforms the initial query into one that is more contextually appropriate and
more likely to yield a response that directly addresses the user's needs.

A running example -- Alice engages her personal agent with the query: "How should I
prepare for my presentation tomorrow?". The agent extracts relevant sensing data
(heart rate, physical activity, sleep patterns) from her wearable device. The data
reveals elevated stress and reduced sleep. The QRM rewrites Alice's query to:
"Considering my moderate activity levels, elevated stress, and low sleep quality,
how should I prepare for my presentation to ensure it goes smoothly and I can manage
my stress effectively?".
"""

"""
DATA PATHS:

1: /Users/kausar/Documents/flash-fusion/data/bus
--- contains: processed raw README.md snapshots

2: /Users/kausar/Documents/flash-fusion/data/Agent_dataset
-- contains: ECG.0 IMU

3: /Users/kausar/Documents/visig
-- contains: data			data_csv		signal_codebooks	vsigtocsv.py
"""

import os
import pandas as pd
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

try:
    from langchain_tavily import TavilySearch
    _TAVILY_AVAILABLE = True
except ImportError:
    _TAVILY_AVAILABLE = False

# ====================================================
# Column metadata builder (shared with eval.py)
# ====================================================

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


# ====================================================
# Stage 1.5: Tavily Enrichment
# ====================================================
# First, an LLM novelty classifier decides which REASONING concepts are
# JARGON (technical standards, clinical/scientific terms) vs. COMMON
# (everyday words the LLM handles confidently without web lookup).
# Only JARGON concepts trigger a Tavily search.
# If TAVILY_API_KEY is not set this entire stage is a no-op.

NOVELTY_CLASSIFIER_PROMPT = """
You are a concept novelty classifier for IoT sensor and medical data analysis.

For each REASONING concept below, decide whether it is:
  COMMON — everyday language that any general-purpose LLM reliably understands
           (e.g., "bumpy", "smooth", "dangerous", "comfortable", "rough")
  JARGON — a technical standard, clinical term, engineering specification, or
           domain-specific acronym that requires a precise external definition
           to be accurately mapped to sensor columns
           (e.g., "ISO 2631 discomfort weighting", "ST depression", "QRS complex",
           "RMS jerk", "Wk weighting", "VDV", "HRV", "SpO2 desaturation")

Output one line per concept, exactly:
<concept>: COMMON
<concept>: JARGON
"""


# ====================================================
# Stage 1: Concept Extraction
# ====================================================
# Identifies abstract concepts in the query and classifies them as
# DATA (must map to a column) or REASONING (qualitative, needs a proxy).

CONCEPT_EXTRACTION_PROMPT = """
You are a concept extraction specialist for IoT sensor data queries.

Given a user's natural language query, identify every distinct semantic concept
and classify each as one of:
  DATA     — refers to a measurable quantity that should map directly to a dataset
             column (e.g., "acceleration", "location", "time", "heart rate")
  REASONING — a qualitative, interpretive, or standards-based idea that requires
             deriving a proxy from one or more columns. This includes:
               • qualitative judgments  (e.g., "bumpy", "dangerous", "comfortable")
               • named technical standards or weighting methods
                 (e.g., "ISO 2631 discomfort weighting", "Wk weighting", "VDV")
               • clinical or engineering scoring criteria
                 (e.g., "ST depression", "RMS jerk", "SpO2 desaturation")
             These concepts define *how to interpret* data, not a column to read
             directly — even when they sound technical or measurement-like.

Output format (strict):
DATA: <comma-separated data concepts, or NONE>
REASONING: <comma-separated reasoning concepts, or NONE>
"""

# ====================================================
# Stage 2: Schema Grounding
# ====================================================
# Maps DATA concepts to columns, and REASONING concepts to column+operation
# proxies using the column metadata.

SCHEMA_GROUNDING_PROMPT = """
You are a schema grounding specialist for IoT sensor data.

You receive:
* Extracted concepts (DATA and REASONING) from a user query.
* Available dataset columns and their metadata:
{column_metadata}
{enriched_definitions}

Your task:
1. For each DATA concept, find the best matching column(s). Output one mapping per line.
2. For each REASONING concept, define a concrete proxy — which column(s) and what
   operation(s) approximate that concept. For example:
   - "bumpy" → high values of accel_variance; spikes in accel_stats_z_p99
   - "dangerous" → accel_stats_x_p99 or accel_stats_y_p99 exceeding mean + 2*std
   - "smooth" → low accel_variance; small range in accel_stats_z_p99
   Where external definitions are provided above, use them to construct a
   more precise proxy (e.g., a specific weighting band or clinical threshold).

If a DATA concept cannot map to any column, mark it UNMAPPABLE.

Output format (strict):
MAPPINGS:
  <concept> → <column(s) and operation>
  ...
UNMAPPABLE: <comma-separated unmappable concepts, or NONE>
"""

# ====================================================
# Stage 3: Sub-query Generation
# ====================================================
# Decomposes the original query into 2-4 concrete sub-questions
# grounded in exact column names.

SUBQUERY_GENERATION_PROMPT = """
You are a query decomposition specialist for IoT sensor data analysis.

You receive:
* The user's original abstract query.
* Schema grounding mappings (concept → column/operation).
* Dataset column metadata:
{column_metadata}

Your task:
Decompose the original query into 2-4 concrete, column-grounded sub-questions.
Each sub-question must:
  - Reference exact column names from the dataset.
  - Specify a single analytical operation (filter, aggregate, group-by, compare, correlate).
  - Be independently answerable by a Pandas DataFrame agent.

Also provide a one-line synthesis hint: how should the sub-answers be combined
to produce a final natural-language response to the original query?

Output format (strict):
SUB_Q1: <first concrete sub-question>
SUB_Q2: <second concrete sub-question>
[SUB_Q3: <optional third>]
[SUB_Q4: <optional fourth>]
SYNTHESIS_HINT: <one-line guidance on combining sub-answers>
"""


# ====================================================
# QueryRewriter class — full 3-stage pipeline
# ====================================================

class QueryRewriter:
    """
    Three-stage abstract→structured query rewriter.

    Usage:
        rewriter = QueryRewriter(df)
        result = rewriter.rewrite("Was it a bumpy ride?")
        # result = {
        #     "original_query": "Was it a bumpy ride?",
        #     "concepts": {"DATA": [...], "REASONING": [...]},
        #     "mappings": [...],
        #     "unmappable": [],
        #     "sub_queries": [...],
        #     "synthesis_hint": "...",
        # }
    """

    def __init__(
        self,
        df: pd.DataFrame,
        groq_api_key: str | None = None,
        tavily_api_key: str | None = None,
        model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    ):
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("Missing GROQ_API_KEY")

        self.llm = ChatGroq(groq_api_key=api_key, model_name=model, temperature=0.0)
        self.column_metadata = build_column_metadata(df)
        self._meta_str = "\n".join(
            f"- '{col}': {info}" for col, info in self.column_metadata.items()
        )

        # Stage 1.5: optional Tavily enrichment
        t_key = tavily_api_key or os.getenv("TAVILY_API_KEY")
        if not _TAVILY_AVAILABLE:
            print("[Tavily] langchain-tavily not installed — Stage 1.5 disabled.")
            self._tavily = None
        elif not t_key:
            print("[Tavily] TAVILY_API_KEY not set — Stage 1.5 disabled.")
            self._tavily = None
        else:
            os.environ.setdefault("TAVILY_API_KEY", t_key)
            self._tavily = TavilySearch(max_results=2)
            print("[Tavily] Enabled — JARGON concepts will trigger web enrichment.")

        # Novelty classifier chain (used only when Tavily is available)
        self._novelty_chain = (
            ChatPromptTemplate.from_messages([
                ("system", NOVELTY_CLASSIFIER_PROMPT),
                ("human", "Concepts to classify:\n{concepts}"),
            ])
            | self.llm
            | StrOutputParser()
        )

        # Stage 1
        self._concept_chain = (
            ChatPromptTemplate.from_messages([
                ("system", CONCEPT_EXTRACTION_PROMPT),
                ("human", "Query: {query}"),
            ])
            | self.llm
            | StrOutputParser()
        )

        # Stage 2
        self._grounding_chain = (
            ChatPromptTemplate.from_messages([
                ("system", SCHEMA_GROUNDING_PROMPT),
                ("human", "Concepts:\n{concepts}\n\nQuery context: {query}"),
            ])
            | self.llm
            | StrOutputParser()
        )

        # Stage 3
        self._subquery_chain = (
            ChatPromptTemplate.from_messages([
                ("system", SUBQUERY_GENERATION_PROMPT),
                ("human", "Original query: {query}\n\nGrounding:\n{grounding}"),
            ])
            | self.llm
            | StrOutputParser()
        )

    # ------ parsing helpers ------

    def _enrich_with_tavily(
        self, reasoning_concepts: list[str]
    ) -> dict[str, str]:
        """
        Stage 1.5 implementation.

        1. Ask the novelty classifier which concepts are JARGON.
        2. For each JARGON concept, run a focused Tavily search.
        3. Return a dict mapping concept → concise definition snippet.
        """
        if not self._tavily or not reasoning_concepts:
            return {}

        # Step 1: classify
        classifier_input = "\n".join(f"- {c}" for c in reasoning_concepts)
        raw = self._novelty_chain.invoke({"concepts": classifier_input}).strip()

        jargon_concepts = []
        for line in raw.splitlines():
            line = line.strip()
            if line.endswith(": JARGON"):
                term = line[: -len(": JARGON")].lstrip("- ").strip()
                jargon_concepts.append(term)

        if not jargon_concepts:
            return {}

        # Step 2: search each JARGON concept and extract a definition snippet
        definitions = {}
        for concept in jargon_concepts:
            search_query = f"{concept} definition IoT sensor engineering standards"
            try:
                print(f"[Tavily] Searching: {search_query!r}")
                results = self._tavily.invoke({"query": search_query})
                # TavilySearch returns a dict: {"results": [{"content": ..., "url": ...}, ...]}
                # TavilySearchResults (old API) returned a bare list of dicts.
                snippet = None
                if isinstance(results, dict):
                    items = results.get("results", [])
                    parts = [
                        r["content"][:300].replace("\n", " ")
                        for r in items[:2]
                        if isinstance(r, dict) and r.get("content", "").strip()
                    ]
                    if parts:
                        snippet = " | ".join(parts)
                elif isinstance(results, list):
                    parts = [
                        r["content"][:300].replace("\n", " ")
                        for r in results[:2]
                        if isinstance(r, dict) and r.get("content", "").strip()
                    ]
                    if parts:
                        snippet = " | ".join(parts)
                if snippet:
                    definitions[concept] = snippet
                    print(f"[Tavily] Got definition for '{concept}'")
                else:
                    print(f"[Tavily] No content in results for '{concept}'")
            except Exception as e:
                print(f"[Tavily] Search failed for '{concept}': {e}")

        return definitions

    @staticmethod
    def _parse_concepts(response: str) -> dict:
        concepts = {"DATA": [], "REASONING": []}
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("DATA:"):
                raw = line.split("DATA:", 1)[1].strip()
                if raw.upper() != "NONE":
                    concepts["DATA"] = [c.strip() for c in raw.split(",")]
            elif line.startswith("REASONING:"):
                raw = line.split("REASONING:", 1)[1].strip()
                if raw.upper() != "NONE":
                    concepts["REASONING"] = [c.strip() for c in raw.split(",")]
        return concepts

    @staticmethod
    def _parse_grounding(response: str) -> tuple[list[str], list[str]]:
        mappings = []
        unmappable = []
        in_mappings = False
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("MAPPINGS:"):
                in_mappings = True
                continue
            elif line.startswith("UNMAPPABLE:"):
                in_mappings = False
                raw = line.split("UNMAPPABLE:", 1)[1].strip()
                if raw.upper() != "NONE":
                    unmappable = [c.strip() for c in raw.split(",")]
            elif in_mappings and "→" in line:
                mappings.append(line.strip())
        return mappings, unmappable

    @staticmethod
    def _parse_subqueries(response: str) -> tuple[list[str], str]:
        sub_queries = []
        synthesis_hint = ""
        for line in response.strip().splitlines():
            line = line.strip()
            if line.startswith("SUB_Q"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    sub_queries.append(parts[1].strip())
            elif line.startswith("SYNTHESIS_HINT:"):
                synthesis_hint = line.split("SYNTHESIS_HINT:", 1)[1].strip()
        return sub_queries, synthesis_hint

    # ------ main entry point ------

    def rewrite(self, user_query: str) -> dict:
        """
        Run the full pipeline on an abstract query.
        Returns a structured dict with all intermediate + final outputs.

        Stages:
          1   — Concept extraction (DATA vs. REASONING)
          1.5 — Tavily enrichment for JARGON REASONING concepts (if key set)
          2   — Schema grounding (concepts → column proxies)
          3   — Sub-query generation (concrete, executable sub-questions)
        """
        # Stage 1: extract concepts
        concept_response = self._concept_chain.invoke({"query": user_query}).strip()
        concepts = self._parse_concepts(concept_response)

        # Stage 1.5: optional Tavily enrichment for domain-specific REASONING concepts
        enriched_definitions = self._enrich_with_tavily(concepts["REASONING"])

        # Build the definitions block injected into Stage 2
        if enriched_definitions:
            defs_block = (
                "\nExternal definitions retrieved for domain-specific concepts "
                "(use these to build precise column proxies):\n"
                + "\n".join(
                    f"  {term}: {defn}"
                    for term, defn in enriched_definitions.items()
                )
            )
        else:
            defs_block = ""  # no enrichment — SCHEMA_GROUNDING_PROMPT placeholder becomes empty

        # Stage 2: ground concepts to schema
        concept_summary = (
            f"DATA: {', '.join(concepts['DATA']) or 'NONE'}\n"
            f"REASONING: {', '.join(concepts['REASONING']) or 'NONE'}"
        )
        grounding_response = self._grounding_chain.invoke({
            "concepts": concept_summary,
            "query": user_query,
            "column_metadata": self._meta_str,
            "enriched_definitions": defs_block,
        }).strip()
        mappings, unmappable = self._parse_grounding(grounding_response)

        # Stage 3: generate sub-queries
        subquery_response = self._subquery_chain.invoke({
            "query": user_query,
            "grounding": grounding_response,
            "column_metadata": self._meta_str,
        }).strip()
        sub_queries, synthesis_hint = self._parse_subqueries(subquery_response)

        return {
            "original_query": user_query,
            "concepts": concepts,
            "tavily_enriched": enriched_definitions,
            "mappings": mappings,
            "unmappable": unmappable,
            "sub_queries": sub_queries,
            "synthesis_hint": synthesis_hint,
        }


# ====================================================
# Standalone demo
# ====================================================
# Recommended testing sequence:
#
#   Step 1 — verify environment
#     export GROQ_API_KEY="your_groq_api_key_here"
#     export TAVILY_API_KEY="your_tavily_api_key_here"   # optional
#     pip install -r env/requirements.txt
#
#   Step 2 — run this file directly BEFORE calling eval.py
#     python src/scripts/query_rewrite.py
#       → runs the 4 built-in demo queries, prints JSON decomposition for each
#
#     python src/scripts/query_rewrite.py "Was it a bumpy ride?"
#       → COMMON concept path: no Tavily call, pure LLM grounding
#
#     python src/scripts/query_rewrite.py "Did the bus experience any potholes based on the ISO 2631 discomfort weighting?"
#       → JARGON concept path: Tavily fetches the ISO 2631 definition,
#         injected into Stage 2 so the proxy references the correct
#         frequency-weighting band (1-80 Hz → accel_stats_z_p90/p99)
#
#   What to check in the JSON output:
#     'concepts'       — DATA and REASONING buckets correctly populated
#     'tavily_enriched'— non-empty only for jargon terms (empty dict for common words)
#     'mappings'       — every REASONING concept mapped to real column names
#     'unmappable'     — should be empty for answerable queries
#     'sub_queries'    — 2-4 concrete, column-grounded sub-questions
#     'synthesis_hint' — single line guiding the final answer assembly
#
#   If sub_queries is empty or mappings are wrong, fix the prompt
#   templates above before running the full pipeline via eval.py.

if __name__ == "__main__":
    import json
    import sys

    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    CSV_PATH = os.path.join(BASE_DIR, "data", "bus", "raw", "bus_data.csv")

    demo_queries = [
        "Was it a bumpy ride?",
        "Were there any dangerous driving moments?",
        "Give me a quick summary of this trip.",
        "How's the driving quality on this route?",
    ]

    query_arg = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"Loading dataset: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"Rows: {len(df):,}  Columns: {list(df.columns)}\n")

    rewriter = QueryRewriter(df)

    queries_to_run = [query_arg] if query_arg else demo_queries

    for q in queries_to_run:
        print(f"{'═' * 70}")
        print(f"QUERY: {q}")
        print(f"{'═' * 70}")
        result = rewriter.rewrite(q)
        print(json.dumps(result, indent=2, default=str))
        print()
