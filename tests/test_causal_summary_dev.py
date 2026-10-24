"""Mock plumbing verifies isolation and does not count as semantic model evidence."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from team_assistant.dispatch import Outcome
ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(os.environ.get('CLOUDOPS_VENDOR', str(ROOT.parent/'preparation/291a-team-agent/vendor/Cloud-OpsBench')))
spec = importlib.util.spec_from_file_location('causal_dev', ROOT/'scripts/run_causal_summary_dev.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

class MockProvider:
    def __init__(self, adopt=True, eligible=True):
        self.records = []; self.halted = False; self.adopt = adopt; self.eligible = eligible
    async def complete(self, instruction, request):
        step = request['step']
        if step.endswith('_plan'):
            answer = {'queries': []}
        elif step == 'configuration_summary':
            answer = {'service': 'adservice', 'liveness_protocol': 'httpGet' if self.eligible else 'unknown',
                      'readiness_protocol': 'grpc', 'summary': 'mock extraction'}
        else:
            fact = request['inputs']['configuration_summary']['liveness_protocol'] if self.adopt else 'httpGet'
            answer = {'status': 'uncertain', 'adservice_liveness_protocol': fact,
                      'reasoning': 'fixture, not model evidence'}
        self.records.append({'step': step, 'response': answer, 'tokens': 10})
        return Outcome(answer, 10)

@unittest.skipUnless(VENDOR.exists(), 'requires seen snapshot')
class CausalTest(unittest.TestCase):
    def execute(self, provider):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            asyncio.run(runner.experiment(out, VENDOR, json.loads((ROOT/'configs/splits.json').read_text()), 'mock', provider))
            return {str(p.relative_to(out)): json.loads(p.read_text()) for p in out.rglob('*.json')}
    def test_paired_recovery_isolation_and_reuse(self):
        files = self.execute(MockProvider())
        summary = files['summary.json']; arms = summary['arms']
        self.assertEqual(summary['calls'], 11)
        self.assertTrue(arms['checks']['semantic_recovery_demonstrated'])
        self.assertEqual(len(arms['restart']['calls']), 4)
        self.assertEqual(len(arms['selective']['calls']), 2)
        self.assertIn('telemetry_evidence', files['selective/reuse.json']['valid_unchanged_artifacts'])
        self.assertEqual(files['restart/reuse.json']['valid_unchanged_artifacts'], [])
        self.assertTrue(arms['no-recovery']['stale_republication_rejected'])
        self.assertEqual(files['selective/ledger.json'][-1]['supersedes'], 1)
        self.assertEqual(files['selective/reuse.json']['same_notification'], files['restart/reuse.json']['same_notification'])
    def test_rejected_fault_not_counted_as_semantic_recovery(self):
        arms = self.execute(MockProvider(adopt=False))['summary.json']['arms']
        self.assertFalse(arms['checks']['wrong_fact_adopted'])
        self.assertFalse(arms['checks']['semantic_recovery_demonstrated'])
    def test_ineligible_is_retained_and_not_retargeted(self):
        summary = self.execute(MockProvider(eligible=False))['summary.json']
        self.assertEqual(summary['calls'], 4)
        self.assertEqual(summary['arms']['selective']['state'], 'not_injected')
