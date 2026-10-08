import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'


@unittest.skipUnless(VENDOR.exists(), 'Local benchmark checkout unavailable')
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((ROOT / 'configs/splits.json').read_text())
        self.commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        self.snapshot = Snapshot(VENDOR, 'boutique/runtime/1', self.registry, self.commit)

    def test_real_snapshot_queries_without_oracle_interface(self):
        record = self.snapshot.query('GetResources', {'resource_type': 'pods', 'namespace': 'boutique'})
        self.assertIn('NAME', record['text'])
        self.assertNotIn('raw_text', record)
        self.assertNotIn('source_version', record)
        self.assertTrue(self.snapshot.question)
        self.assertEqual(len(self.snapshot.source_version), 64)
        self.assertNotIn('GetSourceCode', self.snapshot.specifications())

    def test_validation_and_locked_final(self):
        for name, args in [('shell', {}), ('GetAlerts', {'path': '../../metadata.json'}),
                           ('GetResources', {'resource_type': 99})]:
            with self.assertRaises(ValueError):
                self.snapshot.query(name, args)
        with self.assertRaises(ValueError):
            Snapshot(VENDOR, self.registry['splits']['final_locked'][0], self.registry, self.commit)

    def test_missing_connectivity_is_not_observed_failure(self):
        result = self.snapshot.query('CheckServiceConnectivity',
                                    {'namespace': 'boutique', 'service_name': 'adservice', 'port': 65534})
        self.assertEqual(result['availability'], 'missing')
        self.assertNotEqual(result['text'], 'Connection failed')
