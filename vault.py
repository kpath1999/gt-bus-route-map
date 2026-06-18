"""
vault.py — API key loader for the Flash-Fusion repo.

Usage (Python files and notebooks):
    import vault   # loads .env into os.environ; safe to call multiple times

The .env file lives at the repo root and is excluded from git.
Keys already present in the environment are NOT overridden (override=False).
"""
from pathlib import Path
from dotenv import load_dotenv

_repo_root = Path(__file__).parent
load_dotenv(_repo_root / ".env", override=False)
