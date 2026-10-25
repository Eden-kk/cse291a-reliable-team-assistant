"""Explicit symmetric contract injection retains existing calls and gate ownership."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from test_handoff_v3 import Provider,VENDOR,ROOT,runner as handoff
from test_seeded_handoff_v3 import SAVED,runner as seeded
from semantic_claim_contract import SEMANTIC_CLAIM_CONTRACT,with_semantic_contract,semantic_receipt

class Recording(Provider):
    def __init__(self,**kwargs): super().__init__(**kwargs); self.instructions=[]
    async def complete(self,instruction,request):
        self.instructions.append(instruction)
        return await super().complete(instruction,request)

class OptInTests(unittest.TestCase):
    def test_default_identity_and_receipt(self):
        backend=Recording()
        self.assertIs(with_semantic_contract(backend),backend)
        self.assertEqual(semantic_receipt(False),{'semantic_v5':False,'semantic_contract_sha256':None})
        self.assertEqual(len(semantic_receipt(True)['semantic_contract_sha256']),64)
    @unittest.skipUnless(VENDOR.exists(),'seen vendor fixture needed')
    def test_all_handoff_calls_same_contract_and_bound(self):
        backend=Recording();wrapper=with_semantic_contract(backend,True)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(handoff.experiment(Path(tmp),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',wrapper))
        self.assertEqual(len(backend.records),36)
        self.assertTrue(all(i.endswith(SEMANTIC_CLAIM_CONTRACT) and i.count(SEMANTIC_CLAIM_CONTRACT)==1 for i in backend.instructions))
        self.assertIs(wrapper.records,backend.records)
    @unittest.skipUnless(VENDOR.exists() and SAVED.exists(),'seen saved fixture needed')
    def test_seeded_both_methods_and_plans_receive_contract(self):
        backend=Recording(extraction='httpGet')
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(seeded.experiment(Path(tmp),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',with_semantic_contract(backend,True),SAVED,json.loads((ROOT/'configs/seeded-handoff-v3.json').read_text())))
        self.assertEqual(len(backend.records),9)
        self.assertTrue(all(i.endswith(SEMANTIC_CLAIM_CONTRACT) and i.count(SEMANTIC_CLAIM_CONTRACT)==1 for i in backend.instructions))
    @unittest.skipUnless(VENDOR.exists(),'seen vendor fixture needed')
    def test_failure_still_stops_without_retry(self):
        backend=Recording(fail=True)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(handoff.experiment(Path(tmp),VENDOR,json.loads((ROOT/'configs/splits.json').read_text()),'mock',with_semantic_contract(backend,True)))
            summary=json.loads((Path(tmp)/'summary.json').read_text())
        self.assertEqual(len(backend.instructions),5)
        self.assertEqual(summary['repeats'][0]['arms']['clean']['state'],'failed')
        self.assertEqual(summary['repeats'][1]['arms']['clean']['state'],'not_run')

if __name__=='__main__':unittest.main()
