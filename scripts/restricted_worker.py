#!/usr/bin/env python3
"""Enter the Linux file policy, then run exactly the declared worker script."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trippilot.execution.isolation import enter_landlock

if __name__ == "__main__":
    policy = json.loads(Path(sys.argv[1]).read_text())
    command = sys.argv[2:]
    enter_landlock(policy)
    sys.argv = command
    runpy.run_path(command[0], run_name="__main__")
