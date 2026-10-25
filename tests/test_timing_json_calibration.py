"""Transport-only followup keeps prompts fixed and stops on unsupported JSON mode."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT/'scripts'))
import run_timing_json_calibration as runner
from team_assistant.triton_provider import TritonProvider, BASE_URL

class JsonCalibrationTests(unittest.TestCase):
    def run_stage(self, unsupported=False):
        config, frozen = runner.load_stage()
        requests = []
        def post(body, key, timeout):
            requests.append(json.loads(body))
            if unsupported: return 400, {}, b'{"error":"unsupported response_format"}'
            response = {'usage':{'prompt_tokens':20,'completion_tokens':10},
                'choices':[{'finish_reason':'stop','message':{'content':'{"status":"uncertain"}'}}]}
            return 200, {}, json.dumps(response).encode()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'OPENAI_BASE_URL':BASE_URL,'OPENAI_API_KEY':'fake-key'}):
            out = Path(tmp)
            provider = TritonProvider(out/'calls',max_calls=3,maximum_usd='0.25',input_byte_cap=40000,json_object=True)
            with patch('team_assistant.triton_provider._post',side_effect=post):
                asyncio.run(runner.experiment(out,provider,config,frozen))
            summary = json.loads((out/'summary.json').read_text())
        return requests, summary, frozen
    def test_three_fixed_revised_requests(self):
        requests, summary, frozen = self.run_stage()
        self.assertEqual(len(requests),3)
        for body, (_,source) in zip(requests,frozen):
            self.assertEqual(body['response_format'],{'type':'json_object'})
            self.assertEqual(json.loads(body['messages'][1]['content']),source['request'])
            self.assertEqual(body['messages'][0]['content'],
                'No tools or files. Use supplied evidence only. Return one JSON object.\n'+source['instruction']+runner.TIMING_RULES)
        self.assertTrue(all(s['state']=='completed' for s in summary['assignments']))
    def test_unsupported_first_call_no_retry(self):
        requests, summary, _ = self.run_stage(True)
        self.assertEqual(len(requests),1)
        self.assertEqual([s['state'] for s in summary['assignments']],['failed','not_run','not_run'])

if __name__ == '__main__': unittest.main()
