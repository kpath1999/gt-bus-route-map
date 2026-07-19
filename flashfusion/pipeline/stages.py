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

import difflib
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

_OPERATION_TOKENS: frozenset[str] = frozenset(
    {
        "max",
        "maximum",
        "highest",
        "peak",
        "min",
        "minimum",
        "lowest",
        "mean",
        "average",
        "median",
        "sum",
        "total",
        "count",
    }
)

# Fixed vocabulary of standard operations Stage 2 is allowed to ground
# DERIVED_STAT/PROXY concepts to. Used to strip operation tokens before
# column-existence validation, and to detect operation-wrapped RHS forms.
_DERIVED_STAT_OPERATIONS: frozenset[str] = frozenset(
    {
        "MEDIAN", "MEAN", "SUM", "COUNT", "MIN", "MAX", "STD", "VARIANCE",
        "PERCENTILE", "GROUP_COMPARE", "PROXY", "DIFFERENCE",
    }
)

# Heuristics for detecting that a column name ALREADY embodies a given
# operation's result (e.g. a column named "accel_stats_z_p99" already IS a
# 99th-percentile value). Used by Stage2._collapse_redundant_stat_wrapping to
# strip out operation wrappers the model incorrectly re-applied on top of an
# already-computed statistic column — a deterministic safety net for cases
# where a weaker grounding model doesn't reliably follow that instruction.
_REDUNDANT_STAT_COLUMN_HINTS: dict[str, "re.Pattern[str]"] = {
    "PERCENTILE": re.compile(r"(?:^|_)p\d{1,3}(?:_|$)|percentile", re.IGNORECASE),
    "MEAN": re.compile(r"mean", re.IGNORECASE),
    "MEDIAN": re.compile(r"median", re.IGNORECASE),
    "SUM": re.compile(r"sum|total", re.IGNORECASE),
    "VARIANCE": re.compile(r"var(?:iance)?", re.IGNORECASE),
    "STD": re.compile(r"std(?:dev)?", re.IGNORECASE),
    "MIN": re.compile(r"min", re.IGNORECASE),
    "MAX": re.compile(r"max", re.IGNORECASE),
}

# Matches the innermost OPERATION(args) call (no nested parens inside args)
# for any operation with a redundancy hint above.
_INNERMOST_STAT_CALL_RE = re.compile(
    r"\b(" + "|".join(_REDUNDANT_STAT_COLUMN_HINTS) + r")\(([^()]+)\)"
)


class Stage1_ConceptExtraction:
    """
    Stage 1: Classify every query concept into one of three buckets.

    COLUMN        — maps directly to an existing dataset column.
    DERIVED_STAT  — computable via a standard operation over column(s)
                    (median, mean, threshold split, group comparison, etc.).
    PROXY         — qualitative concept requiring a heuristic column
                    substitution (no formulaic operation available).
    """

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a chat model.
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
                "COLUMN"       list[str] — column-mappable concepts (may be empty)
                "DERIVED_STAT" list[str] — standard-operation concepts (may be empty)
                "PROXY"        list[str] — heuristic-proxy concepts (may be empty)

        Retry behaviour (see CLAUDE.md):
            - If all three lists are empty and len(query.strip()) > 20: retry once with
              an explicit instruction appended to the input.
            - If still all empty after retry: apply keyword extraction fallback.

        Keyword extraction fallback:
            stopwords = {"the","a","an","is","are","was","were","what","which","how",
                         "many","in","of","for","by","with","from","to","and","or",
                         "not","that","this","these","those","do","does","their","its"}
            tokens = [w for w in query.lower().split()
                      if w not in stopwords and len(w) > 3]
            column_concepts = tokens[:5]
        """
        response = self.client.invoke_chain(
            self._chain, {"input": query}, stage="S1"
        )
        parsed = self._parse_concepts(response)

        if (
            not parsed["COLUMN"]
            and not parsed["DERIVED_STAT"]
            and not parsed["PROXY"]
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
                if parsed["COLUMN"] or parsed["DERIVED_STAT"] or parsed["PROXY"]:
                    break

        if not parsed["COLUMN"] and not parsed["DERIVED_STAT"] and not parsed["PROXY"]:
            tokens = [
                w
                for w in query.lower().split()
                if w not in _STOPWORDS and len(w) > 3
            ]
            parsed["COLUMN"] = tokens[:5]

        return parsed

    @staticmethod
    def _parse_concepts(response: str) -> dict:
        """
        Parse the LLM response into COLUMN, DERIVED_STAT, and PROXY concept lists.

        Expects lines of the form:
            COLUMN: <comma-separated list, or NONE>
            DERIVED_STAT: <comma-separated list, or NONE>
            PROXY: <comma-separated list, or NONE>

        Returns:
            {"COLUMN": list[str], "DERIVED_STAT": list[str], "PROXY": list[str]}
        """
        lines = response.splitlines()

        def find_line(prefix: str) -> str:
            return next(
                (l for l in lines if l.strip().upper().startswith(prefix)),
                f"{prefix} NONE",
            )

        # DERIVED_STAT: must be matched before a generic "DATA:"-style prefix
        # would ever collide; prefixes are distinct so simple startswith works.
        column_line = find_line("COLUMN:")
        derived_line = find_line("DERIVED_STAT:")
        proxy_line = find_line("PROXY:")

        def extract(line: str) -> list[str]:
            content = line.split(":", 1)[1].strip()
            if content.upper() in ("NONE", ""):
                return []
            return [c.strip() for c in content.split(",") if c.strip()]

        return {
            "COLUMN": extract(column_line),
            "DERIVED_STAT": extract(derived_line),
            "PROXY": extract(proxy_line),
        }


class Stage2_SchemaGrounding:
    """
    Stage 2: Map concepts to dataset columns and operation proxies.
    """

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a chat model.
        """
        self.client = client
        # Note: the chain is built inside run() because SCHEMA_GROUNDING_PROMPT
        # contains {column_metadata} that must be formatted first.

    @staticmethod
    def _normalize_text(value: str) -> str:
        """Normalize concept/query text for robust lexical matching."""
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()

    @classmethod
    def _concept_is_query_critical(cls, concept: str, query: str) -> bool:
        """Heuristic gate: retain concepts with a direct lexical signal in query."""
        concept_norm = cls._normalize_text(concept)
        query_norm = cls._normalize_text(query)
        if not concept_norm or not query_norm:
            return False
        if concept_norm in query_norm:
            return True

        concept_tokens = [t for t in concept_norm.split() if t not in _STOPWORDS and len(t) > 2]
        query_tokens = {t for t in query_norm.split() if t not in _STOPWORDS and len(t) > 2}
        if not concept_tokens or not query_tokens:
            return False

        overlap = sum(1 for token in concept_tokens if token in query_tokens)
        return overlap > 0

    @classmethod
    def _filter_query_critical_concepts(
        cls, concepts: dict, query: str
    ) -> tuple[list[str], list[str], list[str]]:
        """Drop non-critical concepts but preserve original lists if all are filtered."""
        column_concepts = concepts.get("COLUMN", []) or []
        derived_concepts = concepts.get("DERIVED_STAT", []) or []
        proxy_concepts = concepts.get("PROXY", []) or []

        filtered_column = [c for c in column_concepts if cls._concept_is_query_critical(c, query)]
        filtered_derived = [c for c in derived_concepts if cls._concept_is_query_critical(c, query)]
        filtered_proxy = [c for c in proxy_concepts if cls._concept_is_query_critical(c, query)]

        if not filtered_column and not filtered_derived and not filtered_proxy:
            return column_concepts, derived_concepts, proxy_concepts
        return filtered_column, filtered_derived, filtered_proxy

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
            concepts:      Output of Stage1.run() —
                           {"COLUMN": [...], "DERIVED_STAT": [...], "PROXY": [...]}
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
        system_prompt = SCHEMA_GROUNDING_PROMPT.format(column_metadata=meta_str)
        chain = (
            ChatPromptTemplate.from_messages(
                [("system", system_prompt), ("human", "{input}")]
            )
            | self.client.llm
            | StrOutputParser()
        )

        column_concepts, derived_concepts, proxy_concepts = self._filter_query_critical_concepts(
            concepts, query
        )
        input_text = (
            f"COLUMN concepts: {', '.join(column_concepts) if column_concepts else 'NONE'}\n"
            f"DERIVED_STAT concepts: {', '.join(derived_concepts) if derived_concepts else 'NONE'}\n"
            f"PROXY concepts: {', '.join(proxy_concepts) if proxy_concepts else 'NONE'}\n"
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

        # Second retry hook: weak/light models sometimes dump DERIVED_STAT or
        # PROXY concepts into UNMAPPABLE even though a COLUMN concept exists
        # that could serve as their operand (e.g. "median"/"average" marked
        # UNMAPPABLE while "acceleration variance" was successfully grounded).
        # Re-ask once, naming the specific concepts that must be grounded.
        mapped_concepts = {
            m.split("→", 1)[0].strip().lower() for m in mappings if "→" in m
        }
        critical_missing = [
            c for c in derived_concepts + proxy_concepts
            if c.lower() not in mapped_concepts and c in unmappable
        ]
        if critical_missing and mappings:
            retry_input = (
                input_text
                + "\n\nCRITICAL: the following DERIVED_STAT/PROXY concepts were "
                "left UNMAPPABLE but MUST be grounded to an OPERATION(column) "
                "expression using one of the COLUMN concepts already available: "
                f"{', '.join(critical_missing)}. Re-output the FULL MAPPINGS/"
                "UNMAPPABLE block with these concepts grounded."
            )
            response = self.client.invoke_chain(
                chain, {"input": retry_input}, stage="S2-retry-derived"
            )
            retried_mappings, retried_unmappable = self._parse_grounding(response)
            if retried_mappings:
                mappings, unmappable = retried_mappings, retried_unmappable

        valid_cols = set(df.columns) | _KNOWN_COLUMNS
        mappings = [self._repair_operation_misgrounding(m, valid_cols) for m in mappings]
        mappings = self._repair_unresolved_column_reference(mappings, valid_cols)
        mappings = [self._collapse_redundant_stat_wrapping(m) for m in mappings]
        mappings = self._repair_derived_stat_column_drift(mappings, valid_cols)

        validated: list[str] = []
        for mapping in mappings:
            invalid_cols = self._detect_invalid_columns(mapping, valid_cols)
            if invalid_cols:
                validated.append(f"INVALID({','.join(invalid_cols)}): {mapping}")
            else:
                validated.append(mapping)

        normalized_grounding = self._format_grounding(validated, unmappable)

        return {
            "mappings": validated,
            "unmappable": unmappable,
            "raw_grounding": normalized_grounding,
            # Concepts as they were sent to the grounding LLM, i.e. AFTER the
            # query-critical filter dropped non-critical S1 concepts. Exposed
            # so callers (e.g. eval/trace_query.py) can show what the mappings
            # looked like/were grounded against before repair/validation ran.
            "filtered_concepts": {
                "COLUMN": column_concepts,
                "DERIVED_STAT": derived_concepts,
                "PROXY": proxy_concepts,
            },
        }

    @staticmethod
    def _closest_valid_column(candidate: str, valid_cols: set[str]) -> str | None:
        """Fuzzy-match an invented/abbreviated column reference to a real column.

        Handles the class of bug where the LLM invents a plausible-looking but
        non-existent column name by lightly rewording a concept — e.g. emitting
        "acceleration_variance" when the real (abbreviated) column is
        "accel_variance". Requires the same number of underscore-separated
        tokens, with each token pair being a prefix of one another (so
        "accel"/"acceleration" matches but unrelated columns do not), then
        breaks ties with a string-similarity ratio.
        """
        if candidate in valid_cols:
            return candidate
        cand_tokens = [t for t in candidate.lower().split("_") if t]
        if not cand_tokens:
            return None

        best: tuple[float, str] | None = None
        for col in valid_cols:
            col_tokens = [t for t in col.lower().split("_") if t]
            if not col_tokens or len(col_tokens) != len(cand_tokens):
                continue
            if not all(
                a == b or a.startswith(b) or b.startswith(a)
                for a, b in zip(cand_tokens, col_tokens)
            ):
                continue
            ratio = difflib.SequenceMatcher(None, candidate.lower(), col.lower()).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, col)

        if best and best[0] >= 0.6:
            return best[1]
        return None

    @classmethod
    def _repair_unresolved_column_reference(
        cls, mappings: list[str], valid_cols: set[str]
    ) -> list[str]:
        """Auto-correct RHS column references that don't exist but closely
        match a real column (e.g. the LLM invents 'acceleration_variance' for
        the real column 'accel_variance'). Runs before invalid-column
        validation so a correctable near-miss never gets flagged INVALID."""
        repaired: list[str] = []
        for mapping in mappings:
            if "→" not in mapping:
                repaired.append(mapping)
                continue
            lhs, rhs = [part.strip() for part in mapping.split("→", 1)]

            def _fix(match: "re.Match") -> str:
                token = match.group(0)
                if token.upper() in _DERIVED_STAT_OPERATIONS or token in valid_cols:
                    return token
                fixed = cls._closest_valid_column(token, valid_cols)
                return fixed if fixed else token

            new_rhs = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", _fix, rhs)
            repaired.append(f"{lhs} → {new_rhs}" if new_rhs != rhs else mapping)
        return repaired

    @staticmethod
    def _collapse_redundant_stat_wrapping(mapping: str) -> str:
        """Strip operation wrappers applied on top of an already-computed column.

        Deterministic safety net for the class of bug where a (typically
        weaker/lighter) grounding model re-wraps a column that already IS the
        named statistic — e.g. emitting ``PERCENTILE(accel_stats_z_p99, 0.99)``
        when ``accel_stats_z_p99`` already is the 99th-percentile value, or
        ``DIFFERENCE(PERCENTILE(a, 0.99), PERCENTILE(b, 0.01))`` instead of the
        expected ``DIFFERENCE(a, b)``. Repeatedly collapses the innermost
        ``OPERATION(column, ...)`` call whenever ``column``'s name already
        matches that operation's redundancy hint, leaving other (legitimate)
        operation calls untouched.
        """
        if "→" not in mapping:
            return mapping
        lhs, rhs = [part.strip() for part in mapping.split("→", 1)]
        if not rhs:
            return mapping

        def _collapse_once(text: str) -> tuple[str, bool]:
            changed = False

            def _replace(match: "re.Match[str]") -> str:
                nonlocal changed
                op_name = match.group(1).upper()
                args = match.group(2)
                first_arg = args.split(",", 1)[0].strip().strip("`\"'")
                hint = _REDUNDANT_STAT_COLUMN_HINTS.get(op_name)
                if hint and re.search(r"[A-Za-z_][A-Za-z0-9_]*", first_arg) and hint.search(first_arg):
                    changed = True
                    return first_arg
                return match.group(0)

            new_text = _INNERMOST_STAT_CALL_RE.sub(_replace, text)
            return new_text, changed

        current = rhs
        for _ in range(5):  # bounded passes to unwrap nested calls safely
            current, changed = _collapse_once(current)
            if not changed:
                break

        return f"{lhs} → {current}" if current != rhs else mapping

    @staticmethod
    def _detect_invalid_columns(mapping: str, valid_cols: set) -> list[str]:
        """Detect words that look like column names but aren't in valid_cols.

        Known operation keywords (MEDIAN, MEAN, VARIANCE, PROXY, ...) are
        stripped out first so an operation name is never mistaken for an
        invalid column reference — only its arguments are validated.
        """
        rhs = mapping.split("→", 1)[1] if "→" in mapping else mapping
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rhs)
        invalid: list[str] = []
        for token in tokens:
            if token.upper() in _DERIVED_STAT_OPERATIONS:
                continue
            if token in valid_cols:
                continue
            if "_" in token and token.lower() not in {
                "subject_id", "activity_label", "activity_name",
                "z_score", "z_scores", "groupby_column", "n_unique",
            }:
                invalid.append(token)
        return list(dict.fromkeys(invalid))

    @staticmethod
    def _repair_operation_misgrounding(mapping: str, valid_cols: set[str]) -> str:
        """Repair lines where a concrete column was incorrectly grounded to an op token."""
        if "→" not in mapping:
            return mapping

        lhs, rhs = [part.strip() for part in mapping.split("→", 1)]
        if not lhs or not rhs:
            return mapping

        valid_lower = {c.lower(): c for c in valid_cols}
        lhs_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", lhs)
        rhs_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rhs)
        if not rhs_tokens:
            return mapping

        rhs_first = rhs_tokens[0].lower()
        lhs_col = next((valid_lower[t.lower()] for t in lhs_tokens if t.lower() in valid_lower), None)
        rhs_col = next((valid_lower[t.lower()] for t in rhs_tokens if t.lower() in valid_lower), None)

        if rhs_first in _OPERATION_TOKENS and lhs_col:
            return f"{lhs} → {lhs_col} ({rhs_first})"

        if rhs_col is None and lhs_col:
            return f"{lhs} → {lhs_col}"

        return mapping

    @staticmethod
    def _repair_derived_stat_column_drift(mappings: list[str], valid_cols: set[str]) -> list[str]:
        """Force DERIVED_STAT/PROXY mappings to reuse an already-grounded COLUMN's column.

        Prevents the class of bug where a concept like "median" or "average
        acceleration variance" drifts onto an unrelated column (e.g. a
        percentile column) purely because it sounds statistically similar.
        If a mapping's concept text (LHS) lexically overlaps a concept that
        was already grounded to a concrete column via a plain COLUMN mapping
        (bare "concept → column" form, no operation wrapper), any operation
        arguments in OTHER mappings that share that overlap are rewritten to
        reference the same column.
        """
        valid_lower = {c.lower(): c for c in valid_cols}

        # First pass: collect concept-root -> column from plain COLUMN mappings
        # (RHS has no "(" i.e. not an OPERATION(...) wrapped expression).
        grounded_roots: dict[str, str] = {}
        for mapping in mappings:
            if "→" not in mapping:
                continue
            lhs, rhs = [part.strip() for part in mapping.split("→", 1)]
            rhs_first_token = re.split(r"[\s(]", rhs)[0].strip("`\"'")
            if "(" in rhs or not rhs_first_token:
                continue
            resolved = valid_lower.get(rhs_first_token.lower())
            if not resolved:
                continue
            for token in re.findall(r"[A-Za-z0-9]+", lhs.lower()):
                if token not in _STOPWORDS and len(token) > 2:
                    grounded_roots[token] = resolved

        if not grounded_roots:
            return mappings

        repaired: list[str] = []
        for mapping in mappings:
            if "→" not in mapping or "(" not in mapping:
                repaired.append(mapping)
                continue

            lhs, rhs = [part.strip() for part in mapping.split("→", 1)]
            lhs_tokens = [
                t for t in re.findall(r"[A-Za-z0-9]+", lhs.lower())
                if t not in _STOPWORDS and len(t) > 2
            ]
            candidate_cols = {grounded_roots[t] for t in lhs_tokens if t in grounded_roots}
            if len(candidate_cols) != 1:
                repaired.append(mapping)
                continue
            expected_col = next(iter(candidate_cols))

            # Find column-like identifiers already present inside the RHS
            # (arguments to an operation call) that are valid columns but
            # differ from the expected column — swap them.
            def _swap(match: "re.Match") -> str:
                token = match.group(0)
                if token.upper() in _DERIVED_STAT_OPERATIONS:
                    return token
                if token.lower() in valid_lower and valid_lower[token.lower()] != expected_col:
                    return expected_col
                return token

            new_rhs = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", _swap, rhs)
            repaired.append(f"{lhs} → {new_rhs}" if new_rhs != rhs else mapping)

        return repaired

    @staticmethod
    def _format_grounding(mappings: list[str], unmappable: list[str]) -> str:
        lines = ["MAPPINGS:"]
        for mapping in mappings:
            lines.append(f"  {mapping}")
        if not mappings:
            lines.append("  (none)")
        unmappable_text = ", ".join(unmappable) if unmappable else "NONE"
        lines.append(f"UNMAPPABLE: {unmappable_text}")
        return "\n".join(lines)

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
        {"FILTER", "AGGREGATE", "GROUPBY", "CORRELATE", "WINDOW", "RANK", "SELECT", "DERIVE"}
    )

    @staticmethod
    def _extract_meta_columns(meta_str: str) -> list[str]:
        cols: list[str] = []
        for line in meta_str.splitlines():
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", line.strip())
            if match:
                cols.append(match.group(1))
        return cols

    @staticmethod
    def _is_typed_sub_query(sub_query: str) -> bool:
        match = re.match(r"^\s*\[([A-Za-z_]+)\]\s*(.+)$", sub_query.strip())
        if not match:
            return False
        body = match.group(2).strip()
        if "=" not in body:
            return False
        parts = [p.strip() for p in body.split("|") if p.strip()]
        return bool(parts) and all("=" in p for p in parts)

    @staticmethod
    def _split_compound_aggregate_sub_query(sub_query: str) -> list[str] | None:
        match = re.match(r"^\s*\[AGGREGATE\]\s*(.+)$", sub_query.strip(), flags=re.IGNORECASE)
        if not match:
            return None
        body = match.group(1)
        if body.lower().count("column=") < 2 or body.lower().count("stat=") < 2:
            return None

        parts = [p.strip() for p in body.split("|") if p.strip()]
        pairs: list[tuple[str, str]] = []
        pending_column = ""
        pending_stat = ""
        for part in parts:
            key, _, value = part.partition("=")
            key_l = key.strip().lower()
            value_s = value.strip()
            if key_l == "column":
                pending_column = value_s
            elif key_l == "stat":
                pending_stat = value_s
            if pending_column and pending_stat:
                pairs.append((pending_column, pending_stat))
                pending_column = ""
                pending_stat = ""

        if len(pairs) < 2:
            return None
        return [
            f"[AGGREGATE] column={column} | stat={stat}"
            for column, stat in pairs
        ]

    @classmethod
    def _normalize_sub_queries(cls, sub_queries: list[str]) -> list[str]:
        normalized: list[str] = []
        for sq in sub_queries:
            sq_clean = sq.strip()
            compound = cls._split_compound_aggregate_sub_query(sq_clean)
            if compound is not None:
                normalized.extend(compound)
                continue

            if cls._is_typed_sub_query(sq_clean):
                normalized.append(sq_clean)
                continue

            filter_prev_match = re.match(
                r"^\s*\[FILTER\]\s*Keep rows where\s+([A-Za-z_][A-Za-z0-9_]*)\s+equals\s+the\s+previously\s+computed\s+aggregate\s+result\.?\s*$",
                sq_clean,
                flags=re.IGNORECASE,
            )
            if filter_prev_match:
                col = filter_prev_match.group(1)
                normalized.append(f"[FILTER] column={col} | comparator=eq | value=PREV")
                continue

            filter_num_match = re.match(
                r"^\s*\[FILTER\]\s*Keep rows where\s+([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|>|<|==|=)\s*(-?\d+(?:\.\d+)?)\.?\s*$",
                sq_clean,
                flags=re.IGNORECASE,
            )
            if filter_num_match:
                col = filter_num_match.group(1)
                cmp = filter_num_match.group(2)
                val = filter_num_match.group(3)
                cmp_map = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "=": "eq", "==": "eq"}
                normalized.append(f"[FILTER] column={col} | comparator={cmp_map[cmp]} | value={val}")
                continue

            agg_match = re.match(
                r"^\s*\[AGGREGATE\]\s*Compute\s+the\s+(max|min|mean|median|sum|count)\s+of\s+([A-Za-z_][A-Za-z0-9_]*)\.?\s*$",
                sq_clean,
                flags=re.IGNORECASE,
            )
            if agg_match:
                stat = agg_match.group(1).lower()
                col = agg_match.group(2)
                normalized.append(f"[AGGREGATE] column={col} | stat={stat}")
                continue

            normalized.append(sq_clean)
        return normalized

    @staticmethod
    def _typed_step_to_display(step: dict) -> str:
        op = str(step.get("op", "")).upper().strip()
        if op == "AGGREGATE_COLUMN":
            return f"[AGGREGATE] column={step.get('column', '')} | stat={step.get('aggregate', '')}"
        if op == "FILTER_EQ_PREV":
            return f"[FILTER] column={step.get('column', '')} | comparator=eq | value=PREV"
        if op == "FILTER_COMPARE":
            return (
                f"[FILTER] column={step.get('column', '')} | comparator={step.get('comparator', '')} "
                f"| value={step.get('value', '')}"
            )
        if op == "AGGREGATE_COUNT_ROWS":
            return "[AGGREGATE] stat=count_rows"
        if op == "SELECT_LIST":
            return f"[SELECT] columns={step.get('column', '')} | as=list"
        if op == "SPLIT_BY_THRESHOLD":
            return (
                f"[SPLIT_BY_THRESHOLD] column={step.get('column', '')} | comparator={step.get('comparator', '')} "
                f"| threshold=MEDIAN(column) | label={step.get('label', '')}"
            )
        if op == "GROUP_AGGREGATE":
            groups = step.get("groups") or []
            groups_txt = ",".join(str(g) for g in groups)
            return (
                f"[GROUP_AGGREGATE] column={step.get('column', '')} | aggregate={step.get('aggregate', '')} "
                f"| groups={groups_txt}"
            )
        if op == "COMPARE_GROUPS":
            return "[COMPARE_GROUPS] source=PREV_GROUP_AGGREGATE"
        return f"[{op}]"

    @classmethod
    def _typed_steps_to_display(cls, typed_steps: list[dict]) -> list[str]:
        return [cls._typed_step_to_display(step) for step in typed_steps]

    @classmethod
    def _compile_arg_extreme_plan(cls, query: str, meta_cols: list[str]) -> dict | None:
        query_l = query.lower()
        query_cols = [c for c in meta_cols if c.lower() in query_l]
        if len(query_cols) < 2:
            return None

        op = None
        if re.search(r"\b(max|maximum|highest|largest|peak)\b", query_l):
            op = "max"
        elif re.search(r"\b(min|minimum|lowest|smallest)\b", query_l):
            op = "min"
        if not op:
            return None

        if not re.search(r"\b(where|when)\b", query_l):
            return None

        projection_col = "timestamp" if "timestamp" in query_cols else query_cols[0]
        metric_candidates = [c for c in query_cols if c != projection_col]
        if not metric_candidates:
            return None
        metric_col = metric_candidates[-1]

        return {
            "sub_queries": cls._typed_steps_to_display([
                {"op": "AGGREGATE_COLUMN", "column": metric_col, "aggregate": op},
                {"op": "FILTER_EQ_PREV", "column": metric_col},
                {"op": "SELECT_LIST", "column": projection_col},
            ]),
            "synthesis_hint": (
                f"Return all {projection_col} values for rows where {metric_col} equals the dataset {op}."
            ),
            "raw_subqueries": "COMPILED_ARG_EXTREME_PLAN",
            "compiled_plan": True,
            "typed_sub_queries": [
                {"op": "AGGREGATE_COLUMN", "column": metric_col, "aggregate": op},
                {"op": "FILTER_EQ_PREV", "column": metric_col},
                {"op": "SELECT_LIST", "column": projection_col},
            ],
        }

    @classmethod
    def _compile_threshold_count_plan(cls, query: str, meta_cols: list[str]) -> dict | None:
        """Compile count-with-threshold questions into typed deterministic steps."""
        query_l = query.lower()
        if not re.search(r"\b(how many|count|number of)\b", query_l):
            return None

        threshold_match = re.search(
            r"\b(strictly\s+)?(greater than|less than|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\b",
            query_l,
        )
        if not threshold_match:
            return None

        strict_word = threshold_match.group(1) or ""
        relation = threshold_match.group(2)
        value = float(threshold_match.group(3))

        query_cols = [c for c in meta_cols if c.lower() in query_l]
        if not query_cols:
            return None
        filter_col = query_cols[-1]

        comparator = ""
        relation_norm = relation.replace(" ", "")
        if relation in (">", ">=") or relation_norm in (">", ">="):
            comparator = "gte" if relation_norm == ">=" else "gt"
        elif relation in ("<", "<=") or relation_norm in ("<", "<="):
            comparator = "lte" if relation_norm == "<=" else "lt"
        elif relation == "greater than":
            comparator = "gt"
        elif relation == "less than":
            comparator = "lt"

        if strict_word.strip() and comparator == "gte":
            comparator = "gt"
        if strict_word.strip() and comparator == "lte":
            comparator = "lt"
        if not comparator:
            return None

        symbol_by_cmp = {
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
            "eq": "==",
        }
        cmp_symbol = symbol_by_cmp[comparator]

        return {
            "sub_queries": cls._typed_steps_to_display([
                {
                    "op": "FILTER_COMPARE",
                    "column": filter_col,
                    "comparator": comparator,
                    "value": value,
                },
                {"op": "AGGREGATE_COUNT_ROWS"},
            ]),
            "synthesis_hint": (
                f"Return the count of rows where {filter_col} {cmp_symbol} {value}."
            ),
            "raw_subqueries": "COMPILED_THRESHOLD_COUNT_PLAN",
            "compiled_plan": True,
            "typed_sub_queries": [
                {
                    "op": "FILTER_COMPARE",
                    "column": filter_col,
                    "comparator": comparator,
                    "value": value,
                },
                {"op": "AGGREGATE_COUNT_ROWS"},
            ],
        }

    @classmethod
    def _compile_group_median_split_plan(
        cls, query: str, grounding_raw: str, meta_cols: list[str]
    ) -> dict | None:
        """Compile "is group A vs group B (split by median) rougher/higher" plans.

        Detects queries that split the dataset into two groups via a median
        threshold on one column, then compare an aggregate of another column
        (typically a DERIVED_STAT/GROUP_COMPARE grounding) between the groups.
        Relies on Stage 2 having emitted MEDIAN(column) / GROUP_COMPARE(...)
        expressions so the split and metric columns are taken directly from
        the grounding rather than re-guessed from free text.
        """
        query_l = query.lower()

        comparison_signal = re.search(
            r"\b(rougher|smoother|higher|lower|greater|more|less|bumpier|"
            r"compare|versus|vs\.?|different)\b",
            query_l,
        )
        split_signal = re.search(
            r"\b(half|median|above|below|north|south|upper|lower)\b", query_l
        )
        if not comparison_signal or not split_signal:
            return None

        median_match = re.search(r"MEDIAN\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", grounding_raw)
        if not median_match:
            return None
        split_col = median_match.group(1)
        if split_col not in meta_cols:
            return None

        metric_col = None
        group_compare_match = re.search(
            r"GROUP_COMPARE\([^,]+,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,", grounding_raw
        )
        if group_compare_match:
            metric_col = group_compare_match.group(1)
        else:
            mean_matches = [
                m for m in re.findall(r"MEAN\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", grounding_raw)
                if m != split_col
            ]
            if mean_matches:
                metric_col = mean_matches[0]

        if metric_col is None:
            candidates = [c for c in meta_cols if c.lower() in query_l and c != split_col]
            if len(candidates) != 1:
                return None
            metric_col = candidates[0]

        if metric_col not in meta_cols:
            return None

        typed_steps = [
            {"op": "SPLIT_BY_THRESHOLD", "column": split_col, "comparator": "gt", "label": "group_a"},
            {"op": "SPLIT_BY_THRESHOLD", "column": split_col, "comparator": "lte", "label": "group_b"},
            {
                "op": "GROUP_AGGREGATE",
                "column": metric_col,
                "aggregate": "mean",
                "groups": ["group_a", "group_b"],
            },
            {"op": "COMPARE_GROUPS"},
        ]

        return {
            "sub_queries": cls._typed_steps_to_display(typed_steps),
            "synthesis_hint": (
                f"State which group (split by {split_col} median) has the higher mean "
                f"{metric_col}, and report both group means."
            ),
            "raw_subqueries": "COMPILED_GROUP_MEDIAN_SPLIT_PLAN",
            "compiled_plan": True,
            "typed_sub_queries": typed_steps,
        }

    def __init__(self, client: "LLMClient") -> None:
        """
        Args:
            client: LLMClient wrapping a chat model.
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
            sub_queries = re.findall(r"SUB_Q\\d+:\\s*(.+)", response)
            hints       = re.findall(r"SYNTHESIS_HINT:\\s*(.+)", response)
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
        sub_queries = self._normalize_sub_queries(sub_queries)
        hints = re.findall(r"SYNTHESIS_HINT:\s*(.+)", response)
        synthesis_hint = (
            hints[0].strip()
            if hints
            else "Combine all sub-answers into a direct response."
        )

        if not sub_queries:
            sub_queries = [f"[AGGREGATE] {query}"]

        compiled = self._compile_threshold_count_plan(
            query=query,
            meta_cols=self._extract_meta_columns(meta_str),
        )
        if compiled is not None:
            return compiled

        compiled = self._compile_arg_extreme_plan(
            query=query,
            meta_cols=self._extract_meta_columns(meta_str),
        )
        if compiled is not None:
            return compiled

        compiled = self._compile_group_median_split_plan(
            query=query,
            grounding_raw=grounding_raw,
            meta_cols=self._extract_meta_columns(meta_str),
        )
        if compiled is not None:
            return compiled

        return {
            "sub_queries": sub_queries,
            "synthesis_hint": synthesis_hint,
            "raw_subqueries": response,
            "compiled_plan": False,
        }
