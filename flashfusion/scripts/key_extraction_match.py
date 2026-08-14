from __future__ import annotations

import os, textwrap

"""
scripts/key_extraction_match.py — structured intent signatures + template cache.

Pipeline position:

    User Query -> Key Extraction (~100 tokens) -> Cache Lookup
        -> HIT:  Adapt skeleton with params (parameter binding, ~100 tokens)
        -> MISS: Full Planner -> Extract skeleton -> Store in cache
    -> Executor

build_operator_skeleton_cache.py keys its plan cache on the exact query
text, so "What is the minimum MLII value recorded for record_id 101?" and
"For record 101, what is the minimum MLII measurement?" never share an
entry even though they ask the same thing. This script closes the gap:

  1. Offline (build): every known query is reduced to a structured intent
     signature — operation pipeline, aggregate, signals, slotted filters,
     group-by, rank — and stored in a JSON cache keyed by the canonical
     signature string. Parameter values (record_id 101, threshold 0, ...)
     become slots, so paraphrases collapse to one entry.
  2. Live (match): an incoming query goes through the same extraction,
     then (a) exact signature lookup, (b) weighted fuzzy fallback, or
     (c) MISS -> full-planner seam -> store the new skeleton.

Key extraction here is deterministic (lexicons + regexes, zero LLM tokens).
extract_intent() is the seam where a ~100-token LLM extractor can replace
the rules later without changing the cache format or the matcher.

Usage (from the repo root):

    python flashfusion/scripts/key_extraction_match.py build --out intent_cache.json
    python flashfusion/scripts/key_extraction_match.py match --cache intent_cache.json --query "For record 101, what is the minimum MLII measurement?"
    python flashfusion/scripts/key_extraction_match.py demo
"""

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Lexicons. Extend via --schema-cols (signals) or by editing these tables.
# ---------------------------------------------------------------------------

# Single-token aggregate cues. Multi-word phrases are handled separately so
# "root mean square" does not also fire AVG via "mean".
AGG_LEXICON: dict[str, tuple[str, ...]] = {
    "MIN": ("min", "minimum", "lowest", "smallest", "least"),
    "MAX": ("max", "maximum", "highest", "largest", "greatest", "peak"),
    "AVG": ("avg", "average", "mean"),
    "SUM": ("sum", "total"),
    "COUNT": ("count",),
    "RMS": ("rms",),
    "STDDEV": ("std",),
    "MEDIAN": ("median",),
    "PERCENTILE": ("percentile",),
}

MULTIWORD_AGG: dict[str, str] = {
    "root mean square": "RMS",
    "standard deviation": "STDDEV",
    "how many": "COUNT",
    "number of": "COUNT",
}

# Superlatives that rank groups rather than aggregate directly, e.g.
# "which 10-second interval contains the highest number of annotated beats?"
RANK_KEYWORDS: dict[str, str] = {
    "highest": "MAX", "largest": "MAX", "most": "MAX", "top": "MAX",
    "lowest": "MIN", "fewest": "MIN", "smallest": "MIN",
}

# Tie-break only; proximity to a signal mention is the primary signal.
AGG_PRIORITY = ["RMS", "STDDEV", "PERCENTILE", "MEDIAN", "COUNT", "MIN", "MAX", "AVG", "SUM"]

DEFAULT_SIGNALS: tuple[str, ...] = (
    "mlii", "time_s", "timestamp", "beats",
    "x_axis", "y_axis", "z_axis", "accel_x", "accel_y", "accel_z",
)

ACTIVITY_VALUES: tuple[str, ...] = (
    "walking", "jogging", "sitting", "standing", "upstairs", "downstairs",
)

OUT_OF_SCOPE_MARKERS: tuple[str, ...] = (
    "predict", "geographic location", "location where", "cannot be determined",
)

# Fuzzy-match weights (must sum to 1.0). Justified in match_report output.
W_AGG, W_SIGNAL, W_FILTER, W_GROUPBY, W_RANK = 0.35, 0.30, 0.25, 0.05, 0.05


# ---------------------------------------------------------------------------
# Intent model
# ---------------------------------------------------------------------------

@dataclass
class Filter:
    field: str
    op: str
    slot: str  # parameter slot name; the concrete value lives in params

    def canonical(self) -> str:
        return f"{self.field}{self.op}@{{{self.slot}}}"


@dataclass
class Intent:
    operation: str                       # e.g. "FILTER+DERIVE+GROUPBY+COUNT+RANK"
    aggregate: str | None = None
    signals: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    groupby: str | None = None           # "window:10s" or "field:user_id"
    rank: str | None = None              # "MAX" | "MIN"
    orderby: str | None = None
    out_of_scope: bool = False

    def signature(self) -> str:
        filt = ",".join(sorted(f.canonical() for f in self.filters)) or "-"
        sig = ",".join(sorted(self.signals)) or "-"
        return (
            f"op={self.operation}|agg={self.aggregate or '-'}|sig={sig}"
            f"|filters={filt}|groupby={self.groupby or '-'}|rank={self.rank or '-'}"
        )

    def sig_id(self) -> str:
        return hashlib.sha1(self.signature().encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Key extraction (deterministic; swap for an LLM extractor at this seam)
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Canonicalize surface forms so paraphrases converge before extraction."""
    t = text.lower().strip()
    # "record 101" / "record #101" / "record_id=101" -> "record_id 101"
    t = re.sub(r"\brecord[\s_]*(?:id)?\s*#?\s*[:=]?\s*(\d+)", r" record_id \1 ", t)
    t = re.sub(r"\buser[\s_]*(?:id)?\s*#?\s*[:=]?\s*(\d+)", r" user_id \1 ", t)
    # "10-second interval" / "10 second window" -> "10s_window"
    t = re.sub(r"(\d+)\s*[-\s]*seconds?\s+(?:interval|window|bin|bucket)s?", r" \1s_window ", t)
    # space out comparisons: "mlii>0" -> "mlii > 0"
    t = re.sub(r"([a-z_][a-z0-9_]*)\s*(>=|<=|!=|>|<)\s*(-?\d+(?:\.\d+)?)", r" \1 \2 \3 ", t)
    t = re.sub(r"[?.,;:'\"()\[\]!]", " ", t)  # keep ><=! intact
    return re.sub(r"\s+", " ", t).strip()


def _detect_aggregate(tokens: list[str], signal_idx: list[int], rank_idx: int | None) -> str | None:
    consumed: set[int] = set()
    cands: list[tuple[str, int]] = []
    for n in (3, 2):
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            if gram in MULTIWORD_AGG:
                cands.append((MULTIWORD_AGG[gram], i))
                consumed.update(range(i, i + n))
    for i, tok in enumerate(tokens):
        if i in consumed:
            continue
        for agg, words in AGG_LEXICON.items():
            if tok in words:
                cands.append((agg, i))
                break
    if rank_idx is not None:  # the superlative ranks groups; it is not the aggregate
        cands = [(a, i) for a, i in cands if i != rank_idx]
    if not cands:
        return None
    if signal_idx:
        def dist(item: tuple[str, int]) -> int:
            return min(abs(item[1] - s) for s in signal_idx)
        best_d = min(dist(c) for c in cands)
        cands = [c for c in cands if dist(c) == best_d]
    cands.sort(key=lambda c: AGG_PRIORITY.index(c[0]) if c[0] in AGG_PRIORITY else len(AGG_PRIORITY))
    return cands[0][0]


def extract_intent(query_text: str, extra_signals: tuple[str, ...] = ()) -> tuple[Intent, dict[str, Any]]:
    """Map a natural-language query to (structured intent, bound parameters).

    Paraphrase invariance comes from three normalizations: synonym lexicons
    ("minimum"/"smallest" -> MIN), entity canonicalization ("record 101" ->
    record_id), and parameter slotting (values become @{slot} in the signature).
    """
    norm = normalize_text(query_text)
    tokens = norm.split()
    params: dict[str, Any] = {}

    if any(marker in norm for marker in OUT_OF_SCOPE_MARKERS):
        return Intent(operation="OUT_OF_SCOPE", out_of_scope=True), params

    signals_vocab = DEFAULT_SIGNALS + tuple(s.lower() for s in extra_signals)
    signal_idx = [i for i, tok in enumerate(tokens) if tok in signals_vocab]
    signals = sorted({tok for tok in tokens if tok in signals_vocab})

    groupby = None
    m = re.search(r"(\d+)s_window", norm)
    if m:
        groupby = f"window:{m.group(1)}s"
    else:
        m = re.search(r"\b(?:per|by|each)\s+(user_id|activity|record_id)\b", norm)
        if m:
            groupby = f"field:{m.group(1)}"

    rank, rank_idx = None, None
    if groupby:
        for i, tok in enumerate(tokens):
            if tok in RANK_KEYWORDS:
                rank, rank_idx = RANK_KEYWORDS[tok], i
                break

    aggregate = _detect_aggregate(tokens, signal_idx, rank_idx)

    filters: list[Filter] = []
    m = re.search(r"\brecord_id\s+(\d+)\b", norm)
    if m:
        filters.append(Filter("record_id", "=", "record_id"))
        params["record_id"] = int(m.group(1))
    m = re.search(r"\buser_id\s+(\d+)\b", norm)
    if m:
        filters.append(Filter("user_id", "=", "user_id"))
        params["user_id"] = int(m.group(1))
    for m in re.finditer(r"\b([a-z][a-z0-9_]*)\s*(>=|<=|!=|>|<)\s*(-?\d+(?:\.\d+)?)\b", norm):
        fld = m.group(1)
        if fld in {"record_id", "user_id"}:
            continue
        num = float(m.group(3))
        params[fld] = int(num) if num.is_integer() else num
        filters.append(Filter(fld, m.group(2), fld))
        if fld in signals_vocab and fld not in signals:
            signals.append(fld)
    m = re.search(r"\bactivity\s+(?:label\s+)?([a-z]+)", norm)
    act = m.group(1) if m else next((a for a in ACTIVITY_VALUES if re.search(rf"\b{a}\b", norm)), None)
    if act:
        filters.append(Filter("activity", "=", "activity"))
        params["activity"] = act
    if re.search(r"\busers?\b", norm) and not any(f.field == "user_id" for f in filters):
        signals.append("user_id")  # e.g. "how many users ..." -> COUNT over user_id

    orderby = None
    if "sort" in tokens:
        m = re.search(r"\bby\s+([a-z_][a-z0-9_]*)", norm)
        direction = "desc" if re.search(r"\b(desc|descending)\b", norm) else "asc"
        orderby = f"{m.group(1) if m else 'timestamp'}:{direction}"

    parts: list[str] = []
    if filters:
        parts.append("FILTER")
    if (groupby or "").startswith("window:") or aggregate == "RMS":
        parts.append("DERIVE")
    if groupby:
        parts.append("GROUPBY")
    if aggregate == "COUNT":
        parts.append("COUNT")
    elif aggregate:
        parts.append("AGGREGATE")
    if rank:
        parts.append("RANK")
    if orderby:
        parts.append("SORT")
    operation = "+".join(dict.fromkeys(parts)) or "SCAN"

    intent = Intent(
        operation=operation,
        aggregate=aggregate,
        signals=sorted(signals),
        filters=filters,
        groupby=groupby,
        rank=rank,
        orderby=orderby,
    )
    return intent, params


# ---------------------------------------------------------------------------
# Cache: build (offline) and match (live)
# ---------------------------------------------------------------------------

def build_cache(queries: list[dict], extra_signals: tuple[str, ...] = ()) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for q in queries:
        intent, _ = extract_intent(q["text"], extra_signals)
        sig = intent.signature()
        entry = entries.setdefault(
            sig,
            {
                "signature": sig,
                "sig_id": intent.sig_id(),
                "intent": asdict(intent),
                "query_ids": [],
                "examples": [],
                "skeleton": None,  # join point for build_operator_skeleton_cache.py output
                "source": "offline_build",
            },
        )
        entry["query_ids"].append(q.get("id"))
        entry["examples"].append(q["text"])
    try:  # provenance, mirrors build_operator_skeleton_cache.py
        from flashfusion.pipeline.operators import PLANNER_PREFIX_SHA256
    except Exception:
        PLANNER_PREFIX_SHA256 = None
    return {
        "version": 1,
        "generator": "flashfusion/scripts/key_extraction_match.py",
        "planner_prefix_sha256": PLANNER_PREFIX_SHA256,
        "num_entries": len(entries),
        "entries": entries,
    }


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 1.0


def similarity(a: Intent, b: dict[str, Any]) -> float:
    score = 0.0
    if a.aggregate == b.get("aggregate"):
        score += W_AGG
    score += W_SIGNAL * _jaccard(a.signals, b.get("signals") or [])
    score += W_FILTER * _jaccard([f.field for f in a.filters], [f["field"] for f in b.get("filters") or []])
    if a.groupby == b.get("groupby"):
        score += W_GROUPBY
    if a.rank == b.get("rank"):
        score += W_RANK
    return score


def lookup(cache: dict[str, Any], intent: Intent, threshold: float) -> tuple[str, dict[str, Any] | None, float]:
    entries = cache.get("entries", {})
    sig = intent.signature()
    if sig in entries:
        return "HIT_EXACT", entries[sig], 1.0
    best_sig, best_score = None, 0.0
    for cand_sig, entry in entries.items():
        s = similarity(intent, entry["intent"])
        if s > best_score:
            best_sig, best_score = cand_sig, s
    if best_sig is not None and best_score >= threshold:
        return "HIT_FUZZY", entries[best_sig], round(best_score, 3)
    return "MISS", None, round(best_score, 3)


def bind_plan(entry: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt the cached skeleton with live parameters (the cheap HIT path)."""
    intent = entry["intent"]
    steps: list[dict[str, Any]] = []
    for part in intent["operation"].split("+"):
        if part == "FILTER":
            preds = [
                {"field": f["field"], "op": f["op"], "value": params.get(f["slot"], f"@{{{f['slot']}}}")}
                for f in intent["filters"]
            ]
            steps.append({"op": "FILTER", "predicates": preds})
        elif part == "DERIVE":
            if (intent.get("groupby") or "").startswith("window:"):
                steps.append({"op": "DERIVE", "kind": "time_window", "window": intent["groupby"].split(":")[1]})
            elif intent.get("aggregate") == "RMS":
                steps.append({"op": "DERIVE", "kind": "square", "signals": intent["signals"]})
            else:
                steps.append({"op": "DERIVE", "kind": "generic"})
        elif part == "GROUPBY":
            steps.append({"op": "GROUPBY", "key": intent.get("groupby")})
        elif part in ("AGGREGATE", "COUNT"):
            steps.append({"op": part, "fn": intent.get("aggregate"), "signals": intent["signals"]})
        elif part == "RANK":
            steps.append({"op": "RANK", "by": intent.get("aggregate"), "order": intent.get("rank")})
        elif part == "SORT":
            steps.append({"op": "SORT", "orderby": intent.get("orderby")})
    return steps


def run_full_planner(query_text: str) -> dict[str, Any] | None:
    """MISS seam: invoke the full Flash-Fusion planner here (pipeline.runner /
    pipeline.stages), then extract its operator skeleton for storage. Returns
    None until wired; the MISS entry is still cached so the next identical
    intent HITs once a skeleton is attached."""
    return None


# ---------------------------------------------------------------------------
# Query loading: import -> AST literal_eval -> --queries-json
# ---------------------------------------------------------------------------

def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "flashfusion" / "eval" / "queries.py").is_file():
            return parent
    return None


def load_queries(queries_json: str | None = None) -> list[dict]:
    if queries_json:
        return json.loads(Path(queries_json).read_text())
    root = _repo_root()
    if root is None:
        raise SystemExit("Could not locate flashfusion/eval/queries.py; pass --queries-json.")
    sys.path.insert(0, str(root))
    try:
        from flashfusion.eval.queries import WISDM_QUERIES
        return list(WISDM_QUERIES)
    except Exception:
        pass
    tree = ast.parse((root / "flashfusion" / "eval" / "queries.py").read_text())
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "WISDM_QUERIES" for t in targets):
            return ast.literal_eval(node.value)
    raise SystemExit("WISDM_QUERIES not found in flashfusion/eval/queries.py; pass --queries-json.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(query: str, intent: Intent, status: str, entry: dict[str, Any] | None, score: float, params: dict[str, Any]) -> None:
    print(f"Query : {query}")
    print(f"Intent: {intent.signature()}")
    print(f"Status: {status}" + (f" (score={score})" if status == "HIT_FUZZY" else ""))
    if entry is not None:
        print(f"Match : sig_id={entry['sig_id']} query_ids={entry['query_ids']}")
        print(f"        example: {entry['examples'][0]}")
        print(f"Params: {params}")
        print("Plan  :")
        for step in bind_plan(entry, params):
            print(f"        {json.dumps(step)}")
        print("Effect: full planner skipped; only parameter binding runs on this path.")
    else:
        print(f"Params: {params}")
        print("Effect: MISS -> run_full_planner() seam, then extract skeleton and store.")


def cmd_build(args: argparse.Namespace) -> int:
    queries = load_queries(args.queries_json)
    cache = build_cache(queries, tuple(args.schema_cols or ()))
    Path(args.out).write_text(json.dumps(cache, indent=2))
    print(f"Wrote {cache['num_entries']} intent entries from {len(queries)} queries -> {args.out}")
    for sig, entry in cache["entries"].items():
        print(f"  [{entry['sig_id']}] ids={entry['query_ids']} {sig}")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    cache = json.loads(Path(args.cache).read_text())
    query = args.query or sys.stdin.read().strip()
    intent, params = extract_intent(query, tuple(args.schema_cols or ()))
    if intent.out_of_scope:
        print(f"Query : {query}\nStatus: OUT_OF_SCOPE (reject before planning; mirrors eval complexity tag)")
        return 0
    status, entry, score = lookup(cache, intent, args.threshold)
    if status == "MISS" and args.store_on_miss:
        skeleton = run_full_planner(query)
        sig = intent.signature()
        cache["entries"][sig] = {
            "signature": sig, "sig_id": intent.sig_id(), "intent": asdict(intent),
            "query_ids": [], "examples": [query], "skeleton": skeleton,
            "source": "planner_miss_pending" if skeleton is None else "planner_miss",
        }
        Path(args.cache).write_text(json.dumps(cache, indent=2))
        print(f"Stored new intent entry [{intent.sig_id()}] (skeleton pending planner).")
    if args.json:
        print(json.dumps({
            "query": query, "status": status, "score": score,
            "intent": asdict(intent), "params": params,
            "plan": bind_plan(entry, params) if entry else None,
        }, indent=2))
    else:
        _print_report(query, intent, status, entry, score, params)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    queries = load_queries(args.queries_json)
    cache = build_cache(queries, tuple(args.schema_cols or ()))
    live = [
        "For record 101, what is the minimum MLII measurement?",
        "What's the smallest MLII for record 107?",
        "Count samples where MLII > 0 for record 106",
        "What is the median MLII for record_id 101?",
    ]
    print(f"Built {cache['num_entries']} intent entries from {len(queries)} offline queries.\n")
    for q in live:
        intent, params = extract_intent(q)
        status, entry, score = lookup(cache, intent, args.threshold)
        _print_report(q, intent, status, entry, score, params)
        print("-" * 78)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("build", cmd_build), ("match", cmd_match), ("demo", cmd_demo)):
        p = sub.add_parser(name)
        p.add_argument("--queries-json", help="Fallback query list (JSON) if the eval module is unavailable.")
        p.add_argument("--schema-cols", nargs="*", help="Extra column names to treat as signals.")
        p.add_argument("--threshold", type=float, default=0.75, help="Fuzzy-match cutoff in [0,1] (default 0.75).")
        if name == "build":
            p.add_argument("--out", default="intent_cache.json")
        if name == "match":
            p.add_argument("--cache", default="intent_cache.json")
            p.add_argument("--query", help="Live query text (defaults to stdin).")
            p.add_argument("--store-on-miss", action="store_true", help="Cache the new intent after a MISS.")
            p.add_argument("--json", action="store_true", help="Emit the match report as JSON.")
        p.set_defaults(fn=fn)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
'''

base = "/tmp/fftest/flashfusion"
os.makedirs(f"{base}/scripts", exist_ok=True)
os.makedirs(f"{base}/eval", exist_ok=True)
with open(f"{base}/scripts/key_extraction_match.py", "w") as f:
    f.write(script)

mock_queries = '''"""Mock of flashfusion/eval/queries.py schema for sandbox testing."""

WISDM_QUERIES: list[dict] = [
    {"id": 1, "text": "What is the minimum MLII value recorded for record_id 101?", "complexity": "direct", "operation": "FILTER+AGGREGATE"},
    {"id": 2, "text": "What is the total recording duration in seconds (maximum time_s) for record_id 234?", "complexity": "direct", "operation": "FILTER+AGGREGATE"},
    {"id": 3, "text": "For record_id 106, how many samples have MLII > 0?", "complexity": "direct", "operation": "FILTER+COUNT"},
    {"id": 4, "text": "How many users have the activity label walking?", "complexity": "direct", "operation": "FILTER+COUNT"},
    {"id": 7, "text": "For record_id 101, which 10-second interval contains the highest number of annotated beats?", "complexity": "intermediate", "operation": "DERIVE+FILTER+GROUPBY+RANK"},
    {"id": 8, "text": "Calculate the root mean square (RMS) of the MLII signal for record_id 106.", "complexity": "intermediate", "operation": "FILTER+DERIVE+AGGREGATE"},
    {"id": 10, "text": "Based on the acceleration data, predict the exact geographic location where user 10 was jogging.", "complexity": "out_of_scope", "operation": "OUT_OF_SCOPE"},
    {"id": 16, "text": "Sort all bus rows by timestamp in ascending order.", "complexity": "direct", "operation": "SORT"},
]

'''
with open(f"{base}/eval/queries.py", "w") as f:
    f.write(mock_queries)

print("written", os.path.getsize(f"{base}/scripts/key_extraction_match.py"), "bytes")
'''