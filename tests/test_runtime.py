import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.dispatch import Budget, Dispatcher, Outcome
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime, StaleAttempt


def promote(memory, version, rule):
    policy = {'check': rule}
    memory.promote('team', version, policy, {'accepted': True, 'sha256': digest(policy)})


async def backend(request):
    return Outcome({'query': request.get('policy', {}).get('check', 'metrics')}, 10)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.memory = PolicyMemory()
        promote(self.memory, 'v1', 'config')
        self.runtime = Runtime(self.memory, Dispatcher(Budget(2000)))
        self.incident = self.runtime.start('A', 'team', 'group-A')

    async def test_promotion_pins_existing_and_revoke_scopes_actual_consumers(self):
        r = self.runtime
        await r.step('A', 'telemetry', backend)
        await r.step('A', 'plan', backend, use_memory=True)
        await r.step('A', 'diagnosis', backend, dependencies=('plan', 'telemetry'))
        r.publish('A', 'diagnosis', lambda _: True)
        promote(self.memory, 'v2', 'events')
        r.start('C', 'team', 'group-C')
        self.assertFalse(self.incident.store.invalid())
        self.assertEqual(self.memory.runs['A']['version'], 'v1')
        self.assertEqual(r.revoke('team', 'v1')['A'], ['diagnosis', 'plan'])
        self.assertFalse(r.incidents['C'].store.invalid())
        with self.assertRaises(ValueError):
            r.publish('A', 'diagnosis', lambda _: True)
        self.memory.rebind('A')
        await r.step('A', 'plan', backend, use_memory=True, kind='recovery')
        await r.step('A', 'diagnosis', backend, dependencies=('plan', 'telemetry'), kind='recovery')
        r.publish('A', 'diagnosis', lambda _: True, supersedes=1)
        self.assertEqual(self.incident.store.results['telemetry'].version, 1)
        self.assertEqual(self.incident.store.results['plan'].payload['query'], 'events')
        self.assertEqual(len(self.incident.store.ledger), 2)
        self.assertEqual(self.incident.store.ledger[1]['supersedes'], 1)

    async def test_inflight_revoked_read_is_rejected_but_charged(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return Outcome({'query': 'old'}, 31)
        task = asyncio.create_task(self.runtime.step('A', 'plan', delayed, use_memory=True))
        await entered.wait()
        self.runtime.revoke('team', 'v1')
        release.set()
        with self.assertRaises(StaleAttempt):
            await task
        self.assertEqual(self.runtime.dispatcher.budget.spent, 31)
        self.assertNotIn('plan', self.incident.store.results)

    async def test_independent_inflight_survives_revocation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return Outcome({'metrics': 'fresh'}, 12)
        task = asyncio.create_task(self.runtime.step('A', 'telemetry', delayed))
        await entered.wait()
        self.runtime.revoke('team', 'v1')
        release.set()
        await task
        self.assertFalse(self.incident.store.invalid())

    async def test_gate_rejects_modified_candidate_and_scope(self):
        with self.assertRaises(ValueError):
            self.memory.promote('team', 'v2', {'check': 'changed'},
                                {'accepted': True, 'sha256': digest({'check': 'original'})})
        with self.assertRaises(ValueError):
            self.memory.activate('another-team', 'v1')

    async def test_rebind_does_not_hide_previous_consumers(self):
        await self.runtime.step('A', 'plan', backend, use_memory=True)
        promote(self.memory, 'v2', 'events')
        self.memory.rebind('A')
        self.assertEqual(self.runtime.revoke('team', 'v1')['A'], ['plan'])

    async def test_newer_attempt_wins(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return Outcome({'old': True}, 10)
        task = asyncio.create_task(self.runtime.step('A', 'plan', delayed))
        await entered.wait()
        await self.runtime.step('A', 'plan', backend)
        release.set()
        with self.assertRaises(StaleAttempt):
            await task
