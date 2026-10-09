"""Deterministic lifecycle and finite-budget checks for the opt-in guard."""
import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.dispatch import Budget, BudgetExhausted, Dispatcher, Outcome


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_protects_next_call_across_handoff(self):
        d = Dispatcher(Budget(100), foreground_reservation=40)
        first_started, release, handoff, proceed = [asyncio.Event() for _ in range(4)]
        order = []
        async def first():
            first_started.set()
            await release.wait()
            return Outcome({}, 30)
        async def invoke(label):
            order.append(label)
            return Outcome({}, 10)
        async def workflow():
            with d.response_workflow('fg', calls=2):
                await d.call('fg', 'response', 40, first)
                handoff.set()
                await proceed.wait()
                await d.call('fg', 'response', 40, lambda: invoke('next'))
        task = asyncio.create_task(workflow())
        await first_started.wait()
        bg = asyncio.create_task(d.call('bg', 'recovery', 40, lambda: invoke('background')))
        await asyncio.sleep(0)
        release.set()
        await handoff.wait()
        await asyncio.sleep(0)
        self.assertEqual(order, [])
        proceed.set()
        await asyncio.wait_for(asyncio.gather(task, bg), 1)
        self.assertEqual(order, ['next', 'background'])
        self.assertEqual((d.budget.spent, d.budget.reserved), (50, 0))
        self.assertEqual(d.foreground_workflows, {})

    async def test_no_unarrived_workflow_and_no_extra_hold_for_last_call(self):
        d = Dispatcher(Budget(80), foreground_reservation=40)
        entered, release = asyncio.Event(), asyncio.Event()
        async def initial():
            entered.set()
            await release.wait()
            return Outcome({}, 0)
        async def background():
            release.set()
            return Outcome({}, 0)
        async def workflow():
            with d.response_workflow('arrived', 1):
                await d.call('arrived', 'response', 40, initial)
        task = asyncio.create_task(workflow())
        await entered.wait()
        await asyncio.wait_for(d.call('bg', 'recovery', 40, background), 1)
        await task
        # A future group has no registration and cannot consume an allowance.
        self.assertEqual(d.foreground_workflows, {})

    async def test_cancel_during_handoff_releases_background(self):
        d = Dispatcher(Budget(60), foreground_reservation=40)
        entered = asyncio.Event()
        async def workflow():
            with d.response_workflow('fg', 2):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(workflow())
        await entered.wait()
        async def invoke(): return Outcome({}, 10)
        bg = asyncio.create_task(d.call('bg', 'recovery', 40, invoke))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        await asyncio.wait_for(bg, 1)
        self.assertEqual(d.foreground_workflows, {})

    async def test_inflight_cancel_keeps_usage_reservation(self):
        d = Dispatcher(Budget(100), foreground_reservation=40)
        entered, release = asyncio.Event(), asyncio.Event()
        async def invoke():
            entered.set()
            await release.wait()
            return Outcome({}, 17)
        async def workflow():
            with d.response_workflow('fg', 2):
                await d.call('fg', 'response', 40, invoke)
        task = asyncio.create_task(workflow())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(d.budget.reserved, 40)
        self.assertEqual(d.foreground_workflows, {})
        release.set()
        await asyncio.wait_for(d.join(), 1)
        self.assertEqual((d.budget.spent, d.budget.reserved), (17, 0))

    async def test_failure_and_unknown_usage_release_but_halt(self):
        for unknown in (False, True):
            d = Dispatcher(Budget(100), foreground_reservation=40)
            async def fail():
                if unknown: raise RuntimeError('unknown provider usage')
                return Outcome({}, 5, 'known provider failure')
            with self.assertRaises(RuntimeError):
                with d.response_workflow('fg', 2):
                    await d.call('fg', 'response', 40, fail)
            self.assertEqual(d.foreground_workflows, {})
            self.assertEqual(d.budget.halted, unknown)
            self.assertEqual(d.budget.spent, 40 if unknown else 5)
            if unknown:
                with self.assertRaises(BudgetExhausted):
                    await d.call('bg', 'recovery', 40, fail)

    async def test_budget_exhaustion_does_not_hang(self):
        d = Dispatcher(Budget(60), foreground_reservation=40)
        async def invoke(): return Outcome({}, 30)
        async def workflow():
            with d.response_workflow('fg', 2):
                await d.call('fg', 'response', 40, invoke)
                await d.call('fg', 'response', 40, invoke)
        with self.assertRaises(BudgetExhausted):
            await asyncio.wait_for(workflow(), 1)
        self.assertEqual(d.foreground_workflows, {})
        await asyncio.wait_for(d.join(), 1)

    async def test_mismatched_or_unregistered_calls_fail_closed(self):
        d = Dispatcher(Budget(100), foreground_reservation=40)
        with self.assertRaises(ValueError):
            await d.call('fg', 'response', 40, None)
        with d.response_workflow('fg', 1):
            with self.assertRaises(ValueError):
                await d.call('fg', 'response', 30, None)
            with self.assertRaises(ValueError):
                with d.response_workflow('fg', 1): pass
            async def invoke(): return Outcome({}, 1)
            await d.call('fg', 'response', 40, invoke)
            with self.assertRaises(ValueError):
                await d.call('fg', 'response', 40, invoke)
