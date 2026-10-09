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
import run_e5_recovery_pair as runner
from team_assistant.dispatch import Outcome
from team_assistant.phase2_budget import PhysicalProvider
from team_assistant.provider import CodexProvider


class RecoveryPairTests(unittest.TestCase):
    def test_fixed_protocol(self):
        spec=json.loads((runner.ROOT/'configs/phase2/e5-recovery-pair-v1.json').read_text())
        registry=json.loads((runner.ROOT/'configs/splits.json').read_text());runner.validate(spec,registry)
        spec['logical_window_cap']=360000
        with self.assertRaises(ValueError):runner.validate(spec,registry)

    def test_same_prefix_fake_provider_branches(self):
        vendor=os.environ.get('CLOUDOPS_VENDOR')
        if not vendor:self.skipTest('Set CLOUDOPS_VENDOR')
        class Fake:
            def __init__(self):self.records=[]
            async def complete(self,instruction,request):
                await asyncio.sleep(0)
                value=({'queries':[{'tool':'GetAppYAML','arguments':{'app_name':'adservice'}}]}
                       if request['step']=='configuration_plan' else
                       {'queries':[]} if request['step']=='telemetry_plan' else {'status':'uncertain'})
                self.records.append({'tokens':10,'request':request,'response':value})
                return Outcome(value,10)
        original=runner.subprocess.check_output
        def git(*args,**kwargs):return '' if 'status' in args[0] else original(*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'run'
            with patch.object(runner,'provider',side_effect=lambda path,budget:PhysicalProvider(Fake(),budget)), \
                 patch.object(runner.subprocess,'check_output',side_effect=git), \
                 patch.object(CodexProvider,'complete',side_effect=AssertionError('Real inference forbidden')) as real:
                asyncio.run(runner.main(SimpleNamespace(output=output,vendor=Path(vendor))))
                real.assert_not_called()
            summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['physical_tokens'],130)
            for method,cost in [('restart',60),('selective',40)]:
                w=summary['windows'][method]
                self.assertEqual(w['physical_incremental_tokens'],cost)
                self.assertEqual(w['logical_total_tokens'],cost+30)
                self.assertEqual(len(w['ledgers']['recovery']),2)
                self.assertEqual(w['ledgers']['recovery'][1]['supersedes'],1)
                state=json.loads((output/method/'recovery/state.json').read_text())
                self.assertEqual(state['telemetry_evidence']['version'],2 if method=='restart' else 1)
                self.assertGreaterEqual(w['tool_attempts']['recovery'],3)
            self.assertEqual(summary['windows']['restart']['ledgers']['recovery'][0],
                             summary['windows']['selective']['ledgers']['recovery'][0])
