#!/usr/bin/env python3
"""Offline integrity and metric verification for the published artifact set."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "evaluation/checksums.json").read_text())
    failures = []
    for relative, wanted in manifest["files"].items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            failures.append(relative)
    metrics = json.loads((ROOT / "evaluation/results.json").read_text())
    for run, methods in metrics["runs"].items():
        folder = ROOT / "evaluation/runs" / run
        scores = json.loads((folder / "scores.json").read_text())["rows"]
        for method, expected in methods.items():
            selected = [r for r in scores if r["method"] == method]
            records = [json.loads(p.read_text()) for p in (folder / method).glob("*/record.json")]
            passed = sum(bool(r["all_pass"]) for r in selected)
            tokens = sum(r["ledger"].get("observed_usage", {}).get("total_tokens", 0) or 0 for r in records)
            if len(selected) != expected["n"] or len(records) != expected["n"] or passed != expected["pass"] or tokens != expected["tokens"]:
                failures.append(run + "/" + method + ": metric mismatch")
    if failures:
        print(json.dumps({"status": "failed", "files": failures}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "verified", "hashed_files": len(manifest["files"]), "runs": len(metrics["runs"]), "model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
