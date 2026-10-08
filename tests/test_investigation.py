import json
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.cloudops import Snapshot
from team_assistant.dispatch import Budget, Dispatcher, Outcome
from team_assistant.investigation import Investigation
from team_assistant.memory import PolicyMemory, digest
from team_assistant.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT.parent / 'preparation/291a-team-agent/vendor/Cloud-OpsBench'


@unittest.skipUnless(VENDOR.exists(), 'Benchmark unavailable')
class InvestigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_precedes_queries_and_real_observations_reach_coordinator(self):
        class Provider:
            records = []
            async def complete(self, instruction, request):
                self.records.append(request)
                if request['step'].endswith('_plan'):
                    assert request['policy']['rules'] == ['inspect config']
                    value = {'queries': [{'tool': 'GetAppYAML', 'arguments': {'app_name': 'adservice'}}]}
                else:
                    assert request['inputs']['configuration_evidence']['observations'][0]['tool'] == 'GetAppYAML'
                    value = {'status': 'uncertain', 'reasoning': 'Controlled test only'}
                return Outcome(value, 10)
        registry = json.loads((ROOT / 'configs/splits.json').read_text())
        commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
        snapshot = Snapshot(VENDOR, 'boutique/runtime/1', registry, commit)
        memory = PolicyMemory()
        policy = {'rules': ['inspect config']}
        memory.promote('oncall', 'v1', policy, {'accepted': True, 'sha256': digest(policy)})
        runtime = Runtime(memory, Dispatcher(Budget(1000)))
        run = Investigation(runtime, Provider(), snapshot, reservation=100)
        result = await run.run()
        self.assertEqual(result['budget']['spent'], 30)
        self.assertEqual(len(result['observations']), 4)
        self.assertNotIn('boutique/runtime/1', json.dumps(result['model_calls']))
        invalid = runtime.revoke('oncall', 'v1')['incident-1']
        self.assertIn('diagnosis_0', invalid)
        self.assertNotIn('initial', invalid)
