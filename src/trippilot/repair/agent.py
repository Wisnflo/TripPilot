"""Tool-grounded incremental repair: propose, select, validate, atomic commit.

Rules is an explicit no-LLM ablation. Patch commits a verified deterministic
proposal first; an optional selector ablation asks a compact semantic choice.
When bounded proposals cannot solve the event, Patch escalates to a shared-budget
ReAct fallback. It never reads scoring labels or historical revised plans.
"""
import json
import time
from datetime import datetime, timezone

from ..agent.loop import Trajectory, extract_plan, run_react
from ..models.openai_adapter import chat_step, ModelRequestError
from .events import apply_event_to_task, event_text
from .proposals import build_proposals
from .transaction import apply_edits, revision
from .verification import validate_revision


def repair_incremental(task, seed, event, config, toolbox, policy="patch", max_steps=8,
                       max_seconds=300, step_fn=None):
    clock = time.monotonic()
    deadline = clock + max_seconds
    started = datetime.now(timezone.utc).isoformat()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    steps = []
    meta = {"policy": policy, "fallback": False, "usage_complete": True,
            "source_revision": revision(seed), "budget_steps": max_steps,
            "budget_seconds": max_seconds, "tool_trace": []}
    plan, terminal, checker = None, "no_candidate", None
    try:
        candidates, trace, rejected = build_proposals(task, seed, event, toolbox, deadline=deadline)
        meta.update(tool_trace=trace, candidates=len(candidates), rejected_proposals=rejected)
        chosen = candidates[0] if candidates else None
        if chosen and policy == "selector":
            # Send diffs, never full copies of all plans. All candidate facts are
            # sourced from allowlisted tool responses above and verified equally.
            catalog = [{"id": c["id"], "edits": c["edits"], "keep_days": c["keep_days"],
                        "preservation": c["preservation"]} for c in candidates]
            messages = [{"role": "system", "content": (
                'Select one verified repair candidate for the user request. Preserve preferences, '
                'minimize unnecessary changes and cost. Return ONLY {"candidate_id":"cN"}. '
                'Do not output a full itinerary. Candidates are mechanically feasible but semantic '
                'preferences need your judgment. The new event supersedes original days/budget.')},
                {"role": "user", "content": json.dumps({
                    "request": task.get("nature_language_en") or task.get("nature_language"),
                    "event": event_text(event), "seed_summary": [
                        {"day": d["day"], "activities": [
                            {k: v for k, v in act.items() if k in ("type", "position", "start", "end", "start_time", "end_time", "TrainID", "FlightID")}
                            for act in d.get("activities", [])]} for d in seed["itinerary"]], "candidates": catalog}, ensure_ascii=False)}]
            result = step_fn(messages, None) if step_fn else chat_step(config, messages, deadline=deadline)
            for key in usage:
                value = getattr(result, key, None)
                if value is None:
                    meta["usage_complete"] = False
                else:
                    usage[key] += value
            steps.append({"step": 1, "phase": "select_patch", "content": result.content,
                          "attempts": result.attempts, "latency_seconds": result.latency_seconds,
                          "request_latencies": result.request_latencies,
                          "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens})
            selection = extract_plan(result.content) or {}
            chosen = next((c for c in candidates if c["id"] == selection.get("candidate_id")), None)
            if chosen is None:
                terminal = "invalid_selection"
        if chosen:
            candidate = apply_edits(seed, chosen["revision"], chosen["edits"], chosen["scope"], chosen["keep_days"])
            checked = validate_revision(task, seed, candidate, event, toolbox)
            if checked.ok and time.monotonic() < deadline:
                plan, terminal, checker = candidate, "checked_ok", checked.as_dict()
                meta.update(selected_candidate=chosen["id"], edits=chosen["edits"],
                            edit_scope=chosen["scope"], preservation=chosen["preservation"],
                            commit_revision=revision(candidate), atomic_commit=True)
            else:
                terminal = "timeout" if time.monotonic() >= deadline else "verification_failed"
        # Both policies expose the same no-candidate rate. Only Patch escalates;
        # cost and elapsed time of the first phase are retained and budget shared.
        if plan is None and policy in ("patch", "selector") and len(steps) < max_steps and time.monotonic() < deadline:
            meta["fallback"] = True
            out = run_react(apply_event_to_task(task, event), config, toolbox, method="b2",
                            seed_plan=seed, event_text=event_text(event),
                            checker=lambda _task, p: validate_revision(task, seed, p, event, toolbox),
                            max_steps=max_steps - len(steps), max_seconds=deadline - time.monotonic())
            meta["fallback_metadata"] = out.metadata
            meta["usage_complete"] = meta["usage_complete"] and out.metadata.get("usage_complete", True)
            for key in usage:
                usage[key] += out.usage.get(key, 0) or 0
            steps.extend(dict(s, phase="full_fallback") for s in out.steps)
            plan, terminal, checker = out.plan, out.terminal_state, out.checker
    except TimeoutError:
        terminal = "timeout"
    except ModelRequestError as exc:
        terminal = "request_error"
        meta.update(usage_complete=False, request_error=str(exc))
        steps.append({"phase": "select_patch", "attempts": exc.attempts,
                      "request_latencies": exc.request_latencies, "error": type(exc.last_error).__name__})
    if time.monotonic() >= deadline:
        terminal = "timeout"
    meta["request_count"] = sum(s.get("attempts", 1) for s in steps)
    meta["retry_count"] = sum(max(0, s.get("attempts", 1) - 1) for s in steps)
    meta["request_latency_seconds"] = sum(sum(s.get("request_latencies", [])) for s in steps)
    return Trajectory(str(task.get("uid")), policy, config.model if config else "none",
                      terminal, plan, steps=steps, usage=usage, metadata=meta, checker=checker,
                      started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
                      duration_seconds=time.monotonic() - clock)
