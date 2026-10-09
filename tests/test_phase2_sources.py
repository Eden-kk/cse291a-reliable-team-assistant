import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('phase2_sources_control', ROOT / 'scripts/run_phase2_sources.py')
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class SourceControlTests(unittest.TestCase):
    def fixture(self):
        def artifact(name, inputs=None, observation=None):
            return {'version': 1, 'inputs': inputs or {}, 'payload':
                    {'observations': [observation]} if observation else {}}
        def obs(identifier, tool, text):
            return {'id': identifier, 'tool': tool, 'arguments': {}, 'text': text, 'truncated': False}
        state = {
            'initial': artifact('initial', observation=obs('o1', 'GetAlerts', 'alert')),
            'configuration_plan': artifact('configuration_plan', {'initial': 1}),
            'configuration_evidence': artifact('configuration_evidence', {'configuration_plan': 1}, obs('o2', 'GetAppYAML', 'port: 9555')),
            'telemetry_plan': artifact('telemetry_plan', {'initial': 1}),
            'telemetry_evidence': artifact('telemetry_evidence', {'telemetry_plan': 1}, obs('o3', 'GetRecentLogs', 'failed'))}
        return state, {'case': 'boutique/runtime/1', 'current': '9555', 'historical': '9556'}

    def test_sparse_staleness_keeps_independent_work_but_not_diagnosis(self):
        state, spec = self.fixture()
        before = copy.deepcopy(state)
        result = CONTROL.replay(state, spec, 'trusted_stale')
        self.assertEqual(state, before)
        self.assertEqual(result['invalidated'], ['configuration_evidence', 'diagnosis'])
        self.assertEqual(result['citation_only_invalidated'], ['configuration_evidence'])
        self.assertEqual(result['stale_observations'], 1)
        self.assertEqual(len(result['retained_unchanged']), 4)
        self.assertEqual(result['pending_semantic_recompute'], ['diagnosis'])
        self.assertTrue(result['late_commit_rejected'])
        self.assertTrue(result['publication_history_preserved'])
        obs = result['state_after_refresh']['configuration_evidence']['payload']['observations'][0]
        self.assertEqual(obs['text'], 'port: 9555')
        self.assertEqual(obs['source_revision'], 2)

    def test_dense_control_has_no_selective_saving(self):
        state, spec = self.fixture()
        result = CONTROL.replay(state, spec, 'dense_version_only')
        self.assertEqual(result['selective_recompute_nodes'], result['restart_nodes'])
        self.assertFalse(result['retained_unchanged'])
        self.assertEqual(result['refreshed_evidence'], ['initial'])

    def test_no_change_no_invalidation(self):
        state, spec = self.fixture()
        result = CONTROL.replay(state, spec, 'unchanged')
        self.assertFalse(result['invalidated'])
        self.assertFalse(result['late_commit_rejected'])
        self.assertEqual(result['authentic_observations'], result['metadata_checks'])

    def test_missing_fixed_content_does_not_retarget(self):
        state, spec = self.fixture()
        spec['current'] = 'absent'
        self.assertEqual(CONTROL.replay(state, spec, 'trusted_stale')['status'], 'not_injected')

    def test_truncated_history_is_rejected(self):
        state, spec = self.fixture()
        state['configuration_evidence']['payload']['observations'][0]['truncated'] = True
        with self.assertRaises(ValueError):
            CONTROL.replay(state, spec, 'trusted_stale')
