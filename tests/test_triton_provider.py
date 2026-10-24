import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from team_assistant.triton_provider import BASE_URL, TritonProvider


def response(**updates):
    data = {'usage': {'prompt_tokens': 20, 'completion_tokens': 10,
                      'prompt_tokens_details': {'cached_tokens': 5}},
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
    data.update(updates)
    return 200, {'x-litellm-response-cost': '0.0001', 'x-cache': 'hit'}, json.dumps(data).encode()


class TritonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'OPENAI_BASE_URL': BASE_URL, 'OPENAI_API_KEY': 'secret-test-key'})
        env.start()
        self.addCleanup(env.stop)
        self.output = Path(self.tmp.name) / 'calls'

    def provider(self, **kwargs):
        return TritonProvider(self.output, max_calls=kwargs.pop('max_calls', 2),
                              maximum_usd=kwargs.pop('maximum_usd', 1), **kwargs)

    async def test_valid_record_and_request_before_send(self):
        provider = self.provider()
        def send(body, key, timeout):
            self.assertTrue((self.output / '001.request.json').exists())
            payload = json.loads(body)
            self.assertEqual(payload['model'], 'api-glm-5.3')
            self.assertEqual(payload['max_tokens'], 8192)
            self.assertEqual(payload['reasoning_effort'], 'low')
            self.assertNotIn('tools', payload)
            self.assertEqual(timeout, 180)
            return response()
        with patch('team_assistant.triton_provider._post', side_effect=send) as transport:
            outcome = await provider.complete('instruction', {'evidence': 'x'})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(outcome.value, {'ok': True})
        self.assertEqual(outcome.tokens, 30)
        self.assertIsNone(outcome.error)
        self.assertEqual(provider.spent_usd, Decimal('0.0001'))
        self.assertEqual(provider.records[0]['finish_reason'], 'stop')
        self.assertEqual(provider.records[0]['usage']['prompt_tokens_details']['cached_tokens'], 5)
        self.assertTrue((self.output / '001.response.json').exists())

    async def test_preflight_no_send(self):
        provider = self.provider(input_byte_cap=1000)
        with patch('team_assistant.triton_provider._post') as send:
            result = await provider.complete('x', {'text': 'é' * 1000})
            self.assertEqual(result.tokens, 0)
            self.assertIsNotNone(result.error)
            send.assert_not_called()
        self.assertEqual(provider.next_id, 0)
        self.assertEqual(provider.spent_usd, 0)

    async def test_call_cap(self):
        provider = self.provider(max_calls=1)
        with patch('team_assistant.triton_provider._post', return_value=response()) as send:
            await provider.complete('x', {})
            result = await provider.complete('x', {})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result.error, 'Call cap exceeded')

    async def test_price_cap_before_send(self):
        provider = self.provider(maximum_usd='0.001')
        with patch('team_assistant.triton_provider._post') as send:
            result = await provider.complete('x', {})
        send.assert_not_called()
        self.assertEqual(result.tokens, 0)

    async def test_unknown_usage_halts_and_keeps_reserve(self):
        provider = self.provider()
        with patch('team_assistant.triton_provider._post', return_value=response(usage={})) as send:
            outcome = await provider.complete('x', {})
            await provider.complete('x', {})
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(outcome.tokens)
        self.assertTrue(provider.halted)
        self.assertEqual(provider.spent_usd, provider.reservation_usd)

    async def test_truncation_tools_invalid_json_halt(self):
        cases = [('length', {'content': '{}'}), ('stop', {'content': '{}', 'tool_calls': [{'id': '1'}]}),
                 ('stop', {'content': '[]'}), ('stop', {'content': '{"x":NaN}'}),
                 ('stop', {'content': 'not JSON'})]
        for index, (finish, message) in enumerate(cases):
            self.output = Path(self.tmp.name) / str(index)
            provider = self.provider()
            raw = response(choices=[{'finish_reason': finish, 'message': message}])
            with patch('team_assistant.triton_provider._post', return_value=raw):
                outcome = await provider.complete('x', {})
            self.assertTrue(provider.halted)
            self.assertIsNotNone(outcome.error)
            self.assertEqual(outcome.tokens, 30)

    async def test_error_redaction_and_no_retry(self):
        provider = self.provider()
        with patch('team_assistant.triton_provider._post', side_effect=RuntimeError('secret-test-key')) as send:
            outcome = await provider.complete('x', {})
            await provider.complete('x', {})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(outcome.error, 'RuntimeError')
        self.assertTrue(provider.halted)
        self.assertNotIn('secret-test-key', ''.join(p.read_text() for p in self.output.iterdir()))

    async def test_http_error_saved_redacted_and_halts(self):
        provider = self.provider()
        with patch('team_assistant.triton_provider._post', return_value=(401, {}, b'secret-test-key')):
            outcome = await provider.complete('x', {})
        self.assertTrue(provider.halted)
        self.assertIsNone(outcome.tokens)
        self.assertEqual((self.output / '001.response.json').read_text(), '[REDACTED]')

    async def test_exclusive_directory_and_validation(self):
        self.provider()
        with self.assertRaises(FileExistsError):
            self.provider()
        for kwargs in ({'input_byte_cap': True}, {'input_byte_cap': 60000}, {'max_calls': 0},
                       {'maximum_usd': 'NaN'}, {'maximum_usd': -1}, {'timeout': 181}):
            with self.assertRaises(ValueError):
                self.provider(**kwargs)
        with patch.dict(os.environ, {'OPENAI_BASE_URL': BASE_URL + '/'}):
            with self.assertRaises(ValueError):
                self.provider()

    async def test_parallel_calls_cannot_overspend(self):
        provider = self.provider(max_calls=1)
        with patch('team_assistant.triton_provider._post', return_value=response()) as send:
            await asyncio.gather(provider.complete('x', {}), provider.complete('y', {}))
        self.assertEqual(send.call_count, 1)

    async def test_cost_overrun_halts(self):
        provider = self.provider()
        status, _, raw = response()
        with patch('team_assistant.triton_provider._post', return_value=(status, {'x-litellm-response-cost': '2'}, raw)):
            result = await provider.complete('x', {})
        self.assertTrue(provider.halted)
        self.assertIsNotNone(result.error)
        self.assertEqual(provider.spent_usd, Decimal('2'))

    async def test_timeout_halts_without_retry(self):
        provider = self.provider(timeout=0.01)
        def slow(*args):
            import time
            time.sleep(0.03)
            return response()
        with patch('team_assistant.triton_provider._post', side_effect=slow) as send:
            result = await provider.complete('x', {})
            await provider.complete('x', {})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result.error, 'TimeoutError')
        self.assertEqual(provider.spent_usd, provider.reservation_usd)

    async def test_output_token_cap_halts_and_charges_known_usage(self):
        provider = self.provider()
        raw = response(usage={'prompt_tokens': 20, 'completion_tokens': 8193})
        with patch('team_assistant.triton_provider._post', return_value=raw):
            result = await provider.complete('x', {})
        self.assertTrue(provider.halted)
        self.assertEqual(result.tokens, 8213)
        self.assertEqual(provider.records[0]['finish_reason'], 'stop')
