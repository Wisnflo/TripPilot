"""Compare activities across plan versions by stable IDs (day:index)."""

import json
from typing import Any, Dict, List


def activity_ids(plan: Dict[str, Any]) -> List[str]:
    ids = []
    for day_index, day in enumerate(plan.get("itinerary") or [], start=1):
        if not isinstance(day, dict):
            continue
        for activity_index, _ in enumerate(day.get("activities") or [], start=1):
            ids.append("d%d:a%d" % (day_index, activity_index))
    return ids


def activity_map(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping = {}
    for day_index, day in enumerate(plan.get("itinerary") or [], start=1):
        if not isinstance(day, dict):
            continue
        for activity_index, activity in enumerate(day.get("activities") or [], start=1):
            if isinstance(activity, dict):
                mapping["d%d:a%d" % (day_index, activity_index)] = activity
    return mapping


def changed_ids(seed: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    seed_map = activity_map(seed)
    new_map = activity_map(new)
    keys = sorted(set(seed_map) | set(new_map))
    changed = []
    for key in keys:
        if key not in seed_map or key not in new_map:
            changed.append(key)
            continue
        if json.dumps(seed_map[key], sort_keys=True, ensure_ascii=False) != json.dumps(
            new_map[key], sort_keys=True, ensure_ascii=False
        ):
            changed.append(key)
    return changed


def preserve_violations(seed: Dict[str, Any], new: Dict[str, Any], preserve_ids: List[str]) -> List[str]:
    seed_map = activity_map(seed)
    new_map = activity_map(new)
    violations = []
    for key in preserve_ids:
        if key not in new_map:
            violations.append(key)
        elif json.dumps(seed_map.get(key), sort_keys=True, ensure_ascii=False) != json.dumps(
            new_map.get(key), sort_keys=True, ensure_ascii=False
        ):
            violations.append(key)
    return violations
