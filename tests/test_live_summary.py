"""Accounting failures must prevent publication of an experiment summary."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('live_summary', Path(__file__).resolve().parents[1] / 'scripts/summarize_live_scheduling.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LiveSummaryTests(unittest.TestCase):
    def test_unknown_charge_and_skipped_window_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, obj):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(obj))
            write('manifest.json', {'call_reservation': 60, 'policy_orders': [['fair']]})
            write('repeat-1/historical-prefix/calls/001.json', {'tokens': None})
            batch = {'repeats': [{'repeat': 1, 'state': 'unknown usage', 'windows': {}}],
                     'physical_tokens': 60, 'physical_halted': True,
                     'physical_settlements': [{'charged': 60}]}
            write('summary.json', batch)
            report = MODULE.summarize(root)
            self.assertEqual(report['unknown_usage_calls'], 1)
            self.assertEqual(report['rows'][0]['state'], 'skipped')
            batch['physical_tokens'] = 0
            write('summary.json', batch)
            with self.assertRaisesRegex(AssertionError, 'ledger mismatch'):
                MODULE.summarize(root)
