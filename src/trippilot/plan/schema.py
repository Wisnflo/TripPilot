"""Plan schema loading and validation."""

import json
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft7Validator

UPSTREAM_ROOT = Path(__file__).resolve().parents[3] / "upstream" / "ChinaTravel"
SCHEMA_PATH = UPSTREAM_ROOT / "chinatravel" / "evaluation" / "output_schema.json"

_SCHEMA: Dict[str, Any] = {}


def load_plan_schema() -> Dict[str, Any]:
    global _SCHEMA
    if not _SCHEMA:
        _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


def validate_plan(plan: Any) -> List[str]:
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    validator = Draft7Validator(load_plan_schema())
    errors = sorted(validator.iter_errors(plan), key=lambda e: list(e.path))
    messages = []
    for error in errors[:8]:
        path = ".".join(str(part) for part in error.path) or "<root>"
        messages.append("%s: %s" % (path, error.message))
    return messages
