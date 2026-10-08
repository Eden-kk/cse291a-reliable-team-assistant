import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.learning_gate import evaluate


class LearningGateTests(unittest.TestCase):
    def setUp(self):
        self.base, self.new = {'rules': []}, {'rules': ['inspect configuration']}
        self.registry = {'dataset_commit': 'dataset', 'splits': {'gate_validation': ['a', 'b']}}
        self.pairs = []
        for case in ('a', 'b'):
            pair = {'case': case}
            for name, policy, tokens in (('baseline', self.base, 100), ('candidate', self.new, 80)):
                pair[name] = {'manifest': {'case': case, 'split': 'gate_validation', 'policy': policy,
                                          'code_commit': 'code', 'dataset_commit': 'dataset',
                                          'model': 'model', 'reservation': 100, 'token_limit': 1000, 'dirty': False},
                              'scores': {'JRA': 1, 'ECR': 1, 'contract_valid': True, 'budget_valid': True},
                              'tokens': tokens, 'semantic_supported': True}
            self.pairs.append(pair)

    def gate(self):
        return evaluate(self.new, self.base, self.pairs, self.registry,
                        {'min_token_saving': .1, 'max_token_ratio': 1}, 40)

    def test_accepts_and_accounts_overhead(self):
        result = self.gate()
        self.assertTrue(result['accepted'])
        self.assertEqual(result['total_learning_tokens'], 400)
        self.assertEqual(result['estimated_break_even_future_cases'], 20)

    def test_aggregate_improvement_cannot_hide_regression(self):
        self.pairs[0]['candidate']['scores']['JRA'] = 0
        self.assertFalse(self.gate()['accepted'])

    def test_over_budget_and_unknown_cost_do_not_produce_payback(self):
        self.pairs[0]["candidate"]["tokens"] = None
        result = self.gate()
        self.assertIsNone(result["total_learning_tokens"])
        self.assertIsNone(result["estimated_break_even_future_cases"])
        self.pairs[0]["baseline"]["tokens"] = 3000
        self.pairs[0]["candidate"]["tokens"] = 2000
        self.assertFalse(self.gate()["accepted"])

    def test_missing_audit_or_changed_policy_fails(self):
        self.pairs[0]['candidate']['semantic_supported'] = False
        self.assertFalse(self.gate()['accepted'])
        self.pairs[0]['candidate']['semantic_supported'] = True
        self.pairs[0]['candidate']['manifest']['policy'] = {}
        self.assertFalse(self.gate()['accepted'])

    def test_duplicate_cases_and_wrong_split_fail(self):
        self.pairs[1] = copy.deepcopy(self.pairs[0])
        self.assertFalse(self.gate()['accepted'])
        self.pairs[0]['baseline']['manifest']['split'] = 'final_locked'
        self.assertFalse(self.gate()['accepted'])

    def test_no_gain_unknown_cost_and_changed_execution_fail(self):
        self.pairs[0]['candidate']['tokens'] = 120
        self.assertIn('no_measurable_benefit', self.gate()['reasons'])
        self.pairs[0]['candidate']['tokens'] = None
        self.assertFalse(self.gate()['accepted'])
        self.pairs[0]['candidate']['manifest']['model'] = 'different'
        self.assertFalse(self.gate()['accepted'])
