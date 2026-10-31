"""Build the only object an agent runtime is allowed to see."""

from typing import Any, Dict, Iterable, List

from .data.schema import AGENT_VISIBLE_FIELDS, assert_agent_safe


def build_agent_input(record: Dict[str, Any]) -> Dict[str, Any]:
    """Copy allowlisted public fields only; raise on forbidden nested keys."""
    payload = {field: record[field] for field in AGENT_VISIBLE_FIELDS if field in record}
    return assert_agent_safe(payload)


def build_agent_inputs(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [build_agent_input(record) for record in records]
