import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.run_gate import authorize_case, REQUIRED_ARTIFACTS


class RunGateTests(unittest.TestCase):
    def test_final_requires_explicit_complete_unchanged_freeze(self):
        registry = {'splits': {'learning': ['dev'], 'final_locked': ['test']},
                    'dataset_commit': 'dataset', 'final_unlocked': False}
        self.assertEqual(authorize_case('dev', registry, 'code'), 'learning')
        with self.assertRaises(ValueError):
            authorize_case('test', registry, 'code')
        registry['final_unlocked'] = True
        freeze = {'code_commit': 'code', 'dataset_commit': 'dataset', 'model': 'fixed-model',
                  'registry_sha256': hashlib.sha256(json.dumps(registry, sort_keys=True).encode()).hexdigest(),
                  'artifacts': {}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                authorize_case('test', registry, 'code', freeze, directory)
            for name in REQUIRED_ARTIFACTS:
                (Path(directory) / name).write_text(name)
                freeze['artifacts'][name] = {'path': name, 'sha256': hashlib.sha256(name.encode()).hexdigest()}
            self.assertEqual(authorize_case('test', registry, 'code', freeze, directory), 'final_locked')
            (Path(directory) / 'memory').write_text('modified-after-test')
            with self.assertRaises(ValueError):
                authorize_case('test', registry, 'code', freeze, directory)
