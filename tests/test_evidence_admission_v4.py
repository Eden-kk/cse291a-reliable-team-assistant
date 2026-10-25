"""Zero-network boundary parity and complete omission accounting."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from evidence_admission_v4 import admit_sources, preflight, encoded_body, citation_errors
from team_assistant.triton_provider import TritonProvider, BASE_URL


def observation(identity,text,**extra):
    return {'id':identity,'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'truncated':False,**extra}

def request(records):
    return {'step':'incident_brief','inputs':{'configuration_summary':{'facts':{'untouched':True}},
                                            'retrieved_sources':{'observations':records}}}


class AdmissionTests(unittest.TestCase):
    def test_provider_serialization_parity_escaped_unicode(self):
        req=request([observation('a','\\"\n日本語é🙂'*40)])
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENAI_BASE_URL':BASE_URL,'OPENAI_API_KEY':'test'}):
            for index,mode in enumerate((False,True)):
                provider=TritonProvider(Path(tmp)/str(index),max_calls=1,maximum_usd='1',input_byte_cap=40000,json_object=mode)
                def post(body,*args):
                    self.assertEqual(body,encoded_body('完整\n"rules',req,json_object=mode))
                    result={'usage':{'prompt_tokens':10,'completion_tokens':10},'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}
                    return 200,{},json.dumps(result).encode()
                with patch('team_assistant.triton_provider._post',side_effect=post) as network:
                    asyncio.run(provider.complete('完整\n"rules',req))
                self.assertEqual(network.call_count,1)  # Mock only, never network.

    def test_exact_cap_and_one_byte_over_zero_network(self):
        req={'pad':''};req['pad']='x'*(40000-preflight('x',req)['actual_total_bytes'])
        self.assertTrue(preflight('x',req)['allowed'])
        self.assertEqual(preflight('x',req)['actual_total_bytes'],40000)
        req['pad']+='x';receipt=preflight('x',req)
        self.assertFalse(receipt['allowed']);self.assertEqual(receipt['actual_total_bytes'],40001)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENAI_BASE_URL':BASE_URL,'OPENAI_API_KEY':'test'}):
            p=TritonProvider(Path(tmp)/'calls',max_calls=1,maximum_usd='1',input_byte_cap=40000,json_object=True)
            with patch('team_assistant.triton_provider._post') as send:
                result=asyncio.run(p.complete('x',req));send.assert_not_called()
            self.assertEqual(result.tokens,0);self.assertEqual(p.spent_usd,0);self.assertEqual(p.records,[])
        self.assertTrue(receipt['zero_network']);self.assertEqual(receipt['physical_calls'],0)

    def test_order_omission_and_context_unchanged(self):
        sources=[observation('first','a'*18000),observation('too-large','b'*23000),observation('last','tail')]
        req=request(sources);before=copy.deepcopy(req)
        result=admit_sources('rules',req,inherited_budget={'spent':1234})
        self.assertEqual(req,before)
        self.assertEqual(result['receipt']['admitted_ids'],['first','last'])
        self.assertEqual([m['id'] for m in result['receipt']['omitted']],['too-large'])
        self.assertEqual(result['receipt']['omitted'][0]['source_sha256'],sources[1]['sha256'])
        self.assertEqual(result['receipt']['inherited_budget'],{'spent':1234})
        self.assertEqual(result['request']['inputs']['configuration_summary'],before['inputs']['configuration_summary'])
        self.assertEqual(result,admit_sources('rules',req,inherited_budget={'spent':1234}))
        self.assertTrue(result['receipt']['actual_total_bytes']<=40000)
        self.assertEqual(citation_errors({'evidence':[{'observation_id':'too-large','quote':'b'}]},result['request']),['unavailable source: too-large'])
        self.assertEqual(citation_errors({'evidence':[{'observation_id':'last','quote':'tail'}]},result['request']),[])

    def test_malformed_json_valid_citations_return_errors(self):
        admitted=admit_sources('x',request([observation('a','raw text')]))['request']
        for evidence in (None, {}, 'a', ['bad'], [None], [{'observation_id':['a'],'quote':'raw'}],
                         [{'observation_id':'a','quote':None}]):
            self.assertTrue(citation_errors({'evidence':evidence},admitted),repr(evidence))
        self.assertEqual(citation_errors(None,admitted),['answer must be an object'])
        self.assertEqual(citation_errors({'evidence':[],'temporal_claims':[]},admitted),[])

    def test_temporal_sources_checked_even_when_uncertain(self):
        admitted=admit_sources('x',request([observation('a','raw text')]))['request']
        self.assertEqual(citation_errors({'status':'uncertain','temporal_claims':[
            {'source_id':'omitted','source_field':'periodSeconds'}]},admitted),['unavailable temporal source: omitted'])
        self.assertEqual(citation_errors({'temporal_claims':[
            {'source_id':'a','source_field':'periodSeconds'}]},admitted),[])
        for claims in (None,{},[None],[{'source_id':['a'],'source_field':'x'}],
                       [{'source_id':'a','source_field':None}]):
            self.assertTrue(citation_errors({'temporal_claims':claims},admitted),repr(claims))

    def test_partial_unknown_size_and_full_archive(self):
        full='日本語'*5000;prefix=full[:10000]
        partial=observation('partial',full);partial.update(text=prefix,truncated=True)
        result=admit_sources('x',request([partial]));r=result['receipt']
        meta=(r['admitted_sources'] or r['omitted'])[0]
        self.assertTrue(meta['partial']);self.assertIsNone(meta['original_total_bytes'])
        self.assertEqual(meta['source_sha256'],hashlib.sha256(full.encode()).hexdigest())
        complete=observation('full','visible');complete.update(text='vis',truncated=True,raw_text='visible')
        result=admit_sources('x',request([complete]));record=result['request']['inputs']['retrieved_sources']['observations'][0]
        self.assertEqual(record['text'],'visible');self.assertFalse(record['truncated'])
        self.assertEqual(record['evidence_completeness']['original_total_bytes'],7)

    def test_empty_and_base_overflow_keep_all_omissions(self):
        for n in (0,1,3):
            result=admit_sources('x',request([observation(str(i),'short') for i in range(n)]))
            self.assertEqual(len(result['receipt']['admitted_ids']),n)
        req=request([observation('one','small'),observation('two','small')]);req['large_context']='x'*40000
        result=admit_sources('x',req)
        self.assertIsNone(result['request']);self.assertEqual(result['receipt']['gate'],'base_with_omission_metadata_exceeds_cap')
        self.assertEqual([m['id'] for m in result['receipt']['omitted']],['one','two'])
        self.assertEqual(result['receipt']['physical_reserved_usd'],'0')

    def test_full_hash_or_duplicate_rejected(self):
        source=observation('a','text');source['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash mismatch'):admit_sources('x',request([source]))
        source=observation('a','text')
        with self.assertRaisesRegex(ValueError,'Duplicate'):admit_sources('x',request([source,source]))

    def test_reconstruct_saved_real_40580_overflow(self):
        saved=Path('/Users/yvette/26fall/cse291a-team-assistant/runs/handoff-v3-20261024/handoff-v3-dataready-results')
        if not saved.exists():self.skipTest('saved development failure not present')
        from run_handoff_v3 import COORDINATE
        from semantic_claim_contract import SEMANTIC_CLAIM_CONTRACT
        call=json.loads((saved/'calls/011.json').read_text())
        state=json.loads((saved/'repeat-2/clean/state.json').read_text())
        req=copy.deepcopy(call['request']);req['step']='incident_brief'
        req['inputs'].update({n:state[n]['payload'] for n in ('coordinator_decision','retrieved_sources')})
        instruction=COORDINATE+'\nRetrieval exhausted. Return final diagnosis or uncertain; do not request more.'+SEMANTIC_CLAIM_CONTRACT
        result=preflight(instruction,req)
        self.assertEqual(result['encoded_body_bytes'],40068);self.assertEqual(result['actual_total_bytes'],40580)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENAI_BASE_URL':BASE_URL,'OPENAI_API_KEY':'test'}):
            provider=TritonProvider(Path(tmp)/'calls',max_calls=1,maximum_usd='1',input_byte_cap=40000,json_object=True)
            with patch('team_assistant.triton_provider._post') as send:
                outcome=asyncio.run(provider.complete(instruction,req));send.assert_not_called()
            self.assertEqual(outcome.tokens,0);self.assertEqual(provider.spent_usd,0)
        admitted=admit_sources(instruction,req)
        self.assertTrue(admitted['receipt']['allowed'])
        self.assertLessEqual(admitted['receipt']['actual_total_bytes'],40000)
        self.assertEqual(set(admitted['receipt']['requested_ids']),set(admitted['receipt']['admitted_ids'])|{m['id'] for m in admitted['receipt']['omitted']})

if __name__=='__main__':unittest.main()
