import unittest
from team_assistant.replay import replay


def node(name, group, dependencies=(), arrival=0, duration=1, tokens=10):
    return dict(id=name, group=group, kind='response', dependencies=list(dependencies),
                arrival=arrival, deadline=arrival+30, duration=duration, tokens=tokens, reservation=60)


class AdmissionReplayTests(unittest.TestCase):
    def run_nodes(self, nodes, limit=300):
        return replay(nodes, 'fair', limit, reserve_foreground=True, max_active_responses=1)

    def test_slot_covers_whole_workflow_and_arrival_wait(self):
        nodes = [node('a1','a'), node('a2','a',['a1']), node('b1','b')]
        r = self.run_nodes(nodes)
        self.assertEqual([e['id'] for e in r['events']], ['a1','a2','b1'])
        self.assertEqual(r['groups']['b']['admission_wait_seconds'], 2)
        self.assertEqual(r['groups']['b']['seconds'], 3)
        self.assertEqual([e['event'] for e in r['admission']['events']], ['admitted','released','admitted','released'])

    def test_parallel_planners_share_one_workflow_slot(self):
        r = self.run_nodes([node('a1','a'),node('a2','a'),node('a3','a',['a1','a2']),node('b1','b')])
        self.assertEqual([e['start'] for e in r['events'][:2]], [0,0])
        self.assertEqual(r['groups']['b']['admission_wait_seconds'], 2)

    def test_budget_failure_retains_deferred_and_partial_spend(self):
        r = self.run_nodes([node('a1','a'),node('a2','a',['a1']),node('b1','b')], 65)
        self.assertEqual(set(r['rejected_ids']), {'a2','b1'})
        self.assertFalse(r['groups']['a']['completed'])
        self.assertEqual(r['groups']['a']['executed_tokens'], 10)
        self.assertFalse(r['groups']['b']['admitted'])
        self.assertTrue(r['groups']['b']['admission_wait_censored'])

    def test_future_arrival_not_admitted_early(self):
        r = self.run_nodes([node('a1','a'),node('b1','b',arrival=5)])
        events = [e for e in r['admission']['events'] if e['event']=='admitted']
        self.assertEqual([e['time'] for e in events], [0,5])
        self.assertEqual(r['groups']['b']['admission_wait_seconds'], 0)

    def test_admission_does_not_sort_by_future_cost_or_duration(self):
        r = self.run_nodes([node('a1','a',duration=10,tokens=50),node('b1','b',duration=1,tokens=1)])
        self.assertEqual(r['admission']['events'][0]['group'], 'a')

    def test_invalid_limit_and_cross_group_dependencies(self):
        for limit in (0,-1,True):
            with self.assertRaises(ValueError):
                replay([node('a1','a')], 'fair', 300, max_active_responses=limit)
        with self.assertRaises(ValueError):
            self.run_nodes([node('a1','a'),node('b1','b',['a1'])])
