import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduce_diagnostics import ROOT, recompute, reproduce


class DiagnosticReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = json.loads((ROOT/'app/data/archive.json').read_text())
        cls.expected = [json.loads(x) for x in (ROOT/'app/data/diagnostics/expected.jsonl').read_text().splitlines()]

    def test_recomputes_all_six_conditions_and_both_suites(self):
        result = reproduce()
        self.assertFalse(result['new_inference_performed'])
        conditions = result['results']['qwen14b']
        self.assertEqual(conditions['direct']['regression80']['counts']['joint'], 52)
        self.assertEqual(conditions['split']['regression80']['counts']['joint'], 69)
        self.assertEqual(conditions['direct']['regression80']['counts']['positive'], 39)
        self.assertEqual(conditions['split']['regression80']['counts']['positive'], 32)
        self.assertFalse(conditions['split']['regression80']['passed'])
        self.assertEqual(len(conditions['split']['probes12']['details']), 12)

    def test_does_not_trust_stored_aggregate_counts(self):
        archive = copy.deepcopy(self.archive)
        archive['models']['qwen14b']['evaluation']['split']['regression80']['counts']['joint'] = 80
        with self.assertRaisesRegex(ValueError, 'disagree'):
            recompute(archive, self.expected)

    def test_changed_raw_prediction_is_detected(self):
        archive = copy.deepcopy(self.archive)
        raw = next(iter(archive['models']['qwen14b']['direct'].values()))
        raw['prediction']['verdict'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'disagree'):
            recompute(archive, self.expected)

    def test_duplicate_oracle_ids_are_rejected(self):
        expected = copy.deepcopy(self.expected)
        expected[1] = expected[0]
        with self.assertRaisesRegex(ValueError, 'unique'):
            recompute(self.archive, expected)

    def test_cli_runs_from_another_directory_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'
            command = [sys.executable, str(ROOT/'scripts/reproduce_diagnostics.py'), '--out', str(output)]
            run = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            first = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, cwd=directory, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), first)
