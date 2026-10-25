import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from team_assistant.project_eval import summarize_formal


class ProjectEvalTests(unittest.TestCase):
    def test_formal_summary_separates_partial_and_all_expiry(self):
        formal = {
            "units": 2,
            "injected_units": 1,
            "not_injected_units": 1,
            "calls": 10,
            "tokens": 100,
            "api_errors": 0,
            "header_cost_sum": "0.1",
            "arm_scores": {
                "restart": {"strict_pass": 1},
                "selective": {"strict_pass": 1},
                "expiry-restart": {"strict_pass": 1},
                "expiry-selective": {"strict_pass": 1},
            },
            "recovery_costs": [
                {"arm": "restart", "state": "completed", "calls": 3, "tokens": 90, "seconds": 9},
                {"arm": "selective", "state": "completed", "calls": 1, "tokens": 45, "seconds": 4},
                {"arm": "restart", "state": "not_injected", "calls": 0, "tokens": 0, "seconds": 0},
                {"arm": "selective", "state": "not_injected", "calls": 0, "tokens": 0, "seconds": 0},
                {"arm": "expiry-restart", "state": "completed", "calls": 3, "tokens": 70, "seconds": 7},
                {"arm": "expiry-selective", "state": "completed", "calls": 3, "tokens": 72, "seconds": 8},
                {"arm": "expiry-restart", "state": "completed", "calls": 3, "tokens": 80, "seconds": 8},
                {"arm": "expiry-selective", "state": "completed", "calls": 3, "tokens": 79, "seconds": 8},
            ],
            "limitations": ["small sample"],
        }
        result = summarize_formal(formal)
        self.assertEqual(result["partial_invalidation"]["token_reduction_percent"], 50)
        self.assertEqual(result["all_expiry"]["restart"]["calls"], 6)
        self.assertTrue(result["all_expiry"]["same_call_count"])
        self.assertFalse(result["partial_invalidation"]["paired_quality_equivalence_established"])

    def test_cli_builds_offline_reviewer_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_project_mainpath.py"), "--output", directory],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            output = Path(directory)
            expected = {
                "results.json",
                "results.csv",
                "conclusion.txt",
                "final-report-skeleton.md",
                "REPRODUCE.txt",
                "project-mainpath.zip",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            result = json.loads((output / "results.json").read_text())
            self.assertEqual(result["formal"]["partial_invalidation"]["restart_strict_pass"], 3)
            self.assertEqual(result["formal"]["partial_invalidation"]["selective_strict_pass"], 3)


if __name__ == "__main__":
    unittest.main()
