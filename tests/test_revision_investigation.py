import asyncio
import hashlib
import unittest
from types import SimpleNamespace
from team_assistant.dispatch import Budget, Outcome, BudgetExhausted
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.revision_investigation import RevisionInvestigation
from team_assistant.sources import VersionedSources
from team_assistant.state import ExecutionStore


def fixture():
    inv = RevisionInvestigation.__new__(RevisionInvestigation)
    inv.incident = SimpleNamespace(store=ExecutionStore())
    inv.snapshot = SimpleNamespace(source_version='snapshot-v1')
    inv.source = 'snapshot'
    inv.incident.store.source_versions['snapshot'] = 'snapshot-v1'
    inv.revisions = VersionedSources('test')
    inv.transform = ('9555', '9556')
    inv.old_phase, inv.originals, inv.revision_events = True, {}, []
    return inv


def observation(identifier='o1'):
    return {'id': identifier, 'tool': 'GetAppYAML', 'arguments': {'name': 'app'},
            'text': 'port 9555', 'truncated': False, 'sha256': hashlib.sha256(b'port 9555').hexdigest()}


class RevisionTests(unittest.TestCase):
    def test_old_authentic_then_stale_and_new_unique_identity(self):
        inv = fixture()
        inv.artifact('configuration_evidence', {'observations': [observation()]})
        old = inv.incident.store.results['configuration_evidence'].payload['observations'][0]
        self.assertEqual(old['text'], 'port 9556')
        self.assertEqual(old['sha256'], hashlib.sha256(b'port 9556').hexdigest())
        self.assertTrue(inv.revisions.inspect(old)['fresh'])
        self.assertIn('configuration_evidence', inv.advance())
        self.assertTrue(inv.revisions.inspect(old)['authentic'])
        self.assertFalse(inv.revisions.inspect(old)['fresh'])
        inv.artifact('configuration_evidence', {'observations': [observation('o2')]})
        new = inv.incident.store.results['configuration_evidence'].payload['observations'][0]
        self.assertEqual(new['text'], 'port 9555')
        self.assertNotEqual(new['id'], old['id'])
        self.assertFalse(inv.incident.store.invalid())

    def test_overlap_and_truncation_fail_closed(self):
        inv = fixture()
        inv.artifact('telemetry_evidence', {'observations': [observation()]})
        with self.assertRaises(ValueError):
            inv.artifact('configuration_evidence', {'observations': [observation('o2')]})
        inv = fixture()
        obs = observation()
        obs['truncated'] = True
        with self.assertRaises(ValueError):
            inv.artifact('configuration_evidence', {'observations': [obs]})

    def test_unknown_usage_halts_all_arms(self):
        class Fake:
            records = []
            async def complete(self, *args):
                return Outcome({}, None, 'timeout')
        async def run():
            budget = Budget(240000)
            await PhysicalProvider(Fake(), budget).complete('', {})
            self.assertEqual(budget.spent, 60000)
            with self.assertRaises(BudgetExhausted):
                await PhysicalProvider(Fake(), budget).complete('', {})
        asyncio.run(run())
