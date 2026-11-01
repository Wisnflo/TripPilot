"""Accepted revision constraints must survive the next product operation."""
from decimal import Decimal
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from trip import worker
from trippilot.models.config import ModelConfig
from trippilot.agent.loop import Trajectory
from trippilot.repair.events import ChangeEvent


class CommittedTaskTest(unittest.TestCase):
    def execute(self,task,seed,event,terminal='checked_ok'):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'job.json'
            path.write_text(json.dumps(dict(command='revise',model='test',seconds=10,request_timeout=2,attempts=1,
                task=task,seed=seed,seed_path=str(Path(directory)/'seed.json'),change='requested change')))
            out=Trajectory(task['uid'],'patch','test',terminal,seed if terminal=='checked_ok' else None)
            with patch.dict('os.environ'),patch('trippilot.models.config.resolve_model_config',return_value=ModelConfig('test','test-key')),patch('trippilot.repair.intent.parse_change',return_value=(event,{})),patch('trippilot.repair.agent.repair_incremental',return_value=out) as repair:
                worker(path)
            return json.loads(path.with_name('worker-result.json').read_text()),repair.call_args.args[0]

    def task_and_seed(self):
        task=dict(uid='chain',start_city='杭州',target_city='杭州',days=2,people_number=1,nature_language='预算500元，去公园。')
        seed=dict(start_city='杭州',target_city='杭州',people_number=1,itinerary=[{'day':1,'activities':[]},{'day':2,'activities':[]}])
        return task,seed

    def test_accepted_budget_is_retained_by_the_next_revision(self):
        task,seed=self.task_and_seed();original=copy.deepcopy(task)
        first,_=self.execute(task,seed,ChangeEvent('budget_cut',new_budget=Decimal('400')))
        self.assertIn('预算400元',first['task']['nature_language']);self.assertEqual(task,original)
        second,seen=self.execute(first['task'],seed,ChangeEvent('transport_unavailable',target='G1'))
        self.assertIn('预算400元',seen['nature_language']);self.assertIn('预算400元',second['task']['nature_language'])
        self.assertEqual(first['state']['task'],original)

    def test_failed_revision_does_not_commit_new_requirements(self):
        task,seed=self.task_and_seed()
        result,_=self.execute(task,seed,ChangeEvent('budget_cut',new_budget=Decimal('400')),terminal='request_error')
        self.assertEqual(result['task']['nature_language'],task['nature_language'])
        self.assertIsNone(result['plan'])
