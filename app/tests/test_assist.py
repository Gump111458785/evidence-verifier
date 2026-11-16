import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT)]

from cloud_runtime.api import route
from cloud_runtime.store import CloudError
from cloud_runtime import assist
from cloud_runtime.library_cases import catalog
from test_cloud_api import Storage

QUESTION = '埃菲尔铁塔是哪一年建成的？'
ANSWER = '埃菲尔铁塔于1899年建成，由古斯塔夫·埃菲尔的公司建造。'
SOURCE = 'The tower was completed in March 1889 for the World Fair. It was built by the company of Gustave Eiffel.'


class AssistModel:
    """Scripted supplier: returns fixed JSON per stage and records payloads."""

    def __init__(self, review=None, verify=None, compose=None):
        self.calls = []
        self.review = review or {
            'modes': {'correct': {'needed': True, 'reason': '有年份'},
                      'supplement': {'needed': True, 'reason': '缺少背景'},
                      'predict': {'needed': True, 'reason': '可能追问'}},
            'claims': [{'id': 'x', 'text': '埃菲尔铁塔于1899年建成', 'query': 'Eiffel Tower completed year'},
                       {'id': 'y', 'text': '由古斯塔夫·埃菲尔的公司建造', 'query': 'Eiffel Tower builder'}],
            'missing': [{'id': 'm', 'point': '建造用途', 'why': '用户可能想知道为什么建', 'kind': 'explanatory',
                         'content': '它是世界博览会的入口拱门。', 'query': None}],
            'next_questions': [{'id': 'q', 'question': '铁塔为什么差点被拆除？'}],
        }
        self.verify = verify or {
            'claims': [{'id': 'C1', 'verdict': 'contradicted', 'explanation': '证据为1889年',
                        'quote': {'evidence_id': 'E1', 'text': 'completed in March 1889'}, 'correction': '铁塔于1889年建成。'},
                       {'id': 'C2', 'verdict': 'supported', 'explanation': '证据一致',
                        'quote': {'evidence_id': 'E2', 'text': 'built by the company of Gustave Eiffel'}, 'correction': None}],
            'supplements': [],
        }
        self.compose = compose or {'revised_answer': '埃菲尔铁塔于1889年建成，由古斯塔夫·埃菲尔的公司建造，曾是世界博览会的入口拱门。',
                                   'changes': [{'finding_ids': ['C1'], 'summary': '改年份'},
                                               {'finding_ids': ['M1'], 'summary': '补用途'}]}

    def complete(self, system, payload, code):
        self.calls.append((system.split('\n')[0], payload))
        if '审阅者' in system:
            value = self.review
        elif '证据核验者' in system:
            value = self.verify
        else:
            value = self.compose
        return {'parsed': value, 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}


def docs(query, code):
    return [{'doc_id': 'https://example.org/eiffel', 'title': 'Eiffel', 'text': SOURCE, 'score': 1}]


class AssistTests(unittest.TestCase):
    def setUp(self):
        self.s = Storage()
        self.env = {'EV_CLOUD_ACCESS_CODE': 'x' * 32, 'EV_DEEPSEEK_API_KEY': 'fixture'}
        self.s._auth_kv = {}

    def call(self, model, method, path, body=None, auth=True):
        headers = {'Authorization': 'Bearer ' + 'x' * 32} if auth else {}
        with mock.patch('cloud_runtime.search.Search.query', side_effect=docs) as searched:
            value = route(method, path, headers, body or {}, self.env, self.s, model)
            self.searches = searched.call_count
        return value

    def run_job(self, model, body):
        job = self.call(model, 'POST', '/api/assist', body)
        searches = 0
        for _ in range(10):
            if job['state'] != 'queued':
                break
            job = self.call(model, 'POST', job['advance_path'])
            searches += self.searches
        return job, searches

    def test_full_pipeline_corrects_supplements_predicts_and_composes(self):
        model = AssistModel()
        job, searches = self.run_job(model, {'question': QUESTION, 'answer': ANSWER})
        self.assertEqual(job['state'], 'completed')
        result = job['result']
        self.assertEqual(result['modes']['correct']['verdict'], 'errors_found')
        claims = {c['id']: c for c in result['modes']['correct']['claims']}
        self.assertEqual(claims['C1']['verdict'], 'contradicted')
        self.assertEqual(claims['C1']['quote']['url'], 'https://example.org/eiffel')
        self.assertEqual(ANSWER[slice(*claims['C1']['span'])], claims['C1']['text'])
        self.assertEqual(result['modes']['supplement']['points'][0]['source_status'], 'not_required')
        self.assertEqual(result['modes']['predict']['questions'][0]['question'], '铁塔为什么差点被拆除？')
        self.assertEqual(result['answer']['status'], 'ok')
        self.assertIn('1889', result['prompts']['correct'])
        # review + verify + compose; two searches (the per-request cap)
        self.assertEqual(len(model.calls), 3)
        self.assertEqual(searches, assist.MAX_SEARCHES)
        self.assertEqual(result['usage'], {'model_calls': 3, 'search_calls': 2})
        # The review payload never includes sources or prior results; extraction is answer-only
        self.assertEqual(set(model.calls[0][1]), {'question', 'answer', 'context', 'requested'})

    def test_quote_that_does_not_replay_is_invalid_not_supported(self):
        verify = {'claims': [{'id': 'C1', 'verdict': 'supported', 'explanation': 'x',
                              'quote': {'evidence_id': 'E1', 'text': 'completed in 1899'}, 'correction': None},
                             {'id': 'C2', 'verdict': 'supported', 'explanation': 'x',
                              'quote': {'evidence_id': 'E2', 'text': 'built by the company of Gustave Eiffel'}, 'correction': None}],
                  'supplements': []}
        job, _ = self.run_job(AssistModel(verify=verify), {'question': QUESTION, 'answer': ANSWER, 'modes': ['correct']})
        claims = {c['id']: c for c in job['result']['modes']['correct']['claims']}
        self.assertEqual(claims['C1']['verdict'], 'invalid_output')
        self.assertEqual(job['result']['modes']['correct']['verdict'], 'unverified')

    def test_claim_not_in_answer_is_not_checked(self):
        model = AssistModel()
        model.review = dict(model.review, claims=[{'id': 'a', 'text': '铁塔高330米', 'query': 'height'}])
        job, searches = self.run_job(model, {'question': QUESTION, 'answer': ANSWER, 'modes': ['correct']})
        claim = job['result']['modes']['correct']['claims'][0]
        self.assertEqual(claim['verdict'], 'invalid_output')
        self.assertEqual(searches, 0)

    def test_manual_modes_skip_unrequested_and_no_compose_without_findings(self):
        model = AssistModel()
        job, searches = self.run_job(model, {'question': QUESTION, 'answer': ANSWER, 'modes': ['predict']})
        result = job['result']
        self.assertFalse(result['modes']['correct']['needed'])
        self.assertEqual(result['modes']['correct']['verdict'], 'not_run')
        self.assertEqual(result['modes']['supplement']['points'], [])
        self.assertEqual(len(result['modes']['predict']['questions']), 1)
        self.assertIsNone(result['answer'])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(searches, 0)

    def test_user_sources_replace_search(self):
        model = AssistModel()
        verify = dict(model.verify)
        verify['claims'] = [dict(c, quote=dict(c['quote'], evidence_id='U1')) for c in verify['claims']]
        model.verify = verify
        job, searches = self.run_job(model, {'question': QUESTION, 'answer': ANSWER, 'modes': ['correct'], 'compose': False,
                                             'sources': [{'text': SOURCE}]})
        self.assertEqual(searches, 0)
        self.assertEqual(job['result']['modes']['correct']['verdict'], 'errors_found')
        self.assertIsNone(job['result']['answer'])

    def test_invalid_review_output_is_reported_not_passed(self):
        model = AssistModel(review={'guess': 'nothing'})
        job, _ = self.run_job(model, {'question': QUESTION, 'answer': ANSWER})
        self.assertEqual(job['result']['status'], 'invalid_output')
        self.assertNotIn('modes', job['result'])

    def test_input_limits_fail_closed(self):
        with self.assertRaises(CloudError):
            self.call(AssistModel(), 'POST', '/api/assist', {'question': QUESTION, 'answer': 'x' * 5001})
        with self.assertRaises(CloudError):
            self.call(AssistModel(), 'POST', '/api/assist', {'question': QUESTION, 'answer': ANSWER, 'modes': ['guess']})

    def test_unauthenticated_cannot_create(self):
        with self.assertRaises(CloudError) as ctx:
            self.call(AssistModel(), 'POST', '/api/assist', {'question': QUESTION, 'answer': ANSWER}, auth=False)
        self.assertEqual(ctx.exception.status, 401)

    def test_history_feedback_and_consent(self):
        model = AssistModel()
        job, _ = self.run_job(model, {'question': QUESTION, 'answer': ANSWER, 'modes': ['predict']})
        self.assertIsNone(self.call(model, 'GET', '/api/consent')['research'])
        self.call(model, 'POST', '/api/consent', {'research': True})
        fb = self.call(model, 'POST', '/api/assist/' + job['id'] + '/feedback', {'target': 'predict', 'value': 'up'})
        self.assertTrue(fb['feedback'][0]['research_consent'])
        fb = self.call(model, 'POST', '/api/assist/' + job['id'] + '/feedback', {'target': 'predict', 'value': 'down'})
        self.assertEqual(len(fb['feedback']), 1)
        history = self.call(model, 'GET', '/api/assist')
        self.assertEqual(history['total'], 1)
        self.assertEqual(history['rows'][0]['predict'], 1)
        self.assertEqual(history['rows'][0]['feedback'], 1)

    def test_library_is_public_demo_and_replayable(self):
        data = self.call(AssistModel(), 'GET', '/api/library', auth=False)
        self.assertGreaterEqual(len(data['cases']), 4)
        for case in data['cases']:
            self.assertEqual(case['source'], 'demo')
            self.assertTrue(case['result']['demo'])
            for claim in case['result']['modes']['correct']['claims']:
                self.assertEqual(case['answer'][slice(*claim['span'])], claim['text'])
                if claim.get('quote'):
                    self.assertIsNone(claim['quote']['url'])
        self.assertEqual(catalog()['source'], 'demo')

    def test_excerpt_keeps_offsets_into_long_sources(self):
        text = 'filler sentence. ' * 400 + 'The Eiffel Tower was completed in 1889. ' + 'more filler. ' * 400
        parts = assist.excerpt(text, 'Eiffel Tower completed')
        self.assertLessEqual(sum(len(p['text']) for p in parts), assist.EXCERPT_CHARS)
        self.assertTrue(any('1889' in p['text'] for p in parts))
        for p in parts:
            self.assertEqual(text[p['start']:p['end']], p['text'])

    def test_markdown_fenced_json_and_omitted_optional_fields_normalize(self):
        from cloud_runtime.model import _parse_json_object
        raw = '```json\n{"modes": {"correct": {"needed": false}, "supplement": {"needed": true}, "predict": {"needed": true}}, "missing": [{"point": "建造背景", "content": "世博会拱门"}], "next_questions": ["铁塔有多高？"]}\n```'
        parsed = _parse_json_object(raw)
        normalized = assist._normalize_for_schema(parsed, 'review')
        self.assertEqual(normalized['missing'][0]['id'], 'M1')
        self.assertIsNone(normalized['missing'][0]['query'])
        self.assertEqual(normalized['next_questions'][0]['question'], '铁塔有多高？')

    def test_admin_avatar_and_unlimited_budget(self):
        from cloud_runtime.auth import Accounts
        acct = Accounts(self.s)
        created = acct.create_user(username='admin01', email='admin01@example.com', password='AdminPassword!123', role='admin')
        token, _ = acct.login('admin01', 'AdminPassword!123')
        headers = {'Authorization': 'Bearer ' + token}
        prof = route('POST', '/api/auth/profile', headers, {'display_name': '管理员一号', 'avatar': 'preset:emerald'}, self.env, self.s, AssistModel())
        self.assertEqual(prof['user']['avatar'], 'preset:emerald')
        self.s.command = lambda cmd: 25000 if cmd[0] == 'GET' else 'OK'
        budget = route('GET', '/api/budget', headers, {}, self.env, self.s, AssistModel())
        self.assertTrue(budget['unlimited'])


if __name__ == '__main__':
    unittest.main()
