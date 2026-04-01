"""Vercel serverless handler for /api/chat.

Domain-agnostic data exploration interface backed by Groq and the Flash-Fusion
analysis pipeline.  Loads actual datasets into Pandas DataFrames and drives
a pandas agent (concept extraction → schema grounding → sub-query generation
→ agent execution → synthesis) for grounded, data-backed responses.

New datasets can be added to DATASET_REGISTRY without any other code changes.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_local_env() -> None:
    if load_dotenv is None:
        return
    chat_root = Path(__file__).resolve().parents[1]
    for env_name in (".env.local", ".env"):
        env_path = chat_root / env_name
        if env_path.exists():
            load_dotenv(env_path, override=False)


_load_local_env()

# ── Path setup for playground imports ───────────────────────
_CHAT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _CHAT_ROOT.parent

# Make the playground package importable from the API handler.
sys.path.insert(0, str(_CHAT_ROOT / "playground"))

import pandas as pd  # noqa: E402
from playground import (  # noqa: E402
    load_data,
    export_ecg_record_to_csv,
    list_ecg_records,
    build_column_metadata,
    meta_to_str,
    BaselineRunner,
    RunResult,
)

# ── Dataset registry ────────────────────────────────────────
# Maps domain key → path relative to _CHAT_ROOT.
# Files live in chat/data/ so they are tracked by git (not affected by the
# root-level `data/` gitignore rule). Add new datasets here; no other code
# changes required.
DATASET_REGISTRY: dict[str, str] = {
    "bus": "data/bus/bus_data.csv",              # 192 KB  — committed directly
    "imu": "data/imu/WISDM_ar_v1.1_raw.txt",   #  48 MB  — Git LFS
    "ecg": "data/ecg/100.csv",                  #  27 MB  — Git LFS (record 100)
}

DEFAULT_ECG_RECORD = "100"

# ── Module-level DataFrame cache (survives warm invocations) ─
_df_cache: dict[str, pd.DataFrame] = {}
_path_cache: dict[str, str] = {}


def _resolve_data_path(domain: str, ecg_record: str | None = None) -> str:
    """Return the absolute path to the data file for *domain*.

    Paths in DATASET_REGISTRY are relative to _CHAT_ROOT (i.e. the chat/
    subdirectory), so they resolve correctly both locally and on Vercel.
    If the registry entry is already a readable file, it is used as-is;
    otherwise (legacy: raw ECG directory) export is attempted.
    """
    rel = DATASET_REGISTRY.get(domain)
    if not rel:
        raise ValueError(f"Unknown domain: {domain}")
    abs_path = str(_CHAT_ROOT / rel)
    if os.path.isfile(abs_path):
        return abs_path
    # Fallback for local dev: if path is a raw ECG directory, convert on the fly.
    if domain == "ecg" and os.path.isdir(abs_path):
        record = ecg_record or DEFAULT_ECG_RECORD
        return export_ecg_record_to_csv(abs_path, record)
    raise FileNotFoundError(f"Dataset not found at: {abs_path}")


def _get_dataframe(domain: str, ecg_record: str | None = None) -> pd.DataFrame:
    """Load (and cache) the DataFrame for *domain*."""
    cache_key = f"{domain}:{ecg_record or ''}"
    if cache_key not in _df_cache:
        path = _resolve_data_path(domain, ecg_record)
        df, _ = load_data(path)
        _df_cache[cache_key] = df
        _path_cache[cache_key] = path
    return _df_cache[cache_key]


def _get_cached_path(domain: str, ecg_record: str | None = None) -> str:
    cache_key = f"{domain}:{ecg_record or ''}"
    if cache_key in _path_cache:
        return _path_cache[cache_key]
    return _resolve_data_path(domain, ecg_record)


# ── Lightweight keyword-based domain routing ────────────────
# Deliberately minimal — the pipeline itself is fully schema-driven.
_DOMAIN_HINTS: dict[str, list[str]] = {
    "ecg": [
        "ecg", "heart", "cardiac", "arrhythmia", "beat", "rhythm",
        "pulse", "qrs", "annotation", "st segment", "r-peak",
    ],
    "imu": [
        "imu", "accelerometer", "activity", "walking", "jogging",
        "motion", "wisdm", "har", "human activity",
    ],
    "bus": [
        "bus", "ride", "pothole", "bump", "road", "driving",
        "route", "aggressive", "passenger", "comfort",
    ],
}


def _detect_domain(query: str, explicit: str | None = None) -> str | None:
    """Return domain from explicit parameter or keyword match."""
    if explicit and explicit in DATASET_REGISTRY:
        return explicit
    q = query.lower()
    scores = {d: sum(1 for kw in kws if kw in q) for d, kws in _DOMAIN_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _schema_summary(df: pd.DataFrame) -> str:
    """One-line-per-column schema overview for the ambiguity response."""
    meta = build_column_metadata(df)
    return meta_to_str(meta)


# ── Vercel handler ──────────────────────────────────────────

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"error": "Invalid JSON"})
            return

        message = (req.get("message") or "").strip()
        if not message:
            self._json_response(400, {"error": "Empty message"})
            return

        model = req.get("model", "llama-3.3-70b-versatile")
        explicit_domain: str | None = req.get("domain")
        ecg_record: str | None = req.get("ecg_record")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            self._json_response(500, {"error": "GROQ_API_KEY not configured"})
            return

        # ── Domain detection: explicit > keyword match ──────
        domain = _detect_domain(message, explicit_domain)
        if not domain:
            available = ", ".join(sorted(DATASET_REGISTRY.keys()))
            self._json_response(200, {
                "reply": (
                    "I couldn't determine which dataset your question targets. "
                    f"Available domains: **{available}**. "
                    "Please select one from the dropdown or mention it in your question."
                ),
                "domain": None,
                "grounded": False,
                "usage": None,
            })
            return

        # ── Load dataset ────────────────────────────────────
        try:
            df = _get_dataframe(domain, ecg_record)
            source_path = _get_cached_path(domain, ecg_record)
        except Exception as exc:
            self._json_response(500, {
                "error": f"Failed to load {domain} dataset: {exc}",
            })
            return

        # ── Run Flash-Fusion B4 pipeline ────────────────────
        try:
            runner = BaselineRunner(
                df=df,
                mode="B4",
                model=model,
                source_path=source_path,
            )
            result: RunResult = runner.run(message)

            self._json_response(200, {
                "reply": result.answer,
                "domain": domain,
                "grounded": result.executed,
                "stages": result.stages_run,
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "total_tokens": result.total_tokens,
                    "cost_usd": result.cost_usd,
                    "latency_s": result.latency_s,
                },
            })
        except Exception as exc:
            self._json_response(502, {"error": f"Pipeline error: {exc}"})

    # ── Helpers ─────────────────────────────────────────────

    def _json_response(self, status: int, data: dict):
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
