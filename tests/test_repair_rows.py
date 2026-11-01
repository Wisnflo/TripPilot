from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import trippilot.repair.proposals as proposals


class RepairRowsTest(unittest.TestCase):
    def test_malformed_public_fares_do_not_enter_proposals_or_cache(self):
        valid={'FlightID':'FL1','From':'A','To':'B','BeginTime':'10:00','EndTime':'11:00','Cost':10}
        rows=[dict(valid,Cost=float('nan')),dict(valid,Cost=float('inf')),dict(valid,Cost=None),dict(valid,FlightID=None),dict(valid,BeginTime=None),valid]
        class Tools:
            def __init__(self):self.calls=0
            def call(self,name,args):
                self.calls+=1
                return {'success':True,'data':{'rows':rows,'row_count':len(rows)}}
        box=Tools();public=proposals.PublicTools(box)
        first=public.rows('A','B','airplane');self.assertEqual(first,[dict(valid)])
        first[0]['Cost']=999
        self.assertEqual(public.rows('A','B','airplane'),[dict(valid,Cost=10)]);self.assertEqual(box.calls,1)
        self.assertEqual(public.trace[-1]['response']['data']['rows'][-1]['Cost'],10)


if __name__=='__main__':unittest.main()
