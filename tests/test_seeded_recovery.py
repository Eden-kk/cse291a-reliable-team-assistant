"""Regression checks for synthetic controller recovery, not LLM performance."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('seeded', Path(__file__).resolve().parents[1] / 'scripts/run_seeded_recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SeededRecoveryTests(unittest.TestCase):
    def test_fixed_work_and_semantic_outcomes(self):
        result = module.run()
        expected = {'derived_wrong_claim': 3, 'source_version_update': 4,
                    'dense_dependency': 6, 'missing_provenance': 6}
        for fixture, selective_work in expected.items():
            arms = result['fixtures'][fixture]['arms']
            self.assertEqual(arms['restart']['work_units'], 6)
            self.assertEqual(arms['selective']['work_units'], selective_work)
            self.assertTrue(arms['selective']['matches_toy_semantic_oracle'])
            self.assertFalse(arms['no_recovery']['published'])
            self.assertFalse(arms['no_recovery']['matches_toy_semantic_oracle'])

    def test_changed_source_is_refetched(self):
        arm = module.run_fixture('source_version_update')['arms']['selective']
        self.assertIn('configuration_raw', arm['executed_nodes'])
        self.assertEqual(arm['state']['configuration_raw']['sources'], {'configuration': 2})
        self.assertEqual(arm['state']['brief']['payload']['inspect_threshold'], 4)

    def test_observable_gap_falls_back(self):
        arm = module.run_fixture('missing_provenance')['arms']['selective']
        self.assertTrue(arm['fallback_restart'])
        self.assertEqual(arm['valid_unchanged_nodes'], [])
        self.assertEqual(arm['observable_provenance_gaps'], {'claim': 'incomplete'})

    def test_independent_closure_oracle_detects_missing_edge(self):
        original = module.edges
        def broken(fixture):
            graph = original(fixture)
            graph['brief'] = ('telemetry_summary',)
            return graph
        module.edges = broken
        try:
            with self.assertRaises(AssertionError): module.run_fixture('derived_wrong_claim')
        finally:
            module.edges = original
