"""Exercise full runner mechanics with deterministic outcomes, without API calls."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from team_assistant.dispatch import Outcome
ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT.parent/'preparation/291a-team-agent/vendor/Cloud-OpsBench'
spec=importlib.util.spec_from_file_location('triton_workflow',ROOT/'scripts/run_triton_workflow.py')
workflow=importlib.util.module_from_spec(spec);spec.loader.exec_module(workflow)
class Controlled:
    def __init__(self,eligible=True):self.records=[];self.halted=False;self.eligible=eligible
    async def complete(self,instruction,request):
        if request['step']=='configuration_plan':
            answer={'queries':[{'tool':'GetAppYAML','arguments':{'app_name':'adservice'}}] if self.eligible else []}
        elif request['step']=='telemetry_plan':answer={'queries':[]}
        else:answer={'status':'uncertain','reasoning':'fixture; not model evidence'}
        self.records.append({'request':request,'response':answer,'tokens':10})
        return Outcome(answer,10)
@unittest.skipUnless(VENDOR.exists(),'requires seen benchmark snapshot')
class WorkflowTest(unittest.TestCase):
    def run_case(self,eligible):
        with tempfile.TemporaryDirectory() as d:
            provider=Controlled(eligible)
            asyncio.run(workflow.matrix(Path(d),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',provider))
            return json.loads((Path(d)/'summary.json').read_text())
    def test_all_arms_and_uncertainty(self):
        result=self.run_case(True)
        self.assertEqual(result['calls'],17)
        self.assertEqual(len(result['arms']['selective']['calls']),1)
        self.assertTrue(result['arms']['clean']['contract_errors'])
        self.assertFalse(result['arms']['clean']['quality_approved'])
        self.assertEqual(result['arms']['no-recovery']['state'],'invalid')
    def test_not_injected_still_runs_expiry(self):
        result=self.run_case(False)
        self.assertEqual(result['calls'],12)
        self.assertEqual(result['arms']['corrupt']['state'],'not_injected')
        self.assertEqual(len(result['arms']['expiry-selective']['calls']),3)
