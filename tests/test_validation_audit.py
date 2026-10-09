"""Batch accounting must fail closed even when the policy gate would accept."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import audit_learning_validation as audit_module
from team_assistant.memory import digest


class BatchAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = {'model': 'gpt-6-astra', 'reasoning_effort': 'low', 'context_window': 131072,
                    'call_reservation': 60000, 'concurrency': 2, 'max_tools': 24, 'source_sha256': {}}
        self.frozen = {'cases': [{'case': str(i), 'order': ['baseline', 'candidate']} for i in range(6)],
                       'planned_physical_token_cap': 900000, 'logical_token_limit_per_run': 360000,
                       'generation_tokens': 10, 'source_collection_tokens': 20, 'known_acquisition_tokens': 30}
        self.data = {'manifest.json': {'execution_settings': settings, 'execution_settings_sha256': digest(settings),
                                     'frozen': self.frozen, 'code_commit': 'abc'},
                     'summary.json': {'outcomes': {}, 'physical_tokens': 120, 'physical_halted': False,
                                      'physical_settlements': [{'actual': 10, 'charged': 10, 'halted': False}] * 12}}
        self.runs = {}
        for i in range(1, 7):
            for arm in ('baseline', 'candidate'):
                key = f'pair-{i}/{arm}'
                self.data['summary.json']['outcomes'][key] = {'state': 'completed', 'tokens': 10}
                calls = [{'tokens': 10, 'error': None}]
                self.data[key + '/usage.json'] = {'calls': calls}
                self.data[key + '/result.json'] = {'model_calls': calls, 'logical_total_tool_attempts': 2}
                self.runs[key] = {'manifest': {'execution_settings_sha256': digest(settings), 'code_commit': 'abc',
                                             'model': 'gpt-6-astra', 'reservation': 60000, 'token_limit': 360000}, 'tokens': 10}
                for name in ('manifest.json', 'usage.json', 'result.json', 'scores.json'):
                    p = self.root / key / name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.touch()

    def run_audit(self):
        def read(p):
            try: return self.data[str(p.relative_to(self.root))]
            except ValueError: return {}
        with patch.object(audit_module, 'validate_freeze', return_value=(self.frozen, {})), \
             patch.object(audit_module, 'read', side_effect=read), \
             patch.object(audit_module, 'load_run', side_effect=lambda p: self.runs[str(p.relative_to(self.root))]), \
             patch.object(audit_module, 'evaluate', return_value={'accepted': True}):
            return audit_module.audit(self.root)

    def test_complete_ledger(self):
        self.assertTrue(self.run_audit()['accepted'])

    def test_missing_usage_cannot_zero_cost(self):
        for p in self.root.glob('pair-*/*/usage.json'): p.unlink()
        self.data['summary.json'].update(physical_tokens=0, physical_settlements=[])
        self.assertFalse(self.run_audit()['accepted'])

    def test_divergent_call_records(self):
        self.data['pair-1/baseline/result.json']['model_calls'] = [{'tokens': 9}]
        self.assertIn('pair-1/baseline:divergent_call_records', self.run_audit()['batch_errors'])

    def test_changed_limits(self):
        self.runs['pair-1/baseline']['manifest']['token_limit'] = 720000
        self.assertFalse(self.run_audit()['accepted'])

    def test_incomplete_pair(self):
        self.data['summary.json']['outcomes']['pair-6/candidate']['state'] = 'failed'
        self.assertFalse(self.run_audit()['accepted'])
