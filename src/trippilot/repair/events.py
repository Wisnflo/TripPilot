"""Change events for dynamic replanning."""

import copy
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

KINDS = ("return_earlier", "transport_unavailable", "budget_cut")


@dataclass(frozen=True)
class ChangeEvent:
    kind: str
    day: Optional[int] = None
    new_days: Optional[int] = None
    new_budget: Optional[Decimal] = None
    target: Optional[str] = None
    description: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("unknown event kind: %s" % self.kind)


def event_text(event: ChangeEvent) -> str:
    if event.kind == "return_earlier":
        return "The user must return earlier: the trip is now %s days instead of the original length." % (
            event.new_days if event.new_days is not None else event.day
        )
    if event.kind == "transport_unavailable":
        return "This transport is no longer available: %s. Replace it and keep the rest feasible." % (
            event.target or "the selected intercity transport"
        )
    if event.kind == "budget_cut":
        return "The user lowered the total budget to %s. Reduce cost while keeping all hard conditions satisfied." % (
            event.new_budget
        )
    return event.description


def apply_event_to_task(task: Dict[str, Any], event: ChangeEvent) -> Dict[str, Any]:
    """Return a task copy that reflects the event conditions for prompt and checker."""
    updated = copy.deepcopy(task)
    if event.kind == "return_earlier" and event.new_days is not None:
        updated["days"] = event.new_days
    if event.kind == "budget_cut" and event.new_budget is not None:
        budget = str(event.new_budget)
        updated["nature_language_en"] = re.sub(
            r"budget(?:\s+of)?\s*[\d,]+", "budget of %s" % budget, updated.get("nature_language_en", "")
        )
        updated["nature_language"] = re.sub(
            r"预算\s*[\d,]+", "预算%s" % budget, updated.get("nature_language", "")
        )
    return updated
