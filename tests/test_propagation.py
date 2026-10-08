import copy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.propagation import corrupt_port, verify_inputs
from team_assistant.investigation import Investigation
from team_assistant.state import ExecutionStore, Result


class PropagationTests(unittest.TestCase):
    def fixture(self):
        obs = {'id': 'o1', 'text': 'port: 9555', 'tool': 'GetAppYAML', 'arguments': {}}
        store = ExecutionStore()
        store.commit(Result('configuration_evidence', 1, {'observations': [copy.deepcopy(obs)]}), 0)
        return SimpleNamespace(incident=SimpleNamespace(store=store),
                               snapshot=SimpleNamespace(observations=[obs], fingerprint=lambda: 'v', source_version='v'))

    def test_corruption_reaches_contract_and_source_stays_clean(self):
        inv = self.fixture()
        self.assertTrue(corrupt_port(inv)['injected'])
        consumed = Investigation.active_observations(inv, ['configuration_evidence'])
        self.assertEqual(consumed[0]['text'], 'port: 9556')
        self.assertEqual(inv.snapshot.observations[0]['text'], 'port: 9555')
        audit = verify_inputs(inv, ['configuration_evidence'])
        self.assertEqual(len(audit['repairs']), 1)
        self.assertEqual(Investigation.active_observations(inv, ['configuration_evidence'])[0]['text'], 'port: 9555')
        self.assertFalse(verify_inputs(inv, ['configuration_evidence'])['repairs'])

    def test_absent_port_does_not_invent_fault(self):
        inv = self.fixture()
        inv.incident.store.results['configuration_evidence'].payload['observations'][0]['text'] = 'port: 80'
        self.assertFalse(corrupt_port(inv)['injected'])

    def test_unknown_identity_fails_closed(self):
        inv = self.fixture()
        inv.incident.store.results['configuration_evidence'].payload['observations'][0]['id'] = 'unknown'
        with self.assertRaises(ValueError):
            verify_inputs(inv, ['configuration_evidence'])
