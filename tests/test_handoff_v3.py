"""Bounded v3 wiring tests with a fixture provider; not semantic inference."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from team_assistant.dispatch import Outcome
ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(os.environ.get('CLOUDOPS_VENDOR', '/Users/yvette/26fall/preparation/291a-team-agent/vendor/Cloud-OpsBench'))
spec = importlib.util.spec_from_file_location('handoff_v3', ROOT/'scripts/run_handoff_v3.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
from handoff_v3_contract import inspect_schema, TIMING_CONTRACT, replace_unique_protocol

class Provider:
    def __init__(self, fail=False, extraction='grpc'):
        self.records=[]; self.halted=False; self.fail=fail; self.extraction=extraction
    async def complete(self, instruction, request):
        step=request['step']; inputs=request['inputs']
        if step.endswith('_plan'): answer={'queries': []}
        elif step=='configuration_summary': answer={'liveness_protocol':self.extraction,'summary':f'adservice livenessProbe uses {self.extraction}; readiness is unchanged.'}
        elif step=='telemetry_summary': answer={'summary':'fixture','source_ids':[]}
        elif step=='coordinator_decision' and self.fail: raise ValueError('fixture')
        elif step=='coordinator_decision' and 'All raw sources' not in instruction:
            answer={'retrieve_source_ids':[inputs['source_catalog']['sources'][-1]['id']]}
        else: answer={'status':'uncertain','reasoning':'fixture','adservice_liveness_protocol':'unknown'}
        body={'model':'api-glm-5.3','messages':[{'role':'system','content':'No tools or files. Use supplied evidence only. Return one JSON object.\n'+instruction},{'role':'user','content':json.dumps(request,allow_nan=False)}],'max_tokens':8192,'temperature':0,'reasoning_effort':'low','stream':False}
        assert len(json.dumps(body,separators=(',',':')).encode())+512<=40000
        if not step.endswith('_plan'): assert TIMING_CONTRACT in instruction
        self.records.append({'step':step,'request':request,'response':answer})
        return Outcome(answer,10)

class TestSchema(unittest.TestCase):
    def test_mapping_and_missing_no_fallback(self):
        for protocol,expected in [('grpc','httpGet'),('httpGet','grpc')]:
            result=inspect_schema({'text':f'  livenessProbe:\n    {protocol}:\n      port: 1\n  readinessProbe:\n    grpc:\n      port: 1','sha256':'abc'})
            self.assertEqual(result['after'],expected)
        self.assertFalse(inspect_schema({'text':'livenessProbe:\n  tcpSocket:\n    port: 1','sha256':'abc'})['eligible'])
        self.assertFalse(inspect_schema({'text':'no field'})['eligible'])
    def test_unique_span_preserves_other_facts(self):
        text='Ready on grpc. adservice livenessProbe uses `grpc`; interval is 5 seconds. Keep this tail.'
        edit=replace_unique_protocol(text,'grpc','httpGet')
        self.assertEqual(edit['summary'],text.replace('uses `grpc`','uses `httpGet`'))
        self.assertEqual(edit['old_text'],'grpc')
        self.assertIsNone(replace_unique_protocol('Both probes use grpc.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe uses grpc; livenessProbe uses grpc.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe does not use grpc.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('It is false that livenessProbe uses grpc.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe uses httpGet.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe uses grpc, not httpGet.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe uses grpc is false.','grpc','httpGet'))
        self.assertIsNone(replace_unique_protocol('livenessProbe uses grpc and readiness uses grpc.','grpc','httpGet'))
    def test_seeded_rejected_before_account(self):
        from types import SimpleNamespace
        with patch.object(runner,'account',side_effect=AssertionError('must not access')):
            with self.assertRaisesRegex(ValueError,'separate budget'):
                asyncio.run(runner.main(SimpleNamespace(seeded_state=True)))

@unittest.skipUnless(VENDOR.exists(),'requires seen case')
class TestPilot(unittest.TestCase):
    def run_pilot(self,provider):
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(runner.experiment(Path(tmp),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',provider))
            return {str(p.relative_to(tmp)):json.loads(p.read_text()) for p in Path(tmp).rglob('*.json')}
    def test_bounds_and_pairing(self):
        p=Provider(); files=self.run_pilot(p)
        self.assertEqual(len(p.records),36)
        self.assertEqual(files['schema-preflight.json']['eligibility']['before'],'grpc')
        for n in (1,2):
            self.assertEqual(files[f'repeat-{n}/fault.json']['after'],'httpGet')
            self.assertTrue(files[f'repeat-{n}/fault.json']['other_summary_text_preserved'])
            self.assertTrue(files[f'repeat-{n}/fault.json']['other_payload_fields_preserved'])
            self.assertTrue(files[f'repeat-{n}/summary.json']['arms']['no-recovery']['stale_republication_rejected'])
            self.assertEqual(len(files[f'repeat-{n}/summary.json']['arms']['restart']['calls']),6)
            self.assertEqual(len(files[f'repeat-{n}/summary.json']['arms']['selective']['calls']),3)
            self.assertIn('telemetry_summary',files[f'repeat-{n}/selective/reuse.json']['valid_unchanged_artifacts'])
            self.assertEqual(files[f'repeat-{n}/selective/ledger.json'][-1]['supersedes'],1)
            self.assertIn('wall_seconds',files[f'repeat-{n}/selective/end-to-end-clock.json'])
    def test_extraction_ineligible_retained(self):
        p=Provider(extraction='unknown'); files=self.run_pilot(p)
        self.assertEqual(len(p.records),12)
        for r in files['summary.json']['repeats']:
            self.assertEqual(len(r['arms']),7)
            self.assertEqual(r['arms']['corrupt']['state'],'not_injected')
    def test_unrecognized_summary_keeps_slots_ineligible(self):
        class NoSpan(Provider):
            async def complete(self,instruction,request):
                out=await super().complete(instruction,request)
                if request['step']=='configuration_summary': out.value['summary']='Both probes use grpc.'
                return out
        p=NoSpan(); files=self.run_pilot(p)
        self.assertEqual(len(p.records),12)
        for r in files['summary.json']['repeats']:
            self.assertEqual(r['arms']['selective']['state'],'not_injected')
    def test_failure_retains_all_assignments(self):
        files=self.run_pilot(Provider(fail=True))
        self.assertEqual(files['summary.json']['repeats'][0]['arms']['clean']['state'],'failed')
        self.assertEqual(files['summary.json']['repeats'][1]['arms']['selective']['state'],'not_run')
    def test_absent_schema_costs_zero(self):
        p=Provider()
        with patch.object(runner,'inspect_schema',return_value={'eligible':False,'reason':'fixture absent'}):
            files=self.run_pilot(p)
        self.assertEqual(p.records,[])
        self.assertTrue(all(a['state']=='schema_ineligible' for r in files['summary.json']['repeats'] for a in r['arms'].values()))
