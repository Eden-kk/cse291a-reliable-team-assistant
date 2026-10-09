import importlib.util
from pathlib import Path
import sys
import unittest
SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
SPEC=importlib.util.spec_from_file_location('learning_sources',SCRIPTS/'run_learning_sources.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LearningSourceTests(unittest.TestCase):
    def test_uses_first_learning_id_in_each_category_only(self):
        registry={'splits':{'learning':['b/runtime/22','b/runtime/26','b/service/47','b/performance/25'],
                            'gate_validation':['b/runtime/14'],'final_locked':['b/service/12']}}
        self.assertEqual(MODULE.selected_cases(registry),['b/runtime/22','b/service/47','b/performance/25'])
        registry['splits']['learning'].pop()
        with self.assertRaises(ValueError):MODULE.selected_cases(registry)
