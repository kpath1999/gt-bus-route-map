"""
pipeline/stages.py — Three-stage query rewriting pipeline.

Stage 1 — Concept Extraction:
    Classifies every concept in the user query as DATA (column-mappable) or
    REASONING (qualitative proxy needed).

Stage 2 — Schema Grounding:
    Maps DATA concepts to actual columns; maps REASONING concepts to
    column+operation proxies. Uses the activity codebook when set.

Stage 3 — Sub-query Generation:
    Decomposes the abstract query into 2–4 concrete, column-grounded
    sub-questions that a pandas agent can execute independently.

See CLAUDE.md §pipeline/stages.py for full implementation algorithms.
Reference: chat/playground/playground.py ~lines 400–650.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from flashfusion.prompts.templates import (
    CONCEPT_EXTRACTION_PROMPT,
    SCHEMA_GROUNDING_PROMPT,
    SUBQUERY_GENERATION_PROMPT,
)
from flashfusion.config import STAGE1_MAX_RETRIES, STAGE2_MAX_RETRIES

if TYPE_CHECKING:
    import pandas as pd
    from flashfusion.pipeline.runner import LLMClient


_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "is", "are", "was", "were", "what", "which", "how",
        "many", "in", "of", "for", "by", "with", "from", "to", "and", "or",
        "not", "that", "this", "these", "those", "do", "does", "their", "its",
        "have", "has", "had", "be", "been", "being", "on", "at", "as", "but",
        "if", "then", "than", "so", "such",
    }
)


_KNOWN_COLUMNS: frozenset[str] = frozenset(
    {
        "subject_id", "activity_label", "timestamp",
        "x", "y", "z", "magnitude", "activity_name",
    }
)


class Stage1_ConceptExtraction:
    """
    Stage 1: Classify every query concept as DATA or REASONING.

    DATA concepts map directly to dataset columns.
    REASONING concepts require qualitative proxy derivation.
    """

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a ChatGroq model.
        """
        self.client = client
        # Build the LangChain chain at construction time.
        # System message: CONCEPT_EXTRACTION_PROMPT (no placeholders)
        # Human message: "{input}" — the user query
        self._chain = (
            ChatPromptTemplate.from_messages([
                ("system", CONCEPT_EXTRACTION_PROMPT),
                ("human", "{input}"),
            ])
            | client.llm
            | StrOutputParser()
        )

    def run(self, query: str) -> dict:
        """
        Extract and classify concepts from the user query.

        Args:
            query: Raw natural language query string.

        Returns:
            dict with keys:
                "DATA"      list[str] — column-mappable concepts (may be empty)
                "REASONING" list[str] — proxy-required concepts (may be empty)

        Retry behaviour (see CLAUDE.md):
            - If both lists are empty and len(query.strip()) > 20: retry once with
              an explicit instruction appended to the input.
            - If still both empty after retry: apply keyword extraction fallback.

        Keyword extraction fallback:
            stopwords = {"the","a","an","is","are","was","were","what","which","how",
                         "many","in","of","for","by","with","from","to","and","or",
                         "not","that","this","these","those","do","does","their","its"}
            tokens = [w for w in query.lower().split()
                      if w not in stopwords and len(w) > 3]
            data_concepts = tokens[:5]
        """
        response = self.client.invoke_chain(
            self._chain, {"input": query}, stage="S1"
        )
        parsed = self._parse_concepts(response)

        if (
            not parsed["DATA"]
            and not parsed["REASONING"]
            and len(query.strip()) > 20
        ):
            for _ in range(STAGE1_MAX_RETRIES):
                retry_input = (
                    query
                    + "\n\nBe explicit. List every distinct semantic concept "
                    "individually."
                )
                response = self.client.invoke_chain(
                    self._chain, {"input": retry_input}, stage="S1-retry"
                )
                parsed = self._parse_concepts(response)
                if parsed["DATA"] or parsed["REASONING"]:
                    break

        if not parsed["DATA"] and not parsed["REASONING"]:
            tokens = [
                w
                for w in query.lower().split()
                if w not in _STOPWORDS and len(w) > 3
            ]
            parsed["DATA"] = tokens[:5]

        return parsed

    @staticmethod
    def _parse_concepts(response: str) -> dict:
        """
        Parse the LLM response into DATA and REASONING concept lists.

        Expects lines of the form:
            DATA: <comma-separated list, or NONE>
            REASONING: <comma-separated list, or NONE>

        Returns:
            {"DATA": list[str], "REASONING": list[str]}
        """
        # Implementation:
        #   lines = response.splitlines()
        #   data_line = next((l for l in lines if l.strip().startswith("DATA:")), "DATA: NONE")
        #   reasoning_line = next((l for l in lines if l.strip().startswith("REASONING:")), "REASONING: NONE")
        #   def extract(line):
        #       content = line.split(":", 1)[1].strip()
        #       if content.upper() in ("NONE", ""): return []
        #       return [c.strip() for c in content.split(",") if c.strip()]
        #   return {"DATA": extract(data_line), "REASONING": extract(reasoning_line)}
        lines = response.splitlines()
        data_line = next(
            (l for l in lines if l.strip().upper().startswith("DATA:")),
            "DATA: NONE",
        )
        reasoning_line = next(
            (l for l in lines if l.strip().upper().startswith("REASONING:")),
            "REASONING: NONE",
        )

        def extract(line: str) -> list[str]:
            content = line.split(":", 1)[1].strip()
            if content.upper() in ("NONE", ""):
                return []
            return [c.strip() for c in content.split(",") if c.strip()]

        return {"DATA": extract(data_line), "REASONING": extract(reasoning_line)}


class Stage2_SchemaGrounding:
    """
    Stage 2: Map concepts to dataset columns and operation proxies.

    The codebook_str attribute must be set by BaselineRunner before calling run()
    when an adapter is available:
        stage2.codebook_str = adapter.get_codebook_str()
    """

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a ChatGroq model.
        """
        self.client = client
        self.codebook_str: str = "No codebook provided."
        # Note: the chain is built inside run() because SCHEMA_GROUNDING_PROMPT
        # contains {column_metadata} and {codebook} that must be formatted first.

    def run(
        self,
        concepts: dict,
        query: str,
        meta_str: str,
        df: "pd.DataFrame",
        enriched_defs: dict = {},
    ) -> dict:
        """
        Ground concepts to schema columns and operation proxies.

        Args:
            concepts:      Output of Stage1.run() — {"DATA": [...], "REASONING": [...]}
            query:         Original user query (for context).
            meta_str:      Output of meta_to_str(build_column_metadata(df)).
            df:            The DataFrame being queried (used for column validation).
            enriched_defs: Unused (reserved for future enrichment). Pass {} always.

        Returns:
            dict with keys:
                "mappings"     list[str] — grounding lines ("concept → column/operation")
                                           Invalid column refs prefixed with "INVALID(...): "
                "unmappable"   list[str] — concepts with no column mapping
                "raw_grounding" str      — full LLM response (for Stage 3 input)

        Retry behaviour:
            If len(mappings) == 0 after parsing, retry once with a stricter instruction.

        Column validation:
            For each mapping line, detect words that look like column names
            (contain "_" or match a known column). Flag as "INVALID(col): " prefix
            if the word is not in df.columns.
            Known columns: subject_id, activity_label, timestamp, x, y, z,
                           magnitude, activity_name.
        """
        system_prompt = SCHEMA_GROUNDING_PROMPT.format(
            column_metadata=meta_str,
            codebook=self.codebook_str or "No codebook provided.",
        )
        chain = (
            ChatPromptTemplate.from_messages(
                [("system", system_prompt), ("human", "{input}")]
            )
            | self.client.llm
            | StrOutputParser()
        )

        data_concepts = concepts.get("DATA", []) or []
        reasoning_concepts = concepts.get("REASONING", []) or []
        input_text = (
            f"DATA concepts: {', '.join(data_concepts) if data_concepts else 'NONE'}\n"
            f"REASONING concepts: {', '.join(reasoning_concepts) if reasoning_concepts else 'NONE'}\n"
            f"Query: {query}"
        )

        response = self.client.invoke_chain(
            chain, {"input": input_text}, stage="S2"
        )
        mappings, unmappable = self._parse_grounding(response)

        retries_left = STAGE2_MAX_RETRIES
        while not mappings and retries_left > 0:
            retries_left -= 1
            retry_input = (
                input_text
                + "\n\nCRITICAL: output at least one MAPPINGS line. "
                "Example:\n  acceleration → x, y, z columns"
            )
            response = self.client.invoke_chain(
                chain, {"input": retry_input}, stage="S2-retry"
            )
            mappings, unmappable = self._parse_grounding(response)

        valid_cols = set(df.columns) | _KNOWN_COLUMNS
        validated: list[str] = []
        for mapping in mappings:
            invalid_cols = self._detect_invalid_columns(mapping, valid_cols)
            if invalid_cols:
                validated.append(f"INVALID({','.join(invalid_cols)}): {mapping}")
            else:
                validated.append(mapping)

        return {
            "mappings": validated,
            "unmappable": unmappable,
            "raw_grounding": response,
        }

    @staticmethod
    def _detect_invalid_columns(mapping: str, valid_cols: set) -> list[str]:
        """Detect words that look like column names but aren't in valid_cols."""
        rhs = mapping.split("→", 1)[1] if "→" in mapping else mapping
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rhs)
        invalid: list[str] = []
        for token in tokens:
            if token in valid_cols:
                continue
            if "_" in token and token.lower() not in {
                "subject_id", "activity_label", "activity_name",
                "z_score", "z_scores", "groupby_column", "n_unique",
            }:
                invalid.append(token)
        return list(dict.fromkeys(invalid))

    @staticmethod
    def _parse_grounding(response: str) -> tuple[list[str], list[str]]:
        """
        Parse MAPPINGS and UNMAPPABLE sections from Stage 2 response.

        Returns:
            (mappings, unmappable)
            mappings   — list of "concept → column/op" strings
            unmappable — list of concept names that could not be mapped
        """
        # Implementation:
        #   lines = response.splitlines()
        #   mappings, unmappable = [], []
        #   in_mappings = False
        #   for line in lines:
        #       stripped = line.strip()
        #       if stripped.startswith("MAPPINGS:"): in_mappings = True; continue
        #       if stripped.startswith("UNMAPPABLE:"):
        #           in_mappings = False
        #           raw = stripped.split(":", 1)[1].strip()
        #           if raw.upper() not in ("NONE", ""):
        #               unmappable = [u.strip() for u in raw.split(",") if u.strip()]
        #           continue
        #       if in_mappings and stripped and ("→" in stripped or stripped.startswith("-")):
        #           mappings.append(stripped)
        #   return mappings, unmappable
        lines = response.splitlines()
        mappings: list[str] = []
        unmappable: list[str] = []
        in_mappings = False
        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("MAPPINGS:"):
                in_mappings = True
                continue
            if upper.startswith("UNMAPPABLE:"):
                in_mappings = False
                raw = stripped.split(":", 1)[1].strip()
                if raw.upper() not in ("NONE", ""):
                    unmappable = [
                        u.strip() for u in raw.split(",") if u.strip()
                    ]
                continue
            if in_mappings and stripped and (
                "→" in stripped or stripped.startswith("-")
            ):
                mappings.append(stripped)
        return mappings, unmappable


class Stage3_SubqueryGeneration:
    """
    Stage 3: Decompose abstract query into concrete, column-grounded sub-questions.

    Each sub-question targets a single operation type and references exact column names,
    making it directly executable by the pandas DataFrame agent.
    """

    VALID_OPS: ClassVar[frozenset] = frozenset(
        {"FILTER", "AGGREGATE", "GROUPBY", "CORRELATE", "WINDOW", "RANK"}
    )

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a ChatGroq model.
        """
        self.client = client
        # Chain is built inside run() because SUBQUERY_GENERATION_PROMPT
        # contains {column_metadata} and {grounding} placeholders.

    def run(self, query: str, grounding_raw: str, meta_str: str) -> dict:
        """
        Generate concrete sub-questions from the original query and grounding.

        Args:
            query:        Original user query.
            grounding_raw: Raw Stage 2 LLM response (full text, not parsed).
            meta_str:     Formatted column metadata string.

        Returns:
            dict with keys:
                "sub_queries"    list[str] — 2–4 concrete sub-questions, each
                                             prefixed with "[OPERATION]"
                "synthesis_hint" str       — guidance for combining sub-answers
                "raw_subqueries" str       — full LLM response (for debugging)

        Parsing:
            sub_queries = re.findall(r"SUB_Q\d+:\s*(.+)", response)
            hints       = re.findall(r"SYNTHESIS_HINT:\s*(.+)", response)
            synthesis_hint = hints[0].strip() if hints else
                             "Combine all sub-answers into a direct response."
        """
        system_prompt = SUBQUERY_GENERATION_PROMPT.format(
            column_metadata=meta_str,
            grounding=grounding_raw,
        )
        chain = (
            ChatPromptTemplate.from_messages(
                [("system", system_prompt), ("human", "{input}")]
            )
            | self.client.llm
            | StrOutputParser()
        )
        response = self.client.invoke_chain(
            chain, {"input": f"Original query: {query}"}, stage="S3"
        )

        sub_queries = [
            sq.strip() for sq in re.findall(r"SUB_Q\d+:\s*(.+)", response)
        ]
        hints = re.findall(r"SYNTHESIS_HINT:\s*(.+)", response)
        synthesis_hint = (
            hints[0].strip()
            if hints
            else "Combine all sub-answers into a direct response."
        )

        if not sub_queries:
            sub_queries = [f"[AGGREGATE] {query}"]

        return {
            "sub_queries": sub_queries,
            "synthesis_hint": synthesis_hint,
            "raw_subqueries": response,
        }
