#!/usr/bin/env python3
"""Compatibility wrapper for the consolidated latency-stage plotting script.

Use flashfusion/viz/latencystages.py as the primary entrypoint.
"""

from __future__ import annotations

from pathlib import Path
import runpy


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "latencystages.py"
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
