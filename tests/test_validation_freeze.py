import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
SPEC=importlib.util.spec_from_file_location('validation_runner',SCRIPTS/'run_learning_validation.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ValidationFreezeTests(unittest.TestCase):
    def test_freeze_matches_and_changed_rules_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            shutil.copytree(MODULE.BUNDLE,root/'bundle')
            shutil.copyfile(MODULE.ROOT/'configs/splits.json',root/'registry.json')
            shutil.copyfile(MODULE.ROOT/'configs/learning-gate.json',root/'thresholds.json')
            args=(root/'bundle',root/'registry.json',root/'thresholds.json')
            frozen,registry=MODULE.validate_freeze(*args)
            self.assertEqual(len(frozen['cases']),6)
            (root/'bundle/candidate-bundle.json').write_text('{"rules":[]}')
            with self.assertRaisesRegex(ValueError,'policy changed'):MODULE.validate_freeze(*args)

    def test_duplicate_or_missing_pairs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            shutil.copytree(MODULE.BUNDLE,root/'bundle')
            freeze=root/'bundle/freeze.json'
            data=json.loads(freeze.read_text());data['cases'][0]['order']=['baseline','baseline'];freeze.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'both policies'):
                MODULE.validate_freeze(root/'bundle',MODULE.ROOT/'configs/splits.json',MODULE.ROOT/'configs/learning-gate.json')
