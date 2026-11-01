"""Controlled policies test execution semantics; they are not model measurements."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from retrieval import SearchSessions
from agent import AgentService, AgentError


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = server.DB
        server.DB = Path(self.temp.name) / 'agent.sqlite'
        server.init()
        with server.db() as c:
            c.execute('DELETE FROM passages')
            c.execute('INSERT INTO passages VALUES (?,?,?)', ('nora', 'Nora', 'Nora died in 1934.'))
        self.tools = SearchSessions(server.db, Path(self.temp.name) / 'absent')
        self.tools.init()
        self.agent = AgentService(server.db, self.tools)
        self.agent.init()
        self.env = patch.dict('os.environ', {'EV_AGENT_URL': 'http://fixture.invalid/v1', 'EV_AGENT_NAME': 'protocol-fixture', 'EV_AGENT_REVISION': 'v1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        server.DB = self.old_db
        self.temp.cleanup()

    @staticmethod
    def policy(messages, config):
        observations = [json.loads(m['content'])['tool_observation'] for m in messages if m['role'] == 'user' and m['content'].startswith('{"tool_observation"')]
        if not observations:
            action = {'action': 'search', 'args': {'query': 'Nora'}}
        elif len(observations) == 1:
            action = {'action': 'open', 'args': {'doc_id': observations[-1]['evidence'][0]['doc_id']}}
        else:
            action = {'action': 'answer', 'answer': '1934', 'reason': 'Controlled fixture',
                      'citations': [observations[-1]['evidence'][0]['id']]}
        return json.dumps(action), {'prompt_tokens': 30, 'completion_tokens': 10}

    def test_search_open_answer_preserves_provenance(self):
        job = self.agent.create('When did Nora die?')
        with patch.object(self.agent, 'generate', self.policy):
            self.agent.execute(job['id'])
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['result']['answer'], '1934')
        self.assertEqual(result['result']['support_status'], 'not_independently_verified')
        self.assertEqual(result['session']['used'], 2)
        self.assertEqual(result['result']['evidence'][0]['text'], 'Nora died in 1934.')
        self.assertEqual(result['result']['usage']['model_calls'], 3)
        self.assertEqual(result['config']['source'], 'protocol_fixture')

    def test_budget_violation_is_failure_not_abstention(self):
        job = self.agent.create('When did Nora die?', budget=1)
        with patch.object(self.agent, 'generate', self.policy):
            self.agent.execute(job['id'])
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertIn('budget exhaustion', result['error'])
        self.assertIsNone(result['result'])
        self.assertEqual(result['session']['used'], 1)

    def test_fabricated_or_search_only_citation_is_rejected(self):
        job = self.agent.create('Question')
        policy = lambda *a: (json.dumps({'action': 'answer', 'answer': '1934', 'reason': 'fixture', 'citations': ['not-opened']}), {})
        with patch.object(self.agent, 'generate', policy):
            self.agent.execute(job['id'])
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertIn('unopened', result['error'])

    def test_explicit_abstention_has_no_invented_answer(self):
        job = self.agent.create('Question')
        with patch.object(self.agent, 'generate', lambda *a: ('{"action":"abstain","reason":"No evidence"}', {})):
            self.agent.execute(job['id'])
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['result']['abstained'])
        self.assertIsNone(result['result']['answer'])

    def test_malformed_action_retains_raw_response(self):
        job = self.agent.create('Question')
        with patch.object(self.agent, 'generate', lambda *a: ('not JSON', {})):
            self.agent.execute(job['id'])
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'failed')
        self.assertTrue(any(e['payload'].get('raw') == 'not JSON' for e in result['events']))

    def test_cancel_during_model_call_prevents_tool_execution(self):
        job = self.agent.create('Question')
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            release.wait(3)
            return '{"action":"search","args":{"query":"Nora"}}', {}
        with patch.object(self.agent, 'generate', blocked):
            worker = threading.Thread(target=self.agent.execute, args=(job['id'],))
            worker.start()
            self.assertTrue(entered.wait(3))
            self.agent.cancel(job['id'])
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'canceled')
        self.assertEqual(result['session']['used'], 0)

    def test_canceled_queued_job_does_not_touch_another_job(self):
        a, b = self.agent.create('A'), self.agent.create('B')
        self.agent.cancel(a['id'])
        self.agent.execute(a['id'])
        self.assertEqual(self.agent.get(a['id'])['state'], 'canceled')
        self.assertEqual(self.agent.get(b['id'])['state'], 'queued')

    def test_repeated_execute_does_not_repeat_tools(self):
        job = self.agent.create('Question')
        with patch.object(self.agent, 'generate', self.policy):
            self.agent.execute(job['id'])
            self.agent.execute(job['id'])
        self.assertEqual(self.agent.get(job['id'])['session']['used'], 2)

    def test_restart_fails_interrupted_jobs(self):
        job = self.agent.create('Question')
        self.agent.init()
        self.assertEqual(self.agent.get(job['id'])['state'], 'failed')

    def test_context_guard_stops_without_silent_truncation(self):
        job = self.agent.create('Question')
        config = job['config']; config['context_char_limit'] = 1
        with server.db() as c:
            c.execute('UPDATE agent_jobs SET config=? WHERE id=?', (json.dumps(config), job['id']))
        with patch.object(self.agent, 'generate') as model:
            self.agent.execute(job['id'])
            model.assert_not_called()
        self.assertIn('no silent truncation', self.agent.get(job['id'])['error'])

    def test_cancel_wins_race_with_final_commit(self):
        job = self.agent.create('Question')
        self.agent.cancel(job['id'])
        self.agent.finish(job['id'], 'completed', {'answer': 'late result'})
        result = self.agent.get(job['id'])
        self.assertEqual(result['state'], 'canceled')
        self.assertIsNone(result['result'])


if __name__ == '__main__':
    unittest.main()
