import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from summarize_e5_live import summarize


class SummaryTests(unittest.TestCase):
    def test_missing_usage_cannot_disappear_from_physical_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'manifest.json').write_text(json.dumps({'preparation_tokens_already_spent':2,'physical_token_cap':20}))
            (root/'summary.json').write_text(json.dumps({'outcomes':{},'settlements':[{'actual':10,'charged':10,'halted':False}],
                'physical_tokens':10,'physical_tokens_including_preparation':12,'rejected_candidate_not_activated':True,'ledgers':{}}))
            with self.assertRaises(ValueError):summarize(root)
            p=root/'prefix';p.mkdir();(p/'usage.json').write_text(json.dumps({'calls':[{'tokens':10,'error':None}]}))
            result=summarize(root)
            self.assertEqual(result['formal_tokens'],10)
            self.assertEqual(result['known_total_tokens'],12)
            self.assertEqual(result['stages']['recovery']['outcome']['state'],'not_started')
            self.assertIsNone(result['stages']['prefix']['semantic_audit'])
