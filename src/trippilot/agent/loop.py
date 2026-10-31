"""ReAct loop with structured tools, schema validation and bounded repair."""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import json_repair

from ..request import request_text
from ..models.config import ModelConfig
from ..models.openai_adapter import ChatStep, ModelRequestError, chat_step
from ..plan.checker import CheckResult, check_plan
from ..plan.compare import activity_map, changed_ids, preserve_violations
from ..plan.normalize import normalise_plan
from ..plan.schema import load_plan_schema, validate_plan
from ..tools.agent_tools import ToolBox


SYSTEM_PROMPT = """You are TripPilot, a travel planning agent for a frozen benchmark sandbox.
Use the provided tools to gather facts. Never invent POIs, prices, opening hours, train/flight IDs or transport times.
Use exact entity names returned by tools. If a tool returns a page (Page/Total), decide whether you need next_page.
When you have enough information, reply with ONLY one JSON object matching this schema:
{schema}

Rules:
- Use the exact English city names from the task (for example Shanghai, Hangzhou) in tool arguments.
- For every leg between activities, call the goto tool and copy its returned transport entries exactly; do not estimate distance or duration.
- Call goto once per activity: for a walk or taxi it returns one leg; for a metro trip it returns three legs (walk, metro, walk) that must be copied in order.
- The first transport of an activity must start at the previous activity position; the last must end at this activity position. Leave transports empty when the position does not change.
- The last transport must end at or before the activity start_time.
- Do not repeat a tool call with identical arguments. If a tool returns no rows, change the query.
- Gather facts with at most 8 tool calls, then output the plan.
- itinerary length equals the requested days; day numbers are 1..N.
- activities within a day are chronological and non-overlapping.
- include intercity train/airplane activities when start_city != target_city; a 1-day round trip needs both directions.
- intercity activities may only be the first or last activity; use a TrainID for train and a FlightID for airplane, copied from intercity_transport_select.
- include one accommodation activity per night (days - 1 nights); 1-day trips need none.
- tickets = people_number on every attraction, train, airplane and metro leg; taxi legs need cars=1; walk legs need no tickets and cost=0.
- cost rules: attraction cost = price * tickets; restaurant cost = price * people; metro cost = price * tickets; taxi cost = price * cars.
- every accommodation activity needs rooms and room_type copied from accommodations_select; accommodation cost = price * rooms.
- meal time windows: breakfast 06:00-09:00, lunch 11:00-14:00, dinner 17:00-20:00.
- respect every stated preference in the request (transport mode, local cuisine or food type, room type); verify cuisine with restaurants_cuisine when the request mentions food.
- include every required numeric field; do not omit tickets, cost, price or transports.
- times use HH:MM; costs are JSON numbers; no prose outside the JSON.
"""


@dataclass
class Trajectory:
    task_uid: str
    method: str
    model: str
    terminal_state: str
    plan: Optional[Dict[str, Any]]
    steps: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    checker: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_uid": self.task_uid,
            "method": self.method,
            "model": self.model,
            "terminal_state": self.terminal_state,
            "plan": self.plan,
            "steps": self.steps,
            "usage": self.usage,
            "checker": self.checker,
            "error": self.error,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_text(task: Dict[str, Any]) -> str:
    request = request_text(task)
    return (
        "Task uid: {uid}\n"
        "Start city: {start}\nTarget city: {target}\nDays: {days}\nPeople: {people}\n"
        "Request: {request}\n"
        "Return only the final JSON plan when done."
    ).format(
        uid=task.get("uid"),
        start=task.get("start_city"),
        target=task.get("target_city"),
        days=task.get("days"),
        people=task.get("people_number"),
        request=request,
    )


def extract_plan(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json_repair.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _tool_call_parts(tool_call: Any):
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        return (
            tool_call.get("id"),
            function.get("name"),
            function.get("arguments"),
        )
    function = getattr(tool_call, "function", None)
    return (
        getattr(tool_call, "id", None),
        getattr(function, "name", None),
        getattr(function, "arguments", None),
    )


def _sum_usage(total: Dict[str, int], step: ChatStep) -> None:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
        value = getattr(step, key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def _feedback(violations, prefix: str) -> str:
    lines = [prefix]
    for violation in violations:
        lines.append("- [%s] %s" % (getattr(violation, "code", "check"), getattr(violation, "message", violation)))
    lines.append("Fix the whole plan and return only the JSON object.")
    return "\n".join(lines)


def run_react(
    task: Dict[str, Any],
    config: ModelConfig,
    toolbox: ToolBox,
    method: str = "b2",
    checker: Callable[[Dict[str, Any], Dict[str, Any]], CheckResult] = check_plan,
    max_steps: int = 12,
    max_repairs: int = 2,
    client: Any = None,
    step_fn: Optional[Callable[[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], ChatStep]] = None,
    seed_plan: Optional[Dict[str, Any]] = None,
    event_text: Optional[str] = None,
    allowed_ids: Optional[List[str]] = None,
    preserve_ids: Optional[List[str]] = None,
    max_seconds: Optional[float] = None,
) -> Trajectory:
    """Run one task. method b1 disables semantic checker feedback; b2 enables it.

    seed_plan/event_text/allowed_ids/preserve_ids turn the loop into a repair run:
    the model sees the original plan and may only change the affected existing
    activities; preserved activities must remain byte-identical.
    """
    tools = toolbox.openai_tools()
    schema_text = json.dumps(load_plan_schema(), ensure_ascii=False)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{schema}", schema_text)},
        {"role": "user", "content": task_text(task)},
    ]
    if seed_plan is not None or event_text or preserve_ids:
        context = []
        if event_text:
            context.append("Change event: %s" % event_text)
        if seed_plan is not None:
            context.append("Current plan:\n%s" % json.dumps(seed_plan, ensure_ascii=False))
        if allowed_ids:
            context.append("You may modify only these existing activity ids: %s" % ", ".join(allowed_ids))
        if preserve_ids:
            context.append("You must preserve these activity ids byte-for-byte: %s" % ", ".join(preserve_ids))
        context.append("Return the full revised plan as JSON; do not return a partial plan.")
        messages.append({"role": "user", "content": "\n\n".join(context)})
    steps: List[Dict[str, Any]] = []
    usage: Dict[str, int] = {}
    repairs = 0
    started = _now()
    clock = time.perf_counter()
    plan = None
    checker_result = None
    terminal = "max_steps"
    metadata: Dict[str, Any] = {"event_text": event_text, "usage_complete": True}
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None

    def call_model(force_no_tools: bool = False) -> ChatStep:
        if step_fn is not None:
            return step_fn(messages, None if force_no_tools else tools)
        return chat_step(config, messages, tools=None if force_no_tools else tools, client=client, deadline=deadline)

    for step_index in range(1, max_steps + 1):
        if max_seconds is not None and time.perf_counter() - clock > max_seconds:
            terminal = "timeout"
            metadata["timeout_seconds"] = max_seconds
            break
        try:
            result = call_model(force_no_tools=(step_index == max_steps))
        except ModelRequestError as exc:
            terminal = "request_error"
            metadata["request_error"] = str(exc)
            metadata["request_attempts"] = exc.attempts
            metadata["request_latencies"] = exc.request_latencies
            metadata["usage_complete"] = False
            steps.append({"step": step_index, "attempts": exc.attempts,
                          "request_latencies": exc.request_latencies,
                          "error": type(exc.last_error).__name__})
            if deadline is not None and time.monotonic() >= deadline:
                terminal = "timeout"
            break
        _sum_usage(usage, result)
        if result.total_tokens is None:
            metadata["usage_complete"] = False
        tool_calls = list(result.tool_calls or [])
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": result.content or ""}
        if tool_calls:
            assistant_message["tool_calls"] = [
                tool_call.model_dump() if hasattr(tool_call, "model_dump") else tool_call
                for tool_call in tool_calls
            ]
        messages.append(assistant_message)

        step_record: Dict[str, Any] = {
            "step": step_index,
            "content": (result.content or "")[:2000],
            "tool_calls": [],
            "tool_results": [],
            "latency_seconds": result.latency_seconds,
            "finish_reason": result.finish_reason,
            "request_id": result.request_id,
            "response_model": result.model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "attempts": getattr(result, "attempts", 1),
            "request_latencies": getattr(result, "request_latencies", []),
        }
        steps.append(step_record)

        if tool_calls:
            for tool_call in tool_calls:
                call_id, name, raw_arguments = _tool_call_parts(tool_call)
                try:
                    arguments = json.loads(raw_arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_result = toolbox.call(str(name), arguments)
                serialized = json.dumps(tool_result, ensure_ascii=False)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": serialized[:8000],
                    }
                )
                step_record["tool_calls"].append({"name": name, "arguments": arguments})
                step_record["tool_results"].append(
                    {
                        "name": name,
                        "success": tool_result.get("success"),
                        "error": tool_result.get("error"),
                        "preview": serialized[:500],
                    }
                )
            continue

        candidate = extract_plan(result.content)
        if candidate is not None:
            candidate = normalise_plan(candidate, task.get("people_number"))
        if candidate is None:
            messages.append(
                {
                    "role": "user",
                    "content": "Return only the final JSON plan, or call a tool if you still need facts.",
                }
            )
            continue

        schema_errors = validate_plan(candidate)
        if schema_errors:
            if repairs < max_repairs:
                repairs += 1
                messages.append(
                    {"role": "user", "content": _feedback(schema_errors, "The plan failed schema validation:")}
                )
                continue
            plan = candidate
            terminal = "schema_invalid"
            break

        plan = candidate
        if method == "b1":
            terminal = "answer"
            break

        if seed_plan is not None and preserve_ids:
            violated = preserve_violations(seed_plan, candidate, preserve_ids)
            if violated:
                if repairs < max_repairs:
                    repairs += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": "You changed preserved activities %r. Restore them exactly and return the full plan."
                            % violated,
                        }
                    )
                    continue
                terminal = "preserve_violated"
                break

        if seed_plan is not None and allowed_ids:
            changed = changed_ids(seed_plan, candidate)
            seed_ids = set(activity_map(seed_plan))
            outside = [key for key in changed if key in seed_ids and key not in allowed_ids]
            metadata["changed_ids"] = changed
            metadata["outside_allowed"] = outside
            if outside:
                if repairs < max_repairs:
                    repairs += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": "You modified activities outside the allowed set %r. Restore them and return the full plan."
                            % outside,
                        }
                    )
                    continue
                terminal = "outside_allowed"
                break

        checker_result = checker(task, candidate)
        if checker_result.violations and repairs < max_repairs:
            repairs += 1
            messages.append(
                {
                    "role": "user",
                    "content": _feedback(checker_result.violations, "Runtime checks found violations:"),
                }
            )
            continue
        terminal = "checked_ok" if checker_result.ok else "checked_with_violations"
        break

    if deadline is not None and time.monotonic() >= deadline:
        terminal = "timeout"
    metadata["request_count"] = sum(step.get("attempts", 1) for step in steps)
    metadata["retry_count"] = sum(max(0, step.get("attempts", 1) - 1) for step in steps)
    metadata["request_latency_seconds"] = sum(sum(step.get("request_latencies") or []) for step in steps)
    trajectory = Trajectory(
        task_uid=str(task.get("uid")),
        method=method,
        model=config.model,
        terminal_state=terminal,
        plan=plan,
        steps=steps,
        usage=usage,
        checker=checker_result.as_dict() if checker_result else None,
        metadata=metadata,
        started_at=started,
        finished_at=_now(),
        duration_seconds=time.perf_counter() - clock,
    )
    return trajectory


def run_single_shot(
    task: Dict[str, Any],
    config: ModelConfig,
    client: Any = None,
    step_fn: Optional[Callable[[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], ChatStep]] = None,
    max_seconds: Optional[float] = None,
) -> Trajectory:
    """B0: one model call, no tools, one format repair at most."""
    schema_text = json.dumps(load_plan_schema(), ensure_ascii=False)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{schema}", schema_text)},
        {"role": "user", "content": task_text(task)},
    ]
    usage: Dict[str, int] = {}
    steps: List[Dict[str, Any]] = []
    started = _now()
    clock = time.perf_counter()
    plan = None
    terminal = "schema_invalid"

    for step_index in range(1, 3):
        if max_seconds is not None and time.perf_counter() - clock > max_seconds:
            terminal = "timeout"
            break
        try:
            result = step_fn(messages, None) if step_fn else chat_step(config, messages, tools=None, client=client)
        except ModelRequestError as exc:
            terminal = "request_error"
            break
        _sum_usage(usage, result)
        steps.append(
            {
                "step": step_index,
                "content": (result.content or "")[:2000],
                "tool_calls": [],
                "tool_results": [],
                "latency_seconds": result.latency_seconds,
                "finish_reason": result.finish_reason,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "reasoning_tokens": result.reasoning_tokens,
            }
        )
        candidate = extract_plan(result.content)
        if candidate is not None:
            candidate = normalise_plan(candidate, task.get("people_number"))
        if candidate is not None and not validate_plan(candidate):
            plan = candidate
            terminal = "answer"
            break
        messages.append({"role": "assistant", "content": result.content or ""})
        messages.append(
            {
                "role": "user",
                "content": "Return only one JSON object that matches the schema; no prose.",
            }
        )

    return Trajectory(
        task_uid=str(task.get("uid")),
        method="b0",
        model=config.model,
        terminal_state=terminal,
        plan=plan,
        steps=steps,
        usage=usage,
        started_at=started,
        finished_at=_now(),
        duration_seconds=time.perf_counter() - clock,
    )
