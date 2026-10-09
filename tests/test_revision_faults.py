import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.revision_faults import historical_record


class RevisionFaultTests(unittest.TestCase):
    def record(self, text, tool='GetAppYAML', arguments=None):
        return {'id': 'o1', 'tool': tool, 'arguments': arguments or {'app_name': 'adservice'},
                'text': text, 'truncated': False, 'sha256': hashlib.sha256(text.encode()).hexdigest()}

    def test_yaml_only_server_limit_changes_not_requests_other_container_or_substrings(self):
        text = ('containers:\n- name: server\n  resources:\n    requests:\n      cpu: 200m\n'
                '    limits:\n      cpu: 200m\n- name: sidecar\n  resources:\n'
                '    limits:\n      cpu: 200m\n# total 2200m\n')
        record = self.record(text)
        new = historical_record(record, ('200m','800m'), 'query-fields-v2')
        expected = text.replace('    limits:\n      cpu: 200m', '    limits:\n      cpu: 800m', 1)
        self.assertEqual(new['text'], expected)
        self.assertEqual(new['sha256'], hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(record['text'], text)

    def test_pod_limit_only_and_node_is_unchanged(self):
        text = ('Containers:\n  server:\n    Limits:\n      cpu:     200m\n'
                '    Requests:\n      cpu:     200m\n  proxy:\n    Limits:\n      cpu: 200m\n'
                'Conditions:\n  unrelated: 2200m\n')
        pod = self.record(text, 'DescribeResource', {'resource_type':'pod','name':'adservice-123','namespace':'boutique'})
        new = historical_record(pod, ('200m','800m'), 'query-fields-v2')
        self.assertEqual(new['text'], text.replace('cpu:     200m', 'cpu:     800m', 1))
        node = self.record('adservice 200m 2200m\nother 200m 2200m', 'DescribeResource', {'resource_type':'node','name':'worker-02'})
        self.assertEqual(historical_record(node, ('200m','800m'), 'query-fields-v2'), node)

    def test_legacy_is_explicitly_reproducible_and_ambiguous_new_scope_fails(self):
        obs = self.record('200m 2200m')
        self.assertEqual(historical_record(obs, ('200m','800m'), 'legacy-substring-v1')['text'],'800m 2800m')
        with self.assertRaises(ValueError):
            historical_record(obs, ('200m','800m'), 'query-fields-v2')

    def test_port_replacement_does_not_change_larger_number(self):
        record = self.record('port: 9555\nannotation: 19555')
        result = historical_record(record, ('9555','9556'), 'query-fields-v2')
        self.assertEqual(result['text'], 'port: 9556\nannotation: 19555')
