"""Offline plumbing and pessimistic call-bound checks, not semantic evidence."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from team_assistant.dispatch import Outcome
ROOT = Path(__file__).resolve().parents[1]
VENDOR = Path(os.environ.get('CLOUDOPS_VENDOR', '/Users/yvette/26fall/preparation/291a-team-agent/vendor/Cloud-OpsBench'))
spec = importlib.util.spec_from_file_location('handoff_dev', ROOT/'scripts/run_handoff_dev.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

class Provider:
    def __init__(self, retrieve): self.records = []; self.halted = False; self.retrieve = retrieve
    async def complete(self, instruction, request):
        step = request['step']; inputs = request['inputs']
        if step.endswith('_plan'): answer = {'queries': []}
        elif step == 'configuration_summary': answer = {'liveness_protocol': 'httpGet', 'summary': 'fixture'}
        elif step == 'telemetry_summary': answer = {'summary': 'fixture', 'source_ids': []}
        elif step == 'coordinator_decision' and self.retrieve and 'All raw sources' not in instruction:
            answer = {'retrieve_source_ids': [inputs['source_catalog']['sources'][-1]['id']]}
        else:
            answer = {'status': 'uncertain', 'adservice_liveness_protocol': inputs['configuration_summary']['liveness_protocol'], 'reasoning': 'fixture'}
        body = {'model': 'api-glm-5.3', 'messages': [{'role':'system','content': 'No tools or files. Use supplied evidence only. Return one JSON object.\n'+instruction}, {'role':'user','content':json.dumps(request,allow_nan=False)}], 'max_tokens':8192,'temperature':0,'reasoning_effort':'low','stream':False}
        size = len(json.dumps(body,separators=(',',':')).encode())
        if size + 512 > 40000: raise AssertionError('input byte bound')
        self.records.append({'step':step,'response':answer,'input_bytes':size,'tokens':10,'request':request})
        return Outcome(answer,10)

@unittest.skipUnless(VENDOR.exists(), 'requires seen case')
class TestHandoff(unittest.TestCase):
    def run_pilot(self, retrieve, provider=None):
        provider = provider or Provider(retrieve)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(runner.experiment(Path(tmp),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',provider))
            files = {str(p.relative_to(tmp)):json.loads(p.read_text()) for p in Path(tmp).rglob('*.json')}
        return files,provider
    def test_maximal_retrieval_call_bound_and_dependencies(self):
        files,p = self.run_pilot(True)
        self.assertEqual(len(p.records),54)
        for n in range(1,4):
            state = files[f'repeat-{n}/selective/state.json']
            self.assertIn('retrieved_sources',state['incident_brief']['inputs'])
            self.assertIn('coordinator_decision',state['retrieved_sources']['inputs'])
            self.assertIn('telemetry_summary',files[f'repeat-{n}/selective/reuse.json']['valid_unchanged_artifacts'])
            self.assertEqual(files[f'repeat-{n}/restart/reuse.json']['valid_unchanged_artifacts'],[])
            self.assertEqual(files[f'repeat-{n}/selective/ledger.json'][-1]['supersedes'],1)
            self.assertTrue(files[f'repeat-{n}/summary.json']['arms']['no-recovery']['stale_republication_rejected'])
        requests = [r['request'] for r in p.records if r['step']=='coordinator_decision']
        for r in requests:
            if 'initial' not in r['inputs']:
                self.assertEqual(set(r['inputs']),set(runner.HANDOFF_NAMES))
    def test_no_retrieval_and_recovery_order(self):
        files,p = self.run_pilot(False)
        self.assertEqual(len(p.records),42)
        self.assertEqual(files['summary.json']['repeats'][1]['recovery_order'],['selective','restart'])
        self.assertEqual(len(files['summary.json']['repeats']),3)
        self.assertTrue(all(a['state'] != 'failed' for r in files['summary.json']['repeats'] for a in r['arms'].values()))

    def test_failed_arm_and_unattempted_assignments_are_retained(self):
        class Failing(Provider):
            async def complete(self, instruction, request):
                if request['step'] == 'coordinator_decision':
                    raise ValueError('fixture failure')
                return await super().complete(instruction, request)
        files, _ = self.run_pilot(False, Failing(False))
        repeats = files['summary.json']['repeats']
        self.assertEqual(len(repeats), 3)
        self.assertEqual(repeats[0]['arms']['clean']['state'], 'failed')
        self.assertEqual(repeats[0]['arms']['corrupt']['state'], 'not_run')
        self.assertEqual(repeats[1]['arms']['prefix']['state'], 'not_run')
        self.assertEqual(repeats[2]['arms']['selective']['state'], 'not_run')

    def test_ineligible_repeats_are_not_replaced(self):
        class Ineligible(Provider):
            async def complete(self, instruction, request):
                result = await super().complete(instruction, request)
                if request['step'] == 'configuration_summary':
                    result.value['liveness_protocol'] = 'unknown'
                return result
        files, p = self.run_pilot(False, Ineligible(False))
        self.assertEqual(len(p.records), 15)
        for repeat in files['summary.json']['repeats']:
            for name in ('raw-visible-corrupt', 'corrupt', 'no-recovery', 'restart', 'selective'):
                self.assertEqual(repeat['arms'][name]['state'], 'not_injected')
