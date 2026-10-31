import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trippilot.agent_input import build_agent_input  # noqa: E402
from trippilot.data.prepare import prepare_splits, sha256_file  # noqa: E402
from trippilot.data.schema import (  # noqa: E402
    AGENT_VISIBLE_FIELDS,
    assert_agent_safe,
    iter_forbidden_paths,
    parse_hard_logic_py,
)

SENTINEL = "SENTINEL_7f3a9c"
FIELDS = [
    "uid",
    "tag",
    "start_city",
    "target_city",
    "days",
    "people_number",
    "limit_rooms",
    "limits_room_type",
    "hard_logic_py",
    "nature_language",
    "nature_language_en",
]


def write_csv(path: Path) -> None:
    rows = [
        {
            "uid": "t_py",
            "tag": "easy",
            "start_city": "上海",
            "target_city": "杭州",
            "days": "2",
            "people_number": "2",
            "limit_rooms": "False",
            "limits_room_type": "False",
            "hard_logic_py": "['result=(day_count(plan)==2)', 'result=(people_count(plan)==2)']",
            "nature_language": "上海出发去杭州两天，两个人。",
            "nature_language_en": "Two days in Hangzhou for two people from Shanghai.",
        },
        {
            "uid": "t_json",
            "tag": "easy",
            "start_city": "上海",
            "target_city": "苏州",
            "days": "1",
            "people_number": "1",
            "limit_rooms": "False",
            "limits_room_type": "False",
            "hard_logic_py": '["result=(day_count(plan)==1)"]',
            "nature_language": "上海去苏州一天。",
            "nature_language_en": "One day in Suzhou from Shanghai.",
        },
        {
            "uid": "t_sentinel",
            "tag": "easy",
            "start_city": "上海",
            "target_city": "南京",
            "days": "1",
            "people_number": "1",
            "limit_rooms": "False",
            "limits_room_type": "False",
            "hard_logic_py": "['result=(day_count(plan)==1)  # %s']" % SENTINEL,
            "nature_language": "上海去南京一天。",
            "nature_language_en": "One day in Nanjing from Shanghai.",
        },
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.agent = self.root / "agent"
        self.scoring = self.root / "scoring"
        self.manifests = self.root / "manifests"
        self.raw.mkdir()
        self.csv = self.raw / "easy.csv"
        write_csv(self.csv)

    def tearDown(self):
        self.tmp.cleanup()

    def _prepare(self):
        return prepare_splits(
            raw_dir=self.raw,
            agent_root=self.agent,
            scoring_root=self.scoring,
            manifest_dir=self.manifests,
            splits=["easy"],
            source_commit="test-commit",
            generated_at="2026-10-26",
        )[0]

    def test_agent_records_use_allowlist_only(self):
        manifest = self._prepare()
        self.assertEqual(manifest["row_count"], 3)
        for path in sorted((self.agent / "easy").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertLessEqual(set(payload), set(AGENT_VISIBLE_FIELDS))
            self.assertEqual(iter_forbidden_paths(payload), [])
            self.assertNotIn(SENTINEL, path.read_text(encoding="utf-8"))

    def test_scoring_records_keep_constraints(self):
        self._prepare()
        scoring = json.loads((self.scoring / "easy" / "t_py.json").read_text(encoding="utf-8"))
        self.assertEqual(len(scoring["hard_logic_py"]), 2)
        sentinel = json.loads((self.scoring / "easy" / "t_sentinel.json").read_text(encoding="utf-8"))
        self.assertTrue(any(SENTINEL in item for item in sentinel["hard_logic_py"]))

    def test_manifest_records_source_hash(self):
        manifest = self._prepare()
        self.assertEqual(manifest["source"]["sha256"], sha256_file(self.csv))
        self.assertEqual(manifest["agent_visible_count"], 3)

    def test_build_agent_input_rejects_nested_forbidden_keys(self):
        with self.assertRaises(ValueError):
            build_agent_input(
                {
                    "uid": "x",
                    "nature_language": {"text": "request", "hard_logic_py": ["secret"]},
                }
            )

    def test_build_agent_input_drops_non_allowlisted_fields(self):
        payload = build_agent_input(
            {
                "uid": "x",
                "nature_language": "request",
                "evidence": {"hard_logic_py": ["secret"]},
            }
        )
        self.assertEqual(set(payload), {"uid", "nature_language"})
        self.assertEqual(iter_forbidden_paths(payload), [])

    def test_assert_agent_safe_rejects_extra_top_level_fields(self):
        with self.assertRaises(ValueError):
            assert_agent_safe({"uid": "x", "unexpected": 1})

    def test_parse_accepts_python_and_json_literals(self):
        self.assertEqual(parse_hard_logic_py("['a', 'b']"), ["a", "b"])
        self.assertEqual(parse_hard_logic_py('["a", "b"]'), ["a", "b"])
        with self.assertRaises(ValueError):
            parse_hard_logic_py("not a list")

    def test_roots_must_not_overlap(self):
        with self.assertRaises(ValueError):
            prepare_splits(
                raw_dir=self.raw,
                agent_root=self.root,
                scoring_root=self.root / "agent" / "scoring",
                manifest_dir=self.manifests,
                splits=["easy"],
                source_commit="test-commit",
                generated_at="2026-10-26",
            )


if __name__ == "__main__":
    unittest.main()
