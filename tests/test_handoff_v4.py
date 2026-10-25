"""Offline fixed-budget v4 wiring; fixture answers never establish model quality."""
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import run_handoff_v4 as runner
from team_assistant.dispatch import Outcome
VENDOR = Path('/Users/yvette/26fall/preparation/291a-team-agent/vendor/Cloud-OpsBench')


class Provider:
    def __init__(self, invalid=False, fail_clean=False):
        self.records, self.halted, self.spent_usd = [], False, Decimal(0)
        self.invalid, self.fail_clean = invalid, fail_clean
    async def complete(self, instruction, request):
        step = request['step']
        if step.endswith('_plan'): value = {'queries': []}
        elif step == 'configuration_extraction':
            raw = request['inputs']['summary_source']['observations'][0]
            canonical = runner.parse_probe_source(raw, raw['source_version'])
            value = runner.expected_extraction(canonical)
            if self.invalid: value['probes']['liveness']['port'] = 999
        elif step == 'telemetry_summary': value = {'summary': 'Fixture uncertainty', 'source_ids': []}
        elif step == 'coordinator_decision' and 'Return final now' not in instruction:
            value = {'retrieve_source_ids': [request['inputs']['source_catalog']['sources'][-1]['id']]}
        else:
            value = {'status': 'uncertain', 'reasoning': 'Fixture uncertainty',
                     'adservice_liveness_protocol': 'unknown'}
        self.records.append({'request': request, 'response': value})
        if self.fail_clean and step == 'incident_brief': return Outcome({}, 0, 'fixture')
        return Outcome(value, 10)


@unittest.skipUnless(VENDOR.exists(), 'requires allowed seen-case snapshot')
class TestRunner(unittest.TestCase):
    def inv(self, provider):
        return runner.build(VENDOR, json.loads((ROOT/'configs/splits.json').read_text()), 'mock', provider)

    def saved(self, inv):
        inv.execute_queries([{'tool': 'GetAlerts', 'arguments': {}}])
        return {'case': runner.CASE, 'source_version': inv.snapshot.source_version,
                'created_at': '2026-10-24T00:00:00+00:00', 'prior_exposure': 'fixture seen',
                'observations': inv.snapshot.observations,
                'telemetry_summary': {'summary': 'Fixture', 'source_ids': []},
                'source_catalog': {'sources': [{'id': 'observation-1'}]},
                'requested_source_ids': ['observation-1']}

    def preflight(self, provider):
        with tempfile.TemporaryDirectory() as tmp:
            audit = runner.AuditedProvider(provider, Path(tmp)/'attempts')
            inv = self.inv(audit)
            result = asyncio.run(runner.preflight_experiment(Path(tmp), inv, self.saved(inv)))
            files = {str(p.relative_to(tmp)): json.loads(p.read_text()) for p in Path(tmp).rglob('*.json')}
            return result, files

    def test_three_calls_frozen_packet_and_zero_adoption_passes_engineering(self):
        provider = Provider()
        result, files = self.preflight(provider)
        self.assertTrue(result['engineering_gate'])
        self.assertEqual(len(provider.records), 3)
        self.assertFalse(result['field_observations']['corrupt_field_adoption'])
        pair = files['frozen-pair.json']
        self.assertEqual(pair['clean']['inputs']['retrieved_sources'], pair['corrupt']['inputs']['retrieved_sources'])
        self.assertEqual(result['saved_context_sha256'], runner.hashlib.sha256(json.dumps(files['saved-context.json'],sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2).encode()).hexdigest())
        self.assertEqual(len(files['clean/ledger.json']), 1)

    def test_invalid_extraction_retains_unattempted_denominators(self):
        provider = Provider(invalid=True)
        result, _ = self.preflight(provider)
        self.assertEqual(len(provider.records), 1)
        self.assertEqual(result['arms']['extraction']['state'], 'ineligible')
        self.assertEqual(result['arms']['clean']['state'], 'not_run')
        self.assertFalse(result['engineering_gate'])

    def test_clean_failure_stops_corrupt(self):
        provider = Provider(fail_clean=True)
        result, _ = self.preflight(provider)
        self.assertEqual(len(provider.records), 2)
        self.assertEqual(result['arms']['clean']['state'], 'failed')
        self.assertEqual(result['arms']['corrupt']['state'], 'not_run')

    def test_fixed_two_prefix_matrix_same_budget_and_superseding_publication(self):
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            backend = runner.AuditedProvider(provider, Path(tmp)/'attempts')
            result = asyncio.run(runner.matrix_experiment(Path(tmp), VENDOR,
                json.loads((ROOT/'configs/splits.json').read_text()), 'mock', backend))
            self.assertEqual(len(provider.records), 36)
            self.assertEqual(result[1]['recovery_order'], ['selective', 'restart'])
            for n in (1, 2):
                root = Path(tmp)/f'repeat-{n}'
                restart = json.loads((root/'restart/reuse.json').read_text())
                selective = json.loads((root/'selective/reuse.json').read_text())
                self.assertEqual(restart['inherited_budget'], selective['inherited_budget'])
                self.assertIn('telemetry_summary', selective['valid_unchanged_artifacts'])
                self.assertEqual(json.loads((root/'selective/ledger.json').read_text())[-1]['supersedes'], 1)


class TestAudits(unittest.TestCase):
    def test_oversize_is_logical_attempt_with_zero_network(self):
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            backend = runner.AuditedProvider(provider, tmp)
            outcome = asyncio.run(backend.complete('x', {'inputs': {'large': 'z' * 50000}}))
            self.assertIsNotNone(outcome.error)
            self.assertEqual(provider.records, [])
            self.assertTrue(backend.attempts[0]['zero_network'])
            self.assertEqual(backend.attempts[0]['physical_reserved_usd'], '0')
