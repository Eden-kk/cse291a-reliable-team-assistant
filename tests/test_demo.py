import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.demo import run


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_connected_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / 'configs/development.json').read_text())
        result = await run(config)
        self.assertTrue(all(result['checks'].values()))
        kinds = {e['kind'] for e in result['dispatch_events'] if e['event'] == 'start'}
        self.assertEqual(kinds, {'response', 'recovery'})

    async def test_reserved_splits_disjoint_and_locked(self):
        root = Path(__file__).resolve().parents[1]
        registry = json.loads((root / 'configs/splits.json').read_text())
        cases = [c for split in registry['splits'].values() for c in split]
        self.assertEqual(len(cases), len(set(cases)))
        self.assertFalse(registry['final_unlocked'])
