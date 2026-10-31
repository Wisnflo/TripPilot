import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from score_run import translate_plan_for_hard  # noqa: E402


def plan(start_city, target_city, position):
    return {
        "start_city": start_city,
        "target_city": target_city,
        "itinerary": [
            {
                "day": 1,
                "activities": [
                    {"type": "attraction", "position": position},
                    {"type": "lunch", "position": "Unknown Place"},
                ],
            }
        ],
    }


NAME_MAPS = {"Hangzhou": {"West Lake": "西湖"}}


class HardTranslationTest(unittest.TestCase):
    def test_english_city_and_position_are_translated(self):
        result = translate_plan_for_hard(plan("Wuhan", "Hangzhou", "West Lake"), NAME_MAPS)
        self.assertEqual(result["target_city"], "杭州")
        self.assertEqual(result["itinerary"][0]["activities"][0]["position"], "西湖")

    def test_chinese_city_still_uses_english_map_key(self):
        result = translate_plan_for_hard(plan("武汉", "杭州", "West Lake"), NAME_MAPS)
        self.assertEqual(result["target_city"], "杭州")
        self.assertEqual(result["itinerary"][0]["activities"][0]["position"], "西湖")

    def test_unknown_position_is_left_unchanged(self):
        result = translate_plan_for_hard(plan("Wuhan", "Hangzhou", "Nowhere"), NAME_MAPS)
        self.assertEqual(result["itinerary"][0]["activities"][0]["position"], "Nowhere")
        self.assertEqual(result["itinerary"][0]["activities"][1]["position"], "Unknown Place")


if __name__ == "__main__":
    unittest.main()
