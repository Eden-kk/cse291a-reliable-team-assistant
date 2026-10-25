"""Offline restore/seeding/budget proofs; mock responses are not semantic results."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from test_handoff_v3 import Provider,VENDOR,ROOT
spec=importlib.util.spec_from_file_location('seeded_v3',ROOT/'scripts/run_seeded_handoff_v3.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
SAVED=Path('/Users/yvette/26fall/cse291a-team-assistant/runs/handoff-dev-v2-20261024/handoff-dev-v2-results-20261024/repeat-1/corrupt')

@unittest.skipUnless(VENDOR.exists() and SAVED.exists(),'needs immutable saved runtime/1 artifacts')
class TestSeeded(unittest.TestCase):
    def config(self): return json.loads((ROOT/'configs/seeded-handoff-v3.json').read_text())
    def registry(self): return json.loads((ROOT/'configs/splits.json').read_text())
    def run_pilot(self,p):
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(runner.experiment(Path(tmp),VENDOR,self.registry(),'mock',p,SAVED,self.config()))
            return {str(f.relative_to(tmp)):json.loads(f.read_text()) for f in Path(tmp).rglob('*.json')}
    def test_restore_seed_and_paired_call_bound(self):
        p=Provider(extraction='httpGet'); f=self.run_pilot(p)
        self.assertEqual(len(p.records),9)
        self.assertEqual(len(f['summary.json']['arms']['restart']['calls']),6)
        self.assertEqual(len(f['summary.json']['arms']['selective']['calls']),3)
        self.assertTrue(f['summary.json']['arms']['no-recovery']['stale_republication_rejected'])
        self.assertFalse(f['seed.json']['organic_model_adoption'])
        self.assertEqual(f['restore.json']['old_logical_tokens'],24410)
        for method in ('restart','selective'):
            self.assertEqual(f[method+'/ledger.json'][-1]['supersedes'],2)
            self.assertEqual(f[method+'/result.json']['result']['logical_tokens_spent'],24410+(60 if method=='restart' else 30))
            self.assertEqual(f[method+'/reuse.json']['invalid_after'],[])
        self.assertIn('telemetry_summary',f['selective/reuse.json']['valid_unchanged_artifacts'])
        self.assertEqual(f['restart/reuse.json']['valid_unchanged_artifacts'],[])
    def test_hash_mismatch_no_inference(self):
        conf=self.config();conf['saved_hashes']['state.json']='bad';p=Provider()
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            runner.restore(VENDOR,self.registry(),'mock',p,SAVED,conf)
        self.assertEqual(p.records,[])
    def test_seed_rebinds_descendants_without_altering_raw(self):
        p=Provider(); inv=runner.restore(VENDOR,self.registry(),'mock',p,SAVED,self.config())
        before=inv.incident.store.results['retrieved_sources'].payload
        result=runner.seed(inv)
        self.assertEqual(inv.incident.store.results['retrieved_sources'].payload,before)
        self.assertEqual(inv.incident.store.results['retrieved_sources'].inputs['coordinator_decision'],2)
        self.assertEqual(inv.incident.store.results['incident_brief'].inputs['retrieved_sources'],2)
        self.assertEqual(inv.incident.store.invalid(),set())
        self.assertEqual(result['seeded_publication_id'],2)
        self.assertIn('Keep the existing',result['wrong_payload']['recommendation'])
    def test_provider_failure_retains_unattempted_selective(self):
        f=self.run_pilot(Provider(fail=True))
        self.assertEqual(f['summary.json']['arms']['restart']['state'],'failed')
        self.assertEqual(f['summary.json']['arms']['selective']['state'],'not_run')
