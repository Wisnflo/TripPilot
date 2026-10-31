import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from trippilot.planning.structured import extract_requirements
from trippilot.models.openai_adapter import ChatStep


class V3ReviewTest(unittest.TestCase):
    def test_explicit_single_bed_beats_bad_translation_prediction(self):
        reply=ChatStep(json.dumps({'room_type':2,'evidence':{'room_type':'单床房'}}),[],
                       'fake',0,10,5,15,0,'stop','fake')
        task={'nature_language':'住一间单床房','nature_language_en':'a twin room'}
        with patch('trippilot.planning.structured.chat_step',return_value=reply):
            result=extract_requirements(task,{k:[] for k in ['attraction','hotel','restaurant']},None,None,lambda *a:None)
        self.assertEqual(result['room_type'],1)
        self.assertEqual(result['rooms'],1)
        self.assertTrue(result['literal_corrections'])

    def test_lodging_waiver_never_drops_mixed_budget_condition(self):
        path=ROOT/'scripts/score_suite.py';spec=importlib.util.spec_from_file_location('v3_judge_review',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertFalse(module.is_lodging_only('result=(room_count(a)==2 and total_cost(plan)<100)'))
        self.assertTrue(module.is_lodging_only("names=set()\nfor a in allactivities(plan):\n if activity_type(a)=='accommodation': names.add(activity_position(a))\nresult=({'Some Hotel'}<=names)"))


if __name__=='__main__':unittest.main()
