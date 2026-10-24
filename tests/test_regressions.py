import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.dispatch import Budget, Dispatcher, Outcome
from team_assistant.state import ExecutionStore, Result


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_commit_rechecks_transitive_source_and_shared_changes(self):
        for kind in ('source_versions', 'shared_versions'):
            with self.subTest(kind=kind):
                store = ExecutionStore()
                versions = getattr(store, kind)
                versions['input'] = 1
                metadata = {'sources' if kind == 'source_versions' else 'shared_reads':
                            {'input': 1}}
                store.commit(Result('root', 1, {}, **metadata), 0)
                store.commit(Result('branch', 1, {}, inputs={'root': 1}), 0)
                store.commit(Result('independent', 1, {}), 0)
                parents = {'independent': 1, 'branch': 1}
                store.commit(Result('answer', 1, {}, inputs=parents), 0)
                versions['input'] = 2
                with self.assertRaisesRegex(ValueError, 'Invalid parent'):
                    store.commit(Result('answer', 2, {}, inputs=parents), 0)
                self.assertEqual(store.results['answer'].version, 1)
                store.commit(Result('independent', 2, {}), 0)

    async def test_provider_cancelled_settles_and_halts(self):
        dispatcher = Dispatcher(Budget(100))
        async def cancelled():
            raise asyncio.CancelledError()
        with self.assertRaisesRegex(RuntimeError, 'usage unknown'):
            await asyncio.wait_for(dispatcher.call('A', 'response', 40, cancelled), 1)
        await dispatcher.join()
        self.assertEqual(dispatcher.budget.reserved, 0)
        self.assertEqual(dispatcher.budget.spent, 40)
        self.assertTrue(dispatcher.budget.halted)
        self.assertFalse(dispatcher.running)

    async def test_cancelled_join_does_not_cancel_provider(self):
        entered, release = asyncio.Event(), asyncio.Event()
        dispatcher = Dispatcher(Budget(100))
        async def invoke():
            entered.set()
            await release.wait()
            return Outcome({}, 15)
        caller = asyncio.create_task(dispatcher.call('A', 'response', 40, invoke))
        await entered.wait()
        joiner = asyncio.create_task(dispatcher.join())
        await asyncio.sleep(0)
        joiner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await joiner
        self.assertEqual(dispatcher.budget.reserved, 40)
        release.set()
        await caller
        await dispatcher.join()
        self.assertEqual(dispatcher.budget.spent, 15)

    async def test_aged_background_competes_with_one_worker(self):
        time = [0.0]
        dispatcher = Dispatcher(Budget(1000), concurrency=1, age_seconds=10, clock=lambda: time[0])
        release = asyncio.Event()
        order = []
        async def blocked():
            await release.wait()
            return Outcome({}, 1)
        first = asyncio.create_task(dispatcher.call('A', 'response', 40, blocked))
        await asyncio.sleep(0)
        async def record(label):
            order.append(label)
            return Outcome({}, 1)
        background = asyncio.create_task(dispatcher.call('B', 'learning', 40, lambda: record('background')))
        await asyncio.sleep(0)
        time[0] = 100
        foreground = asyncio.create_task(dispatcher.call('C', 'response', 40, lambda: record('foreground')))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, background, foreground)
        self.assertEqual(order, ['background', 'foreground'])

    async def test_direct_and_transitive_cycles_rejected(self):
        store = ExecutionStore()
        store.commit(Result('A', 1, {}), 0)
        store.commit(Result('B', 1, {}, inputs={'A': 1}), 0)
        for inputs in ({'A': 1}, {'B': 1}):
            with self.assertRaisesRegex(ValueError, 'cycle'):
                store.commit(Result('A', 2, {}, inputs=inputs), 0)
        self.assertFalse(store.invalid())


    async def test_simultaneous_burst_is_ranked_before_starting_work(self):
        dispatcher = Dispatcher(Budget(1000), concurrency=2)
        order = []
        async def invoke(label):
            order.append(label)
            await asyncio.sleep(0)
            return Outcome({}, 1)
        await asyncio.gather(
            dispatcher.call('A', 'recovery', 40, lambda: invoke('A1')),
            dispatcher.call('A', 'recovery', 40, lambda: invoke('A2')),
            dispatcher.call('C', 'response', 40, lambda: invoke('C1')),
            dispatcher.call('C', 'response', 40, lambda: invoke('C2')))
        self.assertEqual(order[:2], ['C1', 'C2'])
        await dispatcher.join()
        self.assertEqual(dispatcher.budget.spent, 4)
