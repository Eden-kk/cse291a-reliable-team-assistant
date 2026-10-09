import asyncio
import unittest
from team_assistant.admission import WorkflowAdmission
from team_assistant.dispatch import Budget, Dispatcher, Outcome


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_whole_workflow_and_failure_release(self):
        gate = WorkflowAdmission()
        entered = []
        blocker = asyncio.Event()
        async def first():
            async with gate.workflow('a'):
                entered.append('a')
                await blocker.wait()
                raise ValueError('controlled failure')
        async def other(name):
            async with gate.workflow(name):
                entered.append(name)
        a = asyncio.create_task(first())
        await asyncio.sleep(0)
        b, c = asyncio.create_task(other('b')), asyncio.create_task(other('c'))
        await asyncio.sleep(0)
        self.assertEqual(entered, ['a'])
        blocker.set()
        await asyncio.gather(a,b,c,return_exceptions=True)
        self.assertEqual(entered, ['a','b','c'])
        self.assertFalse(gate.active or gate.pending)

    async def test_cancel_waiter_does_not_leak_slot(self):
        gate = WorkflowAdmission()
        async def wait():
            async with gate.workflow('b'):
                self.fail('Cancelled waiter entered')
        async with gate.workflow('a'):
            b = asyncio.create_task(wait())
            await asyncio.sleep(0)
            b.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await b
            self.assertEqual(set(gate.active), {'a'})
            self.assertFalse(gate.pending)
        async with gate.workflow('c'):
            self.assertEqual(set(gate.active), {'c'})

    async def test_cancel_after_grant_before_resume(self):
        gate = WorkflowAdmission()
        async def wait():
            async with gate.workflow('b'):
                self.fail('Cancelled granted waiter entered')
        async with gate.workflow('a'):
            b = asyncio.create_task(wait())
            await asyncio.sleep(0)
        b.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await b
        self.assertFalse(gate.active or gate.pending)

    async def test_guard_registration_only_after_admission(self):
        gate = WorkflowAdmission()
        dispatch = Dispatcher(Budget(300), concurrency=2, foreground_reservation=60)
        entered = asyncio.Event()
        release = asyncio.Event()
        async def work(group):
            async with gate.workflow(group):
                with dispatch.response_workflow(group, calls=1):
                    async def invoke():
                        entered.set()
                        await release.wait()
                        return Outcome({}, 10)
                    return await dispatch.call(group, 'response', 60, invoke)
        a = asyncio.create_task(work('a'))
        await entered.wait()
        b = asyncio.create_task(work('b'))
        await asyncio.sleep(0)
        self.assertNotIn('b',dispatch.foreground_workflows)
        release.set()
        await asyncio.gather(a,b)
        await dispatch.join()
        self.assertEqual(dispatch.budget.spent,20)
        self.assertFalse(gate.active or gate.pending or dispatch.foreground_workflows)

    async def test_duplicate_and_invalid_limit(self):
        for value in (True,0,-1,1.5):
            with self.assertRaises(ValueError):
                WorkflowAdmission(value)
        gate = WorkflowAdmission()
        async with gate.workflow('a'):
            with self.assertRaises(ValueError):
                async with gate.workflow('a'):
                    pass

    async def test_cancelled_owner_cannot_release_reused_group(self):
        gate = WorkflowAdmission()
        async def wait():
            async with gate.workflow('b'):
                self.fail('Cancelled old owner entered')
        async with gate.workflow('a'):
            old = asyncio.create_task(wait())
            await asyncio.sleep(0)
            old.cancel()
        async with gate.workflow('b'):
            await asyncio.sleep(0)
            with self.assertRaises(asyncio.CancelledError):
                await old
            self.assertEqual(set(gate.active), {'b'})
        self.assertFalse(gate.active or gate.pending)
