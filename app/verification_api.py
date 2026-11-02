"""Standalone declared-relation verification requests and cancellation."""
import json
import time
import uuid
from engine import digest
from relations import validate_plan


class VerificationRequestError(ValueError):
    pass


class VerificationConflict(ValueError):
    pass


class VerificationCanceled(RuntimeError):
    pass


class VerificationUnavailable(ValueError):
    pass


def request_spec(body):
    if set(body) - {'evidence', 'relations', 'request_key', 'client_tag'}:
        raise VerificationRequestError('Only evidence, relations, request_key and client_tag are accepted')
    plan = validate_plan(body.get('relations'))
    evidence = body.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 40:
        raise VerificationRequestError('Evidence must contain 1–40 passages')
    cleaned, ids = [], set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {'id', 'text'}:
            raise VerificationRequestError('Each passage must contain exactly id and text')
        eid, text = item['id'], item['text']
        if not isinstance(eid, str) or not eid.strip() or len(eid) > 150 or eid in ids:
            raise VerificationRequestError('Evidence IDs must be unique nonempty strings within 150 characters')
        if not isinstance(text, str) or not text.strip():
            raise VerificationRequestError('Evidence text must be nonempty')
        ids.add(eid); cleaned.append({'id': eid, 'text': text})
    if sum(len(e['text']) for e in cleaned) > 22000:
        raise VerificationRequestError('Evidence exceeds 22000 characters; no silent truncation')
    tag = body.get('client_tag', '')
    if not isinstance(tag, str) or len(tag) > 150:
        raise VerificationRequestError('Client tag must be text within 150 characters')
    spec = {'plan': plan, 'evidence': cleaned, 'parent_kind': 'standalone_evidence', 'parent_id': None,
            'scope': 'declared_relations_only', 'complete_question_coverage': 'not_established',
            'plan_author': 'external_declared', 'client_tag': tag}
    spec['input_sha256'] = digest(spec)
    return spec


class VerificationService:
    def __init__(self, connections):
        self.connections = connections

    def init(self):
        with self.connections() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS verification_requests(
              request_key TEXT PRIMARY KEY,check_id TEXT UNIQUE,input_sha256 TEXT)''')
            columns = {r[1] for r in c.execute('PRAGMA table_info(verification_requests)')}
            if 'request_sha256' not in columns:
                c.execute('ALTER TABLE verification_requests ADD COLUMN request_sha256 TEXT')

    def create(self, body, model_configured=True):
        spec = request_spec(body)
        request_sha = digest({'plan': spec['plan'], 'evidence': spec['evidence']})
        key = body.get('request_key') or uuid.uuid4().hex
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise VerificationRequestError('Request key must contain 1–100 characters')
        cid = uuid.uuid4().hex[:16]
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = c.execute('SELECT * FROM verification_requests WHERE request_key=?', (key,)).fetchone()
            if previous:
                existing_sha = previous['request_sha256']
                if existing_sha is None:
                    original = json.loads(c.execute('SELECT input FROM relation_checks WHERE id=?', (previous['check_id'],)).fetchone()[0])
                    existing_sha = digest({'plan': original['plan'], 'evidence': original['evidence']})
                if existing_sha != request_sha:
                    raise VerificationConflict('Request key already belongs to different evidence or relations')
                cid = previous['check_id']
            else:
                if not model_configured:
                    raise VerificationUnavailable('Verifier model is not configured')
                c.execute('INSERT INTO relation_checks VALUES (?,?,?,?,?,?)',
                          (cid, 'queued', time.time(), json.dumps(spec), None, None))
                c.execute('INSERT INTO verification_requests(request_key,check_id,input_sha256,request_sha256) VALUES (?,?,?,?)',
                          (key, cid, spec['input_sha256'], request_sha))
        return self.get(cid)

    def get(self, cid):
        with self.connections() as c:
            row = c.execute('''SELECT r.*,v.request_key FROM relation_checks r JOIN verification_requests v ON r.id=v.check_id
                               WHERE r.id=?''', (cid,)).fetchone()
            events = c.execute('SELECT * FROM events WHERE run_id=? ORDER BY at,rowid', (cid,)).fetchall()
        if not row:
            raise VerificationRequestError('Standalone verification not found')
        return dict(row, input=json.loads(row['input']), result=json.loads(row['result']) if row['result'] else None,
                    events=[dict(e, payload=json.loads(e['payload'])) for e in events])

    def summaries(self, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise VerificationRequestError('Invalid verification page')
        with self.connections() as c:
            total = c.execute('SELECT count(*) FROM verification_requests').fetchone()[0]
            rows = c.execute('''SELECT r.* FROM relation_checks r JOIN verification_requests v ON r.id=v.check_id
                                ORDER BY r.created DESC,r.id LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
            has_repairs = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repair_jobs'").fetchone()
            repairs = {row['id']: c.execute('SELECT id,state,created,result FROM repair_jobs WHERE parent_check_id=? ORDER BY created DESC,id DESC LIMIT 1',
                       (row['id'],)).fetchone() for row in rows} if has_repairs else {}
        summaries = []
        for row in rows:
            spec = json.loads(row['input'])
            result = json.loads(row['result']) if row['result'] else None
            first = spec['plan'][0]
            verdict = ((result or {}).get('verdict') if (result or {}).get('status') == 'ok' else 'invalid_output') if row['state'] == 'completed' else None
            repair = repairs.get(row['id'])
            latest_repair = None
            if repair:
                repaired = json.loads(repair['result']) if repair['result'] else {}
                final = repaired.get('final') or {}
                latest_repair = {'id': repair['id'], 'state': repair['state'], 'created': repair['created'],
                                 'verdict': (final.get('verdict') if final.get('status') == 'ok' else 'invalid_output') if repair['state'] == 'completed' else None}
            summaries.append({'id': row['id'], 'state': row['state'], 'created': row['created'],
                              'title': first['subject'] + ' → ' + first['predicate'] + ' → ' + first['object'],
                              'relations': len(spec['plan']), 'evidence': len(spec['evidence']),
                              'verdict': verdict, 'source': (result or {}).get('provenance', {}).get('source'),
                              'client_tag': spec.get('client_tag', ''), 'error': row['error'], 'latest_repair': latest_repair})
        return {'rows': summaries, 'total': total, 'offset': offset, 'limit': limit}

    def guard(self, cid):
        with self.connections() as c:
            state = c.execute('SELECT state FROM relation_checks WHERE id=?', (cid,)).fetchone()[0]
        if state in ('cancel_requested', 'canceled'):
            raise VerificationCanceled('Standalone verification canceled')
        if state != 'running':
            raise RuntimeError('Standalone verification is no longer running')

    def cancel(self, cid):
        self.get(cid)
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT state FROM relation_checks WHERE id=?', (cid,)).fetchone()
            if row['state'] in ('queued', 'running'):
                c.execute('UPDATE relation_checks SET state=?,error=? WHERE id=?',
                          ('canceled' if row['state'] == 'queued' else 'cancel_requested', 'Canceled by caller', cid))
        return self.get(cid)
