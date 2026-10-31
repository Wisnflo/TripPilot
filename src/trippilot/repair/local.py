"""Dependency-driven local repair."""

import copy
import time

from typing import Any, Dict, Optional

from ..agent.loop import Trajectory, run_react
from ..models.config import ModelConfig
from ..plan.checker import check_plan
from ..tools.agent_tools import ToolBox
from .dependencies import affected_activity_ids, preserve_activity_ids
from .events import ChangeEvent, event_text


def repair_local(
    task: Dict[str, Any],
    seed_plan: Dict[str, Any],
    event: ChangeEvent,
    config: ModelConfig,
    toolbox: ToolBox,
    max_steps: int = 8,
    max_repairs: int = 2,
    client: Any = None,
    max_seconds: Optional[float] = None,
) -> Trajectory:
    affected = affected_activity_ids(seed_plan, event)
    preserve = preserve_activity_ids(seed_plan, event)
    trajectory = run_react(
        task,
        config,
        toolbox,
        method="ours",
        checker=lambda task_dict, plan: check_plan(task_dict, plan, toolbox=toolbox),
        max_steps=max_steps,
        max_repairs=max_repairs,
        seed_plan=seed_plan,
        event_text=event_text(event),
        allowed_ids=affected,
        preserve_ids=preserve,
        client=client,
        max_seconds=max_seconds,
    )
    trajectory.metadata.update(
        {
            "affected_ids": affected,
            "preserve_ids": preserve,
            "event_kind": event.kind,
            "fallback": False,
        }
    )
    return trajectory


def repair_with_fallback(
    task: Dict[str, Any],
    seed_plan: Dict[str, Any],
    event: ChangeEvent,
    config: ModelConfig,
    toolbox: ToolBox,
    max_steps: int = 8,
    max_repairs: int = 2,
    client: Any = None,
    max_seconds: Optional[float] = None,
) -> Trajectory:
    clock = time.monotonic()
    local = repair_local(task, seed_plan, event, config, toolbox, max_steps, max_repairs, client, max_seconds)
    if local.terminal_state in ("checked_ok", "answer"):
        return local

    remaining_steps = max_steps - len(local.steps)
    remaining_seconds = None if max_seconds is None else max_seconds - (time.monotonic() - clock)
    if (local.terminal_state in ("timeout", "request_error") or remaining_steps <= 0 or
            (remaining_seconds is not None and remaining_seconds <= 0)):
        return local
    fallback = run_react(
        task,
        config,
        toolbox,
        method="ours_fallback",
        checker=lambda task_dict, plan: check_plan(task_dict, plan, toolbox=toolbox),
        max_steps=remaining_steps,
        max_repairs=max_repairs,
        event_text=event_text(event),
        seed_plan=seed_plan,
        client=client,
        max_seconds=remaining_seconds,
    )
    fallback.metadata.update(
        {
            "event_kind": event.kind,
            "fallback": True,
            "local_terminal_state": local.terminal_state,
            "local_error": local.error,
        }
    )
    fallback.metadata["phases"] = [copy.deepcopy(local.as_dict()), copy.deepcopy(fallback.as_dict())]
    fallback.steps = [dict(step, phase="local") for step in local.steps] + [dict(step, phase="fallback") for step in fallback.steps]
    fallback.usage = {key: (local.usage.get(key, 0) or 0) + (fallback.usage.get(key, 0) or 0)
                      for key in set(local.usage) | set(fallback.usage)}
    fallback.started_at = local.started_at
    fallback.duration_seconds = time.monotonic() - clock
    fallback.metadata["request_count"] = sum(step.get("attempts", 1) for step in fallback.steps)
    fallback.metadata["retry_count"] = sum(max(0, step.get("attempts", 1) - 1) for step in fallback.steps)
    fallback.metadata["request_latency_seconds"] = sum(sum(step.get("request_latencies") or []) for step in fallback.steps)
    fallback.metadata["usage_complete"] = local.metadata.get("usage_complete", True) and fallback.metadata.get("usage_complete", True)
    return fallback
