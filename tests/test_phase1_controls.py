"""Offline harness contracts; timing values themselves are not performance assertions."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_phase1_controls import run_controls, tracking_overhead


class Phase1ControlTests(unittest.TestCase):
    def test_simulations_and_measurement_scope(self):
        report = asyncio.run(run_controls(size=9, repeats=2))
        self.assertTrue(report['passed'])
        self.assertEqual(report['live_model_calls'], 0)
        controls = report['controls']
        for name in ('timeout_unknown_usage_stop', 'unknown_usage_stop'):
            self.assertTrue(controls[name]['halted'])
            self.assertTrue(controls[name]['queued_rejected'])
            self.assertEqual(controls[name]['spent'], 40)
            self.assertEqual(controls[name]['reserved'], 0)
        stale = controls['delayed_stale_policy_write']
        self.assertTrue(stale['stale_write_rejected'])
        self.assertEqual(stale['stale_call_charged_tokens'], 31)
        self.assertTrue(stale['unaffected_branch_retained'])
        self.assertFalse(stale['stale_result_present'])
        correction = controls['published_correction']
        self.assertEqual(correction['affected']['incident'], ['answer', 'plan'])
        self.assertTrue(correction['original_unchanged'])
        self.assertEqual([entry['version'] for entry in correction['ledger']], [1, 2])
        self.assertEqual(correction['ledger'][1]['supersedes'], 1)
        actions = correction['action_ledger']
        self.assertEqual([item['authorization'] for item in actions], ['denied', 'allowed', 'allowed'])
        self.assertEqual([item['executed'] for item in actions], [False, True, True])
        self.assertEqual(actions[2]['supersedes'], actions[1]['publication_id'])
        self.assertEqual(actions[2]['causal_inputs'], {'plan': 2, 'telemetry': 1})
        overhead = report['cpu_overhead']
        sizes = overhead['serialized_result_bytes']
        self.assertEqual(sizes['tracked'], sizes['minimal_dictionary'])
        self.assertGreater(sizes['tracked'], sizes['no_dependency'])
        self.assertEqual((overhead['invalidated_nodes'], overhead['retained_nodes']), (5, 4))
        self.assertEqual(overhead['dependency_edges'], 7)
        for values in overhead['samples'].values():
            self.assertEqual(len(values), 2)
            self.assertTrue(all(value >= 0 for value in values))

    def test_fake_scenarios_are_deterministic(self):
        first = asyncio.run(run_controls(size=2, repeats=1))
        second = asyncio.run(run_controls(size=2, repeats=1))
        self.assertEqual(first['controls'], second['controls'])

    def test_invalid_measurement_parameters(self):
        for size, repeats in ((1, 2), (2, 0)):
            with self.assertRaises(ValueError):
                tracking_overhead(size, repeats)

    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'nested' / 'controls.json'
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/run_phase1_controls.py'),
                 '--output', str(output), '--graph-size', '4', '--repeats', '1'],
                check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(completed.stdout)['passed'])
            self.assertTrue(json.loads(output.read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
