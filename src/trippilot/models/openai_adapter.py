"""Minimal parameterized OpenAI-compatible chat adapter."""

import time
import uuid
from ..execution.ledger import emit
from ..execution.budget import current
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from .config import ModelConfig


class ModelRequestError(RuntimeError):
    def __init__(self, message: str, attempts: int, request_latencies: List[float], last_error: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.request_latencies = request_latencies
        self.last_error = last_error


@dataclass
class CompletionResult:
    text: str
    model: str
    latency_seconds: float
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    finish_reason: Optional[str]
    request_id: Optional[str]
    attempts: int = 1
    request_latencies: List[float] = field(default_factory=list)


@dataclass
class ChatStep:
    content: str
    tool_calls: List[Any]
    model: str
    latency_seconds: float
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    finish_reason: Optional[str]
    request_id: Optional[str]
    attempts: int = 1
    request_latencies: List[float] = field(default_factory=list)


def build_client(config: ModelConfig) -> OpenAI:
    kwargs: Dict[str, Any] = {
        "api_key": config.api_key,
        "timeout": config.request_timeout_seconds,
        "max_retries": 0,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return OpenAI(**kwargs)


RETRYABLE = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, APIStatusError)


def _call_with_retries(config: ModelConfig, client: OpenAI, kwargs: Dict[str, Any], deadline=None):
    budget = current()
    if budget is not None:
        kwargs = budget.prepare_request(dict(kwargs))
    latencies: List[float] = []
    last_error: Optional[Exception] = None
    for attempt in range(1, config.max_attempts + 1):
        if deadline is not None and time.monotonic() >= deadline:
            last_error = TimeoutError("task deadline exhausted")
            break
        request_kwargs = dict(kwargs)
        request_kwargs["timeout"] = min(config.request_timeout_seconds,
            max(0.001, deadline - time.monotonic())) if deadline is not None else config.request_timeout_seconds
        attempt_id = uuid.uuid4().hex
        emit("start", attempt_id=attempt_id, model=config.model, attempt=attempt)
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**request_kwargs)
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            emit("success", attempt_id=attempt_id, model=config.model, elapsed=elapsed,
                 response_model=getattr(response, "model", None), request_id=getattr(response, "id", None),
                 **_usage_fields(getattr(response, "usage", None)))
            if budget is not None:
                budget.consume(_usage_fields(getattr(response, "usage", None)))
            return response, latencies
        except RETRYABLE as exc:
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)
            emit("error", attempt_id=attempt_id, model=config.model, elapsed=elapsed,
                 error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None))
            last_error = exc
            status = getattr(exc, "status_code", None)
            if status is not None and status not in (408, 409, 429) and status < 500:
                break  # authentication/model-blocked/bad requests are not transient
            if attempt >= config.max_attempts:
                break
            backoff = config.retry_backoff_seconds * (2 ** (attempt - 1))
            if deadline is not None and time.monotonic() + backoff >= deadline:
                break
            time.sleep(backoff)
    raise ModelRequestError(
        "model request failed after %d attempts: %s" % (len(latencies), last_error),
        attempts=len(latencies),
        request_latencies=latencies,
        last_error=last_error,
    )


def _usage_fields(usage: Any) -> Dict[str, Optional[int]]:
    if not usage:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "reasoning_tokens": None}
    details = getattr(usage, "completion_tokens_details", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    return {
        "cached_prompt_tokens": getattr(prompt_details, "cached_tokens", None) if prompt_details else None,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
    }


def chat_completion(
    config: ModelConfig,
    messages: List[Dict[str, str]],
    client: Optional[OpenAI] = None,
) -> CompletionResult:
    """One chat completion with explicit, recorded adapter retries."""
    client = client or build_client(config)
    response, latencies = _call_with_retries(
        config, client, {"model": config.model, "messages": messages}
    )
    latency = sum(latencies)

    choice = response.choices[0]
    usage = _usage_fields(getattr(response, "usage", None))
    return CompletionResult(
        text=(choice.message.content or ""),
        model=getattr(response, "model", config.model),
        latency_seconds=latency,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        finish_reason=getattr(choice, "finish_reason", None),
        request_id=getattr(response, "id", None),
        attempts=len(latencies),
        request_latencies=latencies,
    )


def chat_step(
    config: ModelConfig,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    client: Optional[OpenAI] = None,
    deadline: Optional[float] = None,
) -> ChatStep:
    """One model step with explicit adapter retries bounded by the shared deadline."""
    client = client or build_client(config)
    kwargs: Dict[str, Any] = {"model": config.model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    response, latencies = _call_with_retries(config, client, kwargs, deadline=deadline)
    latency = sum(latencies)

    choice = response.choices[0]
    usage = _usage_fields(getattr(response, "usage", None))
    message = choice.message
    return ChatStep(
        content=(message.content or ""),
        tool_calls=list(getattr(message, "tool_calls", None) or []),
        model=getattr(response, "model", config.model),
        latency_seconds=latency,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        finish_reason=getattr(choice, "finish_reason", None),
        request_id=getattr(response, "id", None),
        attempts=len(latencies),
        request_latencies=latencies,
    )
