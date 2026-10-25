"""Offline fixed-budget v4 wiring; fixture answers never establish model quality."""
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.records.append({'instruction': instruction, 'request': request, 'response': value})
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

    def test_admission_base_rejection_never_sends_original_candidate(self):
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            audit = runner.AuditedProvider(provider, tmp)
            inv = self.inv(audit)
            runner.load_saved(inv, self.saved(inv))
            rejected = runner.request_check('x', {'inputs': {'large': 'x' * 50000}})
            rejected.update(gate='base_with_omission_metadata_exceeds_cap', requested_ids=['observation-1'], admitted_ids=[])
            with patch.object(runner, 'admit_sources', return_value={'request': None, 'receipt': rejected, 'attempts': []}):
                with self.assertRaises(ValueError):
                    runner.admit(inv, 'x', {'inputs': {}}, ['observation-1'])
            self.assertEqual(provider.records, [])
            self.assertEqual(audit.attempts[0]['gate'], 'base_with_omission_metadata_exceeds_cap')
            self.assertTrue(audit.attempts[0]['zero_network'])

    def test_old_archive_source_outside_active_catalog_is_rejected(self):
        inv = self.inv(Provider())
        runner.load_saved(inv, self.saved(inv))
        inv.execute_queries([{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}])
        fresh = inv.snapshot.observations[-1]
        inv.artifact('source_catalog', {'sources': [fresh]})
        with self.assertRaisesRegex(ValueError, 'inactive'):
            runner.sources_for(inv, ['observation-1'])
        self.assertEqual(runner.sources_for(inv, [fresh['id']]), [fresh])

    def test_catalog_mapping_mismatch_is_rejected(self):
        inv = self.inv(Provider())
        runner.load_saved(inv, self.saved(inv))
        source = inv.snapshot.observations[0]
        for field, wrong in [('tool', 'GetAppYAML'), ('arguments', {'app_name': 'adservice'}),
                             ('sha256', '0' * 64)]:
            inv.artifact('source_catalog', {'sources': [{**source, field: wrong}]})
            with self.assertRaisesRegex(ValueError, 'mapping mismatch'):
                runner.sources_for(inv, [source['id']])

    def test_direct_uncertain_answer_cannot_cite_unread_sources(self):
        inv = self.inv(Provider())
        for extra in ({'evidence': [{'observation_id': 'observation-1', 'quote': 'unread'}]},
                      {'temporal_claims': [{'source_id': 'observation-1', 'source_field': 'text'}]}):
            answer = {'status': 'uncertain', 'reasoning': 'fixture', **extra}
            with self.assertRaisesRegex(ValueError, 'Unavailable or unsupported citation'):
                runner.finish(inv, answer, {'inputs': {}})

    def test_dispatch_budget_rejection_is_logical_attempt(self):
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            audit = runner.AuditedProvider(provider, tmp)
            inv = self.inv(audit)
            inv.runtime.dispatcher.budget.spent = inv.runtime.dispatcher.budget.limit
            with self.assertRaises(runner.BudgetExhausted):
                asyncio.run(runner.fixed_step(inv, 'fixture', 'x', {'inputs': {}}, ()))
            self.assertEqual(provider.records, [])
            self.assertEqual(audit.attempts[0]['gate'], 'logical_budget')

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

    def test_raw_visible_arm_has_final_only_instruction(self):
        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            backend = runner.AuditedProvider(provider, Path(tmp)/'attempts')
            asyncio.run(runner.matrix_experiment(Path(tmp), VENDOR,
                json.loads((ROOT/'configs/splits.json').read_text()), 'mock', backend))
            raw_calls = [record for record in provider.records
                if record['request']['step'] == 'coordinator_decision'
                and 'retrieved_sources' in record['request']['inputs']]
            self.assertEqual(len(raw_calls), 2)
            for record in raw_calls:
                self.assertNotIn('If raw evidence is needed', record['instruction'])
                self.assertIn('Do not return retrieve_source_ids', record['instruction'])


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
