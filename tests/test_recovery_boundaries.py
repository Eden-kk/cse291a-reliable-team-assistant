import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_recovery_boundaries as runner
from team_assistant.dispatch import Budget, Outcome
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider


class BoundaryTests(unittest.TestCase):
    def run_condition(self, name, unknown=False):
        vendor = os.environ.get('CLOUDOPS_VENDOR')
        if not vendor: self.skipTest('Set CLOUDOPS_VENDOR')
        class Fake:
            def __init__(self): self.records = []
            async def complete(self, instruction, request):
                await asyncio.sleep(0)
                step = request['step']
                value = {'queries': [{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}]} if step == 'configuration_plan' else {'queries': []} if step == 'telemetry_plan' else {'status': 'uncertain'}
                self.records.append({'request': request, 'response': value})
                return Outcome(value, None if unknown else 10, 'unknown' if unknown else None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/name
            physical = Budget(1200000)
            registry = json.loads((runner.ROOT/'configs/splits.json').read_text())
            with patch.object(runner, 'provider', side_effect=lambda p,b: PhysicalProvider(Fake(), b)), patch.object(CodexProvider, 'complete', side_effect=AssertionError('Live forbidden')):
                asyncio.run(runner.condition(path, name, Path(vendor), registry, 'test', physical))
            return {str(p.relative_to(path)): json.loads(p.read_text()) for p in path.rglob('*.json')}, physical

    def test_handoff_closure_and_cumulative_budgets(self):
        files, budget = self.run_condition('handoff-expiry')
        arms = files['summary.json']['arms']
        self.assertEqual(budget.spent, 80)
        self.assertEqual(arms['selective']['incremental_tokens'], 20)
        self.assertEqual(arms['selective']['logical_tokens'], 50)
        self.assertEqual(arms['restart']['incremental_tokens'], 30)
        self.assertEqual(set(files['notification.json']['invalid']), runner.EXPECTED['handoff-expiry'])
        self.assertTrue(files['notification.json']['obsolete_publication_rejected'])
        self.assertEqual(arms['selective']['ledger'][1]['supersedes'], 1)
        self.assertEqual(arms['prevention']['state'], 'not_applicable')

    def test_corruption_and_repair_against_unchanged_archive(self):
        files, budget = self.run_condition('wrong-content')
        self.assertEqual(budget.spent, 100)
        self.assertTrue(files['fault.json']['injected'])
        self.assertTrue(any(not o['matches_archive'] for o in files['no-recovery/source-audit.json']))
        for method in ('restart', 'selective', 'prevention'):
            self.assertTrue(all(o['matches_archive'] for o in files[method+'/source-audit.json']))
        self.assertEqual(files['summary.json']['arms']['selective']['logical_tokens'], 40)
        self.assertTrue(files['prevention/integrity-check.json']['repairs'])

    def test_unknown_usage_halts_without_retry(self):
        files, budget = self.run_condition('handoff-expiry', unknown=True)
        self.assertTrue(budget.halted)
        self.assertEqual(files['summary.json']['arms']['prefix']['state'], 'failed')
        self.assertEqual(files['summary.json']['arms']['selective']['state'], 'skipped')

    def test_all_expired_recomputes_every_artifact_with_equal_calls(self):
        files, budget = self.run_condition('all-expiry')
        self.assertEqual(budget.spent, 90)
        arms = files['summary.json']['arms']
        self.assertEqual(set(files['notification.json']['invalid']), runner.EXPECTED['all-expiry'])
        before = files['prefix/state.json']
        for arm in ('selective', 'restart'):
            self.assertEqual(arms[arm]['incremental_tokens'], 30)
            self.assertEqual(arms[arm]['logical_tokens'], 60)
            self.assertFalse(arms[arm]['invalid'])
            after = files[arm+'/state.json']
            self.assertEqual(set(after), set(before))
            for name in before:
                self.assertGreater(after[name]['version'], before[name]['version'])
        self.assertEqual(arms['selective']['tools'], arms['restart']['tools'])
        self.assertEqual(arms['prevention']['state'], 'not_applicable')
