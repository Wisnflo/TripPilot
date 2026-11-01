"""Bounded, tool-grounded edit proposals. No scoring files or model calls."""
import copy
import json
import time
from ..money import amount, number, multiply, sum_cost, plan_total

from ..plan.checker import _minutes, _tool_city
from ..plan.compare import activity_map
from .transaction import apply_edits, revision, patch_between
from .verification import validate_revision


class PublicTools:
    """Record and cache only the same allowlisted tools available to ReAct."""
    def __init__(self, toolbox, deadline=None):
        self.toolbox, self.deadline = toolbox, deadline
        self.cache, self.trace, self.row_cache = {}, [], {}

    def call(self, name, arguments):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("proposal deadline")
        key = (name, json.dumps(arguments, sort_keys=True))
        if key not in self.cache:
            response = self.toolbox.call(name, arguments)
            self.cache[key] = response
            self.trace.append({"name": name, "arguments": arguments, "response": response})
        return self.cache[key]

    def rows(self, origin, destination, kind, earliest="00:00"):
        key = (origin, destination, kind, earliest)
        if key in self.row_cache:
            return copy.deepcopy(self.row_cache[key])
        response = self.call("intercity_transport_select", dict(
            start_city=origin, end_city=destination, intercity_type=kind,
            earliest_leave_time=earliest))
        rows = list((response.get("data") or {}).get("rows", []))
        # Pagination is stateful; do not use the cache for next_page, and consume
        # it before issuing another query. No checker-only full-table shortcut.
        total = (response.get("data") or {}).get("row_count", len(rows))
        for _ in range(20):
            if len(rows) >= total:
                break
            more = self.toolbox.call("next_page", {})
            self.trace.append({"name": "next_page", "arguments": {}, "response": more})
            extra = (more.get("data") or {}).get("rows", [])
            if not extra:
                break
            rows.extend(extra)
        # No-service placeholders and malformed rows are not usable fares.
        valid=[]
        id_key='TrainID' if kind=='train' else 'FlightID'
        for row in rows:
            if not isinstance(row,dict) or row.get('Cost') is None:continue
            try:
                if amount(row['Cost'])<0:continue
            except (ValueError,TypeError):continue
            if not all(isinstance(row.get(field),str) and row[field] for field in (id_key,'From','To')):continue
            if _minutes(row.get('BeginTime')) is None or _minutes(row.get('EndTime')) is None:continue
            valid.append(row)
        self.row_cache[key] = copy.deepcopy(valid)
        return copy.deepcopy(valid)

    def route(self, city, start, end, when, mode, people):
        if start == end:
            return []
        response = self.call("goto", dict(city=city, start=start, end=end,
                                          start_time=when, transport_type=mode))
        rows = response.get("data")
        if not response.get("success") or not isinstance(rows, list) or not rows:
            return None
        routes = copy.deepcopy(rows)
        for leg in routes:
            price = leg.get("cost", 0)
            leg["price"] = price
            if leg.get("mode") == "taxi":
                leg["cars"] = 1
                leg["cost"] = price
            elif leg.get("mode") == "metro":
                leg["tickets"] = people
                leg["cost"] = multiply(price, people)
            else:
                leg["cost"] = 0
        return routes


def transport_activity(row, kind, people):
    key = "TrainID" if kind == "train" else "FlightID"
    return dict(type=kind, start=row["From"], end=row["To"], start_time=row["BeginTime"],
                end_time=row["EndTime"], price=number(row["Cost"]),
                cost=multiply(row["Cost"], people), tickets=people,
                transports=[], **{key: row[key]})


def activity_position(activity):
    return activity.get("end") if activity.get("type") in ("train", "airplane") else activity.get("position")


def build_proposals(task, seed, event, toolbox, max_candidates=12, deadline=None):
    tools = PublicTools(toolbox, deadline)
    candidates, rejected = [], []
    seen = set()
    people = seed["people_number"]
    origin, city = _tool_city(task["start_city"]), _tool_city(task["target_city"])
    snapshot = activity_map(seed)
    # Preserve the seed's transport-mode footprint rather than introducing an
    # arbitrary cheap mode that may contradict the original user's preference.
    modes = set()
    for activity in snapshot.values():
        legs = activity.get("transports") or []
        modes.update(t.get("mode") for t in legs if t.get("mode") in ("taxi", "metro"))
        if legs and all(t.get("mode") == "walk" for t in legs):
            modes.add("walk")
    modes = [mode for mode in ("taxi", "metro", "walk") if mode in modes] or ["taxi"]

    def emit(edits, scope, days=None):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("proposal deadline")
        plan = apply_edits(seed, revision(seed), edits, scope, keep_days=days)
        digest = revision(plan)
        if digest in seen:
            return
        seen.add(digest)
        result = validate_revision(task, seed, plan, event, toolbox)
        if not result.ok:
            rejected.append({"codes": [v.code for v in result.violations]})
            return
        candidates.append(dict(id="c%d" % len(candidates), plan=plan, edits=edits,
                               scope=sorted(scope), keep_days=days, revision=revision(seed),
                               preservation=patch_between(seed, plan), checker=result.as_dict()))

    def connect(prev, current, mode):
        route = tools.route(city, activity_position(prev), current.get("start") or current.get("position"),
                            prev["end_time"], mode, people)
        if route is None or (route and (_minutes(route[-1]["end_time"]) is None or
                                       _minutes(current["start_time"]) is None or
                                       _minutes(route[-1]["end_time"]) > _minutes(current["start_time"]))):
            return None
        current = copy.deepcopy(current)
        current["transports"] = route
        return current

    if event.kind == "return_earlier":
        n = event.new_days if event.new_days is not None else event.day
        if not n or not 1 <= n < len(seed["itinerary"]):
            return [], tools.trace, [{"codes": ["event_invalid"]}]
        final = seed["itinerary"][n - 1]["activities"]
        returns = [a for a in snapshot.values() if a.get("type") in ("train", "airplane")
                   and str(origin).lower() in str(a.get("end", "")).lower()]
        if origin != city and not returns:
            return [], tools.trace, [{"codes": ["seed_return_missing"]}]
        # Remove last-night accommodation; broaden a suffix only if retaining it
        # leaves no feasible return. Never lock that accommodation as V1 did.
        last_nonhotel = len(final)
        while last_nonhotel and final[last_nonhotel - 1].get("type") == "accommodation":
            last_nonhotel -= 1
        for keep in range(last_nonhotel, max(0, last_nonhotel - 3), -1):
            if not keep:
                continue
            edits = [{"op": "remove", "id": "d%d:a%d" % (n, a)} for a in range(keep + 1, len(final) + 1)]
            scope = {"d%d:a%d" % (n, a) for a in range(keep + 1, len(final) + 1)}
            scope.update(key for key in snapshot if int(key.split(":")[0][1:]) > n)
            scope.add("d%d:append" % n)
            if origin == city:
                emit(edits, scope, n)
            else:
                prev = final[keep - 1]
                kind = returns[-1]["type"]
                rows = tools.rows(city, origin, kind, prev["end_time"])
                rows.sort(key=lambda r: (r.get("BeginTime", ""), r.get("Cost", 0)))
                for row in rows[:40]:
                    if _minutes(row["EndTime"]) < _minutes(row["BeginTime"]):
                        continue  # strict same-day return, no midnight ambiguity
                    activity = transport_activity(row, kind, people)
                    for mode in modes:
                        revised = connect(prev, activity, mode)
                        if revised:
                            emit(edits + [{"op": "append", "day": n, "value": revised}], scope, n)
                        if len(candidates) >= max_candidates:
                            break
                    if len(candidates) >= max_candidates:
                        break
            if candidates:
                break  # minimum suffix relaxation before exploring larger scope

    elif event.kind == "transport_unavailable":
        for key, old in snapshot.items():
            if event.target not in (old.get("TrainID"), old.get("FlightID")):
                continue
            d, a = [int(s[1:]) for s in key.split(":")]
            values = seed["itinerary"][d - 1]["activities"]
            outbound = str(origin).lower() in str(old.get("end", "")).lower()
            rows = tools.rows(city if outbound else origin, origin if outbound else city, old["type"])
            id_key = "TrainID" if old["type"] == "train" else "FlightID"
            rows.sort(key=lambda row: (abs((_minutes(row["BeginTime"]) or 0) - (_minutes(old["start_time"]) or 0)), row.get("Cost", 0)))
            for row in rows[:80]:
                if row[id_key] == event.target or _minutes(row["EndTime"]) < _minutes(row["BeginTime"]):
                    continue
                for mode in modes:
                    revised = transport_activity(row, old["type"], people)
                    if a > 1:
                        revised = connect(values[a - 2], revised, mode)
                        if not revised:
                            continue
                    edits = [{"op": "replace", "id": key, "value": revised}]
                    scope = {key}
                    if a < len(values):
                        nxt = connect(revised, values[a], mode)
                        if not nxt:
                            continue
                        next_id = "d%d:a%d" % (d, a + 1)
                        edits.append({"op": "replace", "id": next_id, "value": nxt})
                        scope.add(next_id)
                    emit(edits, scope)
                    if len(candidates) >= max_candidates:
                        break
                if len(candidates) >= max_candidates:
                    break
    elif event.kind == "budget_cut":
        # Replace costly local transit by public-tool alternatives without
        # deleting attractions/meals or silently relaxing user preferences.
        edits, scope = [], set()
        for key, old in snapshot.items():
            routes = old.get("transports") or []
            if not routes or not any(t.get("mode") == "taxi" for t in routes):
                continue
            original_cost = sum_cost(routes)
            options = []
            for mode in [m for m in modes if m in ("metro", "walk")]:
                new = tools.route(city, routes[0]["start"], routes[-1]["end"], routes[0]["start_time"], mode, people)
                if new is not None and (not new or (_minutes(new[-1]["end_time"]) is not None and
                                                     _minutes(new[-1]["end_time"]) <= _minutes(old["start_time"]))):
                    if sum_cost(new) < original_cost:
                        options.append(new)
            if options:
                revised = copy.deepcopy(old)
                revised["transports"] = min(options, key=lambda value: sum_cost(value))
                edits.append({"op": "replace", "id": key, "value": revised})
                scope.add(key)
                emit(edits, scope)
    candidates.sort(key=lambda c: (c["preservation"]["removed_or_modified"],
                                  plan_total(c["plan"])))
    return candidates, tools.trace, rejected
