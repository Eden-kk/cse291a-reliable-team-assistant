import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_collection_recovery import condition
from team_assistant.provider import CodexProvider


class CollectionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiry_matrix_recomputes_exact_dependencies(self):
        vendor=os.environ.get('CLOUDOPS_VENDOR')
        if not vendor:self.skipTest('Set CLOUDOPS_VENDOR')
        with patch.object(CodexProvider,'complete',side_effect=AssertionError('No real model')) as real:
            for mode in ('parallel','telemetry_first'):
                for expired in ('telemetry_evidence','configuration_evidence','initial'):
                    for method in ('selective','restart'):
                        r=await condition(Path(vendor),mode,expired,method,'controlled-test')
                        expected=3 if method=='restart' or expired=='initial' else (
                            2 if mode=='telemetry_first' and expired=='telemetry_evidence' else 1)
                        self.assertEqual(r['recovery_calls'],expected)
                        self.assertEqual(r['recovery_call_steps'][-1],'diagnosis_0')
                        invalid=set(r['invalidated_by_expiry'])
                        expected_invalid={expired,'diagnosis_0'}
                        if expired=='initial':
                            expected_invalid={'initial','telemetry_plan','telemetry_evidence','configuration_plan','configuration_evidence','diagnosis_0'}
                        elif expired=='telemetry_evidence' and mode=='telemetry_first':
                            expected_invalid.update({'configuration_plan','configuration_evidence'})
                        self.assertEqual(invalid,expected_invalid)
                        for name,before in r['before'].items():
                            after=r['after'][name]
                            changed=method=='restart' or name in invalid
                            self.assertEqual(after['version'],before['version']+int(changed))
                            if not changed:self.assertEqual(before,after)
                        if mode=='telemetry_first':
                            self.assertEqual(r['after']['configuration_plan']['inputs']['telemetry_evidence'],
                                             r['after']['telemetry_evidence']['version'])
                        self.assertLessEqual(r['prefix_tools']+r['recovery_tools'],24)
            real.assert_not_called()
