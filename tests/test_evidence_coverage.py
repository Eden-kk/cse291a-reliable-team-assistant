import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_evidence_coverage as runner
from team_assistant.dispatch import Outcome
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider
from team_assistant.state import ExecutionStore, Result


class EvidenceCoverageTests(unittest.TestCase):
    def test_raw_handoff_and_dependency_capture(self):
        vendor=os.environ.get('CLOUDOPS_VENDOR')
        if not vendor:self.skipTest('Set CLOUDOPS_VENDOR')
        class Fake:
            def __init__(self):self.records=[]
            async def complete(self,instruction,request):
                await asyncio.sleep(0)
                if request['step']=='telemetry_plan':value={'queries':[{'tool':'GetErrorLogs','arguments':{'namespace':'boutique','service_name':'frontend'}}]}
                elif request['step']=='configuration_plan':value={'queries':[]}
                else:value={'status':'uncertain'}
                self.records.append({'tokens':10,'request':request,'response':value})
                return Outcome(value,10)
        original=runner.subprocess.check_output
        def git(*args,**kwargs):return '' if 'status' in args[0] else original(*args,**kwargs)
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'run'
            with patch.object(runner,'provider',side_effect=lambda path,budget:PhysicalProvider(Fake(),budget)), \
                 patch.object(runner.subprocess,'check_output',side_effect=git), \
                 patch.object(CodexProvider,'complete',side_effect=AssertionError('Real inference forbidden')) as real:
                asyncio.run(runner.main(SimpleNamespace(output=output,vendor=Path(vendor))))
                real.assert_not_called()
            for name in ('parallel-one','telemetry-first-one','parallel-two'):
                calls=json.loads((output/name/'usage.json').read_text())['calls']
                request=next(c['request'] for c in calls if c['request']['step']=='configuration_plan')
                state=json.loads((output/name/'state.json').read_text())
                store=ExecutionStore()
                store.results={k:Result(**v) for k,v in state.items()}
                for result in store.results.values():
                    store.source_versions.update(result.sources)
                    store.shared_versions.update(result.shared_reads)
                invalid=store.invalid(('telemetry_evidence',))
                if name=='telemetry-first-one':
                    self.assertTrue({'configuration_plan','configuration_evidence','diagnosis_0'} <= invalid)
                    self.assertTrue(request['inputs']['telemetry_evidence']['observations'])
                    self.assertEqual(state['configuration_plan']['inputs']['telemetry_evidence'],1)
                    self.assertEqual(calls[0]['request']['step'],'telemetry_plan')
                else:
                    self.assertNotIn('telemetry_evidence',request['inputs'])
                    self.assertNotIn('configuration_plan',invalid)
            self.assertEqual(json.loads((output/'summary.json').read_text())['physical_tokens'],90)
