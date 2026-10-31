#!/usr/bin/env python3
"""One-call smoke test for the parameterized model adapter."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.models.config import ConfigError, resolve_model_config  # noqa: E402
from trippilot.models.openai_adapter import chat_completion  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="Override CHINATRAVEL_OPENAI_MODEL")
    parser.add_argument("--prompt", default="Reply with exactly: OK")
    parser.add_argument("--price-input-per-1m", type=float, default=None)
    parser.add_argument("--price-output-per-1m", type=float, default=None)
    args = parser.parse_args()

    try:
        config = resolve_model_config(model_override=args.model)
    except ConfigError as exc:
        print("CONFIG_ERROR:", exc)
        print("Hint: export the variables, or copy .env.example to .env (ignored) and fill it.")
        return 2

    print("model=%s" % config.model)
    print("base_url_host=%s" % config.base_url_host)
    print("api_key=set (value not printed)")
    print("prompt_chars=%d" % len(args.prompt))

    try:
        result = chat_completion(
            config,
            [{"role": "user", "content": args.prompt}],
        )
    except Exception as exc:  # keep the smoke test honest about failures
        message = str(exc)
        if config.api_key:
            message = message.replace(config.api_key, "***")
        print("REQUEST_ERROR: %s: %s" % (type(exc).__name__, message))
        return 1

    print("text=%s" % result.text.strip()[:200].replace("\n", "\\n"))
    print("latency_seconds=%.3f" % result.latency_seconds)
    print("prompt_tokens=%s" % result.prompt_tokens)
    print("completion_tokens=%s" % result.completion_tokens)
    print("total_tokens=%s" % result.total_tokens)
    print("finish_reason=%s" % result.finish_reason)
    print("request_id=%s" % result.request_id)

    if (
        args.price_input_per_1m is not None
        and args.price_output_per_1m is not None
        and result.prompt_tokens is not None
        and result.completion_tokens is not None
    ):
        cost = (
            result.prompt_tokens * args.price_input_per_1m
            + result.completion_tokens * args.price_output_per_1m
        ) / 1_000_000
        print("cost=%.8f (from user-supplied per-1M prices)" % cost)
    else:
        print("cost=not_computed (no price table; pass --price-input-per-1m/--price-output-per-1m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
