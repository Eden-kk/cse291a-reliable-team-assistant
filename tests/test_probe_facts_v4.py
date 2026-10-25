"""Schema-only deterministic fixture tests; no native labels or inference."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from probe_facts_v4 import (Ineligible,parse_probe_source,expected_extraction,validate_extraction,
                           canonical_from_extraction,render_probe_facts,inject_liveness_protocol)
YAML='''apiVersion: apps/v1
kind: Deployment
metadata:
  name: adservice
spec:
  template:
    spec:
      containers:
      - name: server
        image: example:v1
        readinessProbe:
          initialDelaySeconds: 5
          periodSeconds: 5
          grpc:
            port: 9555
        livenessProbe:
          initialDelaySeconds: 5
          periodSeconds: 5
          grpc:
            port: 9555
---
kind: Service
metadata:
  name: adservice
'''

def source(text=YAML):
    return {'id':'observation-9','tool':'GetAppYAML','arguments':{'app_name':'adservice'},
            'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'truncated':False}

class TestProbeFacts(unittest.TestCase):
    def canonical(self): return parse_probe_source(source(),'immutable-snapshot-v1')
    def test_fields_missingness_paths_and_units(self):
        c=self.canonical();live=c['facts']['adservice']['liveness']
        self.assertEqual(live['protocol'],'grpc');self.assertEqual(live['port'],9555)
        self.assertIsNone(live['failureThreshold']);self.assertEqual(live['missing_fields'],['failureThreshold'])
        self.assertTrue(live['source_paths']['port'].endswith('livenessProbe.grpc.port'))
        self.assertEqual(live['source_version'],'immutable-snapshot-v1')
        self.assertEqual(live['source_hash'],source()['sha256'])
    def test_joint_model_sentence_not_forwarded_or_needed_for_injection(self):
        c=self.canonical();model=expected_extraction(c)
        model['summary']='Both probes use grpc and five seconds; optional unsourced assertion.'
        accepted=canonical_from_extraction(model,c)
        self.assertEqual(accepted,c)
        text=render_probe_facts(accepted)
        self.assertNotIn('Both probes',text);self.assertEqual(len(text.splitlines()),2)
        bad,receipt=inject_liveness_protocol(accepted)
        self.assertEqual(bad['facts']['adservice']['liveness']['protocol'],'httpGet')
        self.assertEqual(render_probe_facts(bad).splitlines()[1],text.splitlines()[1])
        self.assertEqual(receipt['changed_paths'],['facts.adservice.liveness.protocol'])
        restored=copy.deepcopy(bad);restored['facts']['adservice']['liveness']['protocol']='grpc'
        self.assertEqual(restored,c)
    def test_wrong_model_extraction_and_defaults_rejected(self):
        c=self.canonical();model=expected_extraction(c)
        model['probes']['liveness']['failureThreshold']=3
        self.assertIn('source mismatch',' '.join(validate_extraction(model,c)))
        with self.assertRaises(Ineligible): canonical_from_extraction(model,c)
        model=expected_extraction(c);model['probes']['readiness']['periodSeconds']=True
        self.assertTrue(validate_extraction(model,c))
        model=expected_extraction(c);model['probes']['liveness']['extra']='text'
        self.assertTrue(validate_extraction(model,c))
    def test_multiple_containers_and_duplicate_fields_rejected(self):
        for text in (YAML.replace('---','      - name: sidecar\n        image: example:v1\n---',1),
                     YAML.replace('          periodSeconds: 5','          periodSeconds: 5\n          periodSeconds: 6',1),
                     YAML.replace('          grpc:\n            port: 9555','          grpc:\n            port: 9555\n          httpGet:\n            port: 9555',1)):
            with self.assertRaises(Ineligible): parse_probe_source(source(text),'v1')
    def test_incomplete_hash_and_unsupported_case_fail_closed(self):
        for key,value in [('truncated',True),('sha256','bad')]:
            record=source();record[key]=value
            with self.assertRaises(Ineligible): parse_probe_source(record,'v1')
        with self.assertRaises(Ineligible): parse_probe_source(source(),'')
        with self.assertRaises(Ineligible): parse_probe_source(source(),'v1','boutique/runtime/1')
    def test_missing_probe_flow_and_named_grpc_port_rejected(self):
        for text in (YAML.replace('        livenessProbe:', '        notLivenessProbe:'),
                     YAML.replace('          grpc:', '          grpc: {port: 9555}',1),
                     YAML.replace('            port: 9555','            port: grpc',1)):
            with self.assertRaises(Ineligible): parse_probe_source(source(text),'v1')
    def test_http_get_named_port_and_explicit_absence(self):
        text=YAML.replace('          grpc:', '          httpGet:').replace('            port: 9555','            port: "web"').replace('          initialDelaySeconds: 5\n','')
        c=parse_probe_source(source(text),'v1')
        self.assertEqual(c['facts']['adservice']['liveness']['port'],'web')
        self.assertIsNone(c['facts']['adservice']['liveness']['initialDelaySeconds'])
        bad,_=inject_liveness_protocol(c)
        self.assertEqual(bad['facts']['adservice']['liveness']['protocol'],'grpc')
        self.assertEqual(bad['facts']['adservice']['liveness']['port'],'web')
        # Constructed bad extraction may be invalid configuration; untouched fields stay untouched.
    def test_positive_period_required(self):
        with self.assertRaises(Ineligible): parse_probe_source(source(YAML.replace('periodSeconds: 5','periodSeconds: 0')),'v1')
