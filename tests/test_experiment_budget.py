import unittest
from team_assistant.experiment_budget import paired_budget_plan, require_complete_batch


class ExperimentBudgetTests(unittest.TestCase):
    def plan(self, **changes):
        args = dict(pairs=6, repetitions=1, reservation=60000, logical_limit=360000, physical_limit=900000)
        args.update(changes)
        return paired_budget_plan(**args)

    def test_previous_budget_rejected_before_spending(self):
        p = self.plan()
        self.assertFalse(p['admitted'])
        self.assertEqual(p['required_batch'], 3600000)

    def test_exact_bound_covers_two_parallel_and_three_sequential_calls(self):
        self.assertTrue(self.plan(physical_limit=3600000, logical_limit=300000)['admitted'])
        self.assertFalse(self.plan(physical_limit=3599999)['admitted'])
        self.assertFalse(self.plan(physical_limit=3600000, logical_limit=299999)['admitted'])

    def test_repetitions_and_pair_denominator(self):
        self.assertEqual(self.plan(repetitions=2)['required_batch'], 7200000)
        self.assertEqual(self.plan(pairs=1)['required_batch'], 600000)

    def test_invalid_values(self):
        for key in ('pairs', 'repetitions', 'reservation', 'logical_limit', 'physical_limit'):
            for value in (True, 0, -1, 1.5):
                with self.assertRaises(ValueError): self.plan(**{key: value})

    def test_requirement_raises(self):
        with self.assertRaisesRegex(ValueError, 'required=3600000'):
            require_complete_batch(pairs=6, repetitions=1, reservation=60000,
                                   logical_limit=360000, physical_limit=900000)
