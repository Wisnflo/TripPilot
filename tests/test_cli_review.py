import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))


def module(name):
    spec=importlib.util.spec_from_file_location('review_'+name,ROOT/'scripts'/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class FinalReviewTest(unittest.TestCase):
    def test_scripted_answer_flag_is_not_actual_consent(self):
        judge=module('score_suite')
        spec={'event':{'kind':'return_earlier','new_days':1},'clarification_answer':'cancel_overnight'}
        self.assertFalse(judge.has_lodging_consent(spec,{}))

    def test_only_resolved_question_can_waive_lodging(self):
        judge=module('score_suite');spec={'event':{'kind':'return_earlier','new_days':1},'clarification_answer':'cancel_overnight'}
        result={'clarification':{'explicit_lodging_waiver':True,'answer':'cancel_overnight','question':'取消住宿？','choices':{'cancel_overnight':'取消'}}}
        self.assertTrue(judge.has_lodging_consent(spec,result))
        spec['event']['kind']='budget_cut'
        self.assertFalse(judge.has_lodging_consent(spec,result))

    def test_product_assigns_uid_before_paid_worker_and_saves_result(self):
        cli=module('trip')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task=root/'task.json';output=root/'out'
            task.write_text(json.dumps({'days':1,'people_number':1,'start_city':'上海','target_city':'杭州','nature_language':'去公园'}))
            def fake(command,folder,seconds,cwd):
                job=json.loads((folder/'job.json').read_text());self.assertTrue(job['task']['uid'].startswith('interactive-'))
                (folder/'worker-result.json').write_text(json.dumps({'status':'checked_ok','task':job['task'],'plan':{'itinerary':[]}}))
                return {'hard_timeout':False,'cancelled':False,'wall_seconds':0.1}
            with patch.object(sys,'argv',['trip.py','plan','--task',str(task),'--output-dir',str(output)]), \
                 patch('trippilot.execution.supervisor.supervise',side_effect=fake),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(),0)
            self.assertTrue((output/'record.json').exists());self.assertTrue((output/'plan.json').exists())

    def test_existing_output_does_not_start_paid_worker(self):
        cli=module('trip')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys,'argv',['trip.py','plan','--from-city','上海','--to-city','杭州','--days','1','--request','去公园','--output-dir',directory]), \
                 patch('trippilot.execution.supervisor.supervise') as paid:
                with self.assertRaises(FileExistsError):cli.main()
            paid.assert_not_called()

    def test_zero_limit_and_duplicate_job_id_rejected_before_payment(self):
        runner=module('run_suite')
        with patch.object(sys,'argv',['run_suite.py','--partition','test','--run-id','invalid','--limit','0']), \
             patch.object(runner,'supervise') as paid,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:runner.main()
            self.assertEqual(error.exception.code,2);paid.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            manifest=Path(directory)/'tasks.json';manifest.write_text(json.dumps({'test':[{'uid':'same','split':'easy'}]*2}))
            with patch.object(sys,'argv',['run_suite.py','--manifest',str(manifest),'--partition','test','--run-id','invalid']), \
                 patch.object(runner,'supervise') as paid,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:runner.main()
                self.assertEqual(error.exception.code,2);paid.assert_not_called()


    def test_acceptance_refuses_existing_evidence_before_launch(self):
        runner=module('run_acceptance')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=root/'experiments/v3/acceptance-copy'
            evidence.mkdir(parents=True);state=evidence/'state.json';state.write_text('preserve')
            with patch.object(runner,'ROOT',root),patch.object(sys,'argv',['run_acceptance.py','--prefix','copy']),patch.object(runner,'stage') as paid:
                with self.assertRaises(SystemExit):runner.main()
            self.assertEqual(state.read_text(),'preserve');paid.assert_not_called()

    def test_dynamic_manifest_creates_new_parent_directory(self):
        builder=module('build_dynamic_suite')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);run=root/'experiments/v3/new-static';run.mkdir(parents=True)
            (run/'protocol.json').write_text(json.dumps({'events':[]}))
            (run/'scores.json').write_text(json.dumps({'rows':[]}))
            target=root/'experiments/frozen/new/dynamic.json'
            with patch.object(builder,'ROOT',root),patch.object(sys,'argv',['build_dynamic_suite.py','--static-run-id','new-static','--output',str(target)]),contextlib.redirect_stdout(io.StringIO()):
                builder.main()
            self.assertEqual(json.loads(target.read_text())['parent_count'],0)


    def test_product_intent_call_consumes_revision_call_budget(self):
        cli=module('trip')
        from trippilot.models.config import ModelConfig
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);task={'uid':'x','nature_language':'公园'}
            job={'command':'revise','model':'test','seconds':30,'request_timeout':10,'attempts':1,'task':task,'seed':{},'seed_path':'unused','change':'少玩一会儿'}
            path=folder/'job.json';path.write_text(json.dumps(job))
            state={'status':'ready','task':task,'seed_plan':{},'event':{'kind':'budget_cut','new_budget':'100'},'state_revision':'old'}
            trajectory=SimpleNamespace(terminal_state='checked_ok',plan={'itinerary':[]},as_dict=lambda:{})
            with patch('trippilot.models.config.resolve_model_config',return_value=ModelConfig('test','unused')),patch('trippilot.tools.agent_tools.ToolBox'), \
                 patch('trippilot.repair.intent.parse_change',return_value=(None,{'requests':[{'attempts':1}]})), \
                 patch('trippilot.repair.negotiation.begin_revision',return_value=state), \
                 patch('trippilot.repair.agent.repair_incremental',return_value=trajectory) as repair,patch.dict('os.environ'):
                cli.worker(path)
            self.assertEqual(repair.call_args.kwargs['max_steps'],15)
            self.assertTrue((folder/'worker-result.json').exists())


if __name__=='__main__':unittest.main()
