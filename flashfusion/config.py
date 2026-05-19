"""
config.py — Flash-Fusion global constants.

Import this module everywhere instead of hard-coding values.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# LLM model pricing (USD per 1M tokens, as of 2026-05)
# Override individual rates with environment variables:
#   MODEL_RATE_INPUT_<MODEL_KEY>   e.g. MODEL_RATE_INPUT_LLAMA_3_3_70B=0.59
# ---------------------------------------------------------------------------
MODEL_RATE_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {
        "input": 0.59,
        "output": 0.79,
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "input": 0.11,
        "output": 0.34,
    },
    "groq/compound": {
        "input": 0.15,
        "output": 0.60,
    },
}

# Default model used when --model is not supplied to the CLI
DEFAULT_MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Agent execution limits
# ---------------------------------------------------------------------------
AGENT_MAX_ITERATIONS: int = 6          # LangChain AgentExecutor max_iterations
RESILIENT_PARSER_MAX_IDENTICAL: int = 2  # consecutive identical outputs before fallback
RESILIENT_PARSER_MAX_FAILURES: int = 2   # consecutive parse failures before fallback
EXECUTION_AGENT_BACKEND_DEFAULT: str = "auto"  # auto -> safe on macOS, classic elsewhere
AGENT_SAFE_MAX_ATTEMPTS: int = 3  # codegen+execute retries for safe backend

# ---------------------------------------------------------------------------
# Stage retry limits
# ---------------------------------------------------------------------------
STAGE1_MAX_RETRIES: int = 1   # retries when both DATA and REASONING lists are empty
STAGE2_MAX_RETRIES: int = 1   # retries when no MAPPINGS lines found

# ---------------------------------------------------------------------------
# Sub-query decomposition
# ---------------------------------------------------------------------------
VALID_OPS: frozenset[str] = frozenset(
    {"FILTER", "AGGREGATE", "GROUPBY", "CORRELATE", "WINDOW", "RANK"}
)

# ---------------------------------------------------------------------------
# Accuracy scoring thresholds (used by eval/metrics.py)
# ---------------------------------------------------------------------------
ACCURACY_PASS_SCORE: float = 1.0   # executed=True AND judge==PASS
ACCURACY_EXEC_SCORE: float = 0.5   # executed=True AND (judge==FAIL OR no judge)
ACCURACY_FAIL_SCORE: float = 0.0   # rejected OR not executed

# ---------------------------------------------------------------------------
# Token estimation coefficient (chars → approximate tokens)
# len(text.split()) * TOKEN_ESTIMATE_MULTIPLIER
# ---------------------------------------------------------------------------
TOKEN_ESTIMATE_MULTIPLIER: float = 1.3

# ---------------------------------------------------------------------------
# Default dataset paths (relative to repo root flash-fusion/)
# ---------------------------------------------------------------------------
WISDM_DEFAULT_PATH: str = "chat/data/imu/WISDM_ar_v1.1_raw.txt"
WISDM_ARFF_DIR: str = "chat/data/imu/wisdm-dataset/arff_files"
