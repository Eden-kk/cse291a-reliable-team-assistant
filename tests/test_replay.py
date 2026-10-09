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

    def test_foreground_next_reservation_avoids_background_completion_delay(self):
        first = node('first', duration=2)
        second = node('second', ['first'])
        background = {**node('background', duration=20), 'kind': 'recovery', 'group': 'B'}
        nodes = [first, second, background]
        baseline = replay(nodes, 'fair', 24)
        guarded = replay(nodes, 'fair', 24, reserve_foreground=True)
        self.assertEqual(baseline['groups']['A']['seconds'], 21)
        self.assertEqual(guarded['groups']['A']['seconds'], 3)
        self.assertTrue(guarded['groups']['B']['completed'])
        self.assertEqual(guarded['tokens'], baseline['tokens'])
        self.assertTrue(guarded['reservation_guard']['deferrals'])

    def test_future_unarrived_foreground_does_not_hold_reservation(self):
        background = {**node('background', duration=2), 'kind': 'recovery', 'group': 'B'}
        future = node('future', arrival=10)
        result = replay([background, future], 'fair', 20, reserve_foreground=True)
        self.assertEqual(result['events'][0]['id'], 'background')
        self.assertEqual(result['events'][0]['start'], 0)
        self.assertTrue(all(g['completed'] for g in result['groups'].values()))

    def test_insufficient_budget_terminates_with_rejections(self):
        background = {**node('background'), 'kind': 'recovery', 'group': 'B'}
        result = replay([node('first'), node('second', ['first']), background],
                        'fair', 10, reserve_foreground=True)
        self.assertEqual(set(result['rejected_ids']), {'second', 'background'})
        self.assertFalse(result['groups']['A']['completed'])
        self.assertFalse(result['groups']['B']['completed'])
