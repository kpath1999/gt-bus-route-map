#!/usr/bin/env bash
set -euo pipefail

# Capture script directory BEFORE changing working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

# Default to running all baselines; override with --baseline AUTOIOT_PAPER etc.
BASELINE="${BASELINE:-}"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--baseline)
			BASELINE="$2"
			shift 2
			;;
		--help)
			echo "Usage: $0 [--baseline BASELINE_NAME]"
			echo ""
			echo "BASELINE_NAME options: FLASH_FUSION, REACT_ONLY, AUTOIOT_PAPER, HARGPT_PAPER"
			echo "(omit to run all)"
			exit 0
			;;
		*)
			echo "Unknown option: $1"
			exit 1
			;;
	esac
done

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${GROQ_API_KEY:-}" ]]; then
	echo "Missing API key: set OPENROUTER_API_KEY or GROQ_API_KEY"
	exit 1
fi

if [[ -z "${TAVILY_API_KEY:-}" ]]; then
	echo "Missing API key: set TAVILY_API_KEY (required by AUTOIOT_PAPER)"
	exit 1
fi

# Run as a real .py file — NOT a heredoc (python - <<'PY') — because
# ExecutionLayer's safe backend uses multiprocessing.get_context("spawn").
# Spawn re-executes __main__ from disk; when Python is invoked via stdin
# __file__ is <stdin> and the child process crashes with FileNotFoundError.
export SANITY_BASELINE="$BASELINE"
python "$SCRIPT_DIR/sanity_check_baselines_runner.py"
