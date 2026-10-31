import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.agent.loop import run_react  # noqa: E402
from trippilot.models.config import ModelConfig  # noqa: E402
from trippilot.models.openai_adapter import ChatStep  # noqa: E402
from trippilot.plan.compare import changed_ids, preserve_violations  # noqa: E402
from trippilot.repair.dependencies import (  # noqa: E402
    affected_activity_ids,
    preserve_activity_ids,
)
from trippilot.repair.events import ChangeEvent, apply_event_to_task, event_text  # noqa: E402


TWO_DAY_PLAN = {
    "people_number": 1,
    "start_city": "Shanghai",
    "target_city": "Hangzhou",
    "itinerary": [
        {
            "day": 1,
            "activities": [
                {"type": "train", "TrainID": "G1", "cost": 100.0, "position": ""},
                {"type": "attraction", "position": "A", "cost": 0.0},
            ],
        },
        {
            "day": 2,
            "activities": [
                {"type": "attraction", "position": "B", "cost": 0.0},
                {"type": "train", "TrainID": "G2", "cost": 100.0, "position": ""},
            ],
        },
    ],
}


class DependencyTest(unittest.TestCase):
    def test_return_earlier_affects_removed_day_and_return_transport(self):
        event = ChangeEvent("return_earlier", new_days=1)
        affected = affected_activity_ids(TWO_DAY_PLAN, event)
        self.assertEqual(affected, ["d1:a1", "d2:a1", "d2:a2"])
        self.assertEqual(preserve_activity_ids(TWO_DAY_PLAN, event), ["d1:a2"])

    def test_transport_unavailable_affects_target_and_downstream(self):
        event = ChangeEvent("transport_unavailable", target="G2")
        self.assertEqual(affected_activity_ids(TWO_DAY_PLAN, event), ["d2:a2"])

    def test_budget_cut_affects_cost_activities(self):
        event = ChangeEvent("budget_cut", new_budget=100)
        self.assertEqual(affected_activity_ids(TWO_DAY_PLAN, event), ["d1:a1", "d2:a2"])

    def test_event_text_mentions_new_value(self):
        self.assertIn("2", event_text(ChangeEvent("return_earlier", new_days=2)))
        self.assertIn("1200", event_text(ChangeEvent("budget_cut", new_budget=1200)))

    def test_apply_event_updates_days(self):
        task = {"days": 2, "nature_language_en": "two days", "nature_language": "两天"}
        updated = apply_event_to_task(task, ChangeEvent("return_earlier", new_days=1))
        self.assertEqual(updated["days"], 1)
        self.assertEqual(task["days"], 2)

    def test_apply_event_rewrites_budget_with_thousands(self):
        task = {
            "days": 1,
            "nature_language_en": "a budget of 1,400 RMB",
            "nature_language": "预算1400元",
        }
        updated = apply_event_to_task(task, ChangeEvent("budget_cut", new_budget=900))
        self.assertEqual(updated["nature_language_en"], "a budget of 900 RMB")
        self.assertEqual(updated["nature_language"], "预算900元")
        self.assertIn("1,400", task["nature_language_en"])

    def test_changed_and_preserved_comparison(self):
        new = json.loads(json.dumps(TWO_DAY_PLAN))
        new["itinerary"][0]["activities"][1]["position"] = "A2"
        self.assertEqual(changed_ids(TWO_DAY_PLAN, new), ["d1:a2"])
        self.assertEqual(preserve_violations(TWO_DAY_PLAN, new, ["d1:a2"]), ["d1:a2"])
        self.assertEqual(preserve_violations(TWO_DAY_PLAN, new, ["d1:a1"]), [])


def step(content):
    return ChatStep(
        content=content,
        tool_calls=[],
        model="fake",
        latency_seconds=0.01,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning_tokens=0,
        finish_reason="stop",
        request_id="fake",
    )


class FakeToolBox:
    def openai_tools(self):
        return []

    def call(self, name, arguments):
        return {"success": True}


TASK = {
    "uid": "t1",
    "start_city": "Shanghai",
    "target_city": "Hangzhou",
    "days": 1,
    "people_number": 1,
    "nature_language_en": "One day in Hangzhou from Shanghai, budget 1500.",
}


def one_day_plan():
    return {
        "people_number": 1,
        "start_city": "Shanghai",
        "target_city": "Hangzhou",
        "itinerary": [
            {
                "day": 1,
                "activities": [
                    {
                        "type": "train",
                        "start": "Shanghai",
                        "end": "Hangzhou",
                        "TrainID": "G1",
                        "start_time": "08:00",
                        "end_time": "09:00",
                        "cost": 73.0,
                        "price": 73.0,
                        "tickets": 1,
                        "transports": [],
                    },
                    {
                        "type": "attraction",
                        "position": "West Lake",
                        "start_time": "10:00",
                        "end_time": "11:00",
                        "cost": 0.0,
                        "price": 0.0,
                        "tickets": 1,
                        "transports": [],
                    },
                    {
                        "type": "train",
                        "start": "Hangzhou",
                        "end": "Shanghai",
                        "TrainID": "G2",
                        "start_time": "18:00",
                        "end_time": "19:00",
                        "cost": 73.0,
                        "price": 73.0,
                        "tickets": 1,
                        "transports": [],
                    },
                ],
            }
        ],
    }


class RepairLoopTest(unittest.TestCase):
    def test_preserved_activity_is_enforced(self):
        seed = one_day_plan()
        modified = json.loads(json.dumps(seed))
        modified["itinerary"][0]["activities"][0]["TrainID"] = "G99"
        trajectory = run_react(
            TASK,
            ModelConfig(model="fake", api_key="fake"),
            FakeToolBox(),
            method="b2",
            max_steps=2,
            max_repairs=0,
            seed_plan=seed,
            event_text="transport G1 unavailable",
            allowed_ids=["d1:a1"],
            preserve_ids=["d1:a1"],
            step_fn=lambda messages, tools: step(json.dumps(modified)),
        )
        self.assertEqual(trajectory.terminal_state, "preserve_violated")

    def test_preserved_activity_unchanged_passes(self):
        seed = one_day_plan()
        trajectory = run_react(
            TASK,
            ModelConfig(model="fake", api_key="fake"),
            FakeToolBox(),
            method="b2",
            max_steps=2,
            max_repairs=0,
            seed_plan=seed,
            event_text="budget cut",
            allowed_ids=["d1:a1"],
            preserve_ids=["d1:a2"],
            step_fn=lambda messages, tools: step(json.dumps(seed)),
        )
        self.assertIn(trajectory.terminal_state, ("checked_ok", "checked_with_violations"))
        self.assertEqual(trajectory.metadata.get("changed_ids"), [])


if __name__ == "__main__":
    unittest.main()
