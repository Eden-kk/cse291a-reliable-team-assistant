"""Verify source selection and complete lineage for mixed-incident replay."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('heterogeneous', Path(__file__).resolve().parents[1] / 'scripts/replay_heterogeneous.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HeterogeneousTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = {'dataset_commit': 'dataset', 'splits': {'development_seen': ['service/1']}}
        self.manifest = {'case': 'service/1', 'dataset_commit': 'dataset', 'code_commit': 'code'}
        self.write('clean/manifest.json', self.manifest)
        self.write('collection/manifest.json', self.manifest)
        state = {
            'initial': {'inputs': {}, 'complete': True, 'version': 1, 'payload': {'value': 'initial'}},
            'plan': {'inputs': {'initial': 1}, 'complete': True, 'version': 1, 'payload': {'value': 'plan'}},
            'evidence': {'inputs': {'plan': 1}, 'complete': True, 'version': 1, 'payload': {'value': 'evidence'}},
            'answer': {'inputs': {'evidence': 1}, 'complete': True, 'version': 1, 'payload': {'value': 'answer'}}}
        def call(step, parent):
            return {'call': 1, 'request': {'step': step, 'inputs': {parent: state[parent]['payload']}},
                    'tokens': 10, 'seconds': 2, 'error': None}
        self.write('collection/calls/001.json', call('plan', 'initial'))
        self.write('clean/result.json', {'model_calls': [call('answer', 'evidence')],
                                        'contract_errors': [], 'budget': {'spent': 10, 'halted': False}})
        self.write('clean/state.json', state)
        self.write('clean/scores.json', {'scores': {'JRA': 1}})
        self.spec = {'directory': 'clean', 'collection': 'collection', 'kind': 'recovery'}

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def load(self):
        with patch.object(MODULE, 'ROOT', self.root):
            return MODULE.load_trace(self.spec, self.registry)

    def test_collection_included_and_dependencies_validated(self):
        result = self.load()
        self.assertEqual(sum(n['tokens'] for n in result['nodes']), 20)
        self.assertEqual(result['nodes'][1]['dependencies'], ['plan'])
        self.assertIn('collection/calls/001.json', result['source_hashes'])

    def test_unseen_case_rejected_before_payload_read(self):
        self.write('clean/manifest.json', {**self.manifest, 'case': 'final/1'})
        (self.root / 'clean/result.json').unlink()
        with self.assertRaisesRegex(ValueError, 'development_seen'):
            self.load()

    def test_failed_trace_and_collection_mismatch_rejected(self):
        self.write('clean/failure.json', {'error': 'failed'})
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.load()
        (self.root / 'clean/failure.json').unlink()
        self.write('collection/manifest.json', {**self.manifest, 'case': 'other'})
        with self.assertRaisesRegex(ValueError, 'Collection case'):
            self.load()

    def test_response_without_recorded_planners_rejected(self):
        self.spec['kind'] = 'response'
        with self.assertRaisesRegex(ValueError, 'missing planner'):
            self.load()
