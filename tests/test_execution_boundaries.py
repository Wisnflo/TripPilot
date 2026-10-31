import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trippilot.money import multiply,plan_total,amount
from trippilot.execution.budget import ResourceBudget,activate,BudgetExceeded,_CURRENT
from trippilot.models.openai_adapter import _call_with_retries
from trippilot.models.config import ModelConfig
from trippilot.execution.supervisor import supervise,reset_cancellation


class ExecutionBoundaryTest(unittest.TestCase):
    def test_decimal_half_cent_rounding_and_exact_total(self):
        self.assertEqual(multiply('2.675',1),2.68)
        self.assertEqual(plan_total({'itinerary':[{'activities':[{'cost':0.1},{'cost':0.2}]}]}),amount('0.30'))

    def test_oversized_prompt_stops_before_request(self):
        token=activate(ResourceBudget(token_limit=1000))
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock())))
        try:
            with self.assertRaises(BudgetExceeded):_call_with_retries(ModelConfig('test','unused'),client,{'messages':[{'role':'user','content':'x'*5000}]})
            client.chat.completions.create.assert_not_called()
        finally:_CURRENT.reset(token)

    def test_tool_limit_counts_queries_before_they_execute(self):
        budget=ResourceBudget(tool_limit=1);budget.tool('first',{})
        with self.assertRaises(BudgetExceeded):budget.tool('second',{})
        self.assertEqual(budget.tool_calls,1)

    def test_os_policy_blocks_scoring_file_but_reads_public_input(self):
        visible=next((ROOT/'data/agent_visible/easy').glob('*.json'))
        secret=ROOT/'data/scoring/easy'/visible.name
        self.assertTrue(secret.is_file())
        (ROOT/'output').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'output') as directory:
            folder=Path(directory);script=folder/'probe.py'
            script.write_text('from pathlib import Path\nimport json\np=Path('+repr(str(visible))+')\ntry:p.read_text()\nexcept PermissionError:public_blocked=True\nelse:public_blocked=False\ntry:Path('+repr(str(secret))+').read_text()\nexcept PermissionError:secret_blocked=True\nelse:secret_blocked=False\nprint(json.dumps({"private_blocked":secret_blocked,"outside_job_input_blocked":public_blocked}))\nassert secret_blocked and public_blocked\n')
            reset_cancellation();out=supervise([sys.executable,str(script)],folder,10,restricted=True)
            self.assertEqual(out['returncode'],0,(folder/'worker.log').read_text())


if __name__=='__main__':unittest.main()
