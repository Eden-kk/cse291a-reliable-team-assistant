"""Exercise real admission/dispatch orchestration with a fake inference provider."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from team_assistant.dispatch import Budget, Dispatcher, Outcome
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0,str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('admission_live',SCRIPTS/'run_admission_live.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeInvestigation:
    def __init__(self, runtime, physical, identifier, fail=False):
        self.runtime, self.physical, self.identifier = runtime,physical,identifier
        self.provider = SimpleNamespace(records=[])
        self.snapshot = SimpleNamespace(observations=[])
        self.tool_errors, self.tool_budget_start = [],0
        self.incident = SimpleNamespace(store=SimpleNamespace(results={}))
        self.deadline, self.fail = None,fail

    async def run(self,reuse=False):
        kind='recovery' if reuse else 'response'
        async def call():
            async def backend():
                self.physical.reserve(60000)
                await asyncio.sleep(0)
                self.physical.settle(60000,10)
                self.provider.records.append({'tokens':10})
                return Outcome({},10,'controlled failure' if self.fail else None)
            return await self.runtime.dispatcher.call(self.identifier,kind,60000,backend,self.deadline)
        if reuse:
            await call()
        else:
            outcomes=await asyncio.gather(call(),call(),return_exceptions=True)
            for result in outcomes:
                if isinstance(result,Exception):raise result
            await call()
        return {'contract_errors':[],'answer':{'status':'uncertain'}}


class LiveAdmissionRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def run_window(self, policy, fail=False):
        physical=Budget(600000)
        physical.spent=30
        base=SimpleNamespace(runtime=SimpleNamespace(dispatcher=Dispatcher(Budget(360000))))
        base.runtime.dispatcher.budget.spent=30
        def branch(*args):
            runtime=SimpleNamespace(dispatcher=Dispatcher(Budget(360000)),events=[])
            runtime.dispatcher.budget.spent=30
            return FakeInvestigation(runtime,physical,'recovery')
        def fresh(path,runtime,spec):
            return FakeInvestigation(runtime,physical,spec['group'], fail and spec['group']=='service')
        manifest={'logical_token_cap':360000,'recovery_case':'boutique/performance/1',
                  'foregrounds':[{'group':'service','case':'boutique/service/1','arrival_seconds':0},
                                 {'group':'runtime','case':'boutique/runtime/1','arrival_seconds':0}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE,'provider',return_value=None), patch.object(MODULE,'recovery_branch',side_effect=branch):
            result=await MODULE.window(Path(directory),base,physical,policy,manifest,fresh)
        self.assertTrue(result['lifecycle_drained'])
        self.assertEqual(result['logical_total_tokens'], physical.spent)
        self.assertEqual(result['incremental_physical_tokens'], sum(s['charged'] for s in result['settlements']))
        return result

    async def test_successful_three_group_lifecycle(self):
        result=await self.run_window('fair-admit-one')
        events=result['admission_events']
        self.assertEqual([e['group'] for e in events if e['event']=='admitted'],['service','runtime'])
        release=next(e['time'] for e in events if e['event']=='released' and e['group']=='service')
        start=next(e['time'] for e in result['dispatch_events'] if e['event']=='start' and e['group']=='runtime')
        self.assertGreaterEqual(start,release)
        self.assertEqual(result['incremental_physical_tokens'],70)

    async def test_failed_admitted_workflow_releases_next_group(self):
        result=await self.run_window('fair-admit-one',True)
        self.assertEqual(result['outcomes']['service']['state'],'failed')
        self.assertEqual(result['outcomes']['runtime']['state'],'completed')
        self.assertEqual(result['incremental_physical_tokens'],60)

    async def test_baseline_registers_without_admission(self):
        result=await self.run_window('fair-reserve')
        self.assertEqual(result['admission_events'],[])
        self.assertEqual(result['incremental_physical_tokens'],70)
