import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.sources import VersionedSources, source_key
from team_assistant.state import ExecutionStore, Result


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = VersionedSources('tenant-A/incident-1')
        self.raw = {'id': 'o1', 'tool': 'GetAppYAML', 'arguments': {'name': 'app'}, 'text': 'cpu: 200m'}
        self.old = self.catalog.record(self.raw)
        self.key = self.old['source_key']

    def test_trusted_old_record_is_authentic_but_stale(self):
        self.catalog.advance(self.key, {**self.raw, 'text': 'cpu: 800m'}, 'controlled configuration update')
        self.assertEqual(self.catalog.inspect(self.old), {'authentic': True, 'fresh': False, 'reason': 'stale'})
        fresh = self.catalog.current(self.key, 'o2')
        self.assertEqual(fresh['text'], 'cpu: 800m')
        self.assertTrue(self.catalog.inspect(fresh)['fresh'])
        with self.assertRaises(ValueError):
            self.catalog.dependencies([self.old])

    def test_version_only_change_still_expires_old_record(self):
        self.catalog.advance(self.key, self.raw, 'version-only control')
        self.assertFalse(self.catalog.events[-1]['content_changed'])
        self.assertFalse(self.catalog.inspect(self.old)['fresh'])

    def test_tampering_cannot_be_blessed_by_rehashing(self):
        fake = {**self.old, 'text': 'cpu: 800m'}
        self.assertFalse(self.catalog.inspect(fake)['authentic'])
        with self.assertRaises(ValueError):
            self.catalog.record(fake)

    def test_scope_query_and_revision_isolation(self):
        self.assertNotEqual(self.key, source_key('tenant-B/incident-1', self.raw))
        self.assertFalse(VersionedSources('tenant-B/incident-1').inspect(self.old)['authentic'])
        for revision in (True, [], '1'):
            self.assertFalse(self.catalog.inspect({**self.old, 'source_revision': revision})['authentic'])
        with self.assertRaises(ValueError):
            self.catalog.advance(self.key, {**self.raw, 'arguments': {'name': 'other'}}, 'update')

    def test_archive_and_return_values_do_not_alias(self):
        self.old['text'] = 'mutated'
        self.raw['text'] = 'also mutated'
        self.assertEqual(self.catalog.current(self.key, 'new')['text'], 'cpu: 200m')

    def test_dependency_closure_and_late_commit(self):
        store = ExecutionStore()
        self.catalog.sync(store)
        store.commit(Result('config', 1, {'observations': [self.old]}, sources=self.catalog.dependencies([self.old])), 0)
        store.commit(Result('telemetry', 1, {'healthy': True}), 0)
        store.commit(Result('answer', 1, {}, inputs={'config': 1, 'telemetry': 1}), 0)
        store.publish('answer', 0)
        self.catalog.advance(self.key, {**self.raw, 'text': 'cpu: 800m'}, 'controlled update')
        self.assertEqual(self.catalog.sync(store), {'config', 'answer'})
        with self.assertRaises(ValueError):
            store.commit(Result('late-answer', 1, {}, inputs={'config': 1}), 0)
        with self.assertRaises(ValueError):
            store.publish('answer', 0)
        fresh = self.catalog.current(self.key, 'o2')
        store.commit(Result('config', 2, {'observations': [fresh]}, sources=self.catalog.dependencies([fresh])), 0)
        self.assertEqual(store.invalid(), {'answer'})
        self.assertEqual(store.results['telemetry'].version, 1)
        self.assertEqual(store.ledger[0]['version'], 1)
        store.commit(Result('answer', 2, {}, inputs={'config': 2, 'telemetry': 1}), 0)
        store.publish('answer', 0, supersedes=1)
        self.assertEqual(len(store.ledger), 2)
        self.assertFalse(store.invalid())


class SourceInflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_update_rejects_old_inflight_answer_but_charges_it(self):
        import asyncio
        from team_assistant.dispatch import Budget, Dispatcher, Outcome
        from team_assistant.memory import PolicyMemory, digest
        from team_assistant.runtime import Runtime, StaleAttempt
        memory = PolicyMemory()
        policy = {'rules': []}
        memory.promote('team', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        runtime = Runtime(memory, Dispatcher(Budget(1000)))
        incident = runtime.start('incident', 'team', 'group')
        catalog = VersionedSources('team/incident')
        raw = {'id': 'o1', 'tool': 'GetAppYAML', 'arguments': {}, 'text': 'old'}
        old = catalog.record(raw)
        catalog.sync(incident.store)
        incident.store.commit(Result('config', 1, {'observations': [old]},
                                     sources=catalog.dependencies([old])), 0)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            self.assertEqual(request['inputs']['config']['observations'][0]['text'], 'old')
            entered.set()
            await release.wait()
            return Outcome({'answer': 'old'}, 23)
        task = asyncio.create_task(runtime.step('incident', 'answer', delayed, ('config',), reservation=100))
        await entered.wait()
        catalog.advance(old['source_key'], {**raw, 'text': 'new'}, 'update while request in flight')
        catalog.sync(incident.store)
        release.set()
        with self.assertRaises(StaleAttempt):
            await task
        self.assertEqual(runtime.dispatcher.budget.spent, 23)
        self.assertNotIn('answer', incident.store.results)
        self.assertTrue(any(e['event'] == 'discard' for e in runtime.events))
