"""Build per-UID agent-visible and scoring records from public query CSVs."""

import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schema import (
    assert_agent_safe,
    build_agent_record,
    build_scoring_record,
    field_policy,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _validate_roots(agent_root: Path, scoring_root: Path) -> None:
    agent = agent_root.resolve()
    scoring = scoring_root.resolve()
    if agent == scoring:
        raise ValueError("agent-visible and scoring roots must differ")
    if scoring == agent or agent in scoring.parents:
        raise ValueError("scoring root must not live inside the agent-visible root")
    if scoring in agent.parents:
        raise ValueError("agent-visible root must not live inside the scoring root")


def prepare_split(
    csv_path: Path,
    agent_dir: Path,
    scoring_dir: Path,
    split: str,
    source_commit: str,
    generated_at: str,
    source_note: str = "",
) -> Dict[str, Any]:
    _reset_dir(agent_dir)
    _reset_dir(scoring_dir)

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not rows:
        raise ValueError("query CSV is empty: %s" % csv_path)

    seen = set()
    for row in rows:
        uid = str(row.get("uid", "")).strip()
        if not uid:
            raise ValueError("query row without uid in %s" % csv_path)
        if uid in seen:
            raise ValueError("duplicate uid %r in %s" % (uid, csv_path))
        seen.add(uid)

        agent_record = build_agent_record(row)
        assert_agent_safe(agent_record)
        scoring_record = build_scoring_record(row)

        _write_json(agent_dir / ("%s.json" % uid), agent_record)
        _write_json(scoring_dir / ("%s.json" % uid), scoring_record)

    manifest = {
        "split": split,
        "generated_at": generated_at,
        "row_count": len(rows),
        "agent_visible_count": len(list(agent_dir.glob("*.json"))),
        "scoring_count": len(list(scoring_dir.glob("*.json"))),
        "source": {
            "file": csv_path.name,
            "sha256": sha256_file(csv_path),
            "upstream_commit": source_commit,
            "license": "CC BY 4.0",
            "note": source_note,
        },
        "source_columns": fieldnames,
        "policy": field_policy(),
        "layout": {
            "agent_visible": "agent_visible/%s" % split,
            "scoring": "scoring/%s" % split,
        },
    }
    return manifest


def prepare_splits(
    raw_dir: Path,
    agent_root: Path,
    scoring_root: Path,
    manifest_dir: Path,
    splits: List[str],
    source_commit: str,
    generated_at: str,
    source_note: str = "",
) -> List[Dict[str, Any]]:
    _validate_roots(agent_root, scoring_root)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifests = []
    for split in splits:
        csv_path = raw_dir / ("%s.csv" % split)
        if not csv_path.is_file():
            raise FileNotFoundError("missing raw query CSV: %s" % csv_path)
        manifest = prepare_split(
            csv_path=csv_path,
            agent_dir=agent_root / split,
            scoring_dir=scoring_root / split,
            split=split,
            source_commit=source_commit,
            generated_at=generated_at,
            source_note=source_note,
        )
        _write_json(manifest_dir / ("%s.json" % split), manifest)
        manifests.append(manifest)
    return manifests
