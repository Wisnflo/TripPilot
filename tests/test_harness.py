import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from openai import APIConnectionError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.agent.loop import run_react  # noqa: E402
from trippilot.models.config import ModelConfig  # noqa: E402
from trippilot.models.openai_adapter import ModelRequestError, _call_with_retries  # noqa: E402


class FakeCompletions:
    def __init__(self, failures, response):
        self.failures = failures
        self.response = response
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise APIConnectionError(request=None)
        return self.response


def client_with(failures):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=[]), finish_reason="stop")],
        usage=None,
        model="fake",
        id="rid",
    )
    completions = FakeCompletions(failures, response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


class RetryTest(unittest.TestCase):
    def config(self, attempts=3):
        return ModelConfig(
            model="fake",
            api_key="fake",
            max_attempts=attempts,
            retry_backoff_seconds=0.0,
            request_timeout_seconds=1.0,
        )

    def test_retry_then_success(self):
        client, completions = client_with(failures=2)
        response, latencies = _call_with_retries(self.config(3), client, {"model": "fake", "messages": []})
        self.assertEqual(completions.calls, 3)
        self.assertEqual(len(latencies), 3)
        self.assertTrue(response)

    def test_exhausted_retries_raise(self):
        client, completions = client_with(failures=99)
        with self.assertRaises(ModelRequestError) as ctx:
            _call_with_retries(self.config(2), client, {"model": "fake", "messages": []})
        self.assertEqual(completions.calls, 2)
        self.assertEqual(ctx.exception.attempts, 2)

    def test_task_deadline_stops_before_model_call(self):
        class ToolBox:
            def openai_tools(self):
                return []

        def never_called(messages, tools):
            raise AssertionError("model should not be called after the deadline")

        config = ModelConfig(model="fake", api_key="fake")
        trajectory = run_react(
            {"uid": "t", "people_number": 1, "days": 1},
            config,
            ToolBox(),
            method="b1",
            max_steps=3,
            step_fn=never_called,
            max_seconds=0.0,
        )
        self.assertEqual(trajectory.terminal_state, "timeout")
        self.assertEqual(trajectory.steps, [])


if __name__ == "__main__":
    unittest.main()
