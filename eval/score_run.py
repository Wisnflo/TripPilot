#!/usr/bin/env python3
"""Offline scorer: schema + commonsense + official hard constraints for one run/method."""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "upstream" / "ChinaTravel"
sys.path.insert(0, str(UPSTREAM))

from chinatravel.evaluation.commonsense_constraint import evaluate_commonsense_constraints  # noqa: E402
from chinatravel.evaluation.hard_constraint import evaluate_hard_constraints_v2  # noqa: E402
from chinatravel.evaluation.schema_constraint import evaluate_schema_constraints  # noqa: E402
from chinatravel.environment.language import CITY_NAMES  # noqa: E402

CITY_ZH_TO_EN = dict(zip(CITY_NAMES["zh"], CITY_NAMES["en"]))
CITY_EN_TO_ZH = dict(zip(CITY_NAMES["en"], CITY_NAMES["zh"]))


def build_name_maps() -> dict:
    """Map English entity names back to Chinese by id for hard-constraint evaluation.

    The English sandbox returns English type/cuisine labels, while hard_logic_py
    literals are Chinese (for example 公园, 川菜). Evaluating a Chinese literal
    against an English sandbox can never pass, so hard checks are run on a
    Chinese copy of the plan.
    """
    maps = {}
    root = UPSTREAM / "chinatravel" / "environment"
    for city_en, city_zh in CITY_EN_TO_ZH.items():
        folder = city_en.lower()
        city_map = {}
        for kind, zh_dir, en_dir, en_field in (
            ("attractions", "attractions", "attractions", "name"),
            ("restaurants", "restaurants", "restaurants", "name"),
            ("accommodations", "accommodations", "accommodations", "hotelname_en"),
        ):
            zh_files = sorted((root / "database" / zh_dir / folder).glob("*.csv"))
            en_files = sorted((root / "database_en" / en_dir / folder).glob("*.csv"))
            if not zh_files or not en_files:
                continue
            zh_rows = {row.get("id"): row for row in csv.DictReader(zh_files[0].open(encoding="utf-8"))}
            for row in csv.DictReader(en_files[0].open(encoding="utf-8")):
                zh_row = zh_rows.get(row.get("id"))
                if zh_row and row.get(en_field):
                    city_map[row[en_field]] = zh_row.get("name")
        maps[city_en] = city_map
    return maps


def translate_plan_for_hard(plan: dict, name_maps: dict) -> dict:
    translated = json.loads(json.dumps(plan))
    translated["start_city"] = CITY_EN_TO_ZH.get(translated.get("start_city"), translated.get("start_city"))
    translated["target_city"] = CITY_EN_TO_ZH.get(translated.get("target_city"), translated.get("target_city"))
    target_en = CITY_ZH_TO_EN.get(str(plan.get("target_city")), str(plan.get("target_city")))
    start_en = CITY_ZH_TO_EN.get(str(plan.get("start_city")), str(plan.get("start_city")))
    target_map = name_maps.get(target_en, {})
    start_map = name_maps.get(start_en, {})
    for day in translated.get("itinerary") or []:
        if not isinstance(day, dict):
            continue
        for activity in day.get("activities") or []:
            if not isinstance(activity, dict):
                continue
            position = activity.get("position")
            if position:
                activity["position"] = target_map.get(position) or start_map.get(position) or position
    return translated


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--method", default="b2")
    parser.add_argument("--split", default="easy")
    parser.add_argument("--lang", default="en", choices=["zh", "en"])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    method_dir = run_dir / args.method
    if not method_dir.is_dir():
        print("method dir not found:", method_dir)
        return 2

    schema = load_json(UPSTREAM / "chinatravel" / "evaluation" / "output_schema.json")
    visible_root = ROOT / "data" / "agent_visible" / args.split
    scoring_root = ROOT / "data" / "scoring" / args.split

    uids = sorted(path.stem for path in method_dir.glob("*.json"))
    if not uids:
        print("no result files in", method_dir)
        return 2

    plans = {}
    query_data = {}
    for uid in uids:
        result = load_json(method_dir / ("%s.json" % uid))
        plans[uid] = result.get("plan") or {}
        visible = load_json(visible_root / ("%s.json" % uid))
        scoring = load_json(scoring_root / ("%s.json" % uid))
        eval_query = {**visible, **scoring}
        if args.lang == "en":
            eval_query["start_city"] = CITY_ZH_TO_EN.get(visible.get("start_city"), visible.get("start_city"))
            eval_query["target_city"] = CITY_ZH_TO_EN.get(visible.get("target_city"), visible.get("target_city"))
            if visible.get("nature_language_en"):
                eval_query["nature_language"] = visible["nature_language_en"]
        query_data[uid] = eval_query

    schema_rate, _, schema_pass = evaluate_schema_constraints(uids, plans, schema)
    commonsense_macro, commonsense_micro, _, commonsense_pass = evaluate_commonsense_constraints(
        uids, query_data, plans, verbose=False, lang=args.lang
    )
    hard_plans = plans
    hard_lang = args.lang
    if args.lang == "en":
        name_maps = build_name_maps()
        hard_plans = {uid: translate_plan_for_hard(plan, name_maps) for uid, plan in plans.items()}
        hard_lang = "zh"
    hard_macro, hard_micro, _, _, _, hard_pass = evaluate_hard_constraints_v2(
        uids, query_data, hard_plans, env_pass_id=commonsense_pass, verbose=False, lang=hard_lang
    )

    schema_set, commonsense_set, hard_set = set(schema_pass), set(commonsense_pass), set(hard_pass)
    tasks = []
    for uid in uids:
        tasks.append(
            {
                "uid": uid,
                "schema_pass": uid in schema_set,
                "commonsense_pass": uid in commonsense_set,
                "hard_pass": uid in hard_set,
                "all_pass": uid in schema_set and uid in commonsense_set and uid in hard_set,
            }
        )

    all_pass = sum(1 for task in tasks if task["all_pass"])
    payload = {
        "run_dir": str(run_dir),
        "method": args.method,
        "split": args.split,
        "task_count": len(uids),
        "schema_pass_rate_pct": schema_rate,
        "commonsense_macro_pct": commonsense_macro,
        "commonsense_micro_pct": commonsense_micro,
        "hard_macro_pct": hard_macro,
        "hard_micro_pct": hard_micro,
        "all_pass_count": all_pass,
        "all_pass_rate_pct": 100.0 * all_pass / len(uids),
        "tasks": tasks,
    }
    output = Path(args.output) if args.output else run_dir / ("scores-%s.json" % args.method)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in payload if k != "tasks"}, ensure_ascii=False, indent=2))
    print("scores written to", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
