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
            outcome = await PhysicalProvider(Fake(), budget).complete('', {})
            self.assertEqual(outcome.tokens, 0)
            self.assertEqual(len(budget.settlements), 1)
        asyncio.run(run())

    def test_changed_fresh_fetch_fails_closed(self):
        inv = fixture()
        inv.artifact('configuration_evidence', {'observations': [observation()]})
        inv.advance()
        changed = observation('o2')
        changed['text'] = 'unexpected'
        with self.assertRaises(ValueError):
            inv.artifact('configuration_evidence', {'observations': [changed]})

    def test_real_snapshot_fake_model_budgeted_recovery(self):
        import os
        import json
        from pathlib import Path
        from team_assistant.cloudops import Snapshot
        from team_assistant.dispatch import Dispatcher
        from team_assistant.memory import PolicyMemory, digest
        from team_assistant.runtime import Runtime
        vendor = os.environ.get('CLOUDOPS_VENDOR')
        if not vendor:
            self.skipTest('Set CLOUDOPS_VENDOR for explicit snapshot integration')
        class Fake:
            def __init__(self):
                self.records = []
            async def complete(self, instruction, request):
                self.records.append(request)
                if request['step'] == 'configuration_plan':
                    value = {'queries': [{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}]}
                elif request['step'] == 'telemetry_plan':
                    value = {'queries': []}
                else:
                    value = {'status': 'uncertain'}
                return Outcome(value, 10)
        async def run():
            root = Path(__file__).resolve().parents[1]
            registry = json.loads((root / 'configs/splits.json').read_text())
            memory = PolicyMemory()
            policy = {'rules': []}
            memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
            snapshot = Snapshot(vendor, 'boutique/runtime/1', registry, 'development')
            inv = RevisionInvestigation(Runtime(memory, Dispatcher(Budget(240000))), Fake(),
                                        snapshot, case='boutique/runtime/1', max_tools=24)
            await inv.run()
            import importlib.util
            spec = importlib.util.spec_from_file_location('phase2runner', root / 'scripts/run_phase2_revision.py')
            runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runner)
            self.assertTrue(inv.originals)
            self.assertEqual(inv.runtime.dispatcher.budget.spent, 30)
            initial_tools = len(snapshot.observations)
            self.assertIn('diagnosis_0', inv.advance())
            await inv.run(reuse=True)
            self.assertEqual(inv.runtime.dispatcher.budget.spent, 40)
            self.assertEqual(len(snapshot.observations), initial_tools + 1)
            self.assertEqual(inv.tool_budget_start, 0)
            inv.runtime.dispatcher.budget.spent = 190000
            branch = runner.recovery_branch(inv, Fake(), 240000)
            self.assertEqual(branch.runtime.dispatcher.budget.spent, 190000)
            self.assertEqual(runner.attempts(branch), runner.attempts(inv))
            self.assertFalse(branch.runtime.dispatcher.budget.affordable(60000))
            self.assertNotIn('diagnosis_0', inv.incident.store.invalid())
        asyncio.run(run())
