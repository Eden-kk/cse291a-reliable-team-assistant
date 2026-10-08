import asyncio
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from team_assistant.dispatch import Budget, BudgetExhausted, Dispatcher, Outcome


class BudgetTests(unittest.TestCase):
    def test_actual_releases_unused_reservation(self):
        b = Budget(100);b.reserve(60);b.reserve(40)
        self.assertFalse(b.affordable(1))
        b.settle(60, 20)
        self.assertTrue(b.affordable(40))
        self.assertEqual((b.spent, b.reserved), (20, 40))

    def test_unknown_or_overrun_stops_new_work(self):
        for actual in (None, 101):
            b = Budget(100);b.reserve(100);b.settle(100, actual)
            self.assertTrue(b.halted)
            self.assertFalse(b.affordable(1))
            self.assertGreaterEqual(b.spent, 100)


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_timestamps_use_ready_time(self):
        now = [10.0]
        d = Dispatcher(Budget(100), clock=lambda: now[0])
        async def invoke():
            now[0] = 14.0
            return Outcome({}, 5)
        await d.call("A", "response", 20, invoke, deadline=40)
        queued, started, finished = d.events
        self.assertEqual(queued["event"], "queued")
        self.assertEqual(queued["deadline"], 40)
        self.assertEqual(started["queue_seconds"], 0)
        self.assertEqual(finished["time"] - started["time"], 4)

    async def test_concurrency_and_all_costs(self):
        d = Dispatcher(Budget(100), concurrency=2)
        active = 0;peak = 0
        async def invoke():
            nonlocal active, peak
            active += 1;peak = max(peak, active)
            await asyncio.sleep(.002)
            active -= 1
            return Outcome({}, 10)
        await asyncio.gather(*(d.call(str(i), 'response', 20, invoke) for i in range(6)))
        self.assertEqual(peak, 2)
        self.assertEqual(d.budget.spent, 60)
        self.assertEqual(d.budget.reserved, 0)

    async def test_unaffordable_foreground_does_not_block_background(self):
        d = Dispatcher(Budget(15), concurrency=1)
        started = asyncio.Event();release = asyncio.Event()
        async def first():
            started.set();await release.wait();return Outcome({}, 5)
        a = asyncio.create_task(d.call('a', 'response', 10, first))
        await started.wait()
        b = asyncio.create_task(d.call('b', 'response', 20, lambda: None))
        async def background():return Outcome({'done': True}, 5)
        c = asyncio.create_task(d.call('c', 'learning', 5, background))
        await asyncio.sleep(0);release.set()
        results = await asyncio.gather(a,b,c,return_exceptions=True)
        self.assertIsInstance(results[1], BudgetExhausted)
        self.assertEqual(results[2].value, {'done': True})

    async def test_cancelled_inflight_still_charged(self):
        d = Dispatcher(Budget(100), concurrency=1)
        entered = asyncio.Event();release = asyncio.Event()
        async def invoke():entered.set();await release.wait();return Outcome({}, 27)
        task = asyncio.create_task(d.call('a', 'response', 50, invoke))
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(d.budget.reserved, 50)
        release.set();await d.join()
        self.assertEqual(d.budget.spent, 27)

    async def test_provider_failure_is_not_free(self):
        d = Dispatcher(Budget(100))
        async def failure():raise RuntimeError('provider usage unavailable')
        with self.assertRaises(RuntimeError):await d.call('a','response',40,failure)
        self.assertEqual(d.budget.spent,40)
        self.assertTrue(d.budget.halted)


if __name__ == '__main__':unittest.main()
