"""One persisted search -> proposal -> split check -> bounded repair workflow.

Child executors run in the same worker, avoiding a pool waiting on its own jobs.
Model-proposed question coverage remains unestablished, even for an entailed chain.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from engine import digest
from inference import configuration as verifier_config


class WorkflowError(ValueError):
    pass


class WorkflowCanceled(RuntimeError):
    pass


class WorkflowService:
    def __init__(self, connections, tools, agent, planner, repairer, create_check, execute_check):
        self.connections, self.tools = connections, tools
        self.agent, self.planner, self.repairer = agent, planner, repairer
        self.create_check, self.execute_check = create_check, execute_check

    def configuration(self):
        agent, planner, verifier = self.agent.configuration(), self.planner.configuration(), verifier_config()
        return {'configured': bool(agent['configured'] and planner['configured'] and verifier['configured']),
                'search_model': agent.get('model'), 'planner_model': planner.get('model'),
                'verifier_model': verifier.get('model'), 'shared_tool_budget': True,
                'source': 'protocol_fixture' if any(str(m).startswith('protocol-fixture') for m in
                          (agent.get('model'), planner.get('model'), verifier.get('model'))) else 'live_model'}

    def identity(self):
        verifier = verifier_config()
        return digest({'agent': self.agent.configuration(), 'planner': self.planner.configuration(),
                       'search_endpoint': digest(os.environ.get('EV_AGENT_URL', '')),
                       'verifier': {k: verifier[k] for k in ('urls', 'model', 'revision')}})

    def init(self):
        interrupted_ids = []
        with self.connections() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS workflows(
              id TEXT PRIMARY KEY,request_key TEXT UNIQUE,state TEXT,created REAL,updated REAL,
              input TEXT,result TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS workflow_events(
              workflow_id TEXT,sequence INTEGER,kind TEXT,payload TEXT,created REAL,
              PRIMARY KEY(workflow_id,sequence));''')
            for row in c.execute("SELECT id,result FROM workflows WHERE state IN ('queued','running','cancel_requested')").fetchall():
                result = json.loads(row['result'])
                result.update(outcome='service_restarted', current_stage='interrupted')
                c.execute("UPDATE workflows SET state='failed',result=?,error='Service restarted; inspect retained child records before retrying',updated=? WHERE id=?",
                          (json.dumps(result), time.time(), row['id']))
                interrupted_ids.append(row['id'])
        for wid in interrupted_ids:
            try:
                self.seal_sessions(wid)
            except Exception as exc:
                self.event(wid, 'restart_cleanup_failure', {'error': str(exc)[:1000]})

    def create(self, question, total_budget=6, search_budget=None, request_key=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise WorkflowError('Question must contain 1–2000 characters')
        if type(total_budget) is not int or not 4 <= total_budget <= 12:
            raise WorkflowError('A complete workflow needs a total tool budget from 4 to 12')
        if search_budget is None:
            search_budget = total_budget-2
        if type(search_budget) is not int or not 2 <= search_budget <= total_budget:
            raise WorkflowError('Search allocation must be between 2 and the total budget')
        if not self.configuration()['configured']:
            raise WorkflowError('Search, planner and verifier services must be configured')
        request_key = request_key or uuid.uuid4().hex
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
            raise WorkflowError('Request key must be 1–100 characters')
        spec = {'question': question.strip(), 'total_tool_budget': total_budget, 'search_budget_cap': search_budget,
                'max_repair_rounds': 2, 'model_identity_sha256': self.identity(),
                'services': self.configuration(),
                'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'plan_policy': 'model_proposed_unreviewed', 'scope': 'proposed_relations_only',
                'complete_question_coverage': 'not_established'}
        spec['input_sha256'] = digest(spec)
        result = {'children': {}, 'current_stage': 'queued', 'outcome': 'pending', 'candidate': None,
                  'initial_joint_result': None, 'final_joint_result': None, 'evidence': [],
                  'coverage': None, 'scope': 'proposed_relations_only', 'answer_correctness': None}
        wid, now = uuid.uuid4().hex[:16], time.time()
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT id,input FROM workflows WHERE request_key=?', (request_key,)).fetchone()
            if old:
                if json.loads(old['input'])['input_sha256'] != spec['input_sha256']:
                    raise WorkflowError('Request key already belongs to a different frozen input')
                wid = old['id']
            else:
                c.execute('INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?)',
                          (wid, request_key, 'queued', now, now, json.dumps(spec), json.dumps(result), None))
        return self.get(wid)

    def read(self, wid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM workflows WHERE id=?', (wid,)).fetchone()
        if not row:
            raise WorkflowError('Unknown workflow')
        return dict(row, input=json.loads(row['input']), result=json.loads(row['result']))

    def check_record(self, cid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM relation_checks WHERE id=?', (cid,)).fetchone()
            events = c.execute('SELECT * FROM events WHERE run_id=? ORDER BY at,rowid', (cid,)).fetchall()
        if not row:
            raise WorkflowError('Workflow joint-check artifact is missing')
        return dict(row, input=json.loads(row['input']), result=json.loads(row['result']) if row['result'] else None,
                    events=[dict(e, payload=json.loads(e['payload'])) for e in events])

    def get(self, wid):
        job = self.read(wid)
        children, used, costs, complete = {}, 0, {}, job['state'] == 'completed'
        budget_known = True
        readers = {'search': self.agent.get, 'planning': self.planner.get,
                   'verification': self.check_record, 'repair': self.repairer.get}
        for stage, cid in job['result']['children'].items():
            try:
                child = readers[stage](cid)
                children[stage] = child
                if stage in ('search', 'repair') and child.get('session'):
                    used += child['session']['used']
                result = child.get('result') or {}
                if stage == 'verification':
                    usage = result.get('provenance', {}).get('usage')
                else:
                    usage = result.get('usage')
                if isinstance(usage, dict):
                    costs[stage] = dict(usage)
                    if stage == 'planning':
                        costs[stage]['model_calls'] = 1 if result.get('raw') is not None else None
                    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens')):
                        complete = False
                if child['state'] != 'completed' or not isinstance(usage, dict) or result.get('usage_complete') is False:
                    complete = False
            except Exception as exc:
                children[stage] = {'id': cid, 'state': 'artifact_unavailable', 'error': str(exc)[:500]}
                complete = False
                if stage in ('search', 'repair'):
                    budget_known = False
        totals = {k: sum(v[k] for v in costs.values() if type(v.get(k)) is int and v[k] >= 0) for k in
                  ('model_calls', 'prompt_tokens', 'completion_tokens', 'cache_hits', 'coalesced_hits')}
        with self.connections() as c:
            events = c.execute('SELECT * FROM workflow_events WHERE workflow_id=? ORDER BY sequence', (wid,)).fetchall()
        job.update(children=children, budget={'used': used if budget_known else None, 'limit': job['input']['total_tool_budget'],
                                             'remaining': max(0, job['input']['total_tool_budget']-used) if budget_known else None},
                   usage={'known_totals': totals, 'by_stage': costs, 'complete': complete,
                          'gpu_seconds': None, 'scope': 'recorded_http_usage_only'},
                   events=[dict(e, payload=json.loads(e['payload'])) for e in events])
        return job

    def event(self, wid, kind, payload):
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            n = c.execute('SELECT coalesce(max(sequence),-1)+1 FROM workflow_events WHERE workflow_id=?', (wid,)).fetchone()[0]
            c.execute('INSERT INTO workflow_events VALUES (?,?,?,?,?)', (wid, n, kind, json.dumps(payload), time.time()))

    def checkpoint(self, wid, result):
        with self.connections() as c:
            c.execute('UPDATE workflows SET result=?,updated=? WHERE id=?', (json.dumps(result), time.time(), wid))

    def guard(self, wid):
        job = self.read(wid)
        if job['state'] in ('cancel_requested', 'canceled'):
            raise WorkflowCanceled('Workflow canceled; no next stage will start')
        if job['state'] != 'running':
            raise WorkflowError('Workflow is no longer running')
        if job['input']['model_identity_sha256'] != self.identity():
            raise WorkflowError('Model service identity changed after submission')

    def stop_children(self, wid):
        for stage, cid in self.read(wid)['result']['children'].items():
            if stage == 'search':
                self.agent.cancel(cid)
            elif stage == 'repair':
                child = self.repairer.get(cid)
                if child['input'].get('owner_workflow_id') == wid:
                    self.repairer.cancel(cid)
            elif stage == 'planning':
                with self.connections() as c:
                    c.execute("UPDATE plan_proposals SET state='canceled',error='Parent workflow canceled' WHERE id=? AND state='queued'", (cid,))
            elif stage == 'verification':
                with self.connections() as c:
                    c.execute("UPDATE relation_checks SET state='failed',error='Parent workflow canceled' WHERE id=? AND state='queued'", (cid,))

    def cancel(self, wid):
        self.read(wid)
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT state,result FROM workflows WHERE id=?', (wid,)).fetchone()
            if row['state'] == 'queued':
                result = json.loads(row['result'])
                result.update(outcome='canceled', current_stage='finished')
                changed = c.execute("UPDATE workflows SET state='canceled',result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), wid)).rowcount
            else:
                changed = c.execute("UPDATE workflows SET state='cancel_requested',updated=? WHERE id=? AND state='running'", (time.time(), wid)).rowcount
        if changed:
            self.stop_children(wid)
        return self.get(wid)

    def link(self, wid, result, stage, cid):
        result['children'][stage], result['current_stage'] = cid, stage
        self.checkpoint(wid, result)
        self.event(wid, 'stage_started', {'stage': stage, 'child_id': cid})
        try:
            self.guard(wid)
        except WorkflowCanceled:
            self.stop_children(wid)
            raise

    def seal_sessions(self, wid):
        for stage, cid in self.read(wid)['result']['children'].items():
            if stage in ('search', 'repair'):
                child = (self.agent if stage == 'search' else self.repairer).get(cid)
                if child.get('session_id'):
                    self.tools.close(child['session_id'])

    def finish(self, wid, state, result, error=None):
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT state FROM workflows WHERE id=?', (wid,)).fetchone()
            if row['state'] in ('cancel_requested', 'canceled'):
                state, result['outcome'] = 'canceled', 'canceled'
            c.execute('UPDATE workflows SET state=?,result=?,error=?,updated=? WHERE id=?',
                      (state, json.dumps(result), error[:1500] if error else None, time.time(), wid))

    def execute(self, wid):
        with self.connections() as c:
            claimed = c.execute("UPDATE workflows SET state='running',updated=? WHERE id=? AND state='queued'", (time.time(), wid)).rowcount
        if not claimed:
            return
        started, job = time.monotonic(), self.read(wid)
        spec, result, state, error = job['input'], job['result'], 'completed', None
        try:
            self.guard(wid)
            search = self.agent.create(spec['question'], spec['search_budget_cap'])
            self.link(wid, result, 'search', search['id'])
            self.agent.execute(search['id'])
            search = self.agent.get(search['id'])
            self.tools.close(search['session_id'])
            self.event(wid, 'stage_finished', {'stage': 'search', 'state': search['state']})
            self.guard(wid)
            if search['state'] != 'completed':
                raise WorkflowError(search['error'] or 'Search stage did not complete')
            if search['result']['abstained']:
                result['outcome'] = 'search_abstained'
                return
            result.update(candidate=search['result']['answer'], evidence=search['result']['evidence'])
            proposal = self.planner.create(spec['question'], result['candidate'], 'agent_job', search['id'])
            self.link(wid, result, 'planning', proposal['id'])
            self.planner.execute(proposal['id'])
            proposal = self.planner.get(proposal['id'])
            self.event(wid, 'stage_finished', {'stage': 'planning', 'state': proposal['state']})
            self.guard(wid)
            if proposal['state'] != 'completed':
                result['outcome'] = (proposal.get('result') or {}).get('status', 'planning_failure')
                raise WorkflowError(proposal['error'] or 'Planning failed')
            proposed = proposal['result']['proposal']
            result['coverage'] = proposed['coverage']
            result['plan_author'] = 'model_proposed_unreviewed'
            result['candidate_endpoint_literal_match'] = proposed['candidate_endpoint_literal_match']
            if not proposed['relations']:
                result['outcome'] = 'no_supported_plan'
                return
            checked = self.create_check(search['id'], proposal['id'], wid)
            self.link(wid, result, 'verification', checked['id'])
            self.execute_check(checked['id'], checked['input'], lambda: self.guard(wid))
            checked = self.check_record(checked['id'])
            self.event(wid, 'stage_finished', {'stage': 'verification', 'state': checked['state']})
            self.guard(wid)
            if checked['state'] != 'completed':
                result['outcome'] = ('invalid_output' if any(e['state'] == 'invalid_output' for e in checked['events']) else 'execution_failure')
                raise WorkflowError(checked['error'] or 'Joint verification failed')
            result['initial_joint_result'] = result['final_joint_result'] = checked['result']
            if checked['result']['status'] != 'ok':
                result['outcome'] = 'invalid_output'
                raise WorkflowError('Joint verification output is invalid')
            remaining = spec['total_tool_budget']-self.tools.get(search['session_id'])['used']
            if checked['result']['verdict'] == 'missing' and remaining >= 2:
                self.guard(wid)
                repair = self.repairer.create(checked['id'], remaining, spec['max_repair_rounds'], owner_workflow=wid)
                self.link(wid, result, 'repair', repair['id'])
                self.repairer.execute(repair['id'])
                repair = self.repairer.get(repair['id'])
                self.event(wid, 'stage_finished', {'stage': 'repair', 'state': repair['state']})
                self.guard(wid)
                if repair['state'] != 'completed':
                    result['outcome'] = repair['result'].get('stop_reason') or 'repair_failure'
                    raise WorkflowError(repair['error'] or 'Repair did not complete')
                result['final_joint_result'], result['evidence'] = repair['result']['final'], repair['result']['evidence']
            final = result['final_joint_result']
            result['outcome'] = final['verdict']
            if final['verdict'] == 'entailed':
                if (proposed['coverage']['blocked_clause_indices'] or proposed['coverage']['unattributed_question_spans']
                        or proposed['model_reported_coverage'] != 'complete'):
                    result['outcome'] = 'partial_chain_supported'
                elif not proposed['candidate_endpoint_literal_match']:
                    result['outcome'] = 'candidate_binding_unestablished'
                else:
                    result['outcome'] = 'proposed_chain_supported'
        except WorkflowCanceled:
            state, result['outcome'] = 'canceled', 'canceled'
        except Exception as exc:
            state, error = 'failed', str(exc)[:1500]
            if result['outcome'] == 'pending':
                result['outcome'] = 'execution_failure'
            self.event(wid, 'failure', {'stage': result['current_stage'], 'error': error})
        finally:
            try:
                if state in ('failed', 'canceled'):
                    self.stop_children(wid)
                self.seal_sessions(wid)
            except Exception as exc:
                result['cleanup_error'] = str(exc)[:1500]
                if state == 'completed':
                    state, error, result['outcome'] = 'failed', str(exc)[:1500], 'session_sealing_failure'
            result['current_stage'] = 'finished'
            result['service_seconds'] = round(time.monotonic()-started, 4)
            self.finish(wid, state, result, error)
