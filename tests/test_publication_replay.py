"""Recorded-prefix compatibility and dependency-order replay guards."""
import copy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from team_assistant.sources import VersionedSources
from team_assistant.state import Result

SPEC = importlib.util.spec_from_file_location('publication_replay',
    Path(__file__).resolve().parents[1] / 'scripts/replay_phase2_publication.py')
replay_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay_module)


class PublicationReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        manifest = {'case': 'boutique/runtime/1', 'code_commit': 'test-commit',
                    'dataset_commit': 'test-data', 'fault_scope': 'query-fields-v2'}
        self.write('manifest.json', manifest)
        registry = VersionedSources('oncall/incident-1/boutique/runtime/1')
        observation = {'id': 'o1', 'tool': 'GetAppYAML', 'arguments': {'app_name': 'a'}, 'text': 'old'}
        old_observation = registry.record(observation)
        old_sources = {'heads': copy.deepcopy(registry.heads), 'archive': copy.deepcopy(registry.archive), 'events': []}
        key = old_observation['source_key']
        registry.advance(key, {**observation, 'text': 'new'}, 'test change')
        new_sources = {'heads': registry.heads, 'archive': registry.archive, 'events': registry.events}
        self.write('transition.json', {'events': registry.events, 'invalid': ['evidence', 'diagnosis_0']})
        for arm, version, source, obs in [('historical-prefix', 1, old_sources, old_observation),
                                        ('selective', 2, new_sources, registry.current(key, 'o2'))]:
            answer = {'status': 'uncertain' if version == 1 else 'diagnosed', 'reasoning': str(version)}
            evidence = Result('evidence', version, {'observations': [obs]},
                              sources={key: version, 'snapshot': 'snapshot-test'})
            diagnosis = Result('diagnosis_0', version, answer, inputs={'evidence': version})
            self.write(f'{arm}/state.json', {'diagnosis_0': asdict(diagnosis), 'evidence': asdict(evidence)})
            self.write(f'{arm}/sources.json', source)
            self.write(f'{arm}/result.json', {'answer': answer, 'contract_errors':
                ['uncertain: not a successful diagnosis'] if version == 1 else []})
            self.write(f'{arm}/manifest.json', {**manifest,
                'logical_prefix': 'historical-prefix' if arm == 'selective' else None})

    def write(self, path, value):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(value, default=list))

    def mutate(self, path, operation):
        value = json.loads((self.root / path).read_text())
        operation(value)
        self.write(path, value)

    def test_reorders_dependencies_and_retains_uncertain_original(self):
        result = replay_module.replay(self.root)
        self.assertEqual(result['imported_new_versions'], ['evidence', 'diagnosis_0'])
        self.assertEqual(result['ledger'][0]['payload']['status'], 'uncertain')
        self.assertEqual(result['ledger'][1]['supersedes'], 1)
        self.assertTrue(result['captured_inflight_rejected'])
        self.assertTrue(result['stale_republish_rejected'])
        self.assertEqual(result['new_model_calls'], 0)

    def test_real_contract_error_not_treated_as_uncertainty(self):
        self.mutate('historical-prefix/result.json', lambda d: d['contract_errors'].append('fabricated quote'))
        with self.assertRaisesRegex(ValueError, 'contract errors'):
            replay_module.replay(self.root)

    def test_missing_prefix_file(self):
        (self.root / 'historical-prefix/state.json').unlink()
        with self.assertRaises(FileNotFoundError):
            replay_module.replay(self.root)

    def test_mismatched_prefix_manifest(self):
        self.mutate('selective/manifest.json', lambda d: d.update(logical_prefix='another-window'))
        with self.assertRaisesRegex(ValueError, 'logical prefix'):
            replay_module.replay(self.root)

    def test_different_dataset_manifest(self):
        self.mutate('selective/manifest.json', lambda d: d.update(dataset_commit='wrong'))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            replay_module.replay(self.root)

    def test_wrong_source_head(self):
        self.mutate('selective/sources.json', lambda d: d['heads'].update({next(iter(d['heads'])): 1}))
        with self.assertRaises(ValueError):
            replay_module.replay(self.root)

    def test_out_of_order_advance(self):
        def edit(d):
            d['events'][0]['previous'] = 2
        self.mutate('transition.json', edit)
        self.mutate('selective/sources.json', edit)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            replay_module.replay(self.root)

    def test_missing_parent_not_silently_imported(self):
        self.mutate('selective/state.json', lambda d: d['diagnosis_0']['inputs'].update(evidence=3))
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            replay_module.replay(self.root)

    def test_unchanged_version_cannot_replace_payload(self):
        self.mutate('selective/state.json', lambda d: d['diagnosis_0'].update(version=1))
        with self.assertRaisesRegex(ValueError, 'without a new version'):
            replay_module.replay(self.root)

    def test_historical_archive_cannot_be_rewritten(self):
        self.mutate('selective/sources.json', lambda d: next(iter(d['archive'].values()))['1'].update(text='altered'))
        with self.assertRaisesRegex(ValueError, 'Historical source archive'):
            replay_module.replay(self.root)


if __name__ == '__main__':
    unittest.main()
