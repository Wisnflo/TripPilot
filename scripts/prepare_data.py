#!/usr/bin/env python3
"""Prepare agent-visible and scoring records from downloaded query CSVs."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.data.prepare import prepare_splits  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default=str(ROOT / "data" / "raw_queries"))
    parser.add_argument("--agent-root", default=str(ROOT / "data" / "agent_visible"))
    parser.add_argument("--scoring-root", default=str(ROOT / "data" / "scoring"))
    parser.add_argument("--manifest-dir", default=str(ROOT / "data" / "manifests"))
    parser.add_argument("--splits", nargs="+", default=["easy", "medium", "human"])
    parser.add_argument("--commit", default="04e51969aa0d63cbe8b956e2f2cba0a9d9109102")
    parser.add_argument("--generated-at", default="2026-10-26")
    parser.add_argument(
        "--note",
        default="ModelScope mirror of LAMDA-NeSy/ChinaTravel; see data/README.md",
    )
    args = parser.parse_args()

    manifests = prepare_splits(
        raw_dir=Path(args.raw_dir),
        agent_root=Path(args.agent_root),
        scoring_root=Path(args.scoring_root),
        manifest_dir=Path(args.manifest_dir),
        splits=args.splits,
        source_commit=args.commit,
        generated_at=args.generated_at,
        source_note=args.note,
    )
    for manifest in manifests:
        print(
            "%(split)s: %(row_count)d rows, agent=%(agent_visible_count)d, scoring=%(scoring_count)d"
            % manifest
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
