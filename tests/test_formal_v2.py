"""Offline fail-closed checks; never open heldout data or invoke inference."""
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_formal_v2 import verify, DeadlineProvider
from score_formal_v2 import inputs, seal
class FormalTests(unittest.TestCase):
    def gate_fixture(self, root):
        # Gate behavior is independent of later development edits to the historical tree.
        # This is a new synthetic freeze, never a rewritten historical manifest.
        (root/'configs').mkdir()
        for name in ('formal-evaluation-v2.json', 'formal-window-v1.json'):
            (root/'configs'/name).write_bytes((ROOT/'configs'/name).read_bytes())
        source = root/'fixture.py'
        source.write_text('frozen fixture, never executed\n')
        manifest = {'files': {'fixture.py': hashlib.sha256(source.read_bytes()).hexdigest()}}
        (root/'configs/formal-freeze-v2.json').write_text(json.dumps(manifest))
        return root

    def test_frozen_fixture_valid_then_launch_blocked_by_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.gate_fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError,'Approval'):
                verify(root,{},datetime(2026,10,29,tzinfo=timezone.utc))
    def test_early_launch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.gate_fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError,'Outside launch'):
                verify(root,{},datetime(2026,10,24,tzinfo=timezone.utc))
    def test_tampered_frozen_fixture_rejected_before_other_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.gate_fixture(Path(tmp))
            (root/'fixture.py').write_text('modified after freeze\n')
            with self.assertRaisesRegex(ValueError,'Frozen file mismatch: fixture.py'):
                verify(root,{},datetime(2026,10,24,tzinfo=timezone.utc))
    def test_no_request_near_cutoff(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'OPENAI_BASE_URL':'https://tritonai-api.ucsd.edu/v1','OPENAI_API_KEY':'test-not-real'}):
            provider=DeadlineProvider(Path(d)/'calls',max_calls=102,maximum_usd='20',stop=datetime.now(timezone.utc)+timedelta(seconds=20))
            result=asyncio.run(provider.complete('test',{}))
            self.assertTrue(provider.halted);self.assertEqual(provider.records,[]);self.assertIsNotNone(result.error)
    def test_review_seal_requires_complete_unmodified_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);arm=root/'arm';arm.mkdir();(arm/'result.json').write_text('{}')
            review=root/'review.json'
            doc={'labels_seen':False,'reviewer':'independent reviewer','input_hashes':inputs(root),'rows':{'arm':{'supported':'uncertain','rationale':'insufficient source support','publication_current':True,'fault_adopted':False}}}
            review.write_text(json.dumps(doc));seal(root,review)
            self.assertTrue((root/'evidence-review-seal.json').exists())
            (arm/'result.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'changed'):seal(root,review)

    def test_alternating_order_has_equal_calls_and_explicit_not_run(self):
        from test_triton_workflow import Controlled, workflow
        vendor=Path('/Users/yvette/26fall/preparation/291a-team-agent/vendor/Cloud-OpsBench')
        if not vendor.exists():self.skipTest('seen snapshot unavailable')
        registry=json.loads((ROOT/'configs/splits.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            backend=Controlled(True)
            asyncio.run(workflow.matrix(Path(d),vendor,registry,'mock',backend,order=('selective','restart')))
            arms=json.loads((Path(d)/'summary.json').read_text())['arms']
            self.assertEqual(len(backend.records),17)
            self.assertLess(arms['selective']['calls'][0],arms['restart']['calls'][0])
            self.assertEqual(len(arms['expiry-selective']['calls']),3)
        class Failing(Controlled):
            async def complete(self,*args):
                self.halted=True
                raise ValueError('synthetic failure')
        with tempfile.TemporaryDirectory() as d:
            asyncio.run(workflow.matrix(Path(d),vendor,registry,'mock',Failing()))
            arms=json.loads((Path(d)/'summary.json').read_text())['arms']
            self.assertEqual(arms['prevention']['state'],'not_run')
    def test_two_units_share_provider_and_keep_global_call_ids(self):
        from test_triton_workflow import Controlled, workflow
        vendor=Path('/Users/yvette/26fall/preparation/291a-team-agent/vendor/Cloud-OpsBench')
        if not vendor.exists():self.skipTest('seen snapshot unavailable')
        registry=json.loads((ROOT/'configs/splits.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            backend=Controlled(True)
            for unit,order in enumerate((('restart','selective'),('selective','restart'))):
                asyncio.run(workflow.matrix(Path(d)/str(unit),vendor,registry,'mock',backend,order=order))
            self.assertEqual(len(backend.records),34)
            second=json.loads((Path(d)/'1/summary.json').read_text())
            self.assertEqual(second['arms']['collection']['calls'],[18,19])
            self.assertEqual(second['arms']['expiry-restart']['calls'],[32,33,34])
    def test_unit_build_exception_records_failed_and_remaining_not_run(self):
        from run_formal_v2 import run_units
        from test_triton_workflow import Controlled
        with tempfile.TemporaryDirectory() as d:
            units={'case-r1':{'state':'not_run'},'case-r2':{'state':'not_run'}}
            backend=Controlled()
            with patch('run_formal_v2.matrix',side_effect=ValueError('fixture build error')):
                with self.assertRaises(ValueError):
                    asyncio.run(run_units(Path(d),None,['case'],{},'mock',backend,{},units))
            self.assertEqual(units['case-r1']['state'],'failed')
            self.assertEqual(units['case-r2']['state'],'not_run')
            self.assertTrue(backend.halted)
    def test_score_keeps_invalid_no_recovery_and_all_absent_arms(self):
        from score_formal_v2 import score
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);arm=root/'u/corrupt';arm.mkdir(parents=True)
            (arm/'result.json').write_text(json.dumps({'answer':{'status':'diagnosed'}}))
            (root/'units.json').write_text(json.dumps({'u':{'state':'completed'},'v':{'state':'not_run'}}))
            (root/'u/summary.json').write_text(json.dumps({'arms':{'corrupt':{'state':'completed'},'no-recovery':{'state':'invalid'},'restart':{'state':'not_injected'}}}))
            review=root/'review.json';review.write_text(json.dumps({'reviewer':'fixture','labels_seen':False,'input_hashes':inputs(root),'rows':{'u/corrupt':{'supported':'yes','rationale':'fixture references','publication_current':True,'fault_adopted':True}}}))
            seal(root,review)
            def fake_native(directory,vendor,output):
                output.write_text(json.dumps({'scores':{'JRA':1,'ECR':1,'contract_valid':True,'budget_valid':True}}))
            with patch('score_run.score',side_effect=fake_native):score(root,None)
            rows={r['arm']:r for r in json.loads((root/'formal-scores.json').read_text())['rows']}
            self.assertEqual(len(rows),18)
            self.assertEqual(rows['u/no-recovery']['native']['JRA'],1)
            self.assertFalse(rows['u/no-recovery']['strict_joint'])
            self.assertEqual(rows['v/clean']['state'],'not_run')
            self.assertEqual(rows['u/restart']['state'],'not_injected')
    def test_runtime_receipt_checks_every_pin_and_missing_package(self):
        from run_formal_v2 import runtime_receipt
        import importlib.metadata
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'requirements.lock';lock.write_text('pydantic==2.14.0\ntyping_extensions==4.16.0\n')
            with patch('run_formal_v2.importlib.metadata.version',side_effect=['2.14.0','4.16.0']) as versions:
                good=runtime_receipt(lock)
                self.assertTrue(good['valid']);self.assertEqual(versions.call_count,2)
                self.assertIn('python',good);self.assertIn('platform',good)
            with patch('run_formal_v2.importlib.metadata.version',side_effect=['2.14.1','4.16.0']):
                bad=runtime_receipt(lock)
                self.assertFalse(bad['valid']);self.assertFalse(bad['dependencies'][0]['matches'])
            with patch('run_formal_v2.importlib.metadata.version',side_effect=importlib.metadata.PackageNotFoundError):
                missing=runtime_receipt(lock)
                self.assertFalse(missing['valid']);self.assertIsNone(missing['dependencies'][1]['actual'])
