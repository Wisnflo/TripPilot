"""Regressions for upstream reset handling and consistent evidence reuse."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import httpx2
from openai import RateLimitError

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts'),str(ROOT/'eval')]
from trippilot.models.config import ModelConfig
from trippilot.models.openai_adapter import _call_with_retries,ModelRequestError
from run_suite import service_requires_stop
from report import render


def limited(headers=None,message='busy'):
    response=httpx2.Response(429,request=httpx2.Request('POST','https://unit.invalid'),headers=headers or {})
    return RateLimitError(message,response=response,body=None)


def client(outcomes):
    create=Mock(side_effect=outcomes)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),create


class RateRecoveryTest(unittest.TestCase):
    def test_server_reset_is_respected_before_retry(self):
        error=limited(message='Limit resets at: 1970-01-01 00:17:00 UTC')
        response=SimpleNamespace(usage=None);api,create=client([error,response])
        with patch('trippilot.models.openai_adapter.time.time',return_value=1000),patch('trippilot.models.openai_adapter.time.sleep') as sleep:
            result,_=_call_with_retries(ModelConfig('fake','unused',max_attempts=2),api,{})
        self.assertIs(result,response);self.assertEqual(create.call_count,2);sleep.assert_called_once_with(21.0)

    def test_retry_after_header_is_used_when_no_reset_message_exists(self):
        api,create=client([limited({'Retry-After':'7'}),SimpleNamespace(usage=None)])
        with patch('trippilot.models.openai_adapter.time.sleep') as sleep:_call_with_retries(ModelConfig('fake','unused',max_attempts=2),api,{})
        sleep.assert_called_once_with(8.0);self.assertEqual(create.call_count,2)

    def test_reset_beyond_task_budget_stops_without_sleep_or_new_attempt(self):
        api,create=client([limited({'Retry-After':'50'})])
        with patch('trippilot.models.openai_adapter.time.monotonic',return_value=100),patch('trippilot.models.openai_adapter.time.sleep') as sleep:
            with self.assertRaises(ModelRequestError):_call_with_retries(ModelConfig('fake','unused',max_attempts=2),api,{},deadline=110)
        sleep.assert_not_called();self.assertEqual(create.call_count,1)

    def test_error_text_does_not_expose_credentials_or_provider_key_identifier(self):
        secret='unit-secret-value';api,_=client([limited(message='api_key: provider-identifier token '+secret)])
        with self.assertRaises(ModelRequestError) as failure:_call_with_retries(ModelConfig('fake',secret,max_attempts=1),api,{})
        self.assertNotIn(secret,str(failure.exception));self.assertNotIn('provider-identifier',str(failure.exception))

    def test_recovered_rate_error_does_not_abort_a_successful_batch(self):
        self.assertFalse(service_requires_stop([429],'checked_ok'))
        self.assertTrue(service_requires_stop([429],'request_error'))
        self.assertTrue(service_requires_stop([403],'checked_ok'))

    def test_report_explicitly_attributes_inherited_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'experiments/v3/original-static';folder.mkdir(parents=True)
            fixture={'summary':{},'rows':[],'model':'fake','strata':{},'paired_statistics':{},'paired_successes':{}}
            (folder/'analysis.json').write_text(json.dumps(fixture))
            output=render(root,'amended','original')
            self.assertIn('1/6',output);self.assertIn('来自 `original`',output)
            self.assertIn('旧限流记录保留',output)


if __name__=='__main__':unittest.main()
