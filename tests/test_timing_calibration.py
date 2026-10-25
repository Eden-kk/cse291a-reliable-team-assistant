"""Fake-provider checks for paired inputs, fixed assignment retention, and byte limits."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from team_assistant.dispatch import Outcome
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('timing_calibration', ROOT/'scripts/run_timing_calibration.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

class FakeProvider:
    def __init__(self, fail_at=None):
        self.records = []; self.halted = False; self.fail_at = fail_at
    async def complete(self, instruction, request):
        body = {'model':'api-glm-5.3', 'messages':[
            {'role':'system','content':'No tools or files. Use supplied evidence only. Return one JSON object.\n'+instruction},
            {'role':'user','content':json.dumps(request,allow_nan=False)}],
            'max_tokens':8192,'temperature':0,'reasoning_effort':'low','stream':False}
        assert len(json.dumps(body,separators=(',',':')).encode()) + 512 <= 40000
        self.records.append({'instruction':instruction,'request':request})
        if len(self.records) == self.fail_at:
            self.halted = True
            return Outcome({}, None, 'fake failure')
        return Outcome({'status':'uncertain','reasoning':'fixture'}, 10)

class CalibrationTests(unittest.TestCase):
    def run_pilot(self, fail_at=None):
        config, frozen = runner.load_inputs()
        provider = FakeProvider(fail_at)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(runner.experiment(Path(tmp),provider,config,frozen))
            result = json.loads((Path(tmp)/'summary.json').read_text())
        return provider, result
    def test_six_calls_equal_inputs_counterbalanced(self):
        provider, result = self.run_pilot()
        self.assertEqual(len(provider.records),6)
        self.assertEqual([r['variant'] for r in result['assignments']],
                         ['original','revised','revised','original','original','revised'])
        for i in (0,2,4):
            a,b = provider.records[i:i+2]
            self.assertEqual(a['request'],b['request'])
            self.assertEqual(abs(len(a['instruction'])-len(b['instruction'])),len(runner.TIMING_RULES))
        self.assertTrue(all(a['state']=='completed' for a in result['assignments']))
    def test_error_stops_without_retry_and_retains_slots(self):
        provider, result = self.run_pilot(2)
        self.assertEqual(len(provider.records),2)
        self.assertEqual([a['state'] for a in result['assignments']],
                         ['completed','failed','not_run','not_run','not_run','not_run'])
    def test_mutated_input_rejected_before_inference(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(ROOT/'configs',root/'configs')
            p = root/'configs/timing-calibration-inputs/item-08.json'
            p.write_text(p.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'hash mismatch'): runner.load_inputs(root)

if __name__ == '__main__': unittest.main()
