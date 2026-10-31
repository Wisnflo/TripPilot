"""Field policy for the agent-visible / scoring boundary (M0-02).

The policy is an allowlist, not a blocklist: agent-visible records are rebuilt
from a fixed set of public fields, so an unexpected upstream field can never
ride along by accident.
"""

import ast
import json
from typing import Any, Dict, List, Optional, Sequence


AGENT_VISIBLE_FIELDS = (
    "uid",
    "tag",
    "start_city",
    "target_city",
    "days",
    "people_number",
    "limit_rooms",
    "limits_room_type",
    "nature_language",
    "nature_language_en",
)

# Phase 2 records expose a smaller public schema; not used by M0-02 yet.
PHASE2_AGENT_VISIBLE_FIELDS = (
    "uid",
    "tag",
    "start_city",
    "target_city",
    "days",
    "people_number",
    "nature_language",
)

SCORING_FIELDS = (
    "uid",
    "hard_logic_py",
    "hard_logic_nl",
)

FORBIDDEN_KEYS = frozenset(
    {
        "hard_logic",
        "hard_logic_py",
        "hard_logic_nl",
        "gold_plan",
        "reference_plan",
        "reference_repair",
        "official_solution",
        "__sentinel__",
    }
)

INT_FIELDS = frozenset({"days", "people_number"})
BOOL_FIELDS = frozenset({"limit_rooms", "limits_room_type"})


def _decode_string_list(raw: Any, field: str) -> List[str]:
    """Decode a list that may be a real list, strict JSON, or a Python literal.

    Phase 1 CSVs store hard_logic_py as a Python literal list (single quotes,
    embedded newlines); Phase 2 stores strict JSON. Both are accepted, but the
    result must be a list of strings.
    """
    if isinstance(raw, list):
        parsed = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("%s is empty" % field)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError) as exc:
                raise ValueError("cannot parse %s as JSON or Python literal" % field) from exc
    else:
        raise ValueError("%s must be a string or list, got %s" % (field, type(raw).__name__))

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("%s must decode to a list of strings" % field)
    return parsed


def parse_hard_logic_py(raw: Any) -> List[str]:
    return _decode_string_list(raw, "hard_logic_py")


def parse_hard_logic_nl(raw: Any) -> List[str]:
    return _decode_string_list(raw, "hard_logic_nl")


def _coerce_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError("%s must be an integer, got bool" % field)
    if isinstance(value, int):
        return value
    return int(str(value).strip())


def _coerce_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise ValueError("%s must be a boolean, got %r" % (field, value))


def build_agent_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw query row onto the public allowlist."""
    record = {}
    for field in AGENT_VISIBLE_FIELDS:
        if field not in row:
            continue
        value = row[field]
        if value is None or value == "":
            continue
        if field in INT_FIELDS:
            value = _coerce_int(value, field)
        elif field in BOOL_FIELDS:
            value = _coerce_bool(value, field)
        record[field] = value
    if not record.get("uid"):
        raise ValueError("query row has no uid")
    return record


def build_scoring_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Build the evaluator-side record; never pass this to the agent."""
    record = {"uid": row.get("uid")}
    if not record["uid"]:
        raise ValueError("query row has no uid")
    for field in ("hard_logic_py", "hard_logic_nl"):
        if row.get(field) not in (None, ""):
            record[field] = _decode_string_list(row[field], field)
    return record


def iter_forbidden_paths(obj: Any, path: str = "") -> List[str]:
    """Recursively find forbidden keys in nested dicts/lists."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = "%s.%s" % (path, key) if path else str(key)
            if key in FORBIDDEN_KEYS:
                found.append(child)
            found.extend(iter_forbidden_paths(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(iter_forbidden_paths(value, "%s[%d]" % (path, index)))
    return found


def assert_agent_safe(record: Dict[str, Any], allowed: Sequence[str] = AGENT_VISIBLE_FIELDS) -> Dict[str, Any]:
    """Raise if a record has non-allowlisted top-level keys or forbidden nested keys."""
    extra = sorted(set(record) - set(allowed))
    if extra:
        raise ValueError("agent-visible record has non-allowlisted keys: %s" % ", ".join(extra))
    leaks = iter_forbidden_paths(record)
    if leaks:
        raise ValueError("agent-visible record contains forbidden keys: %s" % ", ".join(leaks))
    return record


def find_sentinel(obj: Any, sentinel: str, path: str = "") -> List[str]:
    """Return paths whose serialized value contains the sentinel."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = "%s.%s" % (path, key) if path else str(key)
            found.extend(find_sentinel(value, sentinel, child))
        if sentinel in str(obj):
            found.append(path or "<root>")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(find_sentinel(value, sentinel, "%s[%d]" % (path, index)))
        if sentinel in str(obj):
            found.append(path or "<root>")
    elif isinstance(obj, str) and sentinel in obj:
        found.append(path or "<value>")
    return found


def field_policy() -> Dict[str, Any]:
    return {
        "agent_visible_fields": list(AGENT_VISIBLE_FIELDS),
        "phase2_agent_visible_fields": list(PHASE2_AGENT_VISIBLE_FIELDS),
        "scoring_fields": list(SCORING_FIELDS),
        "forbidden_keys": sorted(FORBIDDEN_KEYS),
    }
