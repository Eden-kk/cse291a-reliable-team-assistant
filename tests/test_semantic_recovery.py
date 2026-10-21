import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_semantic_recovery as runner
from team_assistant.dispatch import Outcome
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider


class SemanticRecoveryTests(unittest.TestCase):
    def test_fixed_protocol_and_holdout_rejection(self):
        registry = {'splits': {'development_seen': ['boutique/runtime/1']}}
        runner.preflight(runner.SPEC, registry)
        for key, value in [('physical_token_cap', 899999), ('diagnosis_rounds', 2),
                           ('case', 'boutique/runtime/2'), ('order', ['selective', 'restart', 'fallback'])]:
            changed = {**runner.SPEC, key: value}
            with self.assertRaises(ValueError):
                runner.preflight(changed, registry)
        with self.assertRaises(ValueError):
            runner.preflight(runner.SPEC, {'splits': {'development_seen': []}})

    def test_dirty_tree_fails_before_provider(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runner.subprocess, 'check_output', return_value=' M tracked.py'), \
             patch.object(runner, 'provider') as provider:
            with self.assertRaisesRegex(ValueError, 'Commit'):
                asyncio.run(runner.main(SimpleNamespace(output=Path(directory)/'run', vendor=Path(directory))))
            provider.assert_not_called()

    def run_fake(self, failure=False):
        vendor = os.environ.get('CLOUDOPS_VENDOR')
        if not vendor:
            self.skipTest('Set CLOUDOPS_VENDOR')
        class Fake:
            def __init__(self, path):
                self.records = []
                self.path = path
            async def complete(self, instruction, request):
                await asyncio.sleep(0)
                value = ({'queries': [{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}]}
                         if request['step'] == 'configuration_plan' else
                         {'queries': []} if request['step'] == 'telemetry_plan' else {'status': 'uncertain'})
                unknown = failure and self.path.name == 'restart'
                self.records.append({'tokens': None if unknown else 10,
                                     'request': copy.deepcopy(request), 'response': value})
                return Outcome(value, None if unknown else 10, 'unknown usage' if unknown else None)
        original = runner.subprocess.check_output
        def git(*args, **kwargs):
            return '' if 'status' in args[0] else original(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'run'
            with patch.object(runner, 'provider', side_effect=lambda p,b: PhysicalProvider(Fake(p), b)), \
                 patch.object(runner.subprocess, 'check_output', side_effect=git), \
                 patch.object(CodexProvider, 'complete', side_effect=AssertionError('Real model forbidden')) as real:
                asyncio.run(runner.main(SimpleNamespace(output=output, vendor=Path(vendor))))
                real.assert_not_called()
            return {str(p.relative_to(output)): json.loads(p.read_text()) for p in output.rglob('*.json')}

    def test_common_prefix_accounting_source_freshness_and_fallback(self):
        files = self.run_fake()
        summary = files['summary.json']
        self.assertEqual(summary['physical_tokens'], 130)
        self.assertEqual(len(summary['physical_settlements']), 13)
        self.assertEqual(summary['arms']['prefix']['logical_total_tokens'], 30)
        self.assertTrue(files['no-recovery/publication-check.json']['rejected'])
        self.assertEqual(files['fallback/intervention.json']['observed_gaps'],
                         {'configuration_evidence': 'incomplete'})
        historical = files['no-recovery/result.json']
        self.assertEqual(historical, files['prefix/result.json'])
        self.assertTrue(any(not o['fresh'] for o in files['no-recovery/source-audit.json']))
        for method, calls in [('restart', 3), ('selective', 1), ('fallback', 3)]:
            arm = summary['arms'][method]
            self.assertEqual(arm['state'], 'completed')
            self.assertEqual(arm['physical_incremental_tokens'], calls*10)
            self.assertEqual(arm['logical_total_tokens'], 30+calls*10)
            self.assertLessEqual(arm['logical_total_tools'], 24)
            self.assertEqual(len(arm['ledger']), 2)
            self.assertEqual(arm['ledger'][0], summary['arms']['prefix']['ledger'][0])
            self.assertEqual(arm['ledger'][1]['supersedes'], 1)
            self.assertTrue(all(o['fresh'] for o in files[f'{method}/source-audit.json']))
        self.assertTrue(summary['arms']['fallback']['provenance_fallback'])
        self.assertEqual(summary['arms']['restart']['logical_total_tools'],
                         summary['arms']['fallback']['logical_total_tools'])
        prevention = summary['arms']['source-consistency-from-start']
        self.assertEqual(prevention['logical_total_tokens'], 30)
        self.assertEqual(prevention['source_rereads'], 1)
        self.assertTrue(all(o['fresh'] for o in files['source-consistency-from-start/source-audit.json']))

    def test_unknown_usage_retained_and_later_arms_not_retried(self):
        summary = self.run_fake(failure=True)['summary.json']
        self.assertTrue(summary['physical_halted'])
        self.assertEqual(summary['arms']['restart']['state'], 'failed')
        for name in ('selective', 'fallback', 'source-consistency-from-start'):
            self.assertEqual(summary['arms'][name]['state'], 'skipped')
        self.assertGreaterEqual(summary['physical_tokens'], 60030)
