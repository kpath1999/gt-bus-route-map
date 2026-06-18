#!/usr/bin/env bash
# Orchestrator for the LLM-Only vs Flash-Fusion scaling study.
#
# Phase 1: dry-run — 1 question × 1 rep across all datasets/baselines (~$0.10).
# Phase 2: full sweep — 4 questions × 3 reps × {bus,wisdm,ecg} × {LLM_ONLY,FF}.
#
# Both phases: sweep → analyze (grade + summary) → plots.

set -euo pipefail

# ── Load API keys from vault (.env at repo root, never committed) ─────────────
_VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../.env"
if [ -f "$_VAULT" ]; then
    set -a; source "$_VAULT"; set +a
fi
unset _VAULT

PHASE=1
RESET=""
DATASETS="bus,wisdm,ecg"
BASELINES="LLM_ONLY,FLASH_FUSION"

for arg in "$@"; do
  case "$arg" in
    --phase=1) PHASE=1 ;;
    --phase=2) PHASE=2 ;;
    --reset)   RESET="--reset" ;;
    --datasets=*) DATASETS="${arg#--datasets=}" ;;
    --baselines=*) BASELINES="${arg#--baselines=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
  echo "Error: OPENROUTER_API_KEY (or GROQ_API_KEY for transition) environment variable not set." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-${REPO_ROOT}/.venv/bin/python3}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

echo "=== Phase $PHASE: sweep ==="
"$PY" -m flashfusion.llm_only.experiment \
  --phase="$PHASE" \
  --datasets="$DATASETS" \
  --baselines="$BASELINES" \
  $RESET

echo
echo "=== Analyze: grade + summary ==="
"$PY" -m flashfusion.llm_only.analyze

echo
echo "=== Plots ==="
"$PY" -m flashfusion.llm_only.plots

echo
echo "Done. Outputs in flashfusion/llm_only/results/"
