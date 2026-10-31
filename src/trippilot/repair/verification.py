"""Independent public event validation shared by all V2 methods; no gold access."""
from decimal import Decimal

from ..plan.checker import CheckResult, Violation, check_plan, _tool_city
from .events import apply_event_to_task


def event_violations(task, seed, plan, event):
    failures = []
    def fail(code, message):
        failures.append(Violation(code, message))
    for key in ("people_number", "start_city", "target_city"):
        before, after = seed.get(key), plan.get(key)
        if key.endswith("city"):
            before, after = _tool_city(before), _tool_city(after)
        if before != after:
            fail("event_root_changed", "%s must remain unchanged" % key)
    days = plan.get("itinerary") or []
    activities = [a for d in days for a in d.get("activities", [])]
    if event.kind == "transport_unavailable":
        if not event.target:
            fail("event_invalid", "missing unavailable transport ID")
        elif any(str(a.get("TrainID")) == event.target or str(a.get("FlightID")) == event.target
                 for a in activities):
            fail("event_transport_unavailable", "banned transport remains in plan")
    if event.kind == "return_earlier":
        n = event.new_days if event.new_days is not None else event.day
        if n is None or n < 1 or n >= len(seed.get("itinerary", [])):
            fail("event_invalid", "return_earlier needs fewer positive days")
        elif len(days) != n:
            fail("event_days", "trip must have %d days" % n)
    # A shorter array alone does not prove the traveller returned home.
    if event.kind in ("return_earlier", "transport_unavailable"):
        origin, destination = _tool_city(task.get("start_city")), _tool_city(task.get("target_city"))
        if origin != destination:
            last = activities[-1] if activities else {}
            if last.get("type") not in ("train", "airplane") or str(origin).lower() not in str(last.get("end", "")).lower():
                fail("event_return_missing", "last activity must return to the origin city")
    if event.kind == "budget_cut":
        if event.new_budget is None or event.new_budget < 0:
            fail("event_invalid", "budget must be nonnegative")
        else:
            total = sum(Decimal(str(a.get("cost", 0))) +
                        sum(Decimal(str(t.get("cost", 0))) for t in a.get("transports", []))
                        for a in activities)
            if total > event.new_budget:
                fail("event_budget", "total exceeds new budget")
    return failures


def validate_revision(task, seed, plan, event, toolbox=None):
    checked = check_plan(apply_event_to_task(task, event), plan, toolbox=toolbox)
    checked.violations.extend(event_violations(task, seed, plan, event))
    checked.supported.append("explicit_dynamic_event")
    return checked
