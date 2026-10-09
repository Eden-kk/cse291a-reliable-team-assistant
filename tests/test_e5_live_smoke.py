import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_e5_live_smoke as runner
from team_assistant.dispatch import Outcome
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider


class E5LiveSmokeTests(unittest.TestCase):
    def test_changed_budget_or_accepted_receipt_rejected(self):
        spec = json.loads((runner.ROOT / 'configs/phase2/e5-live-smoke-v2.json').read_text())
        registry = json.loads((runner.ROOT / 'configs/splits.json').read_text())
        runner.preflight(spec, registry, {'accepted': False})
        with self.assertRaises(ValueError): runner.preflight(spec, registry, {'accepted': True})
        spec['shared_logical_token_cap'] = 360000
        with self.assertRaises(ValueError): runner.preflight(spec, registry, {'accepted': False})

    def test_real_snapshot_fake_provider_connected_lifecycle(self):
        vendor = os.environ.get('CLOUDOPS_VENDOR')
        if not vendor: self.skipTest('Set CLOUDOPS_VENDOR')
        query_loop = getattr(self, 'query_loop', False)
        class Fake:
            def __init__(self): self.records = []
            async def complete(self, instruction, request):
                await asyncio.sleep(0)
                if request['step'] == 'configuration_plan':
                    value = {'queries': [{'tool':'GetAppYAML','arguments':{'app_name':'adservice'}}]}
                elif request['step'] == 'telemetry_plan': value = {'queries':[]}
                else: value = {'queries':[]} if query_loop else {'status':'uncertain','reasoning':'Controlled missing evidence'}
                self.records.append({'request':request,'response':value,'tokens':10})
                return Outcome(value,10)
        original = runner.subprocess.check_output
        def git(*args, **kwargs):
            if 'status' in args[0]: return ''
            return original(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            spec = json.loads((runner.ROOT/'configs/phase2/e5-live-smoke-v2.json').read_text())
            spec['preparation_tokens_already_spent'] = 0
            protocol = Path(directory)/'mock-protocol.json'
            protocol.write_text(json.dumps(spec))
            with patch.object(runner,'provider',side_effect=lambda path,budget: PhysicalProvider(Fake(),budget)), \
                 patch.object(runner.subprocess,'check_output',side_effect=git), \
                 patch.object(CodexProvider,'complete',side_effect=AssertionError('Real inference forbidden in tests')) as real:

                asyncio.run(runner.main(SimpleNamespace(output=output,vendor=Path(vendor),protocol=protocol)))
                real.assert_not_called()
            summary = json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['physical_tokens'],100 if query_loop else 70)
            self.assertTrue(summary['rejected_candidate_not_activated'])
            self.assertEqual(len(summary['ledgers']['recovery']),2)
            self.assertEqual(summary['ledgers']['recovery'][1]['supersedes'],1)
            self.assertFalse(summary['ledgers']['recovery'][1]['payload']['quality_approved'])
            self.assertEqual(len(summary['ledgers']['foreground']),1)
            transition = json.loads((output/'transition.json').read_text())
            self.assertTrue(transition['stale_republish_blocked'])
            self.assertIn('public-status',transition['invalid'])

    def test_two_diagnosis_round_limit_even_when_model_keeps_querying(self):
        self.query_loop = True
        self.test_real_snapshot_fake_provider_connected_lifecycle()
