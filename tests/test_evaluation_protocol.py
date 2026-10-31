"""Evaluation integrity regressions that do not call a model or open a port."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'eval'),str(ROOT/'src')]
from evaluation_protocol import make_job,source_files,input_files,changed_files
from statistics_tools import paired,wilson
from metrics import EntityIndex,changes,quantile
from trippilot.execution.ledger import summarize
from trippilot.repair.dependencies import affected_activity_ids
from trippilot.repair.events import ChangeEvent
from witnesses import Judge,route
from run_completion import stages
from report import render
from trippilot.execution.isolation import prepare
from trippilot.web.server import Jobs


class ProtocolIntegrityTest(unittest.TestCase):
    def test_worker_contract_excludes_offline_proof_and_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);visible=root/'data/agent_visible/easy';visible.mkdir(parents=True)
            task={'uid':'x','nature_language':'去公园'};(visible/'x.json').write_text(json.dumps(task))
            (root/'seed.json').write_text(json.dumps({'plan':{'people_number':1}}))
            event={'uid':'x','split':'easy','seed':'seed.json','event':{'kind':'budget_cut','new_budget':'100','solution':'secret'},
                   'solution_reference':'offline-only.json','hard_logic_py':['secret'],'clarification_answer':None}
            job=make_job(root,event,'dynamic',{'method':'patch','model':'test'})
            self.assertFalse({'seed','solution_reference','hard_logic_py'} & job.keys())
            self.assertNotIn('solution',job['event']);self.assertEqual(job['task'],task)
            self.assertEqual(job['seed_plan'],{'people_number':1})

    def test_added_runtime_file_and_modified_tool_data_stop_same_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('pyproject.toml','uv.lock','.python-version','upstream/ChinaTravel/chinatravel/evaluation/output_schema.json',
                         'upstream/ChinaTravel/chinatravel/environment/database/city.csv','src/example.py'):
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('original')
            expected=source_files(root)
            (root/'src/new.py').write_text('new')
            (root/'upstream/ChinaTravel/chinatravel/environment/database/city.csv').write_text('changed')
            observed=changed_files(root,expected,{})
            self.assertIn('src/new.py',observed);self.assertIn('upstream/ChinaTravel/chinatravel/environment/database/city.csv',observed)

    def test_pipeline_has_four_methods_and_independent_second_model(self):
        manifest=json.loads((ROOT/'config/completion-tasks.json').read_text())
        recipe=stages('check','config/completion-tasks.json',manifest)
        commands=dict(recipe)
        initial=commands['static'];self.assertEqual(initial[initial.index('--methods')+1:initial.index('--model')],['b0','b1','b2','structured'])
        self.assertEqual(commands['second-model'][commands['second-model'].index('--model')+1],'glm-5.3-flash')
        self.assertLess([n for n,_ in recipe].index('construct-dynamic'),[n for n,_ in recipe].index('dynamic'))

    def test_mac_policy_covers_projects_outside_the_home_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('trippilot.execution.isolation.platform.system',return_value='Darwin'):
                command,env=prepare([sys.executable,str(ROOT/'scripts/job_worker.py')],directory)
            policy=command[2]
            self.assertIn('(deny file-read-data (require-not',policy)
            self.assertNotIn('(subpath "/Users")',policy)
            self.assertEqual(env['HOME'],str(Path(directory).resolve()))
            self.assertNotIn('TRIPPILOT_CONFIG_FILE',env)

    def test_reference_constructor_rejects_event_failure_before_offline_judges(self):
        judge=Judge.__new__(Judge)
        with patch('witnesses.event_violations',return_value=['banned transport remains']):
            self.assertFalse(judge.passes({}, {}, {}, {},ChangeEvent('transport_unavailable',target='G1'),{}))
    def test_constructor_rejects_routes_past_the_same_day_horizon(self):
        class Tools:
            def call(self,*args):return {'success':True,'data':[{'start_time':'23:30','end_time':'25:00','cost':5}]}
        self.assertIsNone(route(Tools(),'Hangzhou','Park','Station','23:30','walk',1))

    def test_non_object_web_request_does_not_start_a_process(self):
        with tempfile.TemporaryDirectory() as directory,patch('trippilot.web.server.subprocess.Popen') as process:
            jobs=Jobs(directory)
            with self.assertRaises(ValueError):jobs.create([])
            process.assert_not_called();self.assertFalse(jobs.items)

    def test_missing_experiments_do_not_turn_into_claimed_improvement(self):
        with tempfile.TemporaryDirectory() as directory:
            text=render(Path(directory),'not-run')
            self.assertIn('不能判断当前实现是否提升',text)
            self.assertIn('0/6',text)



class IndependentMetricsTest(unittest.TestCase):
    def row(self,uid,key,method,success):
        return dict(uid=uid,key=key,method=method,success=success,tokens=10,seconds=1)

    def test_correlated_events_never_get_independent_event_mcnemar(self):
        rows=[self.row('a',key,method,method=='patch') for key in ('event1','event2') for method in ('patch','b2')]
        result=paired(rows,'patch',resamples=100)
        self.assertEqual(result['parents'],1);self.assertEqual(result['pairs'],2)
        self.assertIsNone(result['exact_mcnemar_p']);self.assertEqual(result['success_rate_difference'],1)
        self.assertEqual(result['parent_bootstrap_95']['success_rate_difference'],[1,1])

    def test_duration_percentiles_use_linear_interpolation(self):
        self.assertEqual(quantile([1,2,3,4],.5),2.5)
        self.assertAlmostEqual(quantile([1,2,3,4],.95),3.85)

    def test_parent_paired_test_and_wilson_match_known_values(self):
        rows=[self.row(str(i),str(i),method,method=='structured') for i in range(6) for method in ('structured','b2')]
        result=paired(rows,'structured',resamples=100)
        self.assertEqual(result['exact_mcnemar_p'],.03125)
        low,high=wilson(0,10);self.assertAlmostEqual(low,0);self.assertAlmostEqual(high,.2775327998628892)

    def test_entity_metric_survives_invalid_itinerary(self):
        index=EntityIndex.__new__(EntityIndex);index.places={'Park'};index.transports={'G1'}
        self.assertEqual(index.measure({'itinerary':'invalid'}),{'checked':0,'unmatched':0,'missing':0})
        self.assertEqual(index.measure({'itinerary':[None,{'activities':[None,{'type':'attraction','position':'invented'}]}]})['unmatched'],1)

    def test_actual_public_transport_ids_are_not_phantom_entities(self):
        index=EntityIndex()
        self.assertIn('FL001',index.transports);self.assertIn('G115',index.transports)
        plan={'itinerary':[{'day':1,'activities':[{'type':'airplane','FlightID':'FL001'},{'type':'train','TrainID':'G115'}]}]}
        self.assertEqual(index.measure(plan),{'checked':2,'missing':0,'unmatched':0})

    def test_removing_an_activity_does_not_shift_preservation_matches(self):
        seed={'itinerary':[{'day':1,'activities':[{'type':'attraction','position':'Park','cost':0},{'type':'lunch','position':'Cafe','cost':10}]}]}
        after={'itinerary':[{'day':1,'activities':[seed['itinerary'][0]['activities'][1]]}]}
        result=changes(seed,after,ChangeEvent('budget_cut',new_budget=5))
        self.assertEqual(result['unchanged'],1);self.assertEqual(result['removed_or_modified'],1)
        self.assertEqual(result['added_or_modified'],0)

    def test_final_retained_night_is_affected_by_earlier_return(self):
        seed={'itinerary':[{'day':1,'activities':[{'type':'attraction'},{'type':'accommodation'}]}, {'day':2,'activities':[{'type':'train'}]}]}
        affected=affected_activity_ids(seed,ChangeEvent('return_earlier',new_days=1))
        self.assertIn('d1:a2',affected);self.assertNotIn('d1:a1',affected)

    def test_fractional_list_rates_are_added_without_binary_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'requests.jsonl'
            events=[]
            for i in range(100):events.extend([{'kind':'start','attempt_id':str(i)}, {'kind':'success','attempt_id':str(i),'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'cached_prompt_tokens':0}])
            path.write_text('\n'.join(json.dumps(row) for row in events))
            result=summarize(path,{'input':.1,'cached_input':.01,'output':.2})
            self.assertEqual(result['observed_usage_cost_CNY']['upper'],.00003)
            self.assertFalse(result['unobserved_billing_possible'])


if __name__=='__main__':unittest.main()
