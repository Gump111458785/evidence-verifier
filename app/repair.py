"""Bounded missing-relation retrieval followed by fresh evidence-only verification."""
import copy
import hashlib
import json
from pathlib import Path
import time
import uuid

from engine import digest, live
from inference import configuration
from relations import evaluate, validate_plan
from retrieval import RELATION_TERMS


class RepairError(ValueError):
    pass


class RepairCanceled(RuntimeError):
    pass


class RepairOutputError(RuntimeError):
    pass


def verifier_identity():
    config = configuration()
    return digest({k: config[k] for k in ('urls', 'model', 'revision', 'policy')})


def missing_queries(plan, checked):
    """Local bindings are search hints only; never rewrite the checked plan."""
    local = checked.get('relation_results', [])
    values = {}
    for result in local:
        if result['verdict'] == 'entailed':
            for var, value in result.get('bindings', {}).items():
                values.setdefault(var, {}).setdefault(value.strip().casefold(), value)
    hints = {var: next(iter(options.values())) for var, options in values.items() if len(options) == 1}
    gaps = []
    for i, target in enumerate(plan):
        if i >= len(local) or (local[i]['verdict'] != 'missing' and not checked.get('binding_gap')):
            continue
        grounded = copy.deepcopy(target)
        for role in ('subject', 'object'):
            grounded[role] = hints.get(target[role], target[role])
        names = list(dict.fromkeys(grounded[role] for role in ('subject', 'object')
                                  if not grounded[role].startswith('?')))
        anchors = [grounded[role] for role in ('subject', 'object')
                   if not grounded[role].startswith('?') and grounded[role + '_type'] not in ('date', 'date_range', 'boolean')]
        terms = RELATION_TERMS[target['predicate']] + (' current present' if target.get('time_scope') == 'current' else '')
        query = ' '.join(names + [terms])
        reason = ('unbound_entity' if not anchors else 'anchor_length_limit' if len(anchors[0]) > 300
                  else 'query_length_limit' if len(query) > 500 else None)
        gaps.append({'relation_index': i, 'target': copy.deepcopy(target), 'query_target': grounded,
                     'query': query if not reason else None, 'required_entity': anchors[0] if anchors else None,
                     'binding_hints': {k: v for k, v in hints.items() if k in (target['subject'], target['object'])},
                     'hint_source': 'unique_local_verified_match_not_joint_proof', 'blocked_reason': reason})
    return gaps


def merge_evidence(original, added):
    combined = {e['id']: copy.deepcopy(e) for e in original}
    known_text = {digest(e['text']) for e in original}
    for evidence in added:
        if evidence['id'] in combined and combined[evidence['id']]['text'] != evidence['text']:
            raise RepairError('Collected evidence ID conflicts with original evidence')
        if digest(evidence['text']) in known_text:
            continue
        combined[evidence['id']] = copy.deepcopy(evidence)
        known_text.add(digest(evidence['text']))
    result = list(combined.values())
    if len(result) > 40 or sum(len(e['text']) for e in result) > 22000:
        raise RepairError('Evidence context limit reached; no original or new text silently truncated')
    return result


class RepairService:
    def __init__(self, connections, tools):
        self.connections, self.tools = connections, tools

    def init(self):
        with self.connections() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS repair_jobs(
              id TEXT PRIMARY KEY,parent_check_id TEXT,state TEXT,created REAL,updated REAL,
              session_id TEXT,input TEXT,result TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS repair_events(
              job_id TEXT,sequence INTEGER,kind TEXT,payload TEXT,created REAL,
              PRIMARY KEY(job_id,sequence));
            CREATE UNIQUE INDEX IF NOT EXISTS active_repair_parent ON repair_jobs(parent_check_id)
              WHERE state IN ('preparing','queued','running','cancel_requested');
            ''')
            interrupted = c.execute("SELECT id,state,result FROM repair_jobs WHERE state IN ('preparing','queued','running','cancel_requested')").fetchall()
            for row in interrupted:
                result = json.loads(row['result'])
                result['stop_reason'] = 'service_restarted'
                c.execute("UPDATE repair_jobs SET state='failed',result=?,error='Service restarted during repair',updated=? WHERE id=?", (json.dumps(result), time.time(), row['id']))

    def create(self, parent_id, budget=6, rounds=2, owner_workflow=None):
        if type(budget) is not int or not 2 <= budget <= 12:
            raise RepairError('Additional repair tool budget must be an integer from 2 to 12')
        if type(rounds) is not int or not 1 <= rounds <= 2:
            raise RepairError('Repair supports one or two verification rounds')
        if not configuration()['configured']:
            raise RepairError('Verifier model is not configured')
        with self.connections() as c:
            row = c.execute('SELECT * FROM relation_checks WHERE id=?', (parent_id,)).fetchone()
        if not row or row['state'] != 'completed':
            raise RepairError('A completed joint relation check is required')
        parent, result = json.loads(row['input']), json.loads(row['result'])
        if result.get('status') != 'ok' or result.get('verdict') != 'missing':
            raise RepairError('Automatic repair only accepts a valid missing verdict')
        plan = validate_plan(parent['plan'])
        facts = result.get('fact_rows', parent.get('archived_output', {}).get('facts'))
        if facts is None:
            raise RepairError('This older check has no retained fact rows; submit its plan again')
        checked = evaluate(plan, facts, parent['evidence'])
        if checked.get('status') != 'ok' or checked.get('verdict') != 'missing':
            raise RepairError('Parent facts no longer replay to a valid missing judgment')
        evidence = merge_evidence(parent['evidence'], [])
        original_calls, known_session_calls = None, None
        if parent['parent_kind'] == 'agent_job':
            with self.connections() as c:
                origin = c.execute('SELECT s.used FROM agent_jobs a JOIN search_sessions s ON a.session_id=s.id WHERE a.id=?', (parent['parent_id'],)).fetchone()
            original_calls = origin['used'] if origin else None
        elif parent['parent_kind'] in ('archive_run', 'verifier_run'):
            with self.connections() as c:
                origin = c.execute('SELECT input FROM runs WHERE id=?', (parent['parent_id'],)).fetchone()
            session_id = json.loads(origin['input']).get('retrieval_session') if origin else None
            if session_id:
                # A recent follow-up session does not prove the total cost of
                # its ancestors (especially a historical archive).
                known_session_calls = self.tools.get(session_id)['used']
        spec = {'parent_check_id': parent_id, 'parent_input_sha256': digest(parent),
                'owner_workflow_id': owner_workflow,
                'parent_result_sha256': digest(result), 'plan': plan, 'evidence': evidence,
                'plan_revision': parent.get('plan_revision'), 'initial_tool_calls': original_calls,
                'known_parent_session_calls': known_session_calls,
                'additional_tool_budget': budget, 'max_rounds': rounds,
                'verifier_identity_sha256': verifier_identity(),
                'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'corpus_sha256': self.tools.corpus_identity(), 'query_policy': 'fixed_relation_template_top_documents',
                'scope': 'declared_relations_only', 'complete_question_coverage': 'not_established'}
        spec['input_sha256'] = digest(spec)
        summary = {'initial': checked, 'final': checked, 'evidence': evidence, 'rounds': [],
                   'stop_reason': None, 'model_inference_performed': False,
                   'usage': dict.fromkeys(('model_calls', 'prompt_tokens', 'completion_tokens', 'cache_hits', 'coalesced_hits'), 0),
                   'usage_complete': True, 'source': 'live_retrieval_repair',
                   'scope': 'declared_relations_only', 'complete_question_coverage': 'not_established'}
        jid, now = uuid.uuid4().hex[:16], time.time()
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            active = c.execute("SELECT id,input FROM repair_jobs WHERE parent_check_id=? AND state IN ('preparing','queued','running','cancel_requested')", (parent_id,)).fetchone()
            if active:
                old = json.loads(active['input'])
                if old.get('owner_workflow_id') != owner_workflow:
                    raise RepairError('This active repair belongs to a different execution context')
                if (old['additional_tool_budget'], old['max_rounds']) != (budget, rounds):
                    raise RepairError('This parent already has an active repair with a different budget')
                jid = active['id']
            else:
                c.execute('INSERT INTO repair_jobs VALUES (?,?,?,?,?,?,?,?,?)',
                          (jid, parent_id, 'preparing', now, now, None, json.dumps(spec), json.dumps(summary), None))
        if not active:
            try:
                session = self.tools.create(budget=budget)
                with self.connections() as c:
                    changed = c.execute("UPDATE repair_jobs SET session_id=?,state='queued',updated=? WHERE id=? AND state='preparing'", (session['id'], time.time(), jid)).rowcount
                    if not changed:
                        c.execute('UPDATE repair_jobs SET session_id=? WHERE id=?', (session['id'], jid))
                if not changed:
                    self.finish(jid, 'canceled', summary, 'canceled')
            except Exception as exc:
                self.finish(jid, 'failed', summary, 'preparation_failure', str(exc))
        return self.get(jid)

    def get(self, jid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM repair_jobs WHERE id=?', (jid,)).fetchone()
            events = c.execute('SELECT * FROM repair_events WHERE job_id=? ORDER BY sequence', (jid,)).fetchall()
        if not row:
            raise RepairError('Unknown repair job')
        session = self.tools.get(row['session_id']) if row['session_id'] else None
        return dict(row, input=json.loads(row['input']), result=json.loads(row['result']) if row['result'] else None,
                    session=session, events=[dict(e, payload=json.loads(e['payload'])) for e in events])

    def event(self, jid, kind, payload):
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            n = c.execute('SELECT coalesce(max(sequence),-1)+1 FROM repair_events WHERE job_id=?', (jid,)).fetchone()[0]
            c.execute('INSERT INTO repair_events VALUES (?,?,?,?,?)', (jid, n, kind, json.dumps(payload), time.time()))

    def cancel(self, jid):
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT state,result FROM repair_jobs WHERE id=?', (jid,)).fetchone()
            if not row:
                raise RepairError('Unknown repair job')
            if row['state'] in ('preparing', 'queued'):
                result = json.loads(row['result'])
                result['stop_reason'] = 'canceled'
                c.execute("UPDATE repair_jobs SET state='canceled',result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), jid))
            else:
                c.execute("UPDATE repair_jobs SET state='cancel_requested',updated=? WHERE id=? AND state='running'", (time.time(), jid))
        return self.get(jid)

    def guard(self, jid):
        with self.connections() as c:
            state = c.execute('SELECT state FROM repair_jobs WHERE id=?', (jid,)).fetchone()[0]
        if state in ('cancel_requested', 'canceled'):
            raise RepairCanceled('Repair canceled; no subsequent calls will start')
        if state != 'running':
            raise RepairError('Repair is no longer running')

    def finish(self, jid, state, summary, reason, error=None):
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT state,session_id,input FROM repair_jobs WHERE id=?', (jid,)).fetchone()
            if row['state'] in ('cancel_requested', 'canceled'):
                state, reason = 'canceled', 'canceled'
            summary['stop_reason'] = reason
            if row['session_id']:
                used = c.execute('SELECT used FROM search_sessions WHERE id=?', (row['session_id'],)).fetchone()[0]
                initial = json.loads(row['input'])['initial_tool_calls']
                summary['additional_tool_calls'] = used
                summary['total_tool_calls'] = initial+used if initial is not None else None
            c.execute('UPDATE repair_jobs SET state=?,updated=?,result=?,error=? WHERE id=?',
                      (state, time.time(), json.dumps(summary), error[:1500] if error else None, jid))

    def verify(self, jid, plan, evidence, round_index):
        def get(key):
            with self.connections() as c:
                row = c.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None
        def put(key, value):
            with self.connections() as c:
                c.execute('INSERT OR IGNORE INTO cache VALUES (?,?)', (key, json.dumps(value)))
        def trace(stage, state, payload):
            self.event(jid, 'verification', {'round': round_index, 'stage': stage, 'state': state, 'payload': payload})
            self.guard(jid)
            if state == 'invalid_output':
                raise RepairOutputError('Invalid model output at ' + stage)
        self.guard(jid)
        return live({'id': jid + '-round-' + str(round_index), 'relation': plan[0], 'evidence': evidence},
                    'split', get, put, trace)

    def execute(self, jid):
        with self.connections() as c:
            claimed = c.execute("UPDATE repair_jobs SET state='running',updated=? WHERE id=? AND state='queued'", (time.time(), jid)).rowcount
            state = c.execute('SELECT state FROM repair_jobs WHERE id=?', (jid,)).fetchone()[0]
        if not claimed:
            if state == 'cancel_requested':
                self.finish(jid, 'canceled', self.get(jid)['result'], 'canceled')
            return
        started, job = time.monotonic(), self.get(jid)
        spec, summary, searches, opened = job['input'], job['result'], {}, set()
        sid, plan = job['session_id'], spec['plan']
        reason, state, error = 'round_limit', 'completed', None
        try:
            for index in range(spec['max_rounds']):
                self.guard(jid)
                if self.tools.corpus_identity() != spec['corpus_sha256']:
                    raise RepairError('Corpus changed after repair submission')
                if verifier_identity() != spec['verifier_identity_sha256']:
                    raise RepairError('Verifier service identity changed after repair submission')
                session = self.tools.get(sid)
                remaining = session['budget']-session['used']
                if not remaining:
                    reason = 'tool_budget_exhausted'
                    break
                gaps = missing_queries(plan, summary['final'])
                self.event(jid, 'gap_queries', {'round': index+1, 'gaps': gaps})
                searchable = [g for g in gaps if not g['blocked_reason']]
                if not searchable:
                    reason = 'no_searchable_gap'
                    break
                # Reserve part of the one shared budget for a possible second round.
                quota = remaining if index+1 == spec['max_rounds'] else max(2, remaining//(spec['max_rounds']-index))
                limit = min(session['budget'], session['used']+quota)
                added = []
                for gap in searchable:
                    self.guard(jid)
                    if self.tools.get(sid)['used'] >= limit:
                        break
                    key = digest([gap['query'], gap['required_entity']])
                    if key not in searches:
                        found = self.tools.call(sid, 'search', {'query': gap['query'], 'required_entity': gap['required_entity']})
                        self.event(jid, 'tool_observation', {'round': index+1, 'relation_index': gap['relation_index'], 'action': 'search', 'result': found})
                        searches[key] = [e['doc_id'] for e in found['evidence']] if found['ok'] else []
                    self.guard(jid)
                    if self.tools.get(sid)['used'] >= limit:
                        break
                    doc_id = next((d for d in searches[key] if d not in opened), None)
                    if doc_id:
                        result = self.tools.call(sid, 'open', {'doc_id': doc_id})
                        self.event(jid, 'tool_observation', {'round': index+1, 'relation_index': gap['relation_index'], 'action': 'open', 'doc_id': doc_id, 'result': result})
                        opened.add(doc_id)
                        if result['ok']:
                            added.extend(result['evidence'])
                # Fill this round's remaining quota with other returned documents,
                # not repeated searches. Every document still requires an open call.
                while self.tools.get(sid)['used'] < limit:
                    self.guard(jid)
                    candidate = next(((gap, d) for gap in searchable
                                      for d in searches.get(digest([gap['query'], gap['required_entity']]), [])
                                      if d not in opened), None)
                    if candidate is None:
                        break
                    gap, doc_id = candidate
                    result = self.tools.call(sid, 'open', {'doc_id': doc_id})
                    self.event(jid, 'tool_observation', {'round': index+1, 'relation_index': gap['relation_index'], 'action': 'open', 'doc_id': doc_id, 'result': result})
                    opened.add(doc_id)
                    if result['ok']:
                        added.extend(result['evidence'])
                evidence = merge_evidence(summary['evidence'], added)
                if digest(evidence) == digest(summary['evidence']):
                    pending = any(d not in opened for docs in searches.values() for d in docs)
                    unsearched = any(digest([g['query'], g['required_entity']]) not in searches for g in searchable)
                    if index+1 < spec['max_rounds'] and (pending or unsearched) and self.tools.get(sid)['used'] < spec['additional_tool_budget']:
                        self.event(jid, 'round_without_new_evidence', {'round': index+1, 'remaining_search_work': True})
                        continue
                    reason = 'no_new_evidence'
                    break
                self.guard(jid)
                summary['verification_attempted'] = True
                summary['model_inference_performed'] = None
                # Until the whole call returns, partial token cost lives in events.
                previously_complete = summary['usage_complete']
                summary['usage_complete'] = False
                summary['pending_evidence'] = evidence
                with self.connections() as c:
                    c.execute('UPDATE repair_jobs SET result=?,updated=? WHERE id=?', (json.dumps(summary), time.time(), jid))
                verified = self.verify(jid, plan, evidence, index+1)
                summary['usage_complete'] = previously_complete and verified.get('usage_complete', False)
                for key in summary['usage']:
                    summary['usage'][key] += verified['usage'].get(key, 0)
                summary['model_inference_performed'] = bool(summary['usage']['model_calls'] or summary['usage']['coalesced_hits'])
                out = verified['output']
                checked = (evaluate(plan, out.get('facts', []), evidence) if out['status'] == 'ok'
                           else {'status': 'invalid_output', 'error': 'Extraction or independent verification contains invalid output'})
                checked.update(provenance={k: verified[k] for k in ('source', 'model', 'model_revision', 'kernel_sha256')})
                checked['grounding'] = out.get('grounding')
                checked['fact_rows'] = out.get('facts', [])
                summary['rounds'].append({'round': index+1, 'queries': gaps, 'evidence_sha256': digest(evidence),
                                          'verification': verified, 'joint_result': checked})
                summary['evidence'], summary['final'] = evidence, checked
                summary.pop('pending_evidence', None)
                with self.connections() as c:
                    c.execute('UPDATE repair_jobs SET result=?,updated=? WHERE id=?', (json.dumps(summary), time.time(), jid))
                self.event(jid, 'round_result', {'round': index+1, 'joint_result': checked})
                self.guard(jid)
                if checked['status'] != 'ok':
                    state, reason = 'failed', 'invalid_output'
                    error = checked.get('error', 'Invalid joint output')
                    break
                if checked['verdict'] != 'missing':
                    reason = checked['verdict']
                    break
            if reason == 'round_limit' and self.tools.get(sid)['used'] >= spec['additional_tool_budget']:
                reason = 'tool_budget_exhausted'
        except RepairCanceled:
            state, reason = 'canceled', 'canceled'
        except RepairOutputError as exc:
            state, reason, error = 'failed', 'invalid_output', str(exc)
        except Exception as exc:
            state, reason, error = 'failed', 'execution_failure', str(exc)[:1500]
            self.event(jid, 'failure', {'error': error})
        summary['service_seconds'] = round(time.monotonic()-started, 4)
        self.finish(jid, state, summary, reason, error)
