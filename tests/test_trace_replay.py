import json
from pathlib import Path
import tempfile
import unittest

from team_assistant.trace_replay import derive_arm, experiment, load_pair, schedule


class TraceReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.summary = {'arms': {'historical-prefix': {'state': 'completed', 'logical_total_tokens': 40000}}}
        for arm in ('selective', 'restart'):
            state = {
                'initial': {'inputs': {}, 'complete': True},
                'planner_a': {'inputs': {'initial': 1}, 'complete': True},
                'planner_b': {'inputs': {'initial': 1}, 'complete': True},
                'evidence_a': {'inputs': {'planner_a': 1}, 'complete': True},
                'evidence_b': {'inputs': {'planner_b': 1}, 'complete': True},
                'answer': {'inputs': {'evidence_a': 1, 'evidence_b': 1}, 'complete': True}}
            for name, artifact in state.items():
                artifact.update(version=1, payload={'value': name})
            steps = ['planner_b', 'planner_a', 'answer'] if arm == 'restart' else ['answer']
            calls = [{'call': i+1, 'request': {'step': step, 'inputs': {p: state[p]['payload'] for p in state[step]['inputs']}},
                      'seconds': i+2, 'tokens': 10000, 'error': None} for i,step in enumerate(steps)]
            summary = {'state': 'completed', 'incremental_tokens': len(calls)*10000,
                       'logical_total_tokens': 40000+len(calls)*10000}
            self.summary['arms'][arm] = summary
            self.write(f'{arm}/usage.json', {'calls': calls, 'summary': summary})
            self.write(f'{arm}/state.json', state)
        self.write('summary.json', self.summary)
        self.write('manifest.json', {})

    def write(self, name, value):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_recorded_edges_collapse_tools_not_call_order(self):
        nodes = derive_arm(self.root, 'restart')
        self.assertEqual(nodes[0]['dependencies'], [])
        self.assertEqual(nodes[1]['dependencies'], [])
        self.assertEqual(nodes[2]['dependencies'], ['planner_a', 'planner_b'])
        self.assertEqual(derive_arm(self.root, 'selective')[0]['dependencies'], [])

    def test_failed_trace_rejected(self):
        self.write('restart/failure.json', {'error': '503'})
        with self.assertRaises(ValueError):
            load_pair(self.root)

    def test_unknown_usage_rejected(self):
        path = self.root/'restart/usage.json'
        data = json.loads(path.read_text())
        data['calls'][0]['tokens'] = None
        self.write('restart/usage.json', data)
        with self.assertRaises(ValueError):
            derive_arm(self.root, 'restart')

    def test_same_prefix_budget_and_response_trace(self):
        pair = load_pair(self.root)
        for method in ('selective', 'restart'):
            nodes, service = schedule(pair, 4, 'staggered', 11, method)
            self.assertEqual(sum(service.values()), 80000)
            self.assertTrue(all(n['reservation'] == 60000 for n in nodes))
            self.assertEqual(len([n for n in nodes if n['kind']=='response']), 6)
        rows = experiment(pair)
        self.assertEqual(len(rows), 96)
        self.assertTrue(all(r['prefix_tokens']==r['groups']//2*40000 for r in rows))

    def test_impossible_budget_preserves_unfinished(self):
        pair = load_pair(self.root)
        from team_assistant.replay import replay
        nodes, service = schedule(pair, 2, 'burst', 11, 'restart')
        result = replay(nodes, 'fair', 40001, initial_spent=40000, initial_service=service)
        self.assertEqual(len(result['rejected_ids']), len(nodes))
        self.assertTrue(all(not g['completed'] for g in result['groups'].values()))

    def test_dependency_mismatch_rejected(self):
        state = json.loads((self.root/'restart/state.json').read_text())
        state['answer']['inputs'] = {'initial': 1}
        self.write('restart/state.json', state)
        with self.assertRaises(ValueError):
            derive_arm(self.root, 'restart')

    def test_changed_payload_rejected(self):
        path = self.root/'restart/usage.json'
        data = json.loads(path.read_text())
        data['calls'][0]['request']['inputs']['initial'] = {'value': 'earlier payload'}
        self.write('restart/usage.json', data)
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            derive_arm(self.root, 'restart')

    def test_changed_parent_version_rejected_through_tool_artifact(self):
        state = json.loads((self.root/'restart/state.json').read_text())
        state['evidence_a']['inputs']['planner_a'] = 2
        self.write('restart/state.json', state)
        with self.assertRaisesRegex(ValueError, 'version differs'):
            derive_arm(self.root, 'restart')

    def test_invalid_numeric_measurements_rejected(self):
        original = json.loads((self.root/'restart/usage.json').read_text())
        for field, value in [('tokens', True), ('tokens', float('inf')),
                             ('seconds', True), ('seconds', float('nan')),
                             ('seconds', float('inf'))]:
            with self.subTest(field=field, value=value):
                data = json.loads(json.dumps(original))
                data['calls'][0][field] = value
                self.write('restart/usage.json', data)
                with self.assertRaises(ValueError):
                    derive_arm(self.root, 'restart')
