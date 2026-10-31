"""Shared per-task model, returned-token and public-query limits."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

_CURRENT = ContextVar("trippilot_budget", default=None)


class BudgetExceeded(TimeoutError):
    pass


@dataclass
class ResourceBudget:
    model_limit: int = 16
    token_limit: int = 500000
    tool_limit: int = 400
    output_limit: int = 10000
    feedback_limit: int = 4
    feedback_calls: int = 0
    model_calls: int = 0
    returned_tokens: int = 0
    tool_calls: int = 0
    exhausted: str = ""
    tool_receipts: list = field(default_factory=list)

    def persist(self):
        path=os.environ.get('TRIPPILOT_RESOURCE_JOURNAL')
        if path:
            with Path(path).open('a') as handle:
                handle.write(json.dumps({'feedback_calls':self.feedback_calls,'model_calls':self.model_calls,'returned_tokens':self.returned_tokens,'tool_calls':self.tool_calls,'exhausted':self.exhausted})+'\n')

    def fail(self, name):
        self.exhausted = name
        self.persist()
        raise BudgetExceeded(name + " budget exhausted")

    def prepare_request(self, kwargs):
        if self.model_calls >= self.model_limit:
            self.fail("model_calls")
        # UTF-8 bytes plus message framing is a deliberately conservative input
        # preflight. Actual returned usage is the authoritative consumption.
        prompt = json.dumps({k: v for k, v in kwargs.items() if k in ("messages", "tools")}, ensure_ascii=False)
        reserve = len(prompt.encode()) + 64 * len(kwargs.get("messages", [])) + 1024
        remaining = self.token_limit - self.returned_tokens - reserve
        if remaining <= 0:
            self.fail("tokens")
        self.model_calls += 1
        self.persist()
        kwargs["max_tokens"] = min(self.output_limit, remaining)
        return kwargs

    def consume(self, usage):
        value = usage.get("total_tokens")
        if value is not None:
            self.returned_tokens += value
        self.persist()
        if self.returned_tokens > self.token_limit:
            self.fail("tokens")

    def tool(self, name, arguments):
        if self.tool_calls >= self.tool_limit:
            self.fail("tools")
        self.tool_calls += 1
        self.persist()
        self.tool_receipts.append({"index": self.tool_calls, "name": name, "arguments": arguments})

    def feedback(self):
        if self.feedback_calls >= self.feedback_limit:self.fail("feedback")
        self.feedback_calls += 1
        self.persist()

    def as_dict(self):
        return {"limits": {"feedback":self.feedback_limit,"model_calls": self.model_limit, "tokens": self.token_limit,
                           "tools": self.tool_limit, "output_tokens_per_call": self.output_limit},
                "model_calls": self.model_calls, "returned_tokens": self.returned_tokens,
                "feedback_calls":self.feedback_calls,"tool_calls": self.tool_calls, "exhausted": self.exhausted,
                "tool_receipts": self.tool_receipts,
                "input_accounting": "conservative UTF-8 preflight; actual provider usage recorded; missing remote usage remains unknown"}


def activate(budget):
    return _CURRENT.set(budget)


def current():
    return _CURRENT.get()


def count_tool(name, arguments):
    budget = current()
    if budget is not None:
        budget.tool(name, arguments)


def deactivate(token):
    _CURRENT.reset(token)


def read_resources(path):
    result={'model_calls':0,'returned_tokens':0,'tool_calls':0,'exhausted':''}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:result=json.loads(line)
            except json.JSONDecodeError:continue
    return result


def count_feedback():
    budget=current()
    if budget is not None:budget.feedback()
