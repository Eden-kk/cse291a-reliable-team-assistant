import unittest
from team_assistant.e5_controlled import run


class IntegratedTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_and_full_with_ablations(self):
        for scope in ('partial', 'full'):
            for recovery in ('selective', 'restart'):
                for guard in (False, True):
                    r = await run(scope, recovery, guard)
                    self.assertTrue(all(r['checks'].values()))
                    self.assertTrue(all(o['state'] == 'completed' for o in r['outcomes'].values()))
                    self.assertEqual(r['simulated_tokens'], 127 if scope == 'partial' and recovery == 'selective' else 147)

    async def test_pressure_keeps_failures_and_accounting(self):
        for recovery in ('selective', 'restart'):
            for guard in (False, True):
                r = await run('pressure', recovery, guard)
                self.assertTrue(all(r['checks'].values()))
                self.assertTrue(any(o['state'] == 'failed' for o in r['outcomes'].values()))
                self.assertLessEqual(r['simulated_tokens'],117)
                self.assertEqual(r['outcomes']['C']['state'], 'completed')
