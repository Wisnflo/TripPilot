"""Deterministic plan normalisation for fields fixed by benchmark rules.

The official output schema does not list ``cars`` on taxi legs, but the
official hard constraints require ``cars == 1`` for every taxi.  Tickets are
also always ``people_number`` for attractions, intercity transport and metro
legs, and costs are simple price multipliers.  Filling these values is
arithmetic consistency, not route planning; it is applied to every method.
"""

from typing import Any, Dict

INTERCITY_TYPES = ("train", "airplane")
MEAL_TYPES = ("breakfast", "lunch", "dinner")


def _number(value: Any):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _set_cost(item: Dict[str, Any], multiplier: int) -> None:
    price = _number(item.get("price"))
    if price is not None:
        item["cost"] = round(price * multiplier, 2)


def normalise_plan(plan: Dict[str, Any], people_number: Any) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return plan
    people = people_number if isinstance(people_number, int) and people_number > 0 else 1

    for day in plan.get("itinerary") or []:
        if not isinstance(day, dict):
            continue
        for activity in day.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            kind = activity.get("type")
            if kind in ("attraction",) + INTERCITY_TYPES:
                activity["tickets"] = people
                _set_cost(activity, people)
            elif kind in MEAL_TYPES:
                _set_cost(activity, people)
            elif kind == "accommodation":
                rooms = activity.get("rooms")
                if not isinstance(rooms, int) or rooms < 1:
                    rooms = 1
                activity["rooms"] = rooms
                _set_cost(activity, rooms)
            for transport in activity.get("transports") or []:
                if not isinstance(transport, dict):
                    continue
                mode = transport.get("mode")
                if mode == "taxi":
                    transport["cars"] = 1
                    _set_cost(transport, 1)
                elif mode == "metro":
                    transport["tickets"] = people
                    _set_cost(transport, people)
                elif mode == "walk":
                    transport["cost"] = 0.0
    return plan
