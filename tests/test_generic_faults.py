import copy
import unittest
from types import SimpleNamespace
from team_assistant.generic_faults import mutate_observations, inject_numeric_port
from team_assistant.propagation import verify_inputs
from team_assistant.state import ExecutionStore, Result


def observation(text, identifier='o1', **fields):
    return dict(id=identifier, text=text, tool='GetAppYAML', arguments={'app':'example'},
                availability='returned', truncated=False, sha256='immutable-source', **fields)


class GenericFaultTests(unittest.TestCase):
    def test_first_scalar_and_crlf_are_preserved(self):
        text = '# port: 80\r\n  - containerPort: 9000 # primary\r\n  targetPort: 9000\r\n'
        source = [observation(text)]
        modified, audit = mutate_observations(source)
        self.assertEqual(modified[0]['text'], text.replace('9000 #', '9001 #'))
        self.assertEqual(source[0]['text'], text)
        self.assertEqual(modified[0]['sha256'], 'immutable-source')
        self.assertEqual(audit['before'], '9000')

    def test_order_is_canonical_not_list_order(self):
        a,b = observation('port: 81','a'), observation('port: 82','b')
        b['arguments']={'z':1,'a':2}; a['arguments']={'a':1,'z':2}
        for items in ([a,b],[b,a]):
            _,audit=mutate_observations(items)
            self.assertEqual(audit['observation_id'],'a')

    def test_ineligible_no_mutation(self):
        for text in ('# port: 80','port: http','port: "80"','http://example:80',
                     'port: 0','port: 65535','port: 80junk','port: -1','port: 8.0'):
            items=[observation(text)]; result,audit=mutate_observations(items)
            self.assertFalse(audit['injected'],text); self.assertEqual(result,items)
        for field,value in [('tool','GetRecentLogs'),('availability','error'),('truncated',True)]:
            item=observation('port: 80'); item[field]=value
            self.assertFalse(mutate_observations([item])[1]['injected'])

    def fixture(self):
        source=[observation('port: 80')]; store=ExecutionStore()
        store.commit(Result('configuration_evidence',1,{'observations':copy.deepcopy(source)}),0)
        store.commit(Result('telemetry',1,{'text':'unchanged'}),0)
        store.commit(Result('diagnosis',1,{},inputs={'configuration_evidence':1,'telemetry':1}),0)
        snapshot=SimpleNamespace(observations=source,fingerprint=lambda:'v',source_version='v')
        return SimpleNamespace(snapshot=snapshot,incident=SimpleNamespace(store=store))

    def test_repair_preserves_archive_and_invalidates_descendant(self):
        inv=self.fixture(); pristine=copy.deepcopy(inv.snapshot.observations)
        telemetry=copy.deepcopy(inv.incident.store.results['telemetry'])
        self.assertTrue(inject_numeric_port(inv)['injected'])
        self.assertEqual(inv.snapshot.observations,pristine)
        self.assertIn('diagnosis',inv.incident.store.invalid())
        with self.assertRaises(ValueError): inv.incident.store.publish('diagnosis',0)
        self.assertEqual(len(verify_inputs(inv,['configuration_evidence'])['repairs']),1)
        self.assertEqual(inv.incident.store.results['configuration_evidence'].payload['observations'],pristine)
        self.assertEqual(inv.incident.store.results['telemetry'],telemetry)
        self.assertFalse(verify_inputs(inv,['configuration_evidence'])['repairs'])
        self.assertIn('diagnosis',inv.incident.store.invalid())

    def test_ambiguous_unknown_or_changed_source_fails_closed(self):
        inv=self.fixture(); inv.snapshot.observations*=2
        with self.assertRaises(ValueError): verify_inputs(inv,['configuration_evidence'])
        with self.assertRaises(ValueError): mutate_observations(inv.snapshot.observations)
        inv=self.fixture(); inv.snapshot.source_version='new'
        with self.assertRaises(ValueError): verify_inputs(inv,['configuration_evidence'])
        inv=self.fixture(); inv.incident.store.results['configuration_evidence'].payload['observations'][0]['id']='unknown'
        with self.assertRaises(ValueError): verify_inputs(inv,['configuration_evidence'])
