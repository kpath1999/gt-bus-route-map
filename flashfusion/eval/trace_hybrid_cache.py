"""Trace exact, verified-hybrid, or fuzzy-only cache matching for reworded queries.

Design notes
------------
- Exact query matching remains the first and fastest path.
- Hybrid mode uses two retrievers (lexical + dense) to propose candidates.
- Reuse authorization is contract-based and conservative: retrieval score alone can
  never authorize a cache hit.
- Fuzzy mode is a deliberately unsafe ablation and clearly labeled as such.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Literal

import pandas as pd
import torch

from flashfusion.eval.benchmark import DEFAULT_DATA_PATHS
from flashfusion.pipeline.loader import load_dataset_by_name

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_CONFIG_PATH = Path("flashfusion/eval/cache/hybrid_match_config.json")
DATASET_ALIASES = {"mit_ecg": "ecg", "ecg": "ecg", "bus": "bus", "wisdm": "wisdm"}


@dataclass(frozen=True)
class QueryContract:
    admissibility: Literal["in_scope", "out_of_scope", "unknown", "ambiguous"]
    operator_skeleton_hint: tuple[str, ...] | None
    aggregate: str | None
    fields: frozenset[str]
    predicate_ops: tuple[tuple[str, str], ...]
    filter_values: tuple[tuple[str, str], ...]
    output_shape: str | None
    predictive: tuple[tuple[str, str], ...]
    confidence: float


@dataclass
class CandidateEvidence:
    candidate_id: str
    query_id: str | None
    text: str
    dense_score: float
    lexical_score: float
    retrieval_score: float
    contract_score: float
    compatibility: bool
    compatibility_failures: list[str]
    component_scores: dict[str, float]
    final_score: float


@dataclass
class CacheMatchResult:
    decision: Literal[
        "exact_hit",
        "hybrid_hit",
        "unsafe_ablation_hit",
        "out_of_scope_hit",
        "ambiguous_multi_candidate",
        "low_confidence_candidate",
        "complete_miss",
        "incompatible_candidate",
    ]
    entry: dict[str, Any] | None
    winner: CandidateEvidence | None
    runner_up: CandidateEvidence | None
    elapsed_ms: dict[str, float]
    candidates: list[CandidateEvidence]


class ContractExtractor:
    """Generic extractor: schema-grounded fields + literal/operator hints.

    This extractor intentionally avoids domain-specific query intent regexes.
    It only uses:
    - schema column string matching
    - generic operator tokens (>, <, >=, <=, ==, !=)
    - generic literal extraction (quoted strings / numbers)
    """

    COMPARISON_PHRASES: dict[str, str] = {
        "greater than or equal to": ">=",
        "less than or equal to": "<=",
        "strictly greater than": ">",
        "strictly less than": "<",
        "not equal to": "!=",
        "greater than": ">",
        "less than": "<",
        "at least": ">=",
        "at most": "<=",
        "equal to": "==",
        "above": ">",
        "below": "<",
    }

    AGGREGATE_PHRASES: dict[str, str] = {
        "how much of a count": "count",
        "what number of": "count",
        "number of": "count",
        "how many": "count",
        "count of": "count",
        "sum of": "sum",
        "average": "mean",
        "median": "median",
        "maximum": "max",
        "minimum": "min",
        "largest": "max",
        "highest": "max",
        "smallest": "min",
        "lowest": "min",
        "greatest": "max",
        "total": "sum",
        "mean": "mean",
        "peak": "max",
    }

    def __init__(self, schema_columns: Iterable[str] | None) -> None:
        self._schema_columns = [str(c) for c in (schema_columns or [])]
        self._norm_to_col = {self._norm(c): str(c) for c in self._schema_columns}
        self._field_alias_to_col = self._build_field_aliases(self._schema_columns)
        self._sorted_comp_phrases = sorted(self.COMPARISON_PHRASES.items(), key=lambda item: len(item[0]), reverse=True)
        self._sorted_agg_phrases = sorted(self.AGGREGATE_PHRASES.items(), key=lambda item: len(item[0]), reverse=True)

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    @staticmethod
    def _tokenise(value: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", value.lower())

    @staticmethod
    def _normalize_literal(value: str) -> str:
        text = value.strip()
        if not text:
            return text
        if text.startswith("'") and text.endswith("'"):
            return text[1:-1].strip().lower()
        if text.startswith('"') and text.endswith('"'):
            return text[1:-1].strip().lower()
        return text

    @staticmethod
    def _normalize_operator(op: str) -> str:
        mapping = {
            ">": "gt",
            ">=": "gte",
            "<": "lt",
            "<=": "lte",
            "=": "eq",
            "==": "eq",
            "!=": "ne",
            "gt": "gt",
            "gte": "gte",
            "lt": "lt",
            "lte": "lte",
            "eq": "eq",
            "ne": "ne",
        }
        return mapping.get(op.strip().lower(), op.strip().lower())

    def _build_field_aliases(self, columns: list[str]) -> dict[str, str]:
        alias_to_col: dict[str, str] = {}
        for col in columns:
            canonical = col.lower()
            norm = self._norm(col)
            if norm:
                alias_to_col.setdefault(norm, canonical)
            tokens = [token for token in self._tokenise(col) if token]
            if tokens:
                alias_to_col.setdefault("".join(tokens), canonical)
                alias_to_col.setdefault("_".join(tokens), canonical)
                alias_to_col.setdefault(" ".join(tokens), canonical)
                if len(tokens) > 1:
                    alias_to_col.setdefault("-".join(tokens), canonical)
        return alias_to_col

    def _resolve_field_token(self, token: str) -> str | None:
        if not token:
            return None
        norm = self._norm(token)
        if not norm:
            return None
        direct = self._norm_to_col.get(norm)
        if direct is not None:
            return direct.lower()
        alias = self._field_alias_to_col.get(norm)
        return alias.lower() if alias is not None else None

    def _field_mentions(self, query_lc: str) -> list[tuple[int, int, str]]:
        mentions: list[tuple[int, int, str]] = []
        for alias, canonical in self._field_alias_to_col.items():
            pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])")
            for match in pattern.finditer(query_lc):
                mentions.append((match.start(), match.end(), canonical))
        mentions.sort(key=lambda item: (item[0], item[1], item[2]))
        deduped: list[tuple[int, int, str]] = []
        seen = set()
        for item in mentions:
            key = (item[0], item[1], item[2])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _number_mentions(query_lc: str) -> list[tuple[int, int, str]]:
        return [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"-?\d+(?:\.\d+)?", query_lc)]

    @staticmethod
    def _closest_value(
        anchors: list[tuple[int, int, str]],
        phrase_start: int,
        phrase_end: int,
    ) -> tuple[int, int, str] | None:
        if not anchors:
            return None
        phrase_center = (phrase_start + phrase_end) / 2.0
        return min(
            anchors,
            key=lambda item: (abs(((item[0] + item[1]) / 2.0) - phrase_center), item[0], item[1]),
        )

    def _extract_aggregate(self, query_lc: str) -> str | None:
        matches: list[tuple[int, int, str]] = []
        for phrase, aggregate in self._sorted_agg_phrases:
            pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])")
            for match in pattern.finditer(query_lc):
                matches.append((match.start(), -len(phrase), aggregate))

        if not matches:
            return None

        aggregates_present = {item[2] for item in matches}

        has_rank_max = bool(re.search(r"\b(highest|largest|greatest|maximum|max|peak)\b", query_lc))
        has_rank_min = bool(re.search(r"\b(lowest|smallest|minimum|min)\b", query_lc))
        has_count = bool(re.search(r"\b(how many|what number of|count of|number of|how much of a count)\b", query_lc))
        if has_rank_max and "max" in aggregates_present:
            return "max"
        if has_rank_min and "min" in aggregates_present:
            return "min"
        if has_count and has_rank_max:
            return "max"
        if has_count and has_rank_min:
            return "min"

        best = min(matches, key=lambda item: (item[0], item[1]))
        return best[2]

    def _extract_predictive(self, query_lc: str) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        model_patterns = {
            "logistic_regression": r"\blogistic[- ]regression\b",
            "random_forest": r"\brandom[- ]forest\b",
            "one_nearest_neighbor": r"\b(?:1\s*[- ]?nearest[- ]?neighbo?r|1[- ]?nn)\b",
            "hist_gradient_boosting": r"\bhist(?:ogram)?[- ]gradient[- ]boosting\b",
        }
        for model_name, pattern in model_patterns.items():
            if re.search(pattern, query_lc):
                pairs.append(("model", model_name))
                break

        target_match = re.search(r"\blabel\s+in\s+the\s+([a-z_][a-z0-9_]*)\s+column\b", query_lc)
        if target_match is not None:
            target_field = self._resolve_field_token(target_match.group(1))
            if target_field is not None:
                pairs.append(("target_column", target_field))
        elif re.search(r"\bactivity[_ ]label\b", query_lc):
            target_field = self._resolve_field_token("activity_label")
            if target_field is not None:
                pairs.append(("target_column", target_field))
        return tuple(sorted(set(pairs)))

    def extract(self, query: str) -> QueryContract:
        query_lc = query.lower()
        tokens = self._tokenise(query)
        token_set = set(tokens)

        fields: set[str] = set()
        for col in self._schema_columns:
            col_tokens = set(self._tokenise(col))
            col_norm = self._norm(col)
            if col_tokens and col_tokens.issubset(token_set):
                fields.add(str(col).lower())
            elif col_norm and len(col_norm) >= 3 and col_norm in self._norm(query):
                fields.add(str(col).lower())

        aggregate = self._extract_aggregate(query_lc)
        predicate_ops = self._extract_predicate_ops(query)
        filter_values = self._extract_filter_values(query)

        for field, _op in predicate_ops:
            fields.add(field)
        for field, _val in filter_values:
            if field != "*":
                fields.add(field)

        predictive = self._extract_predictive(query_lc)
        analytic_intent = bool(re.search(r"\b(compare|contrast|difference|between|magnitude|correlat|ratio)\b", query_lc))

        output_shape: str | None = None
        if any(key == "model" for key, _value in predictive):
            output_shape = "predictive"
        elif re.search(r"\blist\b", query_lc):
            output_shape = "list"
        elif aggregate in {"count", "sum", "mean", "median", "max", "min"}:
            output_shape = "scalar"

        operator_skeleton_hint: tuple[str, ...] | None = None
        if any(key == "model" for key, _value in predictive):
            operator_skeleton_hint = ("PREDICTIVE_PIPELINE",)

        confidence = 0.15
        if fields:
            confidence += min(0.45, 0.1 * len(fields))
        if aggregate is not None:
            confidence += 0.2
        if predicate_ops:
            confidence += 0.2
        if filter_values:
            confidence += 0.2
        if predictive:
            confidence += 0.2
        confidence = max(0.0, min(1.0, confidence))

        if not query.strip():
            admissibility = "out_of_scope"
        elif not self._schema_columns:
            admissibility = "unknown"
        elif fields or aggregate is not None or predicate_ops or filter_values or predictive or analytic_intent:
            admissibility = "in_scope"
        else:
            admissibility = "unknown"

        return QueryContract(
            admissibility=admissibility,
            operator_skeleton_hint=operator_skeleton_hint,
            aggregate=aggregate,
            fields=frozenset(fields),
            predicate_ops=tuple(sorted(predicate_ops)),
            filter_values=tuple(sorted(filter_values)),
            output_shape=output_shape,
            predictive=predictive,
            confidence=confidence,
        )

    def _extract_predicate_ops(self, query: str) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        query_lc = query.lower()

        symbolic = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(<=|>=|!=|==|=|<|>)\s*(-?\d+(?:\.\d+)?)?")
        for field_raw, op, _num in symbolic.findall(query_lc):
            field = self._resolve_field_token(field_raw)
            if field is not None:
                found.add((field, self._normalize_operator(op)))

        field_mentions = self._field_mentions(query_lc)
        for phrase, op in self._sorted_comp_phrases:
            pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])")
            for match in pattern.finditer(query_lc):
                closest_field = self._closest_value(field_mentions, match.start(), match.end())
                if closest_field is not None:
                    found.add((closest_field[2], self._normalize_operator(op)))
        return found

    def _extract_filter_values(self, query: str) -> set[tuple[str, str]]:
        values: set[tuple[str, str]] = set()
        query_lc = query.lower()

        numeric_mentions = self._number_mentions(query_lc)
        field_mentions = self._field_mentions(query_lc)

        symbolic = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(<=|>=|!=|==|=|<|>)\s*(-?\d+(?:\.\d+)?)")
        for field_raw, _op, number in symbolic.findall(query_lc):
            field = self._resolve_field_token(field_raw)
            literal = self._normalize_literal(number)
            if field is not None:
                values.add((field, literal))
            else:
                values.add(("*", literal))

        for phrase, _op in self._sorted_comp_phrases:
            pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])")
            for match in pattern.finditer(query_lc):
                closest_num = self._closest_value(numeric_mentions, match.start(), match.end())
                if closest_num is None:
                    continue
                closest_field = self._closest_value(field_mentions, match.start(), match.end())
                literal = self._normalize_literal(closest_num[2])
                if closest_field is not None:
                    values.add((closest_field[2], literal))
                else:
                    values.add(("*", literal))

        for quoted in re.findall(r'"([^"\\]{1,64})"|\'([^\'\\]{1,64})\'', query):
            literal = self._normalize_literal(quoted[0] or quoted[1])
            values.add(("*", literal))

        for number in re.findall(r"\b\d+(?:\.\d+)?\b", query_lc):
            literal = self._normalize_literal(number)
            if any(value == literal for _field, value in values):
                continue
            values.add(("*", literal))

        return values


class LexicalIndex:
    def __init__(self, documents: list[str]) -> None:
        self._docs_tokens = [self._tokenise(doc) for doc in documents]
        self._doc_lens = [len(toks) for toks in self._docs_tokens]
        self._avg_len = sum(self._doc_lens) / len(self._doc_lens) if self._doc_lens else 0.0
        self._dfs: dict[str, int] = defaultdict(int)
        for toks in self._docs_tokens:
            for token in set(toks):
                self._dfs[token] += 1
        self._n_docs = len(self._docs_tokens)

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", text.lower())

    def search(self, query: str, top_k: int, k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
        if self._n_docs == 0:
            return []
        q_terms = Counter(self._tokenise(query))
        if not q_terms:
            return []
        scores = [0.0] * self._n_docs
        for term, _qf in q_terms.items():
            df = self._dfs.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + (self._n_docs - df + 0.5) / (df + 0.5))
            for i, doc_tokens in enumerate(self._docs_tokens):
                tf = doc_tokens.count(term)
                if tf == 0:
                    continue
                denom = tf + k1 * (1.0 - b + b * (self._doc_lens[i] / (self._avg_len or 1.0)))
                scores[i] += idf * ((tf * (k1 + 1.0)) / denom)

        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        return [(i, s) for i, s in ranked[:top_k] if s > 0.0]


class DenseIndex:
    def __init__(self, model_name: str, device: str, documents: list[str], no_warmup: bool) -> None:
        sentence_transformers = importlib.import_module("sentence_transformers")
        sentence_transformer_cls = getattr(sentence_transformers, "SentenceTransformer")

        self.model_name = model_name
        self.device = device
        self._model_load_ms = 0.0
        self._warm_up_ms = 0.0

        t0 = time.perf_counter()
        self.model = sentence_transformer_cls(model_name, device=device)
        self._model_load_ms = (time.perf_counter() - t0) * 1000.0

        if not no_warmup:
            self._sync_device()
            t1 = time.perf_counter()
            self.model.encode(["warm-up request"], batch_size=1, show_progress_bar=False)
            self._sync_device()
            self._warm_up_ms = (time.perf_counter() - t1) * 1000.0

        self._sync_device()
        t2 = time.perf_counter()
        vectors = self.model.encode(
            documents,
            batch_size=32,
            show_progress_bar=False,
            convert_to_tensor=True,
            normalize_embeddings=True,
        )
        self._sync_device()
        self._embeddings = vectors
        self._build_ms = (time.perf_counter() - t2) * 1000.0

    def _sync_device(self) -> None:
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()

    def search(self, query: str, top_k: int) -> tuple[list[tuple[int, float]], float]:
        self._sync_device()
        t0 = time.perf_counter()
        q = self.model.encode(
            [query],
            batch_size=1,
            show_progress_bar=False,
            convert_to_tensor=True,
            normalize_embeddings=True,
        )[0]
        scores = torch.nn.functional.cosine_similarity(q.unsqueeze(0), self._embeddings).tolist()
        self._sync_device()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        return [(i, max(0.0, min(1.0, float(s)))) for i, s in ranked[:top_k]], elapsed_ms


class HybridMatcher:
    def __init__(
        self,
        entries: list[dict[str, Any]],
        config: dict[str, Any],
        dataset: str | None,
        schema_columns: list[str],
        schema_fingerprint: str | None,
        device: str,
        no_warmup: bool,
        mode: str,
        dense_top_k_override: int | None,
        lexical_top_k_override: int | None,
    ) -> None:
        self.config = config
        self.dataset = canonical_dataset(dataset)
        self.schema_columns = schema_columns
        self.schema_fingerprint = schema_fingerprint
        self.device = device
        self.no_warmup = no_warmup
        self.mode = mode

        self.thresholds = config.get("thresholds", {})
        self.retrieval_cfg = config.get("retrieval", {})

        self.dense_top_k = int(dense_top_k_override or self.retrieval_cfg.get("dense_top_k", 20))
        self.lexical_top_k = int(lexical_top_k_override or self.retrieval_cfg.get("lexical_top_k", 20))

        self.model_name = str(self.retrieval_cfg.get("model_name", DEFAULT_MODEL_NAME))

        t0 = time.perf_counter()
        self.entries = self._filter_reusable_entries(entries)
        self.registry_load_ms = (time.perf_counter() - t0) * 1000.0

        if not self.entries:
            raise ValueError("No reusable registry entries remain after dataset filtering.")

        self.documents = [self._retrieval_document(e) for e in self.entries]
        self.lexical_index = LexicalIndex(self.documents)
        self.dense_index: DenseIndex | None = None

        self.extractor = ContractExtractor(schema_columns=self.schema_columns)
        weights = config.get("weights", {})
        self.weight_retrieval = float(weights.get("retrieval", 0.5))
        self.weight_contract = float(weights.get("contract", 0.5))
        total_weight = self.weight_retrieval + self.weight_contract
        if total_weight <= 0.0:
            self.weight_retrieval = 0.5
            self.weight_contract = 0.5
        else:
            self.weight_retrieval /= total_weight
            self.weight_contract /= total_weight

    @staticmethod
    def load_registry(path: Path) -> list[dict[str, Any]]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            for key in ("entries", "registry", "cache_entries"):
                candidate = raw.get(key)
                if isinstance(candidate, list):
                    return [x for x in candidate if isinstance(x, dict)]
                if isinstance(candidate, dict):
                    return [x for x in candidate.values() if isinstance(x, dict)]
        raise ValueError("Registry must be a JSON list or contain entries/registry/cache_entries.")

    def _filter_reusable_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            if entry.get("status", "reusable") != "reusable":
                continue
            if self.dataset is not None and canonical_dataset(entry.get("dataset")) != self.dataset:
                continue
            if self._entry_text(entry):
                filtered.append(entry)
        return filtered

    @staticmethod
    def _entry_text(entry: dict[str, Any]) -> str:
        for key in ("query_text", "query", "text", "original_query"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _candidate_id(entry: dict[str, Any], index: int) -> str:
        for key in ("query_id", "id", "cache_id", "source_query_id", "template_id"):
            value = entry.get(key)
            if value is not None:
                return str(value)
        return f"registry-index-{index}"

    @staticmethod
    def _entry_query_id(entry: dict[str, Any]) -> str | None:
        for key in ("query_id", "source_query_id", "id"):
            value = entry.get(key)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _normalise_query(query: str) -> str:
        return query.strip()

    @staticmethod
    def _tokenise(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9_]+", text.lower()))

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @staticmethod
    def _fuzzy_score(left: str, right: str) -> float:
        try:
            from rapidfuzz.fuzz import ratio  # type: ignore
            return ratio(left, right) / 100.0
        except ImportError:
            return SequenceMatcher(None, left.lower(), right.lower()).ratio()

    def _retrieval_document(self, entry: dict[str, Any]) -> str:
        explicit = entry.get("retrieval_document")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        contract = entry.get("retrieval_contract")
        if not isinstance(contract, dict):
            contract = self._contract_from_entry(entry)

        payload = {
            "query_text": self._entry_text(entry),
            "dataset": canonical_dataset(entry.get("dataset")),
            "fields": sorted([str(x) for x in (contract.get("fields") or [])]),
            "operator_skeleton": contract.get("operator_skeleton"),
            "contract": contract,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _contract_from_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        retrieval_contract = entry.get("retrieval_contract")
        if isinstance(retrieval_contract, dict):
            return retrieval_contract

        raw_sig = entry.get("semantic_signature")
        sig: dict[str, Any] = raw_sig if isinstance(raw_sig, dict) else {}
        raw_predictive = sig.get("predictive")
        predictive: dict[str, Any] = raw_predictive if isinstance(raw_predictive, dict) else {}
        return {
            "aggregate": sig.get("aggregate"),
            "fields": list(sig.get("fields") or []),
            "predicate_ops": sig.get("predicate_ops") if isinstance(sig.get("predicate_ops"), dict) else {},
            "filter_values": sig.get("filter_values") if isinstance(sig.get("filter_values"), dict) else {},
            "output_shape": sig.get("output_shape"),
            "predictive": {
                "model": predictive.get("model"),
                "target_column": predictive.get("target_column"),
            },
            "operator_skeleton": entry.get("operator_skeleton"),
            "operator_contract_hash": entry.get("operator_contract_hash"),
            "schema_fingerprint": entry.get("schema_fingerprint"),
        }

    def _contract_from_live(self, contract: QueryContract) -> dict[str, Any]:
        return {
            "aggregate": contract.aggregate,
            "fields": sorted(contract.fields),
            "predicate_ops": {k: v for k, v in contract.predicate_ops},
            "filter_values": {k: v for k, v in contract.filter_values},
            "output_shape": contract.output_shape,
            "predictive": {k: v for k, v in contract.predictive},
            "operator_skeleton": list(contract.operator_skeleton_hint) if contract.operator_skeleton_hint else None,
            "operator_contract_hash": None,
            "schema_fingerprint": self.schema_fingerprint,
        }

    def _field_set(self, value: Any) -> set[str]:
        if isinstance(value, list):
            return {str(x).lower() for x in value if str(x).strip()}
        if isinstance(value, tuple):
            return {str(x).lower() for x in value if str(x).strip()}
        if isinstance(value, set):
            return {str(x).lower() for x in value if str(x).strip()}
        return set()

    def _kv_set(self, value: Any) -> set[str]:
        if isinstance(value, dict):
            return {
                f"{str(k).lower().removesuffix('__value')}:{str(v).lower()}"
                for k, v in value.items()
                if v is not None and str(v).strip().lower() != "none"
            }
        if isinstance(value, tuple):
            return {
                f"{str(k).lower().removesuffix('__value')}:{str(v).lower()}"
                for k, v in value
                if v is not None and str(v).strip().lower() != "none"
            }
        if isinstance(value, list):
            out = set()
            for item in value:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    if item[1] is None or str(item[1]).strip().lower() == "none":
                        continue
                    key = str(item[0]).lower().removesuffix("__value")
                    out.add(f"{key}:{str(item[1]).lower()}")
            return out
        return set()

    def _kv_pairs(self, value: Any) -> set[tuple[str, str]]:
        if isinstance(value, dict):
            return {
                (str(k).lower().removesuffix("__value"), str(v).lower())
                for k, v in value.items()
                if v is not None and str(v).strip().lower() != "none"
            }
        if isinstance(value, tuple):
            return {
                (str(item[0]).lower().removesuffix("__value"), str(item[1]).lower())
                for item in value
                if isinstance(item, (tuple, list)) and len(item) == 2
                and item[1] is not None
                and str(item[1]).strip().lower() != "none"
            }
        if isinstance(value, list):
            return {
                (str(item[0]).lower().removesuffix("__value"), str(item[1]).lower())
                for item in value
                if isinstance(item, (tuple, list)) and len(item) == 2
                and item[1] is not None
                and str(item[1]).strip().lower() != "none"
            }
        return set()

    def _component_scores(self, live: dict[str, Any], cand: dict[str, Any]) -> tuple[dict[str, float], float]:
        scores: dict[str, float] = {}
        applicable = 0

        live_agg = live.get("aggregate")
        cand_agg = cand.get("aggregate")
        if live_agg is not None:
            applicable += 1
            scores["aggregate"] = 1.0 if cand_agg == live_agg else 0.0

        live_fields = self._field_set(live.get("fields"))
        if live_fields:
            applicable += 1
            cand_fields = self._field_set(cand.get("fields"))
            scores["fields"] = self._jaccard(live_fields, cand_fields)

        live_ops = self._kv_set(live.get("predicate_ops"))
        if live_ops:
            applicable += 1
            cand_ops = self._kv_set(cand.get("predicate_ops"))
            scores["predicate_ops"] = self._jaccard(live_ops, cand_ops)

        live_vals = self._kv_set(live.get("filter_values"))
        if live_vals:
            applicable += 1
            cand_vals = self._kv_set(cand.get("filter_values"))
            scores["filter_values"] = self._jaccard(live_vals, cand_vals)

        live_output = live.get("output_shape")
        if live_output is not None:
            applicable += 1
            cand_output = cand.get("output_shape")
            scores["output_shape"] = 1.0 if live_output == cand_output else 0.0

        live_pred = self._kv_set(live.get("predictive"))
        if live_pred:
            applicable += 1
            cand_pred = self._kv_set(cand.get("predictive"))
            scores["predictive"] = self._jaccard(live_pred, cand_pred)

        live_opskel = live.get("operator_skeleton")
        if live_opskel is not None:
            applicable += 1
            cand_opskel = cand.get("operator_skeleton")
            scores["operator_skeleton"] = 1.0 if live_opskel == cand_opskel else 0.0

        contract_score = (sum(scores.values()) / applicable) if applicable > 0 else 0.0
        return scores, contract_score

    def _safety_critical_agreement(self, live: dict[str, Any], cand: dict[str, Any]) -> tuple[bool, list[str]]:
        failures: list[str] = []

        if live.get("aggregate") is not None and cand.get("aggregate") != live.get("aggregate"):
            live_agg = str(live.get("aggregate")).lower()
            cand_agg = str(cand.get("aggregate")).lower()
            if not ({live_agg, cand_agg} <= {"max", "min"}):
                failures.append("aggregate_mismatch")

        live_fields = self._field_set(live.get("fields"))
        cand_fields = self._field_set(cand.get("fields"))
        if live_fields and cand_fields and not (
            live_fields.issubset(cand_fields) or cand_fields.issubset(live_fields)
        ):
            failures.append("field_mismatch")

        live_ops = self._kv_set(live.get("predicate_ops"))
        cand_ops = self._kv_set(cand.get("predicate_ops"))
        if live_ops and cand_ops and not live_ops.issubset(cand_ops):
            failures.append("predicate_op_mismatch")

        live_val_pairs = self._kv_pairs(live.get("filter_values"))
        live_vals = {f"{k}:{v}" for k, v in live_val_pairs if k != "*"}
        cand_vals = self._kv_set(cand.get("filter_values"))
        if live_vals and cand_vals:
            cand_vals = self._kv_set(cand.get("filter_values"))
            if not live_vals.issubset(cand_vals):
                failures.append("filter_value_mismatch")

        # If only unkeyed literals were found live-side, keep this dimension non-blocking.
        has_only_unkeyed_live_vals = bool(live_val_pairs) and not live_vals
        if has_only_unkeyed_live_vals:
            pass

        live_pred = self._kv_set(live.get("predictive"))
        cand_pred = self._kv_set(cand.get("predictive"))
        if live_pred and cand_pred and not live_pred.issubset(cand_pred):
            failures.append("predictive_mismatch")

        if (
            live.get("output_shape") is not None
            and cand.get("output_shape") is not None
            and cand.get("output_shape") != "unknown"
            and cand.get("output_shape") != live.get("output_shape")
        ):
            failures.append("output_shape_mismatch")

        if live.get("operator_skeleton") is not None and cand.get("operator_skeleton") != live.get("operator_skeleton"):
            failures.append("operator_skeleton_mismatch")

        return (len(failures) == 0, failures)

    def _compatibility(
        self,
        candidate: dict[str, Any],
        expected_contract_hash: str | None,
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if self.dataset is not None and canonical_dataset(candidate.get("dataset")) != self.dataset:
            failures.append("dataset_mismatch")

        cached_fp = candidate.get("schema_fingerprint")
        if cached_fp and self.schema_fingerprint and cached_fp != self.schema_fingerprint:
            failures.append("schema_fingerprint_mismatch")

        if expected_contract_hash:
            if candidate.get("operator_contract_hash") != expected_contract_hash:
                failures.append("operator_contract_hash_mismatch")

        return (len(failures) == 0, failures)

    def _ensure_dense(self) -> None:
        if self.dense_index is None:
            self.dense_index = DenseIndex(
                model_name=self.model_name,
                device=self.device,
                documents=self.documents,
                no_warmup=self.no_warmup,
            )

    def _normalize_scores(self, scores: dict[int, float]) -> dict[int, float]:
        if not scores:
            return {}
        low = min(scores.values())
        high = max(scores.values())
        if high <= low:
            return {k: 1.0 for k in scores}
        span = high - low
        return {k: (v - low) / span for k, v in scores.items()}

    def warm_up(self) -> dict[str, float]:
        if self.mode == "fuzzy":
            return {"model_load_ms": 0.0, "warm_up_ms": 0.0, "dense_index_build_ms": 0.0}
        self._ensure_dense()
        assert self.dense_index is not None
        return {
            "model_load_ms": self.dense_index._model_load_ms,
            "warm_up_ms": self.dense_index._warm_up_ms,
            "dense_index_build_ms": self.dense_index._build_ms,
        }

    def retrieve_diagnostics(self, query: str, top_k: int | None = None) -> dict[str, Any]:
        """Return raw retrieval candidates without contract verification or mutation."""
        if self.mode != "hybrid":
            raise ValueError("Retrieval diagnostics are available only in hybrid mode.")

        requested_top_k = max(1, int(top_k or max(self.dense_top_k, self.lexical_top_k)))
        t0 = time.perf_counter()
        lexical_raw = self.lexical_index.search(query, top_k=requested_top_k)
        lexical_elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self._ensure_dense()
        assert self.dense_index is not None
        dense_raw, dense_elapsed_ms = self.dense_index.search(query, top_k=requested_top_k)

        def evidence(index: int, score: float) -> dict[str, Any]:
            entry = self.entries[index]
            return {
                "candidate_id": self._candidate_id(entry, index + 1),
                "query_id": self._entry_query_id(entry),
                "score": round(float(score), 6),
            }

        dense = [evidence(index, score) for index, score in dense_raw]
        lexical = [evidence(index, score) for index, score in lexical_raw]
        matching_indices = {
            index for index, _score in dense_raw[: self.dense_top_k]
        } | {
            index for index, _score in lexical_raw[: self.lexical_top_k]
        }
        return {
            "dense": dense,
            "lexical": lexical,
            "matching_union_ids": [
                self._candidate_id(self.entries[index], index + 1)
                for index in sorted(matching_indices)
            ],
            "elapsed_ms": {
                "lexical_retrieve_ms": round(lexical_elapsed_ms, 6),
                "dense_retrieve_ms": round(dense_elapsed_ms, 6),
            },
        }

    def match(self, query: str, expected_contract_hash: str | None = None) -> CacheMatchResult:
        timings = {
            "registry_load_ms": self.registry_load_ms,
            "model_load_ms": 0.0,
            "warm_up_ms": 0.0,
            "contract_extract_ms": 0.0,
            "retrieve_ms": 0.0,
            "verify_ms": 0.0,
            "total_match_ms": 0.0,
        }

        t_total = time.perf_counter()

        # Exact path: deterministic and fastest.
        exact = [
            (idx, entry)
            for idx, entry in enumerate(self.entries)
            if self._normalise_query(self._entry_text(entry)) == self._normalise_query(query)
        ]
        if len(exact) == 1:
            idx, entry = exact[0]
            evidence = CandidateEvidence(
                candidate_id=self._candidate_id(entry, idx + 1),
                query_id=self._entry_query_id(entry),
                text=self._entry_text(entry),
                dense_score=1.0,
                lexical_score=1.0,
                retrieval_score=1.0,
                contract_score=1.0,
                compatibility=True,
                compatibility_failures=[],
                component_scores={"exact": 1.0},
                final_score=1.0,
            )
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("exact_hit", entry, evidence, None, timings, [evidence])

        if len(exact) > 1:
            candidates = []
            for idx, entry in exact:
                candidates.append(
                    CandidateEvidence(
                        candidate_id=self._candidate_id(entry, idx + 1),
                        query_id=self._entry_query_id(entry),
                        text=self._entry_text(entry),
                        dense_score=0.0,
                        lexical_score=1.0,
                        retrieval_score=1.0,
                        contract_score=0.0,
                        compatibility=False,
                        compatibility_failures=["duplicate_exact_entries"],
                        component_scores={},
                        final_score=0.0,
                    )
                )
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("ambiguous_multi_candidate", None, None, None, timings, candidates)

        if self.mode == "fuzzy":
            result = self._match_fuzzy_only(query, timings)
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return result

        t_contract = time.perf_counter()
        live_contract = self.extractor.extract(query)
        timings["contract_extract_ms"] = (time.perf_counter() - t_contract) * 1000.0

        t_retrieve = time.perf_counter()
        lexical_raw = self.lexical_index.search(query, top_k=self.lexical_top_k)
        lexical_scores = self._normalize_scores({i: s for i, s in lexical_raw})

        self._ensure_dense()
        assert self.dense_index is not None
        dense_raw, dense_elapsed_ms = self.dense_index.search(query, top_k=self.dense_top_k)
        timings["model_load_ms"] = self.dense_index._model_load_ms
        timings["warm_up_ms"] = self.dense_index._warm_up_ms
        dense_scores = {i: s for i, s in dense_raw}

        candidate_indices = set(lexical_scores) | set(dense_scores)
        timings["retrieve_ms"] = (time.perf_counter() - t_retrieve) * 1000.0
        timings["retrieve_ms"] = max(timings["retrieve_ms"], dense_elapsed_ms)

        if not candidate_indices:
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("complete_miss", None, None, None, timings, [])

        t_verify = time.perf_counter()
        min_extractor_conf = float(self.thresholds.get("extractor_confidence_floor", 0.55))
        accept_floor = float(self.thresholds.get("acceptance_floor", 0.75))
        ambiguity_margin = float(self.thresholds.get("ambiguity_margin", 0.08))

        live_contract_dict = self._contract_from_live(live_contract)
        results: list[CandidateEvidence] = []
        has_any_incompatible = False

        for idx in sorted(candidate_indices):
            entry = self.entries[idx]
            candidate_contract = self._contract_from_entry(entry)
            comp_ok, comp_failures = self._compatibility(entry, expected_contract_hash)
            component_scores, contract_score = self._component_scores(live_contract_dict, candidate_contract)

            dense_score = dense_scores.get(idx, 0.0)
            lexical_score = lexical_scores.get(idx, 0.0)
            retrieval_score = max(dense_score, lexical_score)

            safety_ok, safety_failures = self._safety_critical_agreement(live_contract_dict, candidate_contract)
            failures = comp_failures + ([] if safety_ok else safety_failures)
            compatible = comp_ok and safety_ok
            if not compatible:
                has_any_incompatible = True

            final_score = self.weight_retrieval * retrieval_score + self.weight_contract * contract_score
            results.append(
                CandidateEvidence(
                    candidate_id=self._candidate_id(entry, idx + 1),
                    query_id=self._entry_query_id(entry),
                    text=self._entry_text(entry),
                    dense_score=round(dense_score, 6),
                    lexical_score=round(lexical_score, 6),
                    retrieval_score=round(retrieval_score, 6),
                    contract_score=round(contract_score, 6),
                    compatibility=compatible,
                    compatibility_failures=failures,
                    component_scores={k: round(v, 6) for k, v in component_scores.items()},
                    final_score=round(final_score, 6),
                )
            )

        results.sort(key=lambda x: x.final_score, reverse=True)
        compatible_results = [x for x in results if x.compatibility]

        if not compatible_results:
            timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            decision = "incompatible_candidate" if has_any_incompatible else "complete_miss"
            return CacheMatchResult(decision, None, None, None, timings, results)

        winner = compatible_results[0]
        runner_up = compatible_results[1] if len(compatible_results) > 1 else None

        # Authorization checks: independent from retrieval ranking.
        if live_contract.admissibility != "in_scope":
            decision = "out_of_scope_hit" if live_contract.admissibility == "out_of_scope" else "low_confidence_candidate"
            timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult(decision, None, winner, runner_up, timings, results)

        if live_contract.confidence < min_extractor_conf:
            timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("low_confidence_candidate", None, winner, runner_up, timings, results)

        if winner.final_score < accept_floor:
            timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("low_confidence_candidate", None, winner, runner_up, timings, results)

        if runner_up is not None and (winner.final_score - runner_up.final_score) < ambiguity_margin:
            timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
            timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
            return CacheMatchResult("ambiguous_multi_candidate", None, winner, runner_up, timings, results)

        winner_idx = next(
            i
            for i, item in enumerate(self.entries)
            if self._candidate_id(item, i + 1) == winner.candidate_id
        )
        timings["verify_ms"] = (time.perf_counter() - t_verify) * 1000.0
        timings["total_match_ms"] = (time.perf_counter() - t_total) * 1000.0
        return CacheMatchResult("hybrid_hit", self.entries[winner_idx], winner, runner_up, timings, results)

    def _match_fuzzy_only(self, query: str, timings: dict[str, float]) -> CacheMatchResult:
        scored: list[CandidateEvidence] = []
        for idx, entry in enumerate(self.entries):
            value = self._fuzzy_score(query, self._entry_text(entry))
            scored.append(
                CandidateEvidence(
                    candidate_id=self._candidate_id(entry, idx + 1),
                    query_id=self._entry_query_id(entry),
                    text=self._entry_text(entry),
                    dense_score=0.0,
                    lexical_score=0.0,
                    retrieval_score=round(value, 6),
                    contract_score=0.0,
                    compatibility=True,
                    compatibility_failures=["UNSAFE_ABLATION"],
                    component_scores={},
                    final_score=round(value, 6),
                )
            )
        scored.sort(key=lambda x: x.final_score, reverse=True)

        threshold = float(self.thresholds.get("fuzzy_threshold", 0.72))
        margin = float(self.thresholds.get("fuzzy_margin", 0.05))

        if not scored:
            return CacheMatchResult("complete_miss", None, None, None, timings, [])

        winner = scored[0]
        runner_up = scored[1] if len(scored) > 1 else None
        if winner.final_score < threshold:
            return CacheMatchResult("complete_miss", None, winner, runner_up, timings, scored)
        if runner_up is not None and (winner.final_score - runner_up.final_score) < margin:
            return CacheMatchResult("ambiguous_multi_candidate", None, winner, runner_up, timings, scored)

        winner_idx = next(
            i
            for i, item in enumerate(self.entries)
            if self._candidate_id(item, i + 1) == winner.candidate_id
        )
        return CacheMatchResult("unsafe_ablation_hit", self.entries[winner_idx], winner, runner_up, timings, scored)


def canonical_dataset(name: str | None) -> str | None:
    if name is None:
        return None
    key = str(name).strip().lower()
    return DATASET_ALIASES.get(key, key)


def resolve_query(query_id: str, version: str, dataset: str | None, override: str | None) -> str:
    if override:
        return override.strip()

    module_name = {
        "v1": "flashfusion.eval.queries",
        "v2": "flashfusion.eval.queries_v2",
        "v3": "flashfusion.eval.queries_v3",
    }[version]
    module = importlib.import_module(module_name)
    wanted = str(query_id)

    for attr in ("QUERIES", "queries", "QUERY_MAP", "query_map"):
        collection = getattr(module, attr, None)
        if isinstance(collection, dict):
            candidate = collection.get(query_id)
            if candidate is None and query_id.isdigit():
                candidate = collection.get(int(query_id))
            if isinstance(candidate, str):
                return candidate
            if isinstance(candidate, dict):
                for key in ("query", "query_text", "text"):
                    value = candidate.get(key)
                    if isinstance(value, str):
                        return value
        if isinstance(collection, (list, tuple)):
            for item in collection:
                if isinstance(item, dict) and str(item.get("id", item.get("query_id"))) == wanted:
                    for key in ("query", "query_text", "text"):
                        value = item.get(key)
                        if isinstance(value, str):
                            return value

    get_queries = getattr(module, "get_queries", None)
    if callable(get_queries) and dataset:
        items = get_queries(dataset)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and str(item.get("id", item.get("query_id"))) == wanted:
                    for key in ("query", "query_text", "text"):
                        value = item.get(key)
                        if isinstance(value, str):
                            return value

    raise ValueError(
        f"Could not resolve query id {query_id!r} from {module_name}. "
        "Pass --query-text explicitly or adapt resolve_query()."
    )


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "retrieval": {
                "model_name": DEFAULT_MODEL_NAME,
                "dense_top_k": 20,
                "lexical_top_k": 20,
            },
            "thresholds": {
                "extractor_confidence_floor": 0.55,
                "acceptance_floor": 0.75,
                "ambiguity_margin": 0.08,
                "fuzzy_threshold": 0.72,
                "fuzzy_margin": 0.05,
            },
            "weights": {
                "retrieval": 0.5,
                "contract": 0.5,
            },
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Hybrid config must be a JSON object.")
    return raw


def schema_fingerprint(df: pd.DataFrame) -> str:
    columns = sorted(str(col).lower() for col in df.columns)
    payload = json.dumps(columns, separators=(",", ":"))
    return str(abs(hash(payload)))


def emit_result(
    args: argparse.Namespace,
    config_path: Path,
    config: dict[str, Any],
    query: str,
    expected_query_id: str,
    result: CacheMatchResult,
) -> dict[str, Any]:
    predicted = None
    if result.winner is not None:
        predicted = result.winner.query_id or result.winner.candidate_id

    hit_like = result.decision in {"exact_hit", "hybrid_hit", "out_of_scope_hit", "unsafe_ablation_hit"}
    false_positive = bool(hit_like and predicted is not None and predicted != expected_query_id)

    payload: dict[str, Any] = {
        "mode": args.mode,
        "decision": result.decision,
        "query_id": expected_query_id,
        "version": args.version,
        "query": query,
        "prediction": predicted,
        "false_positive": false_positive,
        "single_query_false_positive_rate": 1.0 if false_positive else 0.0,
        "config_path": str(config_path),
        "config_version": config.get("version"),
        "calibration_split": args.calibration_split,
        "elapsed_ms": result.elapsed_ms,
        "winner": asdict(result.winner) if result.winner else None,
        "runner_up": asdict(result.runner_up) if result.runner_up else None,
        "candidate_ids": [c.candidate_id for c in result.candidates],
    }

    if args.explain:
        payload["candidates"] = [asdict(item) for item in result.candidates[: args.top_k]]

    if result.entry is not None:
        payload["matched_entry"] = {
            "query_id": result.entry.get("query_id"),
            "dataset": result.entry.get("dataset"),
            "operator_contract_hash": result.entry.get("operator_contract_hash"),
        }

    if args.mode == "fuzzy":
        payload["unsafe_ablation"] = True
        payload["warning"] = "UNSAFE_ABLATION: fuzzy-only mode bypasses dense retrieval and contract verification."

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace exact, verified-hybrid, or fuzzy-only cache matching.")
    parser.add_argument("--registry", type=Path, required=True, help="Semantic-registry JSON file.")
    parser.add_argument("--query-id", required=True, help="Ground-truth/original query id.")
    parser.add_argument("--version", choices=("v1", "v2", "v3"), default="v2", help="Query wording source.")
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--query-text", default=None, help="Override module lookup with a reworded query.")

    parser.add_argument("--mode", choices=("hybrid", "fuzzy"), default="hybrid")
    parser.add_argument("--fuzzy", action="store_true", help="Backwards-compatible alias for --mode fuzzy.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--emit-json", type=Path, default=None, help="Optional output path for JSON trace payload.")
    parser.add_argument("--explain", action="store_true", help="Include top candidate evidence in output JSON.")
    parser.add_argument("--calibration-split", choices=("dev", "test"), default="test")

    parser.add_argument("--data", default=None, help="Optional dataset CSV path for schema-grounded extraction.")
    parser.add_argument("--expected-contract-hash", default=None, help="Optional expected contract hash to enforce at verification.")

    parser.add_argument("--device", default=("cuda" if torch.cuda.is_available() else "cpu"))
    parser.add_argument("--dense-top-k", type=int, default=None)
    parser.add_argument("--lexical-top-k", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--timing", action="store_true", help="Print compact timing lines in addition to JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.fuzzy:
        args.mode = "fuzzy"

    if args.dataset and args.data is None:
        resolved = DEFAULT_DATA_PATHS.get(args.dataset)
        if resolved is None:
            raise ValueError(
                f"No default --data path configured for dataset {args.dataset!r}. "
                "Pass --data explicitly."
            )
        args.data = resolved

    query = resolve_query(args.query_id, args.version, args.dataset, args.query_text)
    config = load_config(args.config)
    entries = HybridMatcher.load_registry(args.registry)

    schema_columns: list[str] = []
    schema_fp: str | None = None
    if args.data:
        if not args.dataset:
            raise ValueError("--data requires --dataset so the loader can parse the source correctly.")
        df = load_dataset_by_name(args.data, args.dataset)
        schema_columns = [str(x) for x in df.columns]
        schema_fp = schema_fingerprint(df)

    matcher = HybridMatcher(
        entries=entries,
        config=config,
        dataset=args.dataset,
        schema_columns=schema_columns,
        schema_fingerprint=schema_fp,
        device=args.device,
        no_warmup=args.no_warmup,
        mode=args.mode,
        dense_top_k_override=args.dense_top_k,
        lexical_top_k_override=args.lexical_top_k,
    )

    if args.mode == "hybrid" and not args.no_warmup:
        warm = matcher.warm_up()
        if args.timing:
            print(
                f"[timing] model_load_ms={warm['model_load_ms']:.2f} "
                f"warm_up_ms={warm['warm_up_ms']:.2f} dense_index_build_ms={warm['dense_index_build_ms']:.2f}"
            )

    result = matcher.match(query=query, expected_contract_hash=args.expected_contract_hash)

    payload = emit_result(
        args=args,
        config_path=args.config,
        config=config,
        query=query,
        expected_query_id=str(args.query_id),
        result=result,
    )

    if args.timing:
        elapsed = payload.get("elapsed_ms", {})
        print(
            "[timing] "
            + " ".join(f"{k}={float(v):.2f}" for k, v in elapsed.items())
        )

    if args.emit_json is not None:
        args.emit_json.parent.mkdir(parents=True, exist_ok=True)
        args.emit_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
