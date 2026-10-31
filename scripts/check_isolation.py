#!/usr/bin/env python3
"""Fail if anything under the agent-visible root can reveal scoring fields."""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.data.schema import (  # noqa: E402
    AGENT_VISIBLE_FIELDS,
    iter_forbidden_paths,
)


def scan(agent_root: Path, scoring_root: Path, sentinel: Optional[str] = None) -> List[str]:
    agent = agent_root.resolve()
    scoring = scoring_root.resolve()
    failures = []

    if not agent.is_dir():
        return ["agent root does not exist: %s" % agent]
    if agent == scoring or agent in scoring.parents or scoring in agent.parents:
        failures.append(
            "agent root and scoring root overlap: agent=%s scoring=%s" % (agent, scoring)
        )

    files = sorted(path for path in agent.rglob("*") if path.is_file())
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if sentinel and sentinel in text:
            failures.append("sentinel found in agent-visible file: %s" % path)
        if path.suffix != ".json":
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append("invalid JSON in agent tree: %s (%s)" % (path, exc))
            continue
        if not isinstance(payload, dict):
            failures.append("agent-visible JSON is not an object: %s" % path)
            continue
        extra = sorted(set(payload) - set(AGENT_VISIBLE_FIELDS))
        if extra:
            failures.append("non-allowlisted keys in %s: %s" % (path, ", ".join(extra)))
        leaks = iter_forbidden_paths(payload)
        if leaks:
            failures.append("forbidden keys in %s: %s" % (path, ", ".join(leaks)))

    print(
        "scanned %d files under %s; scoring root %s; sentinel=%r"
        % (len(files), agent, scoring, sentinel)
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-root", default=str(ROOT / "data" / "agent_visible"))
    parser.add_argument("--scoring-root", default=str(ROOT / "data" / "scoring"))
    parser.add_argument("--sentinel", default=None)
    args = parser.parse_args()

    failures = scan(Path(args.agent_root), Path(args.scoring_root), args.sentinel)
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        return 1
    print("PASS: agent-visible tree exposes only allowlisted public fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
