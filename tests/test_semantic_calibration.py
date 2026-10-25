"""Freeze, request parity, input cap and fail-closed checks without live inference."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import run_semantic_calibration as runner
from run_timing_json_calibration import load_stage as load_v4
from team_assistant.dispatch import Outcome

class FakeProvider:
    def __init__(self, fail=False): self.records=[]; self.halted=False; self.fail=fail
    async def complete(self, instruction, request):
        body = {'model':'api-glm-5.3','messages':[
            {'role':'system','content':'No tools or files. Use supplied evidence only. Return one JSON object.\n'+instruction},
            {'role':'user','content':json.dumps(request)}], 'response_format':{'type':'json_object'},
            'temperature':0,'max_tokens':8192,'reasoning_effort':'low','stream':False}
        assert len(json.dumps(body,separators=(',',':')).encode())+512 <=40000
        self.records.append({'instruction':instruction,'request':request})
        self.halted=self.fail
        return Outcome({},10,'fixture failure' if self.fail else None)

class SemanticCalibrationTests(unittest.TestCase):
    def run_stage(self,fail=False):
        config,frozen=runner.load_stage(); backend=FakeProvider(fail)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(runner.experiment(Path(tmp),backend,config,frozen))
            summary=json.loads((Path(tmp)/'summary.json').read_text())
        return backend,summary
    def test_inputs_unchanged_and_contract_applied_exactly_once(self):
        backend,summary=self.run_stage();_,previous=load_v4()
        self.assertEqual(len(backend.records),3)
        for record,(_,old) in zip(backend.records,previous):
            self.assertEqual(record['request'],old['request'])
            self.assertEqual(record['instruction'],old['instruction']+runner.SEMANTIC_CLAIM_CONTRACT+runner.TIMING_RULES)
        self.assertTrue(all(a['state']=='completed' for a in summary['assignments']))
    def test_no_retry_or_substitution(self):
        backend,summary=self.run_stage(True)
        self.assertEqual(len(backend.records),1)
        self.assertEqual([a['state'] for a in summary['assignments']],['failed','not_run','not_run'])
    def test_frozen_contract_hash_rejects_tuning(self):
        from unittest.mock import patch
        with patch.object(runner,'SEMANTIC_CLAIM_CONTRACT',runner.SEMANTIC_CLAIM_CONTRACT+' '):
            with self.assertRaises(AssertionError):runner.load_stage()

if __name__=='__main__': unittest.main()
