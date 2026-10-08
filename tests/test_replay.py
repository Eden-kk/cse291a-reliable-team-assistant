from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.replay import replay


def node(i, dependencies=(), duration=1, arrival=0):
    return {'id': i, 'dependencies': dependencies, 'duration': duration, 'arrival': arrival,
            'group': 'A', 'kind': 'response', 'reservation': 10, 'tokens': 5, 'deadline': 30}


class ReplayTests(unittest.TestCase):
    def test_parallel_join_delay_and_budget(self):
        r = replay([node(1, duration=2), node(2), node(3, [1, 2])], 'fair', 30, join_delay=.5)
        self.assertEqual(r['groups']['A']['seconds'], 3.5)
        self.assertEqual(r['tokens'], 15)
        self.assertFalse(r['rejected_ids'])

    def test_failed_admission_remains_in_denominator(self):
        r = replay([node(1), node(2, [1])], 'fifo', 10)
        self.assertFalse(r['groups']['A']['completed'])
        self.assertEqual(r['rejected_ids'], [2])

    def test_future_arrival_and_cycle(self):
        self.assertEqual(replay([node(1, arrival=4)], 'fifo', 20)['groups']['A']['seconds'], 5)
        with self.assertRaises(ValueError):
            replay([node(1, [2]), node(2, [1])], 'fair', 20)
