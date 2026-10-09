import json
from pathlib import Path
import tempfile
import unittest
from team_assistant.learning_candidates import corpus,validate_candidates
from team_assistant.memory import digest


class CandidateTests(unittest.TestCase):
    def test_exact_source_quote_required(self):
        sources=[{'source':'s1','observations':[{'id':'o1','text':'observed mismatch'}]}]
        value={'candidates':[{'id':'c1','rule':'If symptoms occur, compare configuration and logs before attribution.',
                             'support':[{'source':'s1','observation_id':'o1','quote':'mismatch'}]}]}
        self.assertEqual(len(validate_candidates(value,sources)['rules']),1)
        value['candidates'][0]['support'][0]['quote']='invented'
        with self.assertRaises(ValueError):validate_candidates(value,sources)

    def test_split_and_bound_audit_enforced_before_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            manifest={'case':'learning/1','split':'learning','dataset_commit':'dataset'}
            result={'answer':{'evidence':[{'observation_id':'o1'}]},'observations':[{'id':'o1','text':'fact'}]}
            scores={'scores':{'JRA':1,'ECR':1,'contract_valid':True,'budget_valid':True},'oracle':'MUST NOT BE IN CORPUS'}
            def write(name,value):(root/name).write_text(json.dumps(value))
            for name,value in [('manifest.json',manifest),('result.json',result),('scores.json',scores)]:write(name,value)
            registry={'dataset_commit':'dataset','splits':{'learning':['learning/1']}}
            with self.assertRaisesRegex(ValueError,'No eligible'):corpus([root],registry)
            audit={'supported':True,'reviewer':'independent','rationale':'reviewed','evidence_sha256':digest({'manifest':manifest,'result':result,'scores':scores})}
            write('semantic-audit.json',audit)
            data=corpus([root],registry)
            self.assertNotIn('MUST NOT BE IN CORPUS',json.dumps(data['sources']))
            self.assertNotIn('learning/1',json.dumps(data['sources']))
            result['observations'][0]['text']='changed';write('result.json',result)
            with self.assertRaisesRegex(ValueError,'No eligible'):corpus([root],registry)
            manifest['split']='gate_validation';write('manifest.json',manifest)
            with self.assertRaisesRegex(ValueError,'learning split'):corpus([root],registry)
