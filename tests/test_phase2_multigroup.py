"""No-model orchestration checks for bounded live scheduling trials."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('multigroup_trial', ROOT / 'scripts/run_phase2_multigroup.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
from team_assistant.dispatch import Budget, Dispatcher, Outcome
from team_assistant.phase2_budget import PhysicalProvider


class FakeProvider:
    def __init__(self, unknown=False):
        self.records = []
        self.unknown = unknown

    async def complete(self, instruction, request):
        await asyncio.sleep(0)
        tokens = None if self.unknown else 10000
        self.records.append({'tokens': tokens})
        return Outcome({}, tokens)


class FakeInvestigation:
    def __init__(self, runtime, provider, identifier):
        self.runtime, self.provider, self.identifier = runtime, provider, identifier
        self.snapshot = SimpleNamespace(observations=[])
        self.tool_errors, self.tool_budget_start = [], 0
        self.revision_events = []
        self.revisions = SimpleNamespace(heads={})
        self.deadline = None

    async def run(self, reuse=False):
        async def step():
            return await self.runtime.dispatcher.call(self.identifier,
                'recovery' if reuse else 'response', 60000,
                lambda: self.provider.complete('', {}), self.deadline)
        if not reuse:
            await asyncio.gather(step(), step())
        await step()
        return {'contract_errors': []}


class TrialTest(unittest.IsolatedAsyncioTestCase):
    async def window(self, policy, unknown=False):
        physical = Budget(900000)
        prefix_budget = Budget(200000)
        prefix_budget.spent = 50000
        base = SimpleNamespace(runtime=SimpleNamespace(dispatcher=Dispatcher(prefix_budget)))
        providers = []
        def make_provider(path, ledger):
            provider = PhysicalProvider(FakeProvider(unknown), ledger)
            providers.append(provider)
            return provider
        def clone(base, provider, cap):
            budget = Budget(cap)
            budget.spent = base.runtime.dispatcher.budget.spent
            return FakeInvestigation(SimpleNamespace(dispatcher=Dispatcher(budget)), provider, 'recovery')
        foreground_instances = []
        def fresh(path, runtime):
            inv = FakeInvestigation(runtime, make_provider(path, physical), 'foreground')
            foreground_instances.append(inv)
            return inv
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(trial, 'provider', make_provider), patch.object(trial, 'recovery_branch', clone), patch.object(trial, 'save'):
                summary = await asyncio.wait_for(trial.run_window(path, base, physical, policy, fresh, {}), 2)
            self.assertTrue((path / 'summary.json').exists())
            self.assertFalse(foreground_instances[0].runtime.dispatcher.foreground_workflows)
            return summary, physical

    async def test_all_policies_share_prefix_and_charge_physical_once(self):
        for policy in ('fifo', 'fair', 'fair-reserve'):
            result, physical = await self.window(policy)
            self.assertEqual(physical.spent, 40000)
            self.assertEqual(result['logical_total_tokens'], 90000)
            self.assertEqual(result['prefix_tokens'], 50000)
            self.assertEqual(result['incremental_physical_tokens'], 40000)
            self.assertTrue(all(v['state'] == 'completed' for v in result['outcomes'].values()))

    async def test_unknown_usage_halts_and_retains_failed_outcomes(self):
        result, physical = await self.window('fair-reserve', unknown=True)
        self.assertTrue(physical.halted)
        self.assertGreaterEqual(physical.spent, 60000)
        self.assertTrue(any(v['state'] == 'failed' for v in result['outcomes'].values()))
        self.assertEqual(physical.reserved, 0)

    async def test_existing_output_refused_before_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                await trial.main(SimpleNamespace(output=Path(directory)))

    def test_fixed_protocol_and_bound(self):
        protocol = json.loads(trial.PROTOCOL.read_text())
        self.assertEqual(protocol['physical_token_cap'], 900000)
        self.assertEqual(protocol['foreground_call_bound'], 5)
        self.assertEqual(len(protocol['policy_orders']), 2)
        for order in protocol['policy_orders']:
            self.assertEqual(set(order), {'fifo', 'fair', 'fair-reserve'})
