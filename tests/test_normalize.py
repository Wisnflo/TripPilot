import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.plan.normalize import normalise_plan  # noqa: E402


def plan():
    return {
        "people_number": 2,
        "itinerary": [
            {
                "day": 1,
                "activities": [
                    {
                        "type": "attraction",
                        "price": 10.0,
                        "cost": 999.0,
                        "tickets": 9,
                        "transports": [
                            {
                                "mode": "taxi",
                                "price": 5.0,
                                "cost": 5.0,
                            },
                            {
                                "mode": "walk",
                                "price": 0.0,
                                "cost": 5.0,
                            },
                        ],
                    },
                    {
                        "type": "metro",
                        "price": 3.0,
                        "cost": 3.0,
                        "transports": [
                            {
                                "mode": "metro",
                                "price": 3.0,
                                "cost": 3.0,
                                "tickets": 1,
                            }
                        ],
                    },
                    {
                        "type": "accommodation",
                        "price": 200.0,
                        "cost": 100.0,
                        "rooms": 2,
                    },
                ],
            }
        ],
    }


class NormaliseTest(unittest.TestCase):
    def test_people_and_cars_and_costs(self):
        result = normalise_plan(plan(), 2)
        activities = result["itinerary"][0]["activities"]
        attraction = activities[0]
        self.assertEqual(attraction["tickets"], 2)
        self.assertAlmostEqual(attraction["cost"], 20.0)
        taxi = attraction["transports"][0]
        self.assertEqual(taxi["cars"], 1)
        self.assertAlmostEqual(taxi["cost"], 5.0)
        self.assertEqual(attraction["transports"][1]["cost"], 0.0)
        metro_leg = activities[1]["transports"][0]
        self.assertEqual(metro_leg["tickets"], 2)
        self.assertAlmostEqual(metro_leg["cost"], 6.0)
        hotel = activities[2]
        self.assertAlmostEqual(hotel["cost"], 400.0)

    def test_non_dict_is_returned_unchanged(self):
        self.assertEqual(normalise_plan(None, 2), None)
        self.assertEqual(normalise_plan("bad", 2), "bad")


if __name__ == "__main__":
    unittest.main()
