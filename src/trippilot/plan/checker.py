"""Runtime checker: public-data and user-condition checks, not the official scorer."""

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from ..money import amount, decimal, plan_total
from typing import Any, Dict, List, Optional

from .schema import validate_plan
from ..request import request_text


@dataclass
class Violation:
    code: str
    message: str
    path: str = ""


@dataclass
class CheckResult:
    violations: List[Violation] = field(default_factory=list)
    supported: List[str] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [v.__dict__ for v in self.violations],
            "supported": self.supported,
            "unsupported": self.unsupported,
        }


BUDGET_RE = re.compile(
    r"(?:预算|budget(?:\s+of)?)\s*[:：]?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

INTERCITY_TYPES = ("train", "airplane")
MEAL_WINDOWS = {
    "breakfast": (6 * 60, 9 * 60),
    "lunch": (11 * 60, 14 * 60),
    "dinner": (17 * 60, 20 * 60),
}

CITY_ZH_TO_EN = {
    "北京": "Beijing",
    "上海": "Shanghai",
    "南京": "Nanjing",
    "苏州": "Suzhou",
    "杭州": "Hangzhou",
    "深圳": "Shenzhen",
    "成都": "Chengdu",
    "武汉": "Wuhan",
    "广州": "Guangzhou",
    "重庆": "Chongqing",
}


def _tool_city(city: Any) -> Any:
    if not isinstance(city, str):
        return city
    return CITY_ZH_TO_EN.get(city.strip(), city.strip())


def _minutes(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = TIME_RE.match(value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def extract_budget(text: str) -> Optional[Decimal]:
    match = BUDGET_RE.search(text or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _activities(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    for day in plan.get("itinerary") or []:
        if isinstance(day, dict):
            for activity in day.get("activities") or []:
                if isinstance(activity, dict):
                    result.append(activity)
    return result


def _rows(result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, dict) or not result.get("success"):
        return []
    data = result.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _lookup_place(toolbox: Any, city: str, name: str, kind: str, cache: Dict[Any, Any]) -> Optional[Dict[str, Any]]:
    key = (kind, city, name)
    if key in cache:
        return cache[key]
    if kind == "attraction":
        tool = "attractions_select"
    elif kind == "accommodation":
        tool = "accommodations_select"
    else:
        tool = "restaurants_select"
    result = toolbox.call(tool, {"city": city, "key": "name", "op": "eq", "value": name})
    rows = _rows(result)
    exact = [row for row in rows if str(row.get("name")) == name]
    cache[key] = exact[0] if exact else None
    return cache[key]


def _within(time_value: Any, open_time: Any, close_time: Any) -> Optional[bool]:
    start = _minutes(time_value)
    open_minutes = _minutes(open_time)
    close_minutes = _minutes(close_time)
    if start is None or open_minutes is None or close_minutes is None:
        return None
    if close_minutes < open_minutes:
        return None  # overnight hours are not modelled
    return open_minutes <= start <= close_minutes


def _number(value: Any) -> Optional[float]:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _goto_group(
    toolbox: Any,
    city: str,
    transports: List[Dict[str, Any]],
    cache: Dict[Any, Any],
) -> Optional[List[Dict[str, Any]]]:
    """Call goto once per transport group, like the official transport check."""
    first, last = transports[0], transports[-1]
    mode = "metro" if len(transports) == 3 else first.get("mode")
    key = (city, first.get("start"), last.get("end"), first.get("start_time"), mode)
    if key not in cache:
        tool_result = toolbox.call(
            "goto",
            {
                "city": city,
                "start": first.get("start"),
                "end": last.get("end"),
                "start_time": first.get("start_time"),
                "transport_type": mode,
            },
        )
        cache[key] = _rows(tool_result)
    rows = cache[key]
    return rows if rows else None


def check_plan(task: Dict[str, Any], plan: Dict[str, Any], toolbox: Any = None) -> CheckResult:
    result = CheckResult()
    result.supported = [
        "schema",
        "day_count",
        "people_number",
        "start_target_city",
        "activity_time_order",
        "transport_time_order",
        "transport_structure",
        "activity_cost_rules",
        "meal_time_windows",
        "space_chain",
        "accommodation_nights",
        "intercity_transport_presence",
        "ticket_fields",
        "budget_if_in_text",
    ]
    if toolbox is not None:
        result.supported.extend(
            [
                "entity_grounding",
                "opening_hours",
                "duplicate_activities",
                "meal_type_once",
                "transport_matches_goto",
                "intercity_transport_values",
            ]
        )
    else:
        result.unsupported.extend(
            ["entity_grounding", "opening_hours", "transport_matches_goto", "intercity_transport_values"]
        )
    result.unsupported.extend(["room_rules", "overnight_hours"])

    schema_errors = validate_plan(plan)
    for error in schema_errors:
        result.violations.append(Violation("schema", error))

    itinerary = plan.get("itinerary") if isinstance(plan, dict) else None
    expected_days = task.get("days")
    if isinstance(itinerary, list) and isinstance(expected_days, int):
        if len(itinerary) != expected_days:
            result.violations.append(
                Violation("day_count", "plan has %d days, task expects %d" % (len(itinerary), expected_days))
            )
        day_numbers = [day.get("day") for day in itinerary if isinstance(day, dict)]
        if day_numbers != list(range(1, expected_days + 1)):
            result.violations.append(
                Violation("day_numbers", "day numbers must be 1..%d, got %r" % (expected_days, day_numbers))
            )

    if plan.get("people_number") != task.get("people_number"):
        result.violations.append(
            Violation("people_number", "plan people_number does not match the request")
        )
    if _tool_city(plan.get("start_city")) != _tool_city(task.get("start_city")):
        result.violations.append(Violation("start_city", "plan start_city does not match the request"))
    if _tool_city(plan.get("target_city")) != _tool_city(task.get("target_city")):
        result.violations.append(Violation("target_city", "plan target_city does not match the request"))

    for day_index, day in enumerate(itinerary or []):
        if not isinstance(day, dict):
            continue
        timed = []
        for activity in day.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            start = _minutes(activity.get("start_time"))
            end = _minutes(activity.get("end_time"))
            if start is None or end is None:
                continue
            if end <= start:
                if activity.get("type") in INTERCITY_TYPES and end < start:
                    # Overnight arrival: the official tool table can return a
                    # next-day end time (for example 21:35 -> 00:00).
                    end += 24 * 60
                else:
                    result.violations.append(
                        Violation(
                            "time_order",
                            "activity ends before or at its start time",
                            "itinerary[%d]" % day_index,
                        )
                    )
                    continue
            timed.append((start, end, activity.get("type"), activity.get("position") or activity.get("name")))
        timed.sort(key=lambda item: item[0])
        for previous, current in zip(timed, timed[1:]):
            if previous[1] > current[0]:
                result.violations.append(
                    Violation(
                        "activity_overlap",
                        "activity %r overlaps the next activity" % (previous[3],),
                        "itinerary[%d]" % day_index,
                    )
                )

        previous_end = 0
        for activity in sorted(
            (a for a in (day.get("activities") or []) if isinstance(a, dict)),
            key=lambda a: _minutes(a.get("start_time")) or 0,
        ):
            activity_start = _minutes(activity.get("start_time"))
            transports = [t for t in (activity.get("transports") or []) if isinstance(t, dict)]
            transport_times = []
            for transport in transports:
                t_start = _minutes(transport.get("start_time"))
                t_end = _minutes(transport.get("end_time"))
                if t_start is None or t_end is None:
                    continue
                if t_end < t_start:
                    result.violations.append(
                        Violation("transport_time_order", "transport ends before its start", "itinerary[%d]" % day_index)
                    )
                    continue
                transport_times.append((t_start, t_end, transport.get("mode")))
            transport_times.sort(key=lambda item: item[0])
            for previous, current in zip(transport_times, transport_times[1:]):
                if previous[1] > current[0]:
                    result.violations.append(
                        Violation("transport_overlap", "two transports overlap", "itinerary[%d]" % day_index)
                    )
            if transport_times:
                if transport_times[0][0] < previous_end:
                    result.violations.append(
                        Violation(
                            "transport_starts_before_previous_activity",
                            "transport starts before the previous activity ends",
                            "itinerary[%d]" % day_index,
                        )
                    )
                if activity_start is not None and transport_times[-1][1] > activity_start:
                    result.violations.append(
                        Violation(
                            "arrival_after_activity_start",
                            "last transport ends after the activity start time",
                            "itinerary[%d]" % day_index,
                        )
                    )
            if activity_start is not None:
                previous_end = max(previous_end, _minutes(activity.get("end_time")) or activity_start)

    accommodation_count = sum(1 for activity in _activities(plan) if activity.get("type") == "accommodation")
    if (expected_days or 1) > 1 and accommodation_count == 0:
        result.violations.append(
            Violation(
                "accommodation_required",
                "trips longer than one day need at least one accommodation activity",
            )
        )

    if task.get("start_city") != task.get("target_city"):
        intercity = [
            activity
            for activity in _activities(plan)
            if activity.get("type") in ("train", "airplane")
        ]
        if not intercity:
            result.violations.append(
                Violation("intercity_missing", "no intercity train/airplane activity in the plan")
            )

    # The official hard constraints require tickets == 1 for attractions and
    # intercity transport, tickets == 1 for metro legs, and cars == 1 for taxis.
    people_number = plan.get("people_number") or task.get("people_number") or 1
    for activity in _activities(plan):
        kind = activity.get("type")
        if kind in ("attraction", "train", "airplane"):
            if activity.get("tickets") != people_number:
                result.violations.append(
                    Violation(
                        "activity_tickets",
                        "%s activity must have tickets == people_number (%s)"
                        % (kind, people_number),
                    )
                )
        if kind in ("attraction", "lunch", "dinner", "breakfast", "accommodation"):
            price = _number(activity.get("price"))
            cost = _number(activity.get("cost"))
            if kind == "attraction":
                multiplier = _number(activity.get("tickets"))
            elif kind == "accommodation":
                multiplier = _number(activity.get("rooms"))
            else:
                multiplier = _number(people_number)
            if price is not None and cost is not None and multiplier is not None:
                if amount(decimal(price) * decimal(multiplier)) != amount(cost):
                    result.violations.append(
                        Violation(
                            "activity_cost_rule",
                            "%s cost must equal price * %s"
                            % (
                                kind,
                                "tickets"
                                if kind == "attraction"
                                else "rooms"
                                if kind == "accommodation"
                                else "people_number",
                            ),
                        )
                    )
        transports = [t for t in (activity.get("transports") or []) if isinstance(t, dict)]
        if not transports:
            continue
        modes = [t.get("mode") for t in transports]
        if not (
            (len(transports) == 1 and modes[0] in ("walk", "taxi"))
            or (len(transports) == 3 and modes == ["walk", "metro", "walk"])
        ):
            result.violations.append(
                Violation(
                    "transport_structure",
                    "inner-city transport must be one walk/taxi leg or walk-metro-walk",
                )
            )
        for transport in transports:
            mode = transport.get("mode")
            price = _number(transport.get("price"))
            cost = _number(transport.get("cost"))
            if mode == "walk":
                if cost is not None and cost != 0:
                    result.violations.append(
                        Violation("transport_cost_rule", "walk transport must have cost == 0")
                    )
            elif mode == "metro":
                tickets = transport.get("tickets")
                if tickets != people_number:
                    result.violations.append(
                        Violation(
                            "transport_tickets",
                            "metro transport must have tickets == people_number (%s)" % people_number,
                        )
                    )
                if price is not None and cost is not None and _number(tickets) is not None:
                    if amount(decimal(price) * decimal(tickets)) != amount(cost):
                        result.violations.append(
                            Violation("transport_cost_rule", "metro cost must equal price * tickets")
                        )
            elif mode == "taxi":
                if transport.get("cars") != 1:
                    result.violations.append(
                        Violation("taxi_cars", "taxi transport must have cars == 1")
                    )
                if price is not None and cost is not None and amount(price) != amount(cost):
                    result.violations.append(
                        Violation("transport_cost_rule", "taxi cost must equal price * cars")
                    )

    place_counter = Counter(
        (activity.get("type"), activity.get("position"))
        for activity in _activities(plan)
        if activity.get("type") in ("attraction", "lunch", "dinner", "breakfast")
        and activity.get("position")
    )
    for (kind, name), count in place_counter.items():
        if count > 1:
            result.violations.append(
                Violation("duplicate_activity", "%s %r appears %d times" % (kind, name, count))
            )

    restaurant_counter = Counter(
        activity.get("position")
        for activity in _activities(plan)
        if activity.get("type") in ("lunch", "dinner", "breakfast") and activity.get("position")
    )
    for name, count in restaurant_counter.items():
        if count > 1:
            result.violations.append(
                Violation(
                    "duplicate_restaurant",
                    "restaurant %r appears %d times in the trip" % (name, count),
                )
            )

    for day_index, day in enumerate(itinerary or []):
        if not isinstance(day, dict):
            continue
        meal_counter = Counter(
            activity.get("type")
            for activity in day.get("activities") or []
            if isinstance(activity, dict) and activity.get("type") in ("breakfast", "lunch", "dinner")
        )
        for meal, count in meal_counter.items():
            if count > 1:
                result.violations.append(
                    Violation(
                        "meal_type_once",
                        "%s appears %d times in day %d" % (meal, count, day_index + 1),
                    )
                )
        for activity in day.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            kind = activity.get("type")
            window = MEAL_WINDOWS.get(kind)
            if window is None:
                continue
            start = _minutes(activity.get("start_time"))
            end = _minutes(activity.get("end_time"))
            if start is None or end is None:
                continue
            lower, upper = window
            if start >= upper or end <= lower:
                result.violations.append(
                    Violation(
                        "meal_time_window",
                        "%s must be scheduled inside %02d:%02d--%02d:%02d"
                        % (kind, lower // 60, lower % 60, upper // 60, upper % 60),
                    )
                )

    # Mirror the official space check: inner-city transports must chain the
    # previous activity position to the current one, and intercity activities
    # may only be the first or last activity of the whole plan.
    position_list: List[str] = []
    days = [day for day in (itinerary or []) if isinstance(day, dict)]
    last_day_index = len(days) - 1
    for day_index, day in enumerate(days):
        activities = [a for a in (day.get("activities") or []) if isinstance(a, dict)]
        for activity_index, activity in enumerate(activities):
            kind = activity.get("type")
            is_first = day_index == 0 and activity_index == 0
            is_last = day_index == last_day_index and activity_index == len(activities) - 1
            if kind in INTERCITY_TYPES and not (is_first or is_last):
                result.violations.append(
                    Violation(
                        "intercity_position",
                        "intercity activity must be the first or last activity of the plan",
                    )
                )
            if activity.get("position"):
                current_position = activity.get("position")
                appended_position = current_position
            elif kind in INTERCITY_TYPES and activity.get("start") and activity.get("end"):
                current_position = activity.get("start")
                appended_position = activity.get("end")
            else:
                result.violations.append(
                    Violation("position_missing", "activity needs position or start/end")
                )
                continue
            transports = [t for t in (activity.get("transports") or []) if isinstance(t, dict)]
            if position_list:
                previous = position_list[-1]
                if current_position != previous:
                    if not transports:
                        result.violations.append(
                            Violation(
                                "transport_missing",
                                "need transport from %r to %r" % (previous, current_position),
                            )
                        )
                    else:
                        if transports[0].get("start") != previous:
                            result.violations.append(
                                Violation(
                                    "transport_chain_origin",
                                    "transport starts at %r, previous position is %r"
                                    % (transports[0].get("start"), previous),
                                )
                            )
                        if transports[-1].get("end") != current_position:
                            result.violations.append(
                                Violation(
                                    "transport_chain_destination",
                                    "transport ends at %r, current position is %r"
                                    % (transports[-1].get("end"), current_position),
                                )
                            )
                elif transports:
                    result.violations.append(
                        Violation(
                            "transport_present_same_position",
                            "transport must be empty between activities at the same position",
                        )
                    )
            position_list.append(appended_position)

    if toolbox is not None:
        cache: Dict[Any, Any] = getattr(toolbox,"validation_cache",{})
        city = _tool_city(task.get("target_city"))
        start_city = _tool_city(task.get("start_city"))
        for activity in _activities(plan):
            kind = activity.get("type")
            if kind not in ("attraction", "lunch", "dinner", "breakfast", "accommodation"):
                continue
            name = activity.get("position")
            if not name or not city:
                continue
            place = _lookup_place(toolbox, city, name, kind, cache)
            if place is None and start_city and start_city != city:
                place = _lookup_place(toolbox, start_city, name, kind, cache)
            if place is None:
                result.violations.append(
                    Violation("entity_not_found", "entity %r not found in the sandbox" % name)
                )
                continue
            place_price = _number(place.get("price"))
            activity_price = _number(activity.get("price"))
            if place_price is not None and activity_price is not None and amount(place_price) != amount(activity_price):
                result.violations.append(
                    Violation(
                        "entity_price_mismatch",
                        "%r price %r does not match sandbox price %r"
                        % (name, activity_price, place_price),
                    )
                )
            if kind == "accommodation":
                expected_room_type = place.get("numbed")
                if expected_room_type is not None and activity.get("room_type") != expected_room_type:
                    result.violations.append(
                        Violation(
                            "room_type_mismatch",
                            "%r room_type %r does not match sandbox %r"
                            % (name, activity.get("room_type"), expected_room_type),
                        )
                    )
            open_time = place.get("opentime")
            close_time = place.get("endtime")
            if open_time and close_time:
                if _within(activity.get("start_time"), open_time, close_time) is False:
                    result.violations.append(
                        Violation(
                            "visits_closed_time",
                            "%r starts outside open hours (%s--%s)" % (name, open_time, close_time),
                        )
                    )
                if _within(activity.get("end_time"), open_time, close_time) is False:
                    result.violations.append(
                        Violation(
                            "visits_closed_time",
                            "%r ends outside open hours (%s--%s)" % (name, open_time, close_time),
                        )
                    )

        for activity in _activities(plan):
            transports = [t for t in (activity.get("transports") or []) if isinstance(t, dict)]
            if not transports or not city:
                continue
            if not (
                (len(transports) == 1 and transports[0].get("mode") in ("walk", "taxi"))
                or (len(transports) == 3 and [t.get("mode") for t in transports] == ["walk", "metro", "walk"])
            ):
                continue
            tool_rows = _goto_group(toolbox, city, transports, cache)
            if tool_rows is None or len(tool_rows) != len(transports):
                if "transport_grounding_unmatched" not in result.unsupported:
                    result.unsupported.append("transport_grounding_unmatched")
                continue
            for transport, tool_row in zip(transports, tool_rows):
                for field in ("start", "end", "start_time", "end_time"):
                    if tool_row.get(field) is not None and transport.get(field) != tool_row.get(field):
                        result.violations.append(
                            Violation(
                                "transport_mismatch",
                                "transport %s mismatch: plan %r vs goto %r"
                                % (field, transport.get(field), tool_row.get(field)),
                            )
                        )
                for plan_field, tool_field in (("price", "cost"), ("distance", "distance")):
                    expected = _number(tool_row.get(tool_field))
                    actual = _number(transport.get(plan_field))
                    if expected is not None and actual is not None and (amount(expected) != amount(actual) if plan_field == "price" else abs(expected - actual) > 0.1):
                        result.violations.append(
                            Violation(
                                "transport_mismatch",
                                "transport %s mismatch: plan %r vs goto %r"
                                % (plan_field, actual, expected),
                            )
                        )

        intercity_rows: Dict[Any, List[Dict[str, Any]]] = {}
        for activity in _activities(plan):
            kind = activity.get("type")
            if kind not in INTERCITY_TYPES:
                continue
            id_field = "TrainID" if kind == "train" else "FlightID"
            transport_id = activity.get(id_field)
            if not transport_id:
                result.violations.append(
                    Violation("intercity_id_missing", "%s activity needs %s" % (kind, id_field))
                )
                continue
            rows: List[Dict[str, Any]] = []
            for origin, destination in ((start_city, city), (city, start_city)):
                if not origin or not destination:
                    continue
                key = (origin, destination, kind)
                if key not in intercity_rows:
                    if hasattr(toolbox, "intercity_rows"):
                        intercity_rows[key] = toolbox.intercity_rows(origin, destination, kind)
                    else:
                        tool_result = toolbox.call(
                            "intercity_transport_select",
                            {
                                "start_city": origin,
                                "end_city": destination,
                                "intercity_type": kind,
                            },
                        )
                        intercity_rows[key] = _rows(tool_result)
                rows.extend(intercity_rows[key])
            matches = [row for row in rows if str(row.get(id_field)) == str(transport_id)]
            if not matches:
                if rows:
                    result.violations.append(
                        Violation(
                            "intercity_not_found",
                            "%s %r not found between %r and %r"
                            % (id_field, transport_id, start_city, city),
                        )
                    )
                elif "intercity_grounding_unmatched" not in result.unsupported:
                    result.unsupported.append("intercity_grounding_unmatched")
                continue
            tool_row = matches[0]
            for plan_field, tool_field in (
                ("start", "From"),
                ("end", "To"),
                ("start_time", "BeginTime"),
                ("end_time", "EndTime"),
            ):
                if tool_row.get(tool_field) is not None and activity.get(plan_field) != tool_row.get(tool_field):
                    result.violations.append(
                        Violation(
                            "intercity_mismatch",
                            "intercity %s mismatch: plan %r vs tool %r"
                            % (plan_field, activity.get(plan_field), tool_row.get(tool_field)),
                        )
                    )
            expected = _number(tool_row.get("Cost"))
            actual = _number(activity.get("price"))
            if expected is not None and actual is not None and amount(expected) != amount(actual):
                result.violations.append(
                    Violation(
                        "intercity_mismatch",
                        "intercity price mismatch: plan %r vs tool %r" % (actual, expected),
                    )
                )
            price = _number(activity.get("price"))
            cost = _number(activity.get("cost"))
            tickets = _number(activity.get("tickets"))
            if price is not None and cost is not None and tickets is not None:
                if amount(decimal(price) * decimal(tickets)) != amount(cost):
                    result.violations.append(
                        Violation(
                            "intercity_cost_rule",
                            "intercity cost must equal price * tickets",
                        )
                    )

    budget = extract_budget(request_text(task))
    if budget is None:
        result.unsupported.append("budget_not_found_in_text")
    else:
        total = Decimal("0")
        for activity in _activities(plan):
            cost = activity.get("cost")
            if isinstance(cost, (int, float)):
                total += Decimal(str(cost))
            for transport in activity.get("transports") or []:
                if not isinstance(transport, dict):
                    continue
                transport_cost = transport.get("cost")
                if isinstance(transport_cost, (int, float)):
                    total += Decimal(str(transport_cost))
        if total > budget:
            result.violations.append(
                Violation("budget_exceeded", "plan total cost %s exceeds budget %s" % (total, budget))
            )
    return result
