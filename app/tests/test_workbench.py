import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine
import server


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = server.DB
        server.DB = Path(self.temp.name) / 'test.sqlite'
        server.init()

    def tearDown(self):
        server.DB = self.old_db
        self.temp.cleanup()

    def test_archive_preserves_all_six_conditions(self):
        for model in engine.MODELS:
            for mode in ('direct', 'split'):
                for case_id in engine.CASES:
                    result = engine.replay(case_id, model, mode)
                    self.assertEqual(result['output']['id'], case_id)
                    self.assertEqual(result['source'], 'archived_v7')
                    self.assertEqual(result['archive_sha256'], engine.ARCHIVE['source_sha256'])

    def test_all_recorded_spans_match_original_evidence(self):
        checked = 0
        for model in engine.MODELS:
            for key, row in engine.CASES.items():
                result = engine.replay(key, model, 'split')
                spans = engine.check_spans(row, result['output'])
                self.assertTrue(all(s['valid'] for s in spans), (model, key))
                checked += len(spans)
        self.assertGreater(checked, 100)

    def test_tampered_citation_is_detected(self):
        row = next(iter(engine.CASES.values()))
        output = copy.deepcopy(engine.replay(row['id'], 'qwen14b', 'split')['output'])
        output['facts'][0]['fact']['subject_spans'][0]['text'] = 'wrong entity'
        self.assertFalse(engine.check_spans(row, output)[0]['valid'])

    def test_run_and_review_are_separate_persisted_objects(self):
        spec = {'case_id': next(iter(engine.CASES)), 'model': 'qwen14b', 'mode': 'split', 'source': 'archive'}
        with server.db() as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('test', 'queued', 0, json.dumps(spec), None, None))
        server.execute('test', spec)
        with server.db() as c:
            row = dict(c.execute("SELECT * FROM runs WHERE id='test'").fetchone())
            self.assertEqual(row['state'], 'completed')
            c.execute('INSERT INTO reviews VALUES (?,?,?,?,?)', ('review', 'test', 'missing', 'Human disagreement', 1))
        with server.db() as c:
            self.assertEqual(c.execute("SELECT result FROM runs WHERE id='test'").fetchone()[0], row['result'])
            self.assertEqual(c.execute('SELECT count(*) FROM reviews').fetchone()[0], 1)

    def test_execution_error_is_not_missing_evidence(self):
        spec = {'case_id': 'unknown', 'model': 'qwen14b', 'mode': 'split', 'source': 'archive'}
        with server.db() as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('bad', 'queued', 0, json.dumps(spec), None, None))
        server.execute('bad', spec)
        with server.db() as c:
            row = c.execute("SELECT * FROM runs WHERE id='bad'").fetchone()
            self.assertEqual(row['state'], 'failed')
            self.assertIsNone(row['result'])

    def test_restart_marks_interrupted_runs_failed(self):
        with server.db() as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('interrupted', 'running', 0, '{}', None, None))
        server.init()
        with server.db() as c:
            row = c.execute("SELECT * FROM runs WHERE id='interrupted'").fetchone()
            self.assertEqual(row['state'], 'failed')
            self.assertIsNone(row['result'])

    def test_kernel_extract_input_contains_no_target(self):
        from evpilot import relation_kernel_v7 as k
        row = next(iter(engine.CASES.values()))
        payload, cat = k.extraction_payload(row['evidence'])
        self.assertEqual(set(payload), {'predicate_definitions', 'evidence'})
        self.assertNotIn('target_relation', payload)
        self.assertTrue(cat)

    def test_fts_is_seeded_idempotently(self):
        with server.db() as c:
            count = c.execute('SELECT count(*) FROM passages').fetchone()[0]
        server.init()
        with server.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM passages').fetchone()[0], count)
            self.assertGreater(c.execute("SELECT count(*) FROM passages WHERE passages MATCH 'University'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
