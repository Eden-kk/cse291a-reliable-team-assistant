import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('phase2batch', ROOT/'scripts/run_phase2_batch.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BatchCostTests(unittest.TestCase):
    def test_unfinished_request_charged_even_without_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = root/'selective/calls'
            calls.mkdir(parents=True)
            for number in (1, 2, 3):
                (calls/f'{number:03}.request.json').write_text('{}')
            (calls/'001.json').write_text(json.dumps({'tokens': 17}))
            (calls/'002.json').write_text(json.dumps({'tokens': None}))
            self.assertEqual(MODULE.physical_usage(root, 60000), (120017, 2))

    def test_no_request_does_not_cost_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'summary.json').write_text('{}')
            self.assertEqual(MODULE.physical_usage(root, 60000), (0, 0))
