import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from retrieval import SearchSessions, RetrievalError


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = server.DB
        server.DB = Path(self.temp.name) / 'work.sqlite'
        server.init()
        with server.db() as c:
            c.execute('DELETE FROM passages')
            c.execute('INSERT INTO passages VALUES (?,?,?)',
                      ('doc-education', 'Mara Education', 'Mara attended Lake University as a student in 1998. Mara later worked at Pine Institute.'))
            c.execute('INSERT INTO passages VALUES (?,?,?)',
                      ('doc-work', 'Lena Work', 'Lena worked at Neri University.'))
        self.tools = SearchSessions(server.db, Path(self.temp.name) / 'no-corpus.sqlite')
        self.tools.init()
        self.target = {'subject': 'Mara', 'subject_type': 'person', 'predicate': 'educated_at',
                       'object': 'Lake University', 'object_type': 'organization'}

    def tearDown(self):
        server.DB = self.previous
        self.temp.cleanup()

    def test_handles_cannot_cross_sessions_even_with_shared_cache(self):
        a, b = self.tools.create(), self.tools.create()
        first = self.tools.call(a['id'], 'search', {'query': 'Mara'})
        did = first['evidence'][0]['doc_id']
        result = self.tools.call(b['id'], 'open', {'doc_id': did})
        self.assertEqual(result['error'], 'unknown_handle')
        second = self.tools.call(b['id'], 'search', {'query': 'Mara'})
        self.assertTrue(second['cached'])
        self.assertTrue(self.tools.call(b['id'], 'open', {'doc_id': did})['ok'])

    def test_find_requires_open_and_preserves_exact_positions(self):
        s = self.tools.create()
        result = self.tools.call(s['id'], 'search', {'query': 'Mara'})
        did = result['evidence'][0]['doc_id']
        self.assertEqual(self.tools.call(s['id'], 'find', {'doc_id': did, 'keyword': 'student'})['error'], 'not_opened')
        original = self.tools.call(s['id'], 'open', {'doc_id': did})['evidence'][0]['text']
        result = self.tools.call(s['id'], 'find', {'doc_id': did, 'keyword': 'student'})
        for e in result['evidence']:
            self.assertEqual(original[e['start']:e['end']], e['text'])

    def test_budget_is_atomic_under_concurrent_requests(self):
        s = self.tools.create(budget=2)
        def request(_):
            try:
                return self.tools.call(s['id'], 'search', {'query': 'Mara'})
            except RetrievalError as e:
                return e.code
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(request, range(6)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 2)
        self.assertEqual(results.count('budget_exhausted'), 4)
        state = self.tools.get(s['id'])
        self.assertEqual(state['used'], 2)
        self.assertEqual(len(state['calls']), 2)
        self.assertEqual(state['state'], 'exhausted')

    def test_invalid_tool_attempt_counts_toward_budget(self):
        s = self.tools.create(budget=1)
        bad = self.tools.call(s['id'], 'open', {'doc_id': 'not-returned'})
        self.assertFalse(bad['ok'])
        self.assertEqual(bad['used'], 1)
        with self.assertRaisesRegex(RetrievalError, 'exhausted'):
            self.tools.call(s['id'], 'search', {'query': 'Mara'})

    def test_export_requires_opened_evidence_and_retains_target(self):
        s = self.tools.create(self.target)
        search = self.tools.call(s['id'], 'search', {'query': 'Mara'})
        with self.assertRaisesRegex(RetrievalError, 'Open at least'):
            self.tools.collected_input(s['id'])
        opened = self.tools.call(s['id'], 'open', {'doc_id': search['evidence'][0]['doc_id']})
        row = self.tools.collected_input(s['id'])
        self.assertEqual(row['relation'], self.target)
        self.assertEqual(row['evidence'][0]['text'], opened['evidence'][0]['text'])
        self.assertEqual(row['retrieval_session'], s['id'])

    def test_changed_corpus_invalidates_session(self):
        s = self.tools.create()
        with server.db() as c:
            c.execute("UPDATE passages SET text='Changed source text' WHERE doc_id='doc-work'")
        with self.assertRaises(RetrievalError) as ctx:
            self.tools.call(s['id'], 'search', {'query': 'Mara'})
        self.assertEqual(ctx.exception.code, 'corpus_changed')
        self.assertEqual(self.tools.get(s['id'])['used'], 0)

    def test_query_template_uses_target_not_gold_or_predicted_facts(self):
        s = self.tools.create(self.target)
        self.assertIn('Mara', s['plan'][0])
        self.assertIn('education', s['plan'][0])
        self.assertEqual(s['plan_source'], 'deterministic_relation_template')

    def test_empty_search_not_reported_as_evidence(self):
        s = self.tools.create()
        result = self.tools.call(s['id'], 'search', {'query': 'zzzz_nonexistent_zzzz'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['used'], 1)

    def test_followup_anchor_prevents_unrelated_entity_results(self):
        s = self.tools.create()
        result = self.tools.call(s['id'], 'search', {'query': 'University education student', 'required_entity': 'Unknown Person'})
        self.assertEqual(result['evidence'], [])
        result = self.tools.call(s['id'], 'search', {'query': 'University education student', 'required_entity': 'Mara'})
        self.assertEqual([e['doc_id'] for e in result['evidence']], ['doc-education'])

    def test_followup_requires_missing_and_original_target(self):
        result = {'input': {'relation': self.target, 'evidence': [{'id': 'E1', 'text': 'An original bridge fact.'}]}, 'output': {'status': 'ok', 'prediction': {'verdict': 'missing'}}}
        with server.db() as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', ('parent', 'completed', 0, '{}', json.dumps(result), None))
        s = self.tools.create(self.target, 'parent')
        self.assertEqual(s['parent_run'], 'parent')
        found = self.tools.call(s['id'], 'search', {'query': 'Mara'})
        self.tools.call(s['id'], 'open', {'doc_id': found['evidence'][0]['doc_id']})
        collected = self.tools.collected_input(s['id'])
        self.assertEqual(collected['evidence'][0], result['input']['evidence'][0])
        self.assertEqual(len(collected['evidence']), 2)
        altered = dict(self.target, object='Other University')
        with self.assertRaises(RetrievalError) as ctx:
            self.tools.create(altered, 'parent')
        self.assertEqual(ctx.exception.code, 'target_mismatch')
        result['output']['prediction']['verdict'] = 'entailed'
        with server.db() as c:
            c.execute('UPDATE runs SET result=? WHERE id=?', (json.dumps(result), 'parent'))
        with self.assertRaises(RetrievalError) as ctx:
            self.tools.create(self.target, 'parent')
        self.assertEqual(ctx.exception.code, 'not_missing')

    def test_session_survives_service_reconstruction(self):
        s = self.tools.create()
        self.tools.call(s['id'], 'search', {'query': 'Mara'})
        replacement = SearchSessions(server.db, self.tools.corpus)
        replacement.init()
        self.assertEqual(replacement.get(s['id'])['used'], 1)
        self.assertEqual(len(replacement.get(s['id'])['documents']), 1)


if __name__ == '__main__':
    unittest.main()
