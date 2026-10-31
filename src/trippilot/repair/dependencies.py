"""Dependency propagation for change events."""

from typing import Any, Dict, List, Set

from ..plan.compare import activity_ids, activity_map
from .events import ChangeEvent


def affected_activity_ids(plan: Dict[str, Any], event: ChangeEvent) -> List[str]:
    ids = activity_ids(plan)
    mapping = activity_map(plan)

    if event.kind == "return_earlier":
        new_days = event.new_days if event.new_days is not None else event.day
        if new_days is None:
            return []
        affected = []
        for activity_id in ids:
            day = int(activity_id.split(":")[0][1:])
            if day > new_days:
                affected.append(activity_id)
        # The last surviving day's return transport may also need to change.
        if new_days >= 1:
            for activity_id in ids:
                day = int(activity_id.split(":")[0][1:])
                activity = mapping[activity_id]
                if day == new_days and activity.get("type") in ("train", "airplane"):
                    affected.append(activity_id)
        return sorted(set(affected))

    if event.kind == "transport_unavailable":
        target = event.target or ""
        target_ids = [
            activity_id
            for activity_id, activity in mapping.items()
            if target and target in str(activity.get("position") or "")
            or target and target == str(activity.get("TrainID") or "")
            or target and target == str(activity.get("FlightID") or "")
        ]
        if not target_ids:
            return ids
        start = min(ids.index(activity_id) for activity_id in target_ids)
        return ids[start:]

    if event.kind == "budget_cut":
        # Budget is global: every cost-bearing activity may need replacement.
        return [
            activity_id
            for activity_id, activity in mapping.items()
            if float(activity.get("cost") or 0) > 0
        ]

    return []


def preserve_activity_ids(plan: Dict[str, Any], event: ChangeEvent) -> List[str]:
    affected: Set[str] = set(affected_activity_ids(plan, event))
    return [activity_id for activity_id in activity_ids(plan) if activity_id not in affected]


def dependency_edges(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    """A readable dependency view: time order, intercity arrival, hotel and budget."""
    edges = []
    ids = activity_ids(plan)
    mapping = activity_map(plan)
    for previous, current in zip(ids, ids[1:]):
        edges.append({"from": previous, "to": current, "kind": "time_order"})
    for activity_id, activity in mapping.items():
        if activity.get("type") in ("train", "airplane"):
            edges.append({"from": activity_id, "to": "arrival", "kind": "intercity_arrival"})
        if activity.get("type") == "accommodation":
            edges.append({"from": activity_id, "to": "next_day", "kind": "hotel_location"})
        if float(activity.get("cost") or 0) > 0:
            edges.append({"from": activity_id, "to": "budget", "kind": "cost"})
    return edges
