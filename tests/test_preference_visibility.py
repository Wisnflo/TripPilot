from pathlib import Path
import copy
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.web.server import view


class PreferenceVisibilityTest(unittest.TestCase):
    def test_unverified_user_preferences_survive_successful_runtime_check(self):
        record={'result':{'terminal_state':'checked_ok','metadata':{'predicted_requirements':{'unsupported':['住西湖附近','住西湖附近',' ']}},'checker':{'unsupported':['room_rules']}}}
        original=copy.deepcopy(record);result=view(record)
        self.assertEqual(result['status'],'checked_ok')
        self.assertEqual(result['unverified_preferences'],['住西湖附近'])
        self.assertEqual(record,original)

    def test_malformed_preferences_do_not_break_display_or_become_labels(self):
        record={'result':{'terminal_state':'checked_ok','metadata':{'predicted_requirements':{'unsupported':'unexpected text'}}}}
        self.assertEqual(view(record)['unverified_preferences'],[])
        record['result']['metadata']['predicted_requirements']['unsupported']=[None,{},'江景房']
        self.assertEqual(view(record)['unverified_preferences'],['江景房'])


if __name__=='__main__':unittest.main()
