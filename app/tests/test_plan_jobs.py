import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from engine import replay
from relations import validate_plan


class PlanJobTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_db = server.DB
        server.DB = Path(self.folder.name) / 'plans.sqlite'
        server.init()
        self.parent = replay('employment-positive.clarified', 'qwen14b', 'split')
        self.spec = {'parent_kind': 'archive_run', 'parent_id': 'source',
                     'plan': validate_plan([self.parent['input']['relation']]),
                     'evidence': self.parent['input']['evidence'], 'archived_output': self.parent['output'],
                     'model': 'qwen14b', 'archive_sha256': self.parent['archive_sha256']}

    def tearDown(self):
        server.DB = self.old_db
        self.folder.cleanup()

    def insert(self):
        with server.db() as c:
            c.execute('INSERT INTO relation_checks VALUES (?,?,?,?,?,?)', ('test', 'queued', 0, json.dumps(self.spec), None, None))

    def result(self):
        with server.db() as c:
            r = c.execute("SELECT * FROM relation_checks WHERE id='test'").fetchone()
        return dict(r, result=json.loads(r['result']) if r['result'] else None)

    def test_archived_check_is_program_execution_over_original_facts(self):
        self.insert();server.execute_plan('test', self.spec)
        r = self.result()
        self.assertEqual(r['state'], 'completed')
        self.assertEqual(r['result']['verdict'], 'entailed')
        self.assertEqual(r['result']['provenance']['source'], 'archived_v7_facts')

    def test_additional_unsupported_relation_is_missing(self):
        self.spec['plan'] += validate_plan([dict(self.parent['input']['relation'], predicate='educated_at')])
        self.insert();server.execute_plan('test', self.spec)
        self.assertEqual(self.result()['result']['verdict'], 'missing')

    def test_live_plan_extracts_once_for_all_relations(self):
        self.spec['parent_kind'] = 'agent_job'
        self.spec['plan'] += validate_plan([dict(self.parent['input']['relation'], predicate='educated_at')])
        self.insert()
        response = dict(self.parent, source='live_vllm', usage={'model_calls': 2}, model_revision='fixture', kernel_sha256='fixture')
        with patch('server.live', return_value=response) as model:
            server.execute_plan('test', self.spec)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.args[0]['evidence'], self.spec['evidence'])
        self.assertEqual(self.result()['result']['verdict'], 'missing')

    def test_invalid_parent_is_not_counted_as_missing(self):
        self.spec['archived_output'] = {'status': 'invalid_output', 'errors': ['format error']}
        self.insert();server.execute_plan('test', self.spec)
        r = self.result()['result']
        self.assertEqual(r['status'], 'invalid_output')
        self.assertNotIn('verdict', r)

    def test_live_failure_remains_failed_job(self):
        self.spec['parent_kind'] = 'agent_job';self.insert()
        with patch('server.live', side_effect=RuntimeError('model service unreachable')):
            server.execute_plan('test', self.spec)
        self.assertEqual(self.result()['state'], 'failed')
        self.assertIsNone(self.result()['result'])


if __name__ == '__main__':
    unittest.main()
