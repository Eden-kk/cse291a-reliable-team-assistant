"""Saved-schema accounting preserves missing/error states and catches hash tampering."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import sys
from test_handoff_v3 import ROOT,VENDOR,Provider,runner as organic_runner
sys.path.insert(0,str(ROOT/'scripts'))
from summarize_handoff_v3_saved import summarize
from test_seeded_handoff_v3 import SAVED,runner as seeded_runner

@unittest.skipUnless(VENDOR.exists() and SAVED.exists(),'requires local immutable fixtures')
class TestSavedReport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def save(self,name,data):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
    def run_fixture(self,seeded=False):
        provider=Provider(extraction='httpGet' if seeded else 'grpc')
        registry=json.loads((ROOT/'configs/splits.json').read_text())
        spec=json.loads((ROOT/('configs/seeded-handoff-v3.json' if seeded else 'configs/handoff-dev-v3.json')).read_text())
        if seeded: asyncio.run(seeded_runner.experiment(self.root,VENDOR,registry,'fixture',provider,SAVED,spec))
        else: asyncio.run(organic_runner.experiment(self.root,VENDOR,registry,'fixture',provider))
        calls=[{**r,'call':i+1,'tokens':10,'headers':{'x-litellm-response-cost':'0.001'},'input_bytes':100,'error':None} for i,r in enumerate(provider.records)]
        self.save('usage.json',{'calls':calls});self.save('protocol.json',spec)
        self.save('account-before.json',{'spend':1});self.save('account-settled.json',{'spend':1+len(calls)*0.001})
        return calls
    def test_organic_actual_runner_schema(self):
        self.run_fixture();r=summarize(self.root)
        self.assertEqual(r['totals']['calls'],36);self.assertEqual(r['assigned_arms'],14)
        self.assertTrue(r['settled_delta_matches_header_cost']);self.assertEqual(r['integrity_findings'],[])
        for row in r['rows']:
            if row['arm'] in ('restart','selective'):
                self.assertTrue(row['publication_checks']['mechanically_valid_recovery'])
                self.assertTrue(row['retained_hashes_verified'])
                self.assertIn('not assessed',row['semantic_success'])
    def test_seeded_expected_field_not_synthetic_grpc(self):
        self.run_fixture(True);r=summarize(self.root)
        self.assertEqual(r['totals']['calls'],9);self.assertEqual(r['assigned_arms'],3)
        no_recovery=r['rows'][0]
        self.assertEqual(no_recovery['expected_protocol_from_raw'],'httpGet')
        self.assertFalse(no_recovery['correct_field'])
        self.assertTrue(r['rows'][-1]['publication_checks']['mechanically_valid_recovery'])
    def test_partial_accounting_and_missing_slot_are_visible(self):
        calls=self.run_fixture();calls[0]['tokens']=None;calls[0]['headers']={};self.save('usage.json',{'calls':calls})
        s=json.loads((self.root/'summary.json').read_text());del s['repeats'][1]['arms']['selective'];self.save('summary.json',s)
        r=summarize(self.root)
        self.assertIsNone(r['totals']['tokens']);self.assertIsNone(r['totals']['header_cost_usd'])
        self.assertEqual(r['totals']['header_cost_missing_calls'],[1])
        self.assertEqual(r['missing_slots'],[{'repeat':2,'arm':'selective'}])
        self.assertTrue(r['unassigned_call_ids'])
    def test_saved_state_tamper_is_not_passed(self):
        self.run_fixture();p=self.root/'repeat-1/selective/state.json';s=json.loads(p.read_text());s['incident_brief']['payload']['reasoning']='tamper';p.write_text(json.dumps(s))
        row=next(r for r in summarize(self.root)['rows'] if r['repeat']==1 and r['arm']=='selective')
        self.assertFalse(row['retained_hashes_verified'])
        self.assertFalse(row['publication_checks']['mechanically_valid_recovery'])
        self.assertIn('state_hash_mismatch:incident_brief',row['integrity_findings'])
    def test_missing_usage_not_zero_spend(self):
        self.run_fixture();(self.root/'usage.json').unlink();r=summarize(self.root)
        self.assertIsNone(r['totals']['calls']);self.assertIsNone(r['totals']['header_cost_usd'])
        self.assertTrue(r['rows'][0]['missing_call_records'])
