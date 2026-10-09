import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('admission_summary',Path(__file__).resolve().parents[1]/'scripts/summarize_admission_live.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdmissionSummaryTests(unittest.TestCase):
    def test_unknown_usage_stops_without_losing_charge(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def write(name,data):
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(data))
            write('manifest.json',{'policies':['fair-reserve','fair-admit-one'],'call_reservation':60,'limitations':[]})
            write('historical-prefix/calls/001.json',{'tokens':None,'error':'503'})
            write('summary.json',{'windows':{},'physical_tokens':60,'physical_settlements':[{'charged':60}], 'stop_reason':'prefix failed'})
            result=MODULE.summarize(root)
            self.assertEqual(result['charged_tokens'],60)
            self.assertEqual(result['unknown_usage_calls'],1)
            self.assertEqual(result['provider_errors'],1)
            self.assertEqual([r['state'] for r in result['rows']],['skipped','skipped'])
            write('summary.json',{'windows':{},'physical_tokens':0,'physical_settlements':[{'charged':60}]})
            with self.assertRaisesRegex(ValueError,'Physical ledger'):
                MODULE.summarize(root)
