import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.plan.checker import check_plan, extract_budget, _tool_city  # noqa: E402
from trippilot.plan.schema import validate_plan  # noqa: E402
from trippilot.tools.agent_tools import AGENT_TOOL_ALLOWLIST, BLOCKED_TOOLS, ToolBox  # noqa: E402


TASK = {
    "uid": "t1",
    "start_city": "Shanghai",
    "target_city": "Hangzhou",
    "days": 1,
    "people_number": 1,
    "nature_language_en": "Current location: Shanghai. One day in Hangzhou with a budget of 1500 RMB.",
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


def valid_plan():
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
                        "12:30",
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


class FakeSandbox:
    """Minimal tool double for entity/opening/transport checks."""

    def __init__(self, open_time="00:00", close_time="23:59", goto_row=None, intercity_data=None):
        self.open_time = open_time
        self.close_time = close_time
        self.goto_row = goto_row
        self.intercity_data = intercity_data or []

    def call(self, name, arguments):
        if name in ("attractions_select", "restaurants_select"):
            return {
                "success": True,
                "data": {
                    "rows": [
                        {
                            "name": arguments.get("value"),
                            "opentime": self.open_time,
                            "endtime": self.close_time,
                        }
                    ]
                },
            }
        if name == "intercity_transport_select":
            return {"success": True, "data": self.intercity_data}
        if name == "goto":
            if self.goto_row is not None:
                return {"success": True, "data": [self.goto_row]}
            return {
                "success": True,
                "data": [
                    {
                        "start": arguments.get("start"),
                        "end": arguments.get("end"),
                        "mode": arguments.get("transport_type"),
                        "start_time": arguments.get("start_time"),
                        "end_time": arguments.get("start_time"),
                        "price": 0.0,
                        "cost": 0.0,
                        "distance": 0.0,
                    }
                ],
            }
        return {"success": False, "error": "unknown tool %s" % name}


class ToolPolicyTest(unittest.TestCase):
    def test_blocked_tools_are_not_exposed(self):
        toolbox = ToolBox(lang="en")
        names = {spec["function"]["name"] for spec in toolbox.openai_tools()}
        self.assertTrue(names <= AGENT_TOOL_ALLOWLIST)
        self.assertFalse(names & BLOCKED_TOOLS)

    def test_blocked_tool_call_is_rejected(self):
        result = ToolBox(lang="en").call("china_travel_load_query", {"split": "easy"})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "ToolNotAllowed")


class CheckerTest(unittest.TestCase):
    def test_valid_plan_passes(self):
        result = check_plan(TASK, valid_plan())
        self.assertTrue(result.ok, result.as_dict())

    def test_budget_exceeded(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][1]["cost"] = 5000.0
        result = check_plan(TASK, plan)
        self.assertIn("budget_exceeded", {v.code for v in result.violations})

    def test_overlap_detected(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][2]["start_time"] = "11:30"
        result = check_plan(TASK, plan)
        self.assertIn("activity_overlap", {v.code for v in result.violations})

    def test_accommodation_required_for_multi_day(self):
        task = dict(TASK)
        task["days"] = 2
        plan = valid_plan()
        plan["itinerary"].append(json.loads(json.dumps(plan["itinerary"][0])))
        plan["itinerary"][1]["day"] = 2
        result = check_plan(task, plan)
        self.assertIn("accommodation_required", {v.code for v in result.violations})

    def test_single_day_plan_may_omit_accommodation(self):
        result = check_plan(TASK, valid_plan())
        self.assertNotIn("accommodation_required", {v.code for v in result.violations})

    def test_schema_rejects_missing_people(self):
        plan = valid_plan()
        del plan["people_number"]
        self.assertTrue(validate_plan(plan))

    def test_transport_tickets_required(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][1]["transports"] = [
            {
                "start": "Shanghai Museum",
                "end": "West Lake",
                "mode": "metro",
                "start_time": "09:10",
                "end_time": "09:40",
                "price": 4.0,
                "cost": 4.0,
                "distance": 1000.0,
            }
        ]
        result = check_plan(TASK, plan)
        self.assertIn("transport_tickets", {v.code for v in result.violations})

    def test_transport_must_end_before_activity_start(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][2]["transports"] = [
            {
                "start": "West Lake Scenic Area",
                "end": "Green Tea Restaurant (Longjing Road Branch)",
                "mode": "walk",
                "start_time": "12:30",
                "end_time": "12:35",
                "price": 0.0,
                "cost": 0.0,
                "distance": 0.52,
            }
        ]
        result = check_plan(TASK, plan)
        self.assertIn("arrival_after_activity_start", {v.code for v in result.violations})

    def test_entity_closed_time_with_toolbox(self):
        plan = valid_plan()
        result = check_plan(TASK, plan, toolbox=FakeSandbox(open_time="13:00", close_time="17:00"))
        self.assertIn("visits_closed_time", {v.code for v in result.violations})

    def test_duplicate_activity(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"].append(
            activity("attraction", "13:00", "14:00", 0.0, position="Shanghai Museum")
        )
        result = check_plan(TASK, plan)
        self.assertIn("duplicate_activity", {v.code for v in result.violations})

    def test_repeated_restaurant_across_meal_types(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"].append(
            activity("breakfast", "08:00", "08:30", 20.0, position="Lunch Place")
        )
        result = check_plan(TASK, plan)
        self.assertIn("duplicate_restaurant", {v.code for v in result.violations})

    def test_repeated_meal_type(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"].append(
            activity("lunch", "14:30", "15:30", 30.0, position="Another Restaurant")
        )
        result = check_plan(TASK, plan)
        self.assertIn("meal_type_once", {v.code for v in result.violations})

    def test_transport_mismatch_against_goto(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][1]["transports"] = [
            {
                "start": "Shanghai Museum",
                "end": "West Lake",
                "mode": "walk",
                "start_time": "09:50",
                "end_time": "10:00",
                "price": 0.0,
                "cost": 0.0,
                "distance": 1.0,
            }
        ]
        tool_row = {
            "start": "Shanghai Museum",
            "end": "West Lake",
            "mode": "walk",
            "start_time": "09:50",
            "end_time": "10:01",
            "price": 0.0,
            "cost": 0.0,
            "distance": 0.5,
        }
        result = check_plan(TASK, plan, toolbox=FakeSandbox(goto_row=tool_row))
        codes = {v.code for v in result.violations}
        self.assertIn("transport_mismatch", codes)

    def test_city_names_are_mapped_for_tool_calls(self):
        self.assertEqual(_tool_city("上海"), "Shanghai")
        self.assertEqual(_tool_city("Hangzhou"), "Hangzhou")

    def test_budget_parser(self):
        self.assertEqual(str(extract_budget("budget of 1500 RMB")), "1500")
        self.assertEqual(str(extract_budget("预算 1500 元")), "1500")
        self.assertIsNone(extract_budget("no budget here"))

    def test_budget_parser_thousands_separator(self):
        self.assertEqual(str(extract_budget("a budget of 1,400 RMB")), "1400")
        self.assertEqual(str(extract_budget("budget of 2,500 RMB")), "2500")

    def test_taxi_requires_cars(self):
        plan = valid_plan()
        del plan["itinerary"][0]["activities"][1]["transports"][0]["cars"]
        result = check_plan(TASK, plan)
        self.assertIn("taxi_cars", {v.code for v in result.violations})

    def test_meal_time_window(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][2]["start_time"] = "14:30"
        plan["itinerary"][0]["activities"][2]["end_time"] = "15:30"
        result = check_plan(TASK, plan)
        self.assertIn("meal_time_window", {v.code for v in result.violations})

    def test_space_chain_requires_transport(self):
        plan = valid_plan()
        plan["itinerary"][0]["activities"][1]["transports"] = []
        result = check_plan(TASK, plan)
        self.assertIn("transport_missing", {v.code for v in result.violations})

    def test_zero_duration_transport_is_allowed(self):
        plan = valid_plan()
        leg = plan["itinerary"][0]["activities"][1]["transports"][0]
        leg["end_time"] = leg["start_time"]
        result = check_plan(TASK, plan)
        self.assertNotIn("transport_time_order", {v.code for v in result.violations})

    def test_chinese_task_cities_match_english_plan(self):
        task = dict(TASK)
        task["start_city"] = "上海"
        task["target_city"] = "杭州"
        task["nature_language"] = "当前位置上海，去杭州一天，预算1500元。"
        del task["nature_language_en"]
        result = check_plan(task, valid_plan())
        codes = {v.code for v in result.violations}
        self.assertNotIn("start_city", codes)
        self.assertNotIn("target_city", codes)

    def test_overnight_intercity_is_allowed(self):
        plan = valid_plan()
        return_train = plan["itinerary"][0]["activities"][-1]
        return_train["start_time"] = "21:35"
        return_train["end_time"] = "00:00"
        result = check_plan(TASK, plan)
        self.assertNotIn("time_order", {v.code for v in result.violations})

    def test_intercity_id_must_exist(self):
        tool_row = {
            "TrainID": "G9999",
            "From": "Hangzhou",
            "To": "Shanghai",
            "BeginTime": "20:00",
            "EndTime": "21:00",
            "Cost": 73.0,
        }
        result = check_plan(TASK, valid_plan(), toolbox=FakeSandbox(intercity_data=[tool_row]))
        self.assertIn("intercity_not_found", {v.code for v in result.violations})


if __name__ == "__main__":
    unittest.main()
