"""Controlled real Investigation runs; no provider API or held-out cases."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, BudgetExhausted, Dispatcher, Outcome
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime, StaleAttempt
from team_assistant.state import Result

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'


class ControlledProvider:
    def __init__(self):
        self.records = []

    async def complete(self, instruction, request):
        self.records.append(copy.deepcopy(request))
        value = ({'queries': [{'tool': 'GetAppYAML', 'arguments': {'app_name': 'cartservice'}}]}
                 if request['step'].endswith('_plan')
                 else {'status': 'uncertain', 'reasoning': 'Controlled lifecycle test'})
        return Outcome(value, 10)


def investigation():
    registry = json.loads((ROOT / 'configs/splits.json').read_text())
    case = 'boutique/service/1'
    assert case in registry['splits']['development_seen']
    snapshot = Snapshot(VENDOR, case, registry, 'controlled-test')
    memory = PolicyMemory()
    policy = {'rules': []}
    memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
    runtime = Runtime(memory, Dispatcher(Budget(1000)))
    return Investigation(runtime, ControlledProvider(), snapshot, reservation=100,
                         max_tools=12, diagnosis_rounds=1)


@unittest.skipUnless(VENDOR.exists(), 'Benchmark unavailable')
class ProvenanceFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_prefix_selective_fallback_and_explicit_restart(self):
        rows = []
        for mode in ('selective', 'incomplete', 'missing_parent', 'restart'):
            inv = investigation()
            await inv.run()
            store = inv.incident.store
            inv.runtime.publish(inv.identifier, 'diagnosis_0', lambda _: True)
            prior = copy.deepcopy(store.ledger)
            before = {name: result.version for name, result in store.results.items()}
            if mode == 'incomplete':
                store.results['configuration_evidence'].complete = False
            elif mode == 'missing_parent':
                store.results['configuration_evidence'].inputs['unavailable_parent'] = 1
            elif mode == 'restart':
                inv.runtime.restart(inv.identifier, 'explicit comparison')
            else:
                store.invalidate(['configuration_evidence'])
            # Direct collect is also the RevisionInvestigation entrypoint.
            await inv.collect(reuse=True)
            result = await inv.diagnose()
            entry = inv.runtime.publish(inv.identifier, 'diagnosis_0', lambda _: True, supersedes=1)
            self.assertEqual(store.ledger[:1], prior)
            self.assertEqual(entry['supersedes'], 1)
            self.assertFalse(store.provenance_gaps())
            self.assertFalse(store.invalid())
            expected_calls = 4 if mode == 'selective' else 6
            self.assertEqual(len(inv.provider.records), expected_calls)
            self.assertEqual(result['budget']['spent'], expected_calls * 10)
            self.assertEqual(len(inv.snapshot.observations), 5 if mode == 'selective' else 8)
            if mode != 'selective':
                self.assertTrue(all(store.results[n].version > v for n, v in before.items()))
            else:
                self.assertEqual(store.results['telemetry_evidence'].version, 1)
            rows.append({'mode': mode, 'prefix_calls': 3, 'cumulative_calls': expected_calls,
                         'cumulative_simulated_tokens': result['budget']['spent'],
                         'cumulative_tools': len(inv.snapshot.observations),
                         'recovery_steps': [r['step'] for r in inv.provider.records[3:]],
                         'answer_status': result['answer']['status'],
                         'publication_count': len(store.ledger)})
        self.__class__.comparison_rows = rows

    async def test_old_inflight_is_rejected_and_charged_even_without_inputs(self):
        inv = investigation()
        await inv.run()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(request):
            entered.set()
            await release.wait()
            return Outcome({'old': True}, 17)
        task = asyncio.create_task(inv.runtime.step(inv.identifier, 'unrelated', delayed,
                                                    reservation=100))
        await entered.wait()
        inv.incident.store.results['initial'].complete = False
        try:
            await inv.collect(reuse=True)
        finally:
            release.set()
        with self.assertRaisesRegex(StaleAttempt, 'Full restart'):
            await task
        self.assertNotIn('unrelated', inv.incident.store.results)
        self.assertEqual(inv.runtime.dispatcher.budget.spent, 67)
        self.assertEqual(inv.runtime.dispatcher.budget.reserved, 0)

    async def test_model_budget_failure_preserves_invalid_state_and_publication(self):
        inv = investigation()
        await inv.run()
        store = inv.incident.store
        inv.runtime.publish(inv.identifier, 'diagnosis_0', lambda _: True)
        store.results['configuration_evidence'].complete = False
        inv.runtime.dispatcher.budget.limit = 100  # spent=30; cannot reserve another 100
        with self.assertRaises(BudgetExhausted):
            await inv.run(reuse=True)
        self.assertEqual(inv.runtime.dispatcher.budget.spent, 30)
        self.assertEqual(len(inv.provider.records), 3)
        self.assertEqual(len(store.ledger), 1)
        with self.assertRaises(ValueError):
            inv.runtime.publish(inv.identifier, 'diagnosis_0', lambda _: True, supersedes=1)

    async def test_fallback_run_cannot_replenish_tool_allowance(self):
        inv = investigation()
        await inv.run()
        self.assertEqual(len(inv.snapshot.observations), 4)
        inv.max_tools = 4
        inv.incident.store.results['configuration_evidence'].complete = False
        result = await inv.run(reuse=True)
        self.assertEqual(len(inv.snapshot.observations), 4)
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['answer']['status'], 'uncertain')
        self.assertEqual(inv.tool_budget_start, 0)
        await inv.run(reuse=True)
        self.assertEqual(inv.tool_budget_start, 0)
        self.assertEqual(len(inv.snapshot.observations), 4)

    async def test_selective_run_cannot_replenish_tool_allowance(self):
        inv = investigation()
        await inv.run()
        inv.max_tools = 4
        inv.incident.store.invalidate(['configuration_evidence'])
        result = await inv.run(reuse=True)
        self.assertEqual(len(inv.snapshot.observations), 4)
        self.assertEqual(inv.tool_budget_start, 0)
        self.assertEqual(inv.incident.store.results['configuration_evidence'].payload['observations'], [])
        self.assertEqual(inv.incident.store.results['telemetry_evidence'].version, 1)
        self.assertEqual(result['budget']['spent'], 40)

    async def test_retired_unneeded_artifact_does_not_force_repeated_restarts(self):
        inv = investigation()
        await inv.run()
        store = inv.incident.store
        store.commit(Result('old_optional_step', 1, {}, complete=False), store.epoch)
        await inv.run(reuse=True)
        self.assertIn('old_optional_step', store.invalid())
        self.assertFalse(store.results['old_optional_step'].complete)
        self.assertFalse(store.provenance_gaps())
        await inv.run(reuse=True)
        self.assertEqual(store.restart_generation, 1)
        inv.runtime.publish(inv.identifier, 'diagnosis_0', lambda _: True)
        with self.assertRaises(ValueError):
            inv.runtime.publish(inv.identifier, 'old_optional_step', lambda _: True)

    async def test_unobservable_omission_is_not_claimed_detectable(self):
        inv = investigation()
        await inv.run()
        inv.incident.store.results['configuration_evidence'].inputs.clear()
        self.assertFalse(inv.incident.store.provenance_gaps())


if __name__ == '__main__':
    import argparse
    import subprocess
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError('Refuse to overwrite validation report')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProvenanceFallbackTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'code_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
              'backend': 'controlled provider; fixed uncertain response, simulated token usage',
              'real_model_calls': 0, 'case': 'boutique/service/1',
              'tests_run': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped),
              'comparison': getattr(ProvenanceFallbackTests, 'comparison_rows', []),
              'checks': ['old input-free in-flight commit rejected, usage charged',
                         'exhausted model budget preserves old ledger and blocks publication',
                         'fallback does not replenish tool allowance',
                         'unrecorded implicit dependencies not claimed detectable']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
