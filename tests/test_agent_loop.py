import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.agent.loop import run_react, run_single_shot  # noqa: E402
from trippilot.models.config import ModelConfig  # noqa: E402
from trippilot.models.openai_adapter import ChatStep  # noqa: E402


CONFIG = ModelConfig(model="fake", api_key="fake")
TASK = {
    "uid": "t1",
    "start_city": "Shanghai",
    "target_city": "Hangzhou",
    "days": 1,
    "people_number": 1,
    "nature_language_en": "One day in Hangzhou from Shanghai, budget 1500.",
}


def activity(kind, start_time, end_time, cost=0.0, position="Shanghai Museum", **extra):
    payload = {
        "type": kind,
        "start_time": start_time,
        "end_time": end_time,
        "cost": cost,
        "price": cost,
        "tickets": 1,
        "transports": [],
    }
    if kind not in ("train", "airplane"):
        payload["position"] = position
    payload.update(extra)
    return payload


def taxi_leg(start, end, start_time, end_time, cost=0.0):
    return {
        "start": start,
        "end": end,
        "mode": "taxi",
        "start_time": start_time,
        "end_time": end_time,
        "cost": cost,
        "price": cost,
        "distance": 0.0,
        "cars": 1,
    }


def plan(overlap=False):
    lunch_start = "11:30" if overlap else "12:30"
    return {
        "people_number": 1,
        "start_city": "Shanghai",
        "target_city": "Hangzhou",
        "itinerary": [
            {
                "day": 1,
                "activities": [
                    activity(
                        "train",
                        "08:00",
                        "09:00",
                        73.0,
                        start="Shanghai",
                        end="Hangzhou",
                        TrainID="G1234",
                    ),
                    activity(
                        "attraction",
                        "10:00",
                        "12:00",
                        0.0,
                        transports=[taxi_leg("Hangzhou", "Shanghai Museum", "09:05", "09:30")],
                    ),
                    activity(
                        "lunch",
                        lunch_start,
                        "13:30",
                        50.0,
                        position="Lunch Place",
                        transports=[taxi_leg("Shanghai Museum", "Lunch Place", "12:10", "12:25")],
                    ),
                    activity(
                        "dinner",
                        "18:00",
                        "19:00",
                        80.0,
                        position="Dinner Place",
                        transports=[taxi_leg("Lunch Place", "Dinner Place", "17:45", "18:00")],
                    ),
                    activity(
                        "train",
                        "20:00",
                        "21:00",
                        73.0,
                        start="Hangzhou",
                        end="Shanghai",
                        TrainID="G1235",
                        transports=[taxi_leg("Dinner Place", "Hangzhou", "19:05", "19:30")],
                    ),
                ],
            }
        ],
    }


def step(content="", tool_calls=None):
    return ChatStep(
        content=content,
        tool_calls=tool_calls or [],
        model="fake",
        latency_seconds=0.01,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning_tokens=0,
        finish_reason="tool_calls" if tool_calls else "stop",
        request_id="fake",
    )


class FakeToolBox:
    def openai_tools(self):
        return []

    def call(self, name, arguments):
        if name == "intercity_transport_select":
            return {"success": True, "data": []}
        if name == "goto":
            hour, minute = (int(part) for part in arguments.get("start_time", "00:00").split(":"))
            end_minute = (hour * 60 + minute + 25) % (24 * 60)
            return {
                "success": True,
                "data": [
                    {
                        "start": arguments.get("start"),
                        "end": arguments.get("end"),
                        "mode": arguments.get("transport_type"),
                        "start_time": arguments.get("start_time"),
                        "end_time": "%02d:%02d" % (end_minute // 60, end_minute % 60),
                        "cost": 0.0,
                        "distance": 0.0,
                    }
                ],
            }
        return {"success": True, "data": {"rows": [{"name": "Shanghai Museum"}]}}


class LoopTest(unittest.TestCase):
    def test_b1_tool_then_answer(self):
        responses = [
            step(tool_calls=[{"id": "c1", "function": {"name": "attractions_types", "arguments": "{}"}}]),
            step(content=json.dumps(plan())),
        ]
        trajectory = run_react(
            TASK,
            CONFIG,
            FakeToolBox(),
            method="b1",
            max_steps=4,
            step_fn=lambda messages, tools: responses.pop(0),
        )
        self.assertEqual(trajectory.terminal_state, "answer")
        self.assertEqual(len(trajectory.steps), 2)
        self.assertEqual(trajectory.usage["total_tokens"], 30)
        self.assertTrue(trajectory.plan)

    def test_b2_repairs_after_checker_violation(self):
        responses = [step(content=json.dumps(plan(overlap=True))), step(content=json.dumps(plan()))]
        trajectory = run_react(
            TASK,
            CONFIG,
            FakeToolBox(),
            method="b2",
            max_steps=4,
            step_fn=lambda messages, tools: responses.pop(0),
        )
        self.assertEqual(trajectory.terminal_state, "checked_ok")
        self.assertEqual(len(trajectory.steps), 2)
        self.assertTrue(trajectory.checker["ok"])

    def test_max_steps_is_explicit(self):
        responses = [
            step(tool_calls=[{"id": "c%d" % i, "function": {"name": "next_page", "arguments": "{}"}}])
            for i in range(3)
        ]
        trajectory = run_react(
            TASK,
            CONFIG,
            FakeToolBox(),
            method="b1",
            max_steps=3,
            step_fn=lambda messages, tools: responses.pop(0),
        )
        self.assertEqual(trajectory.terminal_state, "max_steps")
        self.assertIsNone(trajectory.plan)

    def test_last_step_forces_no_tools(self):
        calls = []

        def fake_step(messages, tools):
            calls.append(tools)
            if len(calls) == 2:
                return step(content=json.dumps(plan()))
            return step(tool_calls=[{"id": "c1", "function": {"name": "next_page", "arguments": "{}"}}])

        trajectory = run_react(
            TASK,
            CONFIG,
            FakeToolBox(),
            method="b1",
            max_steps=2,
            step_fn=fake_step,
        )
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[0])
        self.assertIsNone(calls[1])
        self.assertEqual(trajectory.terminal_state, "answer")

    def test_b0_single_shot(self):
        responses = [step(content=json.dumps(plan()))]
        trajectory = run_single_shot(
            TASK,
            CONFIG,
            step_fn=lambda messages, tools: responses.pop(0),
        )
        self.assertEqual(trajectory.terminal_state, "answer")
        self.assertEqual(len(trajectory.steps), 1)


if __name__ == "__main__":
    unittest.main()
