import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import trippilot.planning.structured as planner
from trippilot.models.config import ModelConfig


class ComponentControlTest(unittest.TestCase):
    def run_case(self,feedback=True,compiler=True):
        task={'uid':'unit','start_city':'上海','target_city':'杭州','days':2,'people_number':1,'nature_language':'去公园'}
        facts={key:{} for key in ('attraction','hotel','restaurant','outbound','inbound')}
        catalog=SimpleNamespace(tools=SimpleNamespace(trace=[]))
        messages=[]
        response=SimpleNamespace(content='{"itinerary":[]}',attempts=1,request_latencies=[.01],request_id='test',model='fake',prompt_tokens=10,completion_tokens=10,total_tokens=20,reasoning_tokens=None)
        def step(config,values,**kwargs):messages.append(copy.deepcopy(values));return response
        failed=SimpleNamespace(ok=False,violations=[SimpleNamespace(message='timing invalid')])
        passed=SimpleNamespace(ok=True,as_dict=lambda:{'ok':True})
        fallback=SimpleNamespace(metadata={'usage_complete':True},steps=[],usage={},plan=None,terminal_state='max_steps',checker=None)
        with patch.object(planner,'Catalog',return_value=catalog),patch.object(planner,'extract_requirements',return_value={}),patch.object(planner,'chat_step',side_effect=step),patch.object(planner,'compile_sketch',return_value={'itinerary':[]}),patch.object(planner,'check_requirements',side_effect=[failed,passed]),patch.object(planner,'count_feedback') as feedback_call,patch.object(planner,'run_react',return_value=fallback) as fallback_call,patch('trippilot.plan.normalize.normalise_plan',return_value={'itinerary':[]}),patch('trippilot.plan.grounding.complete_routes',return_value={'itinerary':[]}):
            result=planner.run_structured(task,ModelConfig('fake','unused'),None,feedback=feedback,compile_plan=compiler,shared_facts=facts)
        return result,messages,feedback_call.call_count,fallback_call.call_count

    def test_disabled_feedback_does_not_emit_feedback_or_fall_back(self):
        result,messages,feedback_calls,fallback_calls=self.run_case(feedback=False)
        self.assertEqual(len(messages),1)
        self.assertEqual(feedback_calls,0)
        self.assertEqual(fallback_calls,0)
        self.assertFalse(result.metadata['components']['feedback'])

    def test_complete_plan_mode_retains_guidance_and_correct_output_contract(self):
        result,messages,_,_=self.run_case(compiler=False)
        self.assertIn('Exactly N days',messages[0][0]['content'])
        self.assertIn('corrected complete JSON plan',messages[1][-1]['content'])
        self.assertNotIn('sketch',messages[1][-1]['content'])
        self.assertEqual(result.terminal_state,'checked_ok')

    def test_regular_compiled_feedback_keeps_its_contract(self):
        result,messages,feedback_calls,fallback_calls=self.run_case()
        self.assertIn('Choose an itinerary SKETCH',messages[0][0]['content'])
        self.assertIn('Revise only the sketch',messages[1][-1]['content'])
        self.assertEqual(feedback_calls,1);self.assertEqual(fallback_calls,0)
        self.assertEqual(result.terminal_state,'checked_ok')


if __name__=='__main__':unittest.main()
