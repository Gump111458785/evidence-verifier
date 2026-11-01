"""Budgeted, persistent search/open/find sessions with isolated document handles."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from engine import digest

RELATION_TERMS = {
    'employed_at': 'employment worked employer', 'educated_at': 'education studied attended',
    'in_region': 'located region', 'in_province': 'located province',
    'has_author': 'author written', 'designed_by': 'designer designed',
    'houses': 'located museum organization', 'has_child': 'child parent',
    'opened_in_year': 'opened year', 'founded_in_year': 'founded established year',
    'lasted_between': 'started ended years', 'has_no_teams': 'sports teams',
}


class RetrievalError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class SearchSessions:
    version = 'session-tools-v1'

    def __init__(self, connections, corpus):
        self.connections = connections
        self.corpus = Path(corpus)
        self._fingerprint_cache = None

    def init(self):
        with self.connections() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS search_sessions(
              id TEXT PRIMARY KEY, created REAL, parent_run TEXT, target TEXT,
              budget INTEGER, used INTEGER, state TEXT, corpus_sha256 TEXT, plan TEXT);
            CREATE TABLE IF NOT EXISTS session_docs(
              session_id TEXT, doc_id TEXT, title TEXT, text TEXT, opened INTEGER,
              PRIMARY KEY(session_id,doc_id));
            CREATE TABLE IF NOT EXISTS tool_calls(
              id TEXT PRIMARY KEY, session_id TEXT, action TEXT, args TEXT, result TEXT,
              created REAL, latency_ms REAL, cached INTEGER);
            CREATE TABLE IF NOT EXISTS retrieval_cache(key TEXT PRIMARY KEY,value TEXT);
            ''')

    def corpus_identity(self):
        if self.corpus.is_file():
            stat = self.corpus.stat()
            signature = (str(self.corpus.resolve()), stat.st_mtime_ns, stat.st_size)
            if self._fingerprint_cache and self._fingerprint_cache[0] == signature:
                return self._fingerprint_cache[1]
            sha = hashlib.sha256()
            with self.corpus.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    sha.update(chunk)
            value = sha.hexdigest()
            self._fingerprint_cache = signature, value
            return value
        with self.connections() as c:
            # Hash only the fallback corpus, not evolving sessions or review records.
            return digest([list(row) for row in c.execute('SELECT doc_id,title,text FROM passages ORDER BY doc_id')])

    def create(self, target=None, parent_run=None, budget=6):
        if type(budget) is not int or not 1 <= budget <= 12:
            raise RetrievalError('invalid_budget', 'Tool budget must be an integer from 1 to 12')
        plan = []
        if target is not None:
            if not isinstance(target, dict) or target.get('predicate') not in RELATION_TERMS:
                raise RetrievalError('unsupported_relation', 'No query template for this relation')
            for key in ('subject', 'object'):
                if not isinstance(target.get(key), str) or not target[key].strip() or len(target[key]) > 300:
                    raise RetrievalError('invalid_target', 'Target requires nonempty subject and object')
            names = [target[k] for k in ('subject', 'object') if not target[k].startswith('?')]
            if not names:
                raise RetrievalError('unbound_target', 'At least one target entity must be bound before retrieval')
            plan = [' '.join(names + [RELATION_TERMS[target['predicate']]])[:500]]
        sid = uuid.uuid4().hex[:16]
        identity = self.corpus_identity()
        with self.connections() as c:
            if parent_run:
                row = c.execute('SELECT state,result FROM runs WHERE id=?', (parent_run,)).fetchone()
                if not row or row['state'] != 'completed':
                    raise RetrievalError('invalid_parent', 'A completed parent run is required')
                result = json.loads(row['result'])
                if result['output'].get('status') != 'ok' or result['output'].get('prediction', {}).get('verdict') != 'missing':
                    raise RetrievalError('not_missing', 'Automatic follow-up requires a missing-evidence verdict')
                if result['input']['relation'] != target:
                    raise RetrievalError('target_mismatch', 'Follow-up target must match its parent run')
            c.execute('INSERT INTO search_sessions VALUES (?,?,?,?,?,?,?,?,?)',
                      (sid, time.time(), parent_run, json.dumps(target), budget, 0, 'active', identity, json.dumps(plan)))
        return self.get(sid)

    def get(self, sid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM search_sessions WHERE id=?', (sid,)).fetchone()
            if not row:
                raise RetrievalError('unknown_session', 'Search session does not exist')
            calls = c.execute('SELECT * FROM tool_calls WHERE session_id=? ORDER BY created,rowid', (sid,)).fetchall()
            docs = c.execute('SELECT doc_id,title,opened FROM session_docs WHERE session_id=?', (sid,)).fetchall()
        return dict(row, target=json.loads(row['target']), plan=json.loads(row['plan']),
                    plan_source='deterministic_relation_template',
                    calls=[dict(x, args=json.loads(x['args']), result=json.loads(x['result']), cached=bool(x['cached'])) for x in calls],
                    documents=[dict(x) for x in docs])

    def close(self, sid):
        """Seal an owned workflow session; existing evidence remains readable."""
        with self.connections() as c:
            changed = c.execute("UPDATE search_sessions SET state='closed' WHERE id=?", (sid,)).rowcount
        if not changed:
            raise RetrievalError('unknown_session', 'Search session does not exist')

    def _search(self, query, required_entity=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise RetrievalError('invalid_query', 'Query must be 1–500 characters')
        words = re.findall(r'\w+', query)[:24]
        if not words:
            raise RetrievalError('invalid_query', 'Query has no searchable terms')
        expression = ' OR '.join('"' + w + '"' for w in words)
        if required_entity is not None:
            if not isinstance(required_entity, str) or not required_entity.strip() or len(required_entity) > 300:
                raise RetrievalError('invalid_anchor', 'Required entity must be 1–300 characters')
            expression = '"' + required_entity.replace('"', '""') + '" AND (' + expression + ')'
        if self.corpus.is_file():
            with sqlite3.connect('file:' + str(self.corpus.resolve()) + '?mode=ro', uri=True) as c:
                c.row_factory = sqlite3.Row
                rows = c.execute('SELECT doc_id,title,text,bm25(search_index,0,2,1) AS score FROM search_index WHERE search_index MATCH ? ORDER BY score,doc_id LIMIT 3', (expression,)).fetchall()
        else:
            with self.connections() as c:
                rows = c.execute('SELECT *,bm25(passages) AS score FROM passages WHERE passages MATCH ? ORDER BY score,doc_id LIMIT 3', (expression,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def evidence(doc, start, end):
        return {'id': 'E' + digest([doc['doc_id'], start, end, doc['text'][start:end]])[:18],
                'doc_id': doc['doc_id'], 'title': doc['title'], 'text': doc['text'][start:end],
                'start': start, 'end': end, 'document_chars': len(doc['text'])}

    def call(self, sid, action, args):
        if action not in ('search', 'open', 'find') or not isinstance(args, dict):
            raise RetrievalError('invalid_action', 'Expected search/open/find with an argument object')
        identity = self.corpus_identity()
        started = time.monotonic()
        with self.connections() as c:
            # Reserve/check budget and persist the result atomically across concurrent requests.
            c.execute('BEGIN IMMEDIATE')
            session = c.execute('SELECT * FROM search_sessions WHERE id=?', (sid,)).fetchone()
            if not session:
                raise RetrievalError('unknown_session', 'Search session does not exist')
            if session['state'] == 'closed':
                raise RetrievalError('session_closed', 'This completed workflow session is read-only')
            if session['used'] >= session['budget']:
                raise RetrievalError('budget_exhausted', 'Tool budget has been exhausted')
            if session['corpus_sha256'] != identity:
                raise RetrievalError('corpus_changed', 'Corpus changed; create a new search session')
            cached = False
            try:
                if action == 'search':
                    key = digest([self.version, identity, args])
                    item = c.execute('SELECT value FROM retrieval_cache WHERE key=?', (key,)).fetchone()
                    if item:
                        docs, cached = json.loads(item[0]), True
                    else:
                        docs = self._search(args.get('query'), args.get('required_entity'))
                        c.execute('INSERT OR IGNORE INTO retrieval_cache VALUES (?,?)', (key, json.dumps(docs)))
                    for doc in docs:
                        c.execute('INSERT OR IGNORE INTO session_docs VALUES (?,?,?,?,0)',
                                  (sid, doc['doc_id'], doc['title'], doc['text']))
                    result = {'ok': True, 'evidence': [dict(self.evidence(d, 0, min(650, len(d['text']))), score=d['score']) for d in docs]}
                else:
                    doc = c.execute('SELECT * FROM session_docs WHERE session_id=? AND doc_id=?', (sid, args.get('doc_id'))).fetchone()
                    if not doc:
                        raise RetrievalError('unknown_handle', 'Search for this document in this session before opening it')
                    if action == 'open':
                        result = {'ok': True, 'evidence': [self.evidence(doc, 0, min(2400, len(doc['text'])))]}
                        c.execute('UPDATE session_docs SET opened=1 WHERE session_id=? AND doc_id=?', (sid, doc['doc_id']))
                    else:
                        if not doc['opened']:
                            raise RetrievalError('not_opened', 'Open the document before finding a keyword')
                        keyword = args.get('keyword')
                        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
                            raise RetrievalError('invalid_keyword', 'Keyword must be 1–100 characters')
                        hits = list(re.finditer(re.escape(keyword), doc['text'], re.I))[:5]
                        result = {'ok': True, 'evidence': [self.evidence(doc, max(0, h.start()-100), min(len(doc['text']), h.end()+250)) for h in hits], 'matches': len(hits)}
            except RetrievalError as exc:
                result = {'ok': False, 'error': exc.code, 'message': str(exc), 'evidence': []}
            used = session['used'] + 1
            c.execute('UPDATE search_sessions SET used=?,state=? WHERE id=?',
                      (used, 'exhausted' if used == session['budget'] else 'active', sid))
            cid = uuid.uuid4().hex[:16]
            c.execute('INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?)',
                      (cid, sid, action, json.dumps(args), json.dumps(result), time.time(),
                       round((time.monotonic()-started)*1000, 3), int(cached)))
        return dict(result, call_id=cid, used=used, budget=session['budget'], cached=cached)

    def collected_input(self, sid):
        session = self.get(sid)
        if not session['target']:
            raise RetrievalError('no_target', 'This exploration session has no target relation')
        evidence = {}
        for call in session['calls']:
            # Search snippets alone are not admitted as reviewed/opened evidence.
            if call['action'] in ('open', 'find') and call['result']['ok']:
                for e in call['result']['evidence']:
                    evidence[e['id']] = {'id': e['id'], 'text': e['text']}
        if not evidence:
            raise RetrievalError('no_opened_evidence', 'Open at least one retrieved document before exporting')
        if session['parent_run']:
            with self.connections() as c:
                parent = c.execute('SELECT result FROM runs WHERE id=?', (session['parent_run'],)).fetchone()
            original = json.loads(parent['result'])['input']['evidence']
            combined = {e['id']: dict(e) for e in original}
            for eid, e in evidence.items():
                if eid in combined and combined[eid]['text'] != e['text']:
                    raise RetrievalError('evidence_id_conflict', 'Collected evidence ID conflicts with parent evidence')
                combined[eid] = e
            evidence = combined
        return {'id': 'search-' + sid, 'relation': session['target'], 'evidence': list(evidence.values()),
                'retrieval_session': sid, 'parent_run': session['parent_run'], 'corpus_sha256': session['corpus_sha256']}
