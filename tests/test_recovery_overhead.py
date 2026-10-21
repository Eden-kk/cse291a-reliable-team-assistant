import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('overhead', Path(__file__).resolve().parents[1] / 'scripts/measure_recovery_overhead.py')
overhead = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overhead)


class RecoveryOverheadTests(unittest.TestCase):
    def test_sparse_preserves_independent_work_dense_invalidates_everything(self):
        with tempfile.TemporaryDirectory() as root:
            sparse = overhead.sample(6, 'sparse', Path(root))
            dense = overhead.sample(6, 'dense', Path(root), True)
        self.assertEqual(sparse['invalidated'], ['n0', 'n1'])
        self.assertEqual(dense['invalidated'], [f'n{i}' for i in range(6)])
        for record in (sparse, dense):
            self.assertEqual(record['checked_observations'], 6)
            self.assertEqual(record['repaired_observations'], 1)
            self.assertGreater(record['serialized_bytes']['tracked'], record['serialized_bytes']['baseline'])
            self.assertTrue(all(m['wall_ns'] >= 0 and m['cpu_ns'] >= 0 for m in record['metrics'].values()))

    def test_report_retains_raw_samples_without_quality_or_latency_claims(self):
        report = overhead.run((4,), repetitions=2, warmups=0)
        self.assertEqual(report['model_calls'], 0)
        self.assertFalse(report['dataset_used'])
        self.assertEqual(len(report['conditions']), 2)
        for condition in report['conditions']:
            self.assertEqual(len(condition['samples']), 2)
            self.assertIn('source_check_repair', condition['median_ns'])
        with self.assertRaises(ValueError):
            overhead.run((4,), repetitions=0)
