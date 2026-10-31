import copy
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trippilot.repair.transaction import apply_edits, revision, EditRejected, patch_between
from trippilot.repair.verification import event_violations
from trippilot.repair.events import ChangeEvent
from trippilot.repair.local import repair_with_fallback
from trippilot.agent.loop import Trajectory
from trippilot.models.config import ModelConfig
from trippilot.models.openai_adapter import _call_with_retries, ModelRequestError
from trippilot.plan.checker import CheckResult
from trippilot.repair.agent import repair_incremental
from trippilot.repair.intent import parse_change


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.seed = {'itinerary': [{'day': 1, 'activities': [{'position': 'a'}, {'position': 'b'}, {'position': 'c'}]},
                                   {'day': 2, 'activities': [{'position': 'd'}]}]}

    def test_delete_does_not_shift_replacement_target(self):
        new = apply_edits(self.seed, revision(self.seed), [
            {'op': 'remove', 'id': 'd1:a1'},
            {'op': 'replace', 'id': 'd1:a2', 'value': {'position': 'new-b'}}], ['d1:a1', 'd1:a2'])
        self.assertEqual(new['itinerary'][0]['activities'], [{'position': 'new-b'}, {'position': 'c'}])
        self.assertEqual(self.seed['itinerary'][0]['activities'][0], {'position': 'a'})

    def test_revision_prevents_stale_commit(self):
        with self.assertRaises(EditRejected):
            apply_edits(self.seed, 'old', [], [])

    def test_empty_permission_does_not_mean_unrestricted(self):
        with self.assertRaises(EditRejected):
            apply_edits(self.seed, revision(self.seed), [{'op': 'remove', 'id': 'd1:a1'}], [])

    def test_conflicts_rejected_before_mutation(self):
        before = copy.deepcopy(self.seed)
        with self.assertRaises(EditRejected):
            apply_edits(self.seed, revision(self.seed), [{'op': 'remove', 'id': 'd1:a1'}] * 2, ['d1:a1'])
        self.assertEqual(self.seed, before)

    def test_cannot_truncate_protected_day(self):
        with self.assertRaises(EditRejected):
            apply_edits(self.seed, revision(self.seed), [], [], keep_days=1)

    def test_preservation_does_not_count_index_shift(self):
        new = copy.deepcopy(self.seed)
        new['itinerary'][0]['activities'].pop(0)
        self.assertEqual(patch_between(self.seed, new),
                         {'unchanged': 3, 'removed_or_modified': 1, 'added_or_modified': 0})


class EventTest(unittest.TestCase):
    task = {'start_city': 'Shanghai', 'target_city': 'Hangzhou'}
    seed = {'start_city': 'Shanghai', 'target_city': 'Hangzhou', 'people_number': 1,
            'itinerary': [{'day': 1, 'activities': []}, {'day': 2, 'activities': []}]}

    def test_banned_train_is_rejected(self):
        plan = copy.deepcopy(self.seed)
        plan['itinerary'][-1]['activities'] = [{'type': 'train', 'TrainID': 'G2', 'end': 'Shanghai Station'}]
        codes = [v.code for v in event_violations(self.task, self.seed, plan, ChangeEvent('transport_unavailable', target='G2'))]
        self.assertIn('event_transport_unavailable', codes)

    def test_shortening_without_return_is_not_success(self):
        plan = copy.deepcopy(self.seed)
        plan['itinerary'] = plan['itinerary'][:1]
        self.assertIn('event_return_missing', [v.code for v in event_violations(
            self.task, self.seed, plan, ChangeEvent('return_earlier', new_days=1))])

    def test_budget_explicit_even_when_text_has_no_budget(self):
        plan = copy.deepcopy(self.seed)
        plan['itinerary'][0]['activities'] = [{'cost': 90, 'transports': [{'cost': 20}]}]
        self.assertIn('event_budget', [v.code for v in event_violations(
            self.task, self.seed, plan, ChangeEvent('budget_cut', new_budget=100))])


class AccountingTest(unittest.TestCase):
    def test_fallback_aggregates_both_phases_and_shares_budget(self):
        first = Trajectory('t', 'ours', 'fake', 'preserve_violated', {}, steps=[{'attempts': 1}], usage={'total_tokens': 100})
        second = Trajectory('t', 'ours', 'fake', 'checked_ok', {}, steps=[{'attempts': 2}], usage={'total_tokens': 200})
        with patch('trippilot.repair.local.repair_local', return_value=first), \
                patch('trippilot.repair.local.run_react', return_value=second) as fallback:
            out = repair_with_fallback({}, {}, ChangeEvent('budget_cut', new_budget=100), None, None, max_steps=8, max_seconds=20)
        self.assertEqual(out.usage['total_tokens'], 300)
        self.assertEqual(fallback.call_args.kwargs['max_steps'], 7)
        self.assertLessEqual(fallback.call_args.kwargs['max_seconds'], 20)
        self.assertEqual(out.metadata['request_count'], 3)
        json.dumps(out.as_dict())  # phase records must not introduce cycles

    def test_exhausted_budget_cannot_start_fallback(self):
        first = Trajectory('t', 'ours', 'fake', 'max_steps', {}, steps=[{}] * 8)
        with patch('trippilot.repair.local.repair_local', return_value=first), \
                patch('trippilot.repair.local.run_react') as fallback:
            repair_with_fallback({}, {}, ChangeEvent('budget_cut', new_budget=100), None, None, max_steps=8)
        fallback.assert_not_called()

    def test_request_timeout_clipped_to_remaining_budget(self):
        completion = SimpleNamespace(create=lambda **kwargs: kwargs)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completion))
        response, _ = _call_with_retries(ModelConfig('fake', 'fake'), client, {}, deadline=time.monotonic()+0.5)
        self.assertGreater(response['timeout'], 0)
        self.assertLessEqual(response['timeout'], 0.5)

    def test_exhausted_deadline_makes_no_request(self):
        with self.assertRaises(ModelRequestError) as ctx:
            _call_with_retries(ModelConfig('fake', 'fake'), None, {}, deadline=time.monotonic()-1)
        self.assertEqual(ctx.exception.attempts, 0)


class PipelineTest(unittest.TestCase):
    def test_verified_fast_path_needs_no_model_and_commits_exact_edits(self):
        seed = {'itinerary':[{'day':1,'activities':[{'position':'a'}]}]}
        edits = [{'op':'replace','id':'d1:a1','value':{'position':'b'}}]
        proposal = {'id':'c0','revision':revision(seed),'edits':edits,'scope':['d1:a1'],
                    'keep_days':None,'preservation':{'unchanged':0}}
        with patch('trippilot.repair.agent.build_proposals',return_value=([proposal],[],[])), \
             patch('trippilot.repair.agent.validate_revision',return_value=CheckResult()), \
             patch('trippilot.repair.agent.chat_step') as model:
            result=repair_incremental({'uid':'t'},seed,ChangeEvent('budget_cut',new_budget=100),
                                      ModelConfig('fake','fake'),None)
        model.assert_not_called()
        self.assertEqual(result.plan['itinerary'][0]['activities'][0]['position'],'b')
        self.assertEqual(seed['itinerary'][0]['activities'][0]['position'],'a')
        self.assertEqual(result.usage['total_tokens'],0)
        self.assertTrue(result.metadata['atomic_commit'])

    def test_no_candidate_escalates_with_remaining_wall_budget(self):
        out=Trajectory('t','b2','fake','checked_ok',{},steps=[{'attempts':1}],usage={'total_tokens':90},
                       metadata={'usage_complete':False})
        with patch('trippilot.repair.agent.build_proposals',return_value=([],[],[])), \
             patch('trippilot.repair.agent.run_react',return_value=out) as model:
            result=repair_incremental({'uid':'t'},{'itinerary':[]},ChangeEvent('budget_cut',new_budget=100),
                                      ModelConfig('fake','fake'),None,max_seconds=10)
        self.assertLessEqual(model.call_args.kwargs['max_seconds'],10)
        self.assertEqual(result.usage['total_tokens'],90)
        self.assertFalse(result.metadata['usage_complete'])

    def test_public_pagination_reports_real_total_and_advances(self):
        from trippilot.tools.agent_tools import ToolBox
        box=ToolBox()
        first=box.call('intercity_transport_select',dict(start_city='Hangzhou',end_city='Shanghai',intercity_type='train'))
        second=box.call('next_page',{})
        self.assertGreater(first['data']['row_count'],10)
        self.assertTrue(second['data']['rows'])
        self.assertNotEqual(first['data']['rows'],second['data']['rows'])


class IntentAndJudgeTest(unittest.TestCase):
    seed={'itinerary':[{'day':1,'activities':[{'type':'train','TrainID':'G123'}]},
                       {'day':2,'activities':[]},{'day':3,'activities':[]}]}

    def test_conversational_changes_are_typed_without_model(self):
        event,usage=parse_change('提前一天回去',self.seed)
        self.assertEqual(event.new_days,2)
        self.assertEqual(usage['usage']['total_tokens'],0)
        event,_=parse_change('预算降到1,200元',self.seed)
        self.assertEqual(event.new_budget,1200)
        event,_=parse_change('G123 停运了',self.seed)
        self.assertEqual(event.target,'G123')

    def test_unknown_transport_and_multiple_changes_are_not_guessed(self):
        with self.assertRaises(ValueError):parse_change('G999 停运了',self.seed)
        with self.assertRaises(ValueError):parse_change('提前一天，预算降到900',self.seed)

    def test_offline_label_rewrite_changes_only_exact_day_equality(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'scripts/score_incremental.py'
        spec=importlib.util.spec_from_file_location('judge_under_test',path)
        judge=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(judge)
        changed,yes=judge.rewrite_day_annotation('result=(day_count(plan)==3)',2)
        namespace={'day_count':lambda p:2,'plan':{}}
        exec(changed,namespace)
        self.assertTrue(namespace['result'])
        self.assertTrue(yes)
        original='result=(day_count(plan)==3 and people_count(plan)==3)'
        self.assertEqual(judge.rewrite_day_annotation(original,2),(original,False))

    def test_permanent_api_error_is_not_retried(self):
        import httpx2
        from openai import APIStatusError
        response=httpx2.Response(403,request=httpx2.Request('POST','https://unit.invalid'))
        completions=SimpleNamespace(create=unittest.mock.Mock(side_effect=APIStatusError('blocked',response=response,body=None)))
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with self.assertRaises(ModelRequestError) as context:
            _call_with_retries(ModelConfig('fake','fake',max_attempts=3),client,{})
        self.assertEqual(context.exception.attempts,1)

    def test_product_parser_failure_keeps_attempts_and_unknown_usage(self):
        import tempfile
        import runpy
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            task=root/'task.json';seed=root/'seed.json';output=root/'result.json'
            task.write_text(json.dumps({'uid':'test','days':3}))
            seed.write_text(json.dumps(self.seed))
            argv=['revise_plan.py','--task',str(task),'--seed',str(seed),
                  '--change','an ambiguous change','--output',str(output)]
            error=ModelRequestError('busy',2,[0.1,0.2],RuntimeError('busy'))
            with patch.object(sys,'argv',argv), \
                 patch('trippilot.models.config.resolve_model_config',return_value=ModelConfig('fake','fake')), \
                 patch('trippilot.repair.intent.parse_change',side_effect=error), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
                runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/revise_plan.py'),run_name='__main__')
            self.assertEqual(context.exception.code,2)
            saved=json.loads(output.read_text())
            self.assertEqual(saved['request_count'],2)
            self.assertIsNone(saved['combined_usage'])
            self.assertIsNone(saved['plan'])


if __name__ == '__main__':
    unittest.main()
