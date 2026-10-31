import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from trippilot.request import request_text
from trippilot.planning.structured import compile_sketch,shortlist
from trippilot.repair.events import ChangeEvent
from trippilot.repair.negotiation import begin_revision,resolve_revision
from trippilot.execution.supervisor import supervise
from trippilot.execution.ledger import summarize


class InputAndCompilerTest(unittest.TestCase):
    def test_source_request_survives_placeholder_translation(self):
        text=request_text({'nature_language':'请住指定酒店','nature_language_en':'Sure, here is the translation:'})
        self.assertIn('请住指定酒店',text)

    def test_public_bilingual_entity_link(self):
        from trippilot.tools.agent_tools import ToolBox
        b=ToolBox()
        result=b.call('attractions_select',{'city':'杭州','key':'name','op':'eq','value':'灵隐寺'})
        rows=result['data']['rows']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['id'],0)
        self.assertEqual(rows[0]['name'],'Lingyin Temple')

    def test_compiler_copies_prices_and_generates_grounded_routes(self):
        class Tools:
            def route(self,city,start,end,when,mode,people):
                h,m=map(int,when.split(':'));endtime='%02d:%02d'%((h*60+m+5)//60,(h*60+m+5)%60)
                return [{'start':start,'end':end,'start_time':when,'end_time':endtime,'mode':'taxi','price':10,'cost':10,'cars':1,'distance':1}]
        task={'days':1,'people_number':2,'start_city':'Hangzhou','target_city':'Hangzhou'}
        sketch={'hotel':None,'days':[[{'kind':'attraction','id':'a1'},{'kind':'lunch','id':'r1'}]]}
        facts={'attraction':{'a1':{'name':'Park','price':20,'opentime':'08:00','endtime':'18:00','recommendmintime':0.5}},
               'restaurant':{'r1':{'name':'Food','price':30,'opentime':'11:00','endtime':'20:00'}},'hotel':{}}
        plan=compile_sketch(task,sketch,facts,{},Tools());acts=plan['itinerary'][0]['activities']
        self.assertEqual(acts[0]['cost'],40);self.assertEqual(acts[0]['tickets'],2)
        self.assertEqual(acts[1]['cost'],60);self.assertEqual(acts[1]['transports'][0]['end'],'Food')
        self.assertEqual(acts[1]['start_time'],'11:00')

    def test_compiler_rejects_invented_entity(self):
        with self.assertRaises(ValueError):
            compile_sketch({'days':1,'people_number':1,'start_city':'A','target_city':'A'},
                {'days':[[{'kind':'attraction','id':'invented'}]]}, {'attraction':{},'restaurant':{},'hotel':{}},{},None)


class ClarificationTest(unittest.TestCase):
    def setUp(self):
        self.task={'uid':'t','nature_language':'需要双床酒店','days':2}
        self.seed={'itinerary':[{'activities':[{'type':'accommodation','position':'Hotel'}]}, {'activities':[]}]}
        self.event=ChangeEvent('return_earlier',new_days=1)

    def test_lodging_not_silently_cancelled(self):
        state=begin_revision(self.task,self.seed,self.event)
        self.assertEqual(state['status'],'needs_clarification')
        self.assertNotIn('explicit_lodging_waiver',state)

    def test_explicit_cancellation_preserves_other_requirements(self):
        state=resolve_revision(begin_revision(self.task,self.seed,self.event),'cancel_overnight')
        self.assertTrue(state['explicit_lodging_waiver']);self.assertIn('All non-lodging requirements remain',state['task']['nature_language_en'])
        self.assertEqual(self.task['nature_language'],'需要双床酒店')

    def test_keep_original_does_not_repair(self):
        state=resolve_revision(begin_revision(self.task,self.seed,self.event),'keep_original')
        self.assertEqual(state['status'],'cancelled');self.assertEqual(state['plan'],self.seed)

    def test_stale_seed_cannot_resume(self):
        with self.assertRaises(ValueError):resolve_revision(begin_revision(self.task,self.seed,self.event),'cancel_overnight',current_seed={'itinerary':[]})

    def test_no_lodging_requirement_does_not_prompt(self):
        state=begin_revision({'nature_language':'去公园散步'},self.seed,self.event)
        self.assertEqual(state['status'],'ready')


class ExecutionTest(unittest.TestCase):
    def test_real_supervisor_stops_a_blocking_child(self):
        with tempfile.TemporaryDirectory() as folder:
            result=supervise([sys.executable,'-c','import time; time.sleep(10)'],folder,0.1)
            self.assertTrue(result['hard_timeout']);self.assertLess(result['wall_seconds'],2)

    def test_cached_tokens_are_costed_without_double_charge(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'requests.jsonl'
            rows=[{'kind':'start','attempt_id':'1'},{'kind':'success','attempt_id':'1','prompt_tokens':1000,
                   'completion_tokens':200,'total_tokens':1200,'cached_prompt_tokens':500}]
            path.write_text('\n'.join(json.dumps(x) for x in rows))
            result=summarize(path,{'input':1,'cached_input':0.1,'output':1})
            self.assertAlmostEqual(result['observed_usage_cost_CNY']['upper'],0.00075)
            self.assertFalse(result['unobserved_billing_possible'])
            self.assertIsNone(result['invoice_total_CNY'])

    def test_killed_request_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'requests.jsonl';path.write_text(json.dumps({'kind':'start','attempt_id':'1'})+'\n')
            result=summarize(path,{'input':1,'cached_input':0.1,'output':1})
            self.assertEqual(result['pending_attempts'],1);self.assertTrue(result['unobserved_billing_possible'])


if __name__=='__main__':unittest.main()
