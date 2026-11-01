"""Local-first Evidence Verifier workbench. Standard-library server, SQLite persistence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from engine import ARCHIVE, CASES, MODELS, digest, replay, live, check_spans
from retrieval import SearchSessions, RetrievalError
from agent import AgentService, AgentError
from relations import validate_plan, evaluate as evaluate_plan
from scenarios import catalog as scenario_catalog, scenario as run_scenario
from inference import configuration as inference_configuration, snapshot as inference_snapshot
from planning import PlanService, PlanningError
from repair import RepairService, RepairError
from curation import CurationService, CurationError
from workflows import WorkflowService, WorkflowError
from verification_api import VerificationService, VerificationRequestError, VerificationConflict, VerificationCanceled, VerificationUnavailable
from plugin_scenarios import catalog as plugin_catalog
from sidecar import report as sidecar_report

ROOT = Path(__file__).parent
DB = Path(os.environ.get('EV_DB', ROOT / 'data/workbench.sqlite'))
POOL = ThreadPoolExecutor(max_workers=2)
CORPUS = Path(os.environ.get('EV_CORPUS', ROOT / 'data/musique-corpus.sqlite'))
_tools = None
_agent = None
_planner = None
_repairer = None
_curator = None
_workflows = None
_verifications = None


def corpus_db():
    return sqlite3.connect('file:' + str(CORPUS.resolve()) + '?mode=ro', uri=True)


def db():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def tools():
    global _tools
    if _tools is None or _tools.corpus != CORPUS:
        _tools = SearchSessions(db, CORPUS)
    return _tools


def agent():
    global _agent
    if _agent is None or _agent.tools is not tools():
        _agent = AgentService(db, tools())
    return _agent


def planner():
    global _planner
    if _planner is None:
        _planner = PlanService(db)
    return _planner


def repairer():
    global _repairer
    if _repairer is None or _repairer.tools is not tools():
        _repairer = RepairService(db, tools())
    return _repairer


def curator():
    global _curator
    if _curator is None:
        _curator = CurationService(db, ROOT / 'data/musique-corpus.sqlite')
    return _curator


def workflows():
    global _workflows
    if _workflows is None or _workflows.tools is not tools():
        _workflows = WorkflowService(db, tools(), agent(), planner(), repairer(), create_workflow_check, execute_plan)
    return _workflows


def verifications():
    global _verifications
    if _verifications is None:
        _verifications = VerificationService(db)
    return _verifications


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, state TEXT, created REAL, input TEXT, result TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS events(run_id TEXT, stage TEXT, state TEXT, payload TEXT, at REAL);
          CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, run_id TEXT, verdict TEXT, note TEXT, created REAL);
          CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE IF NOT EXISTS relation_checks(id TEXT PRIMARY KEY,state TEXT,created REAL,input TEXT,result TEXT,error TEXT);
          CREATE TABLE IF NOT EXISTS demonstrations(id TEXT PRIMARY KEY,created REAL,scenario_id TEXT,result TEXT);
          CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(doc_id UNINDEXED, title, text);''')
        if not c.execute('SELECT count(*) FROM passages').fetchone()[0]:
            docs = {}
            for row in CASES.values():
                for e in row['evidence']:
                    docs[digest(e['text'])[:16]] = e['text']
            c.executemany('INSERT INTO passages VALUES (?,?,?)',
                          [(key, 'Development evidence ' + key[:6], text) for key, text in docs.items()])
        # Interrupted jobs are terminal on restart; do not manufacture results.
        c.execute("UPDATE runs SET state='failed',error='Service restarted during execution' WHERE state IN ('queued','running')")
        c.execute("UPDATE relation_checks SET state='failed',error='Service restarted during relation check' WHERE state IN ('queued','running','cancel_requested')")
    tools().init()
    agent().init()
    planner().init()
    repairer().init()
    curator().init()
    workflows().init()
    verifications().init()


def event(rid, stage, state, payload):
    with db() as c:
        c.execute('INSERT INTO events VALUES (?,?,?,?,?)', (rid, stage, state, json.dumps(payload), time.time()))


def execute(rid, spec):
    start = time.monotonic()
    try:
        with db() as c:
            c.execute("UPDATE runs SET state='running' WHERE id=?", (rid,))
        event(rid, 'input', 'completed', {'sha256': digest(spec)})
        if spec['source'] == 'archive':
            result = replay(spec['case_id'], spec['model'], spec['mode'])
            event(rid, 'archive', 'completed', {'source': result['archive_sha256']})
        else:
            def get(key):
                with db() as c:
                    item = c.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
                return json.loads(item[0]) if item else None
            def put(key, value):
                with db() as c:
                    c.execute('INSERT OR IGNORE INTO cache VALUES (?,?)', (key, json.dumps(value)))
            result = live(spec.get('custom_input') or CASES[spec['case_id']], spec['mode'], get, put,
                          lambda stage, state, payload: event(rid, stage, state, payload))
        result['span_checks'] = check_spans(result['input'], result['output'])
        result['service_seconds'] = round(time.monotonic() - start, 4)
        event(rid, 'provenance', 'completed', {'checks': len(result['span_checks']),
               'invalid': sum(not s['valid'] for s in result['span_checks'])})
        with db() as c:
            c.execute("UPDATE runs SET state='completed',result=? WHERE id=?", (json.dumps(result), rid))
    except Exception as exc:
        event(rid, 'execution', 'failed', {'error': str(exc)[:1500]})
        with db() as c:
            c.execute("UPDATE runs SET state='failed',error=? WHERE id=?", (str(exc)[:1500], rid))


def execute_plan(cid, spec, guard=None):
    with db() as c:
        claimed = c.execute("UPDATE relation_checks SET state='running' WHERE id=? AND state='queued'", (cid,)).rowcount
    if not claimed:
        return
    standalone = spec['parent_kind'] == 'standalone_evidence'
    def check_execution():
        if standalone:
            verifications().guard(cid)
        if guard:
            guard()
    try:
        check_execution()
        if spec['parent_kind'] in ('agent_job', 'standalone_evidence'):
            def get(key):
                with db() as c:
                    r = c.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
                return json.loads(r[0]) if r else None
            def put(key, value):
                with db() as c:
                    c.execute('INSERT OR IGNORE INTO cache VALUES (?,?)', (key, json.dumps(value)))
            # Extract/verify evidence once; joint plan matching never goes into extraction.
            def trace(stage, state, payload):
                event(cid, stage, state, payload)
                check_execution()
            result = live({'id': cid, 'relation': spec['plan'][0], 'evidence': spec['evidence']},
                          'split', get, put, trace)
            out = result['output']
            provenance = {'source': result['source'], 'model': result['model'], 'usage': result['usage'],
                          'usage_complete': result.get('usage_complete', False),
                          'model_revision': result['model_revision'], 'kernel_sha256': result['kernel_sha256']}
        else:
            out = spec['archived_output']
            provenance = spec.get('parent_provenance') or {'source': 'archived_v7_facts', 'model': spec['model'], 'archive_sha256': spec['archive_sha256']}
        checked = (evaluate_plan(spec['plan'], out.get('facts', []), spec['evidence']) if out['status'] == 'ok'
                   else {'status': 'invalid_output', 'error': 'Parent extraction or verification contains invalid output'})
        checked['provenance'] = provenance
        checked['plan_revision'] = spec.get('plan_revision')
        checked['fact_rows'] = out.get('facts', [])
        if 'usage_complete' in provenance:
            checked['usage_complete'] = provenance['usage_complete']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            state = c.execute('SELECT state FROM relation_checks WHERE id=?', (cid,)).fetchone()[0]
            if standalone and state in ('cancel_requested', 'canceled'):
                c.execute("UPDATE relation_checks SET state='canceled',error='Canceled by caller' WHERE id=?", (cid,))
            else:
                c.execute("UPDATE relation_checks SET state='completed',result=? WHERE id=?", (json.dumps(checked), cid))
    except VerificationCanceled as exc:
        with db() as c:
            c.execute("UPDATE relation_checks SET state='canceled',error=? WHERE id=?", (str(exc), cid))
    except Exception as exc:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            state = c.execute('SELECT state FROM relation_checks WHERE id=?', (cid,)).fetchone()[0]
            canceled = standalone and state in ('cancel_requested', 'canceled')
            c.execute('UPDATE relation_checks SET state=?,error=? WHERE id=?',
                      ('canceled' if canceled else 'failed', str(exc)[:1500], cid))


def create_workflow_check(agent_id, proposal_id, workflow_id):
    parent, proposed = agent().get(agent_id), planner().get(proposal_id)
    if parent['state'] != 'completed' or not parent['result'] or parent['result']['abstained']:
        raise ValueError('Workflow search did not produce a candidate')
    identity = proposed['input']
    if (proposed['state'] != 'completed' or identity['parent_kind'] != 'agent_job' or identity['parent_id'] != agent_id
            or identity['question'] != parent['question'].strip() or identity['candidate'] != parent['result']['answer'].strip()):
        raise ValueError('Workflow proposal does not match its search candidate')
    plan = validate_plan(proposed['result']['proposal']['relations'])
    spec = {'plan': plan, 'parent_kind': 'agent_job', 'parent_id': agent_id,
            'owner_workflow_id': workflow_id, 'scope': 'declared_relations_only',
            'plan_author': 'model_proposed_unreviewed',
            'plan_proposal': {'id': proposal_id, 'result_sha256': digest(proposed['result']),
                              'input_sha256': identity['input_sha256']},
            'evidence': [{'id': e['id'], 'text': e['text']} for e in parent['result']['evidence']],
            'question': parent['question'], 'candidate': parent['result']['answer']}
    spec['input_sha256'] = digest(spec)
    cid = uuid.uuid4().hex[:16]
    with db() as c:
        c.execute('INSERT INTO relation_checks VALUES (?,?,?,?,?,?)', (cid, 'queued', time.time(), json.dumps(spec), None, None))
    return {'id': cid, 'state': 'queued', 'input': spec}


class Handler(BaseHTTPRequestHandler):
    def send(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path)
        query = parse_qs(path.query)
        try:
            if path.path == '/api/health':
                with db() as c:
                    count = c.execute('SELECT count(*) FROM passages').fetchone()[0]
                corpus_scope = 'development_diagnostics'
                if CORPUS.is_file():
                    with corpus_db() as c:
                        count = c.execute('SELECT count(*) FROM docs').fetchone()[0]
                    corpus_scope = 'musique_dev_passages'
                return self.send({'ok': True, 'live_configured': inference_configuration()['configured'],
                    'agent': AgentService.configuration(), 'planner': PlanService.configuration(), 'workflow': workflows().configuration(),
                    'corpus_docs': count, 'corpus_scope': corpus_scope, 'scope': ARCHIVE['scope'], 'archive_sha256': ARCHIVE['source_sha256']})
            if path.path.startswith('/api/verifications/'):
                return self.send(verifications().get(path.path.removeprefix('/api/verifications/')))
            if path.path == '/api/data-candidates':
                rows = curator().summaries()
                offset = max(0, int(query.get('offset', ['0'])[0]))
                return self.send({'rows': rows[offset:offset+50], 'total': len(rows), 'offset': offset, 'limit': 50,
                    'counts': {'pending': sum(r['decision'] == 'pending' for r in rows),
                               'included': sum(r['decision'] == 'include' for r in rows),
                               'training_source': sum(r['usage'] == 'train_candidate' for r in rows)}})
            if path.path == '/api/data-template':
                return self.send([json.loads(line) for line in (ROOT/'configs/curation-fixture.jsonl').read_text().splitlines() if line.strip()])
            if path.path.startswith('/api/data-candidates/'):
                return self.send(curator().get(path.path.removeprefix('/api/data-candidates/')))
            if path.path == '/api/data-exports':
                with db() as c:
                    rows = c.execute('SELECT manifest FROM data_exports ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([json.loads(r['manifest']) for r in rows])
            if path.path.startswith('/api/data-exports/'):
                return self.send(curator().exported(path.path.removeprefix('/api/data-exports/')))
            if path.path == '/api/plan-proposals':
                with db() as c:
                    rows = c.execute('SELECT id,state,created,input,error FROM plan_proposals ORDER BY created DESC').fetchall()
                values = [dict(r, input=json.loads(r['input'])) for r in rows]
                for field in ('parent_kind', 'parent_id'):
                    if field in query:
                        values = [r for r in values if r['input'][field] == query[field][0]]
                return self.send(values[:100])
            if path.path.startswith('/api/plan-proposals/'):
                return self.send(planner().get(path.path.removeprefix('/api/plan-proposals/')))
            if path.path.startswith('/api/plan-revisions/'):
                return self.send(planner().revision(path.path.removeprefix('/api/plan-revisions/')))
            if path.path == '/api/agent-jobs':
                with db() as c:
                    rows = c.execute('SELECT id,state,created,updated,question,error FROM agent_jobs ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([dict(r) for r in rows])
            if path.path == '/api/workflows':
                with db() as c:
                    rows = c.execute('SELECT id,state,created,updated,input,result,error FROM workflows ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([{'id': r['id'], 'state': r['state'], 'created': r['created'],
                                   'question': json.loads(r['input'])['question'], 'outcome': json.loads(r['result'])['outcome'],
                                   'error': r['error']} for r in rows])
            if path.path.startswith('/api/workflows/'):
                return self.send(workflows().get(path.path.removeprefix('/api/workflows/')))
            if path.path == '/api/repair-jobs':
                with db() as c:
                    if 'parent_check_id' in query:
                        rows = c.execute('SELECT id,parent_check_id,state,created,updated,session_id,error FROM repair_jobs WHERE parent_check_id=? ORDER BY created DESC LIMIT 100', (query['parent_check_id'][0],)).fetchall()
                    else:
                        rows = c.execute('SELECT id,parent_check_id,state,created,updated,session_id,error FROM repair_jobs ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([dict(r) for r in rows])
            if path.path.startswith('/api/repair-jobs/'):
                return self.send(repairer().get(path.path.removeprefix('/api/repair-jobs/')))
            if path.path.startswith('/api/agent-jobs/'):
                return self.send(agent().get(path.path.removeprefix('/api/agent-jobs/')))
            if path.path == '/api/inference-status':
                return self.send(inference_snapshot())
            if path.path == '/api/cases':
                return self.send(list(CASES.values()))
            if path.path == '/api/scenarios':
                return self.send(scenario_catalog())
            if path.path == '/api/plugin-demo':
                return self.send(plugin_catalog())
            if path.path.startswith('/api/relation-checks/'):
                cid = path.path.removeprefix('/api/relation-checks/')
                with db() as c:
                    row = c.execute('SELECT * FROM relation_checks WHERE id=?', (cid,)).fetchone()
                    events = c.execute('SELECT * FROM events WHERE run_id=? ORDER BY at', (cid,)).fetchall()
                if not row:
                    return self.send({'error': 'Relation check not found'}, 404)
                return self.send(dict(row, input=json.loads(row['input']), result=json.loads(row['result']) if row['result'] else None,
                                      events=[dict(e, payload=json.loads(e['payload'])) for e in events]))
            if path.path == '/api/evaluations':
                return self.send({k: v['evaluation'] for k, v in ARCHIVE['models'].items()})
            if path.path == '/api/search-sessions':
                with db() as c:
                    rows = c.execute('SELECT id,created,parent_run,budget,used,state FROM search_sessions ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([dict(r) for r in rows])
            if path.path.startswith('/api/search-sessions/'):
                parts = path.path.strip('/').split('/')
                if len(parts) == 4 and parts[3] == 'input':
                    return self.send(tools().collected_input(parts[2]))
                if len(parts) == 3:
                    return self.send(tools().get(parts[2]))
                return self.send({'error': 'Not found'}, 404)
            if path.path == '/api/search':
                import re
                words = re.findall(r'\w+', query.get('q', [''])[0])[:20]
                if not words:
                    return self.send([])
                expression = ' OR '.join('"' + w + '"' for w in words)
                if CORPUS.is_file():
                    with corpus_db() as c:
                        c.row_factory = sqlite3.Row
                        rows = c.execute('SELECT doc_id,title,text,bm25(search_index,0,2,1) AS score FROM search_index WHERE search_index MATCH ? ORDER BY score,doc_id LIMIT 20', (expression,)).fetchall()
                    return self.send([dict(row) for row in rows])
                with db() as c:
                    rows = c.execute('SELECT *,bm25(passages) AS score FROM passages WHERE passages MATCH ? ORDER BY score LIMIT 20', (expression,)).fetchall()
                return self.send([dict(row) for row in rows])
            if path.path == '/api/runs':
                with db() as c:
                    rows = c.execute('SELECT id,state,created,input,error FROM runs ORDER BY created DESC LIMIT 100').fetchall()
                return self.send([dict(r, input=json.loads(r['input'])) for r in rows])
            if path.path.startswith('/api/runs/'):
                rid = path.path.removeprefix('/api/runs/')
                with db() as c:
                    row = c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
                    if not row:
                        return self.send({'error': 'Run not found'}, 404)
                    events = c.execute('SELECT * FROM events WHERE run_id=? ORDER BY at', (rid,)).fetchall()
                    reviews = c.execute('SELECT * FROM reviews WHERE run_id=? ORDER BY created', (rid,)).fetchall()
                return self.send(dict(row, input=json.loads(row['input']), result=json.loads(row['result']) if row['result'] else None,
                                      events=[dict(e, payload=json.loads(e['payload'])) for e in events], reviews=[dict(r) for r in reviews]))
            if path.path.startswith('/api/'):
                return self.send({'error': 'Not found'}, 404)
            assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
                      '/sidecar.html': 'sidecar.html', '/sidecar.js': 'sidecar.js', '/theme.css': 'theme.css', '/plugin.html': 'plugin.html', '/plugin.js': 'plugin.js', '/plugin.css': 'plugin.css',
                      '/downloads/evidence-verifier-extension.zip': 'downloads/evidence-verifier-extension.zip'}
            if path.path.startswith('/fonts/'):
                name = path.path.removeprefix('/fonts/')
                if name and '/' not in name and name != '..' and (ROOT / 'static/fonts' / name).is_file():
                    assets[path.path] = 'fonts/' + name
            if path.path not in assets:
                return self.send({'error': 'Not found'}, 404)
            file = ROOT / 'static' / assets[path.path]
            types = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.woff2': 'font/woff2', '.zip': 'application/zip'}
            self.send_response(200)
            self.send_header('Content-Type', types[file.suffix] + ('; charset=utf-8' if file.suffix in ('.html', '.js', '.css') else ''))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(file.read_bytes())
        except RetrievalError as exc:
            self.send({'error': str(exc), 'code': exc.code}, 404 if exc.code == 'unknown_session' else 409)
        except AgentError as exc:
            self.send({'error': str(exc)}, 404)
        except PlanningError as exc:
            self.send({'error': str(exc)}, 404)
        except RepairError as exc:
            self.send({'error': str(exc)}, 404)
        except CurationError as exc:
            self.send({'error': str(exc)}, 404)
        except WorkflowError as exc:
            self.send({'error': str(exc)}, 404)
        except VerificationRequestError as exc:
            self.send({'error': str(exc), 'code': 'verification_not_found'}, 404)
        except Exception as exc:
            self.send({'error': str(exc)}, 500)

    def do_POST(self):
        # Same-origin local workbench; no untrusted site can submit jobs/reviews.
        origin = self.headers.get('Origin')
        extension_id = os.environ.get('EV_EXTENSION_ID', '')
        extension_origin = ('chrome-extension://' + extension_id) if re.fullmatch(r'[a-p]{32}', extension_id) else None
        if origin and origin != 'http://' + self.headers.get('Host', '') and origin != extension_origin:
            return self.send({'error': 'Cross-origin mutation rejected'}, 403)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 200000:
                return self.send({'error': 'Body must be 1–200000 bytes'}, 400)
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Request body must be a JSON object')
            if self.path == '/api/sidecar/plans':
                job = planner().create(body.get('question'), body.get('candidate'), 'sidecar', uuid.uuid4().hex[:16])
                POOL.submit(planner().execute, job['id'])
                return self.send({'id':job['id'], 'state':job['state']}, 202)
            if self.path == '/api/sidecar/report':
                job = verifications().get(body.get('verification_id'))
                repair = repairer().get(body['repair_id']) if body.get('repair_id') else None
                return self.send(sidecar_report(job, body.get('question'), body.get('candidate'), repair))
            if self.path == '/api/verifications':
                job = verifications().create(body, inference_configuration()['configured'])
                if job['state'] == 'queued':
                    POOL.submit(execute_plan, job['id'], job['input'])
                return self.send({'id': job['id'], 'state': job['state']}, 202)
            if self.path.startswith('/api/verifications/') and self.path.endswith('/cancel'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid standalone verification cancellation path')
                return self.send(verifications().cancel(parts[2]))
            if self.path == '/api/workflows':
                job = workflows().create(body.get('question'), body.get('budget', 6), body.get('search_budget'), body.get('request_key'))
                if job['state'] == 'queued':
                    POOL.submit(workflows().execute, job['id'])
                return self.send({'id': job['id'], 'state': job['state']}, 202)
            if self.path.startswith('/api/workflows/') and self.path.endswith('/cancel'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid workflow cancellation path')
                return self.send(workflows().cancel(parts[2]))
            if self.path == '/api/data-candidates':
                return self.send({'ids': curator().add(body.get('records'))}, 201)
            if self.path == '/api/data-candidates/from-run':
                return self.send(curator().from_run(body.get('run_id'), body.get('task'), body.get('fact_index')), 201)
            if self.path == '/api/data-candidates/from-check':
                return self.send(curator().from_check(body.get('check_id'), body.get('task'), body.get('fact_index')), 201)
            if self.path.startswith('/api/data-candidates/') and self.path.endswith('/reviews'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid curation review path')
                return self.send(curator().review(parts[2], body), 201)
            if self.path == '/api/data-exports':
                return self.send(curator().export(), 201)
            if self.path == '/api/repair-jobs':
                if not inference_configuration()['configured']:
                    return self.send({'error': 'Verifier model is not configured'}, 409)
                job = repairer().create(body.get('parent_check_id'), body.get('budget', 6), body.get('rounds', 2))
                if job['state'] == 'queued':
                    POOL.submit(repairer().execute, job['id'])
                return self.send({'id': job['id'], 'state': job['state']}, 202)
            if self.path.startswith('/api/repair-jobs/') and self.path.endswith('/cancel'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid repair cancellation path')
                return self.send(repairer().cancel(parts[2]))
            if self.path == '/api/plan-proposals':
                if not PlanService.configuration()['configured']:
                    return self.send({'error': 'Relation planner model is not configured'}, 409)
                kind, parent_id = body.get('parent_kind'), body.get('parent_id')
                if kind == 'agent_job':
                    parent = agent().get(parent_id)
                    if parent['state'] != 'completed' or not parent['result'] or parent['result']['abstained']:
                        raise ValueError('A proposal requires a completed candidate answer')
                    question, candidate = parent['question'], parent['result']['answer']
                elif kind == 'verifier_run':
                    with db() as c:
                        parent = c.execute('SELECT state,result FROM runs WHERE id=?', (parent_id,)).fetchone()
                    if not parent or parent['state'] != 'completed' or json.loads(parent['result'])['mode'] != 'split':
                        raise ValueError('A proposal requires a completed split verifier run')
                    # Diagnostic archives often contain only a target, so the user
                    # may supply a question here. No evidence is sent to planning.
                    question, candidate = body.get('question'), body.get('candidate')
                else:
                    raise ValueError('Unknown proposal parent source')
                job = planner().create(question, candidate, kind, parent_id)
                POOL.submit(planner().execute, job['id'])
                return self.send({'id': job['id'], 'state': job['state']}, 202)
            if self.path.startswith('/api/plan-proposals/') and self.path.endswith('/revisions'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid revision path')
                return self.send(planner().revise(parts[2], body.get('relations'), body.get('note')), 201)
            if self.path.startswith('/api/scenarios/') and self.path.endswith('/run'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid scenario path')
                result = run_scenario(parts[2])
                result['demonstration_id'] = uuid.uuid4().hex[:16]
                with db() as c:
                    c.execute('INSERT INTO demonstrations VALUES (?,?,?,?)', (result['demonstration_id'], time.time(), parts[2], json.dumps(result)))
                return self.send(result, 201)
            if self.path == '/api/relation-checks':
                plan = validate_plan(body.get('relations'))
                kind = body.get('parent_kind')
                parent_id = body.get('parent_id')
                spec = {'plan': plan, 'parent_kind': kind, 'parent_id': parent_id,
                        'scope': 'declared_relations_only', 'plan_author': 'user_declared'}
                if kind in ('archive_run', 'verifier_run'):
                    with db() as c:
                        parent = c.execute('SELECT state,result FROM runs WHERE id=?', (parent_id,)).fetchone()
                    if not parent or parent['state'] != 'completed':
                        raise ValueError('Parent run must be completed')
                    parent = json.loads(parent['result'])
                    if parent['source'] not in ('archived_v7', 'live_vllm') or parent['mode'] != 'split':
                        raise ValueError('Plan checks require independently verified split facts')
                    spec.update(evidence=parent['input']['evidence'], archived_output=parent['output'], model=parent['model'],
                                archive_sha256=parent.get('archive_sha256'), parent_provenance={
                                    'source': 'archived_v7_facts' if parent['source'] == 'archived_v7' else 'live_verified_facts',
                                    'model': parent['model'], 'archive_sha256': parent.get('archive_sha256'),
                                    'model_revision': parent.get('model_revision'), 'kernel_sha256': parent.get('kernel_sha256')})
                elif kind == 'agent_job':
                    if not inference_configuration()['configured']:
                        return self.send({'error': 'Verifier model is not configured'}, 409)
                    parent = agent().get(parent_id)
                    if parent['state'] != 'completed' or not parent['result'] or parent['result']['abstained']:
                        raise ValueError('Parent must have a completed candidate answer')
                    spec.update(evidence=[{'id': e['id'], 'text': e['text']} for e in parent['result']['evidence']],
                                candidate=parent['result']['answer'], question=parent['question'])
                else:
                    raise ValueError('Unknown relation-plan source')
                if body.get('plan_revision_id'):
                    revision = planner().revision(body['plan_revision_id'])
                    frozen = revision['snapshot']
                    if (frozen['parent_kind'] != ('verifier_run' if kind == 'archive_run' else kind)
                            or frozen['parent_id'] != parent_id or digest(frozen['relations']) != digest(plan)):
                        raise ValueError('Plan revision does not match this parent and relation plan')
                    spec.update(plan_author='user_revised_proposal', plan_revision=revision)
                spec['input_sha256'] = digest(spec)
                cid = uuid.uuid4().hex[:16]
                with db() as c:
                    c.execute('INSERT INTO relation_checks VALUES (?,?,?,?,?,?)', (cid, 'queued', time.time(), json.dumps(spec), None, None))
                POOL.submit(execute_plan, cid, spec)
                return self.send({'id': cid, 'state': 'queued'}, 202)
            if self.path == '/api/agent-jobs':
                if not AgentService.configuration()['configured']:
                    return self.send({'error': 'Search agent model is not configured'}, 409)
                job = agent().create(body.get('question'), body.get('budget', 6))
                POOL.submit(agent().execute, job['id'])
                return self.send({'id': job['id'], 'state': job['state']}, 202)
            if self.path.startswith('/api/agent-jobs/') and self.path.endswith('/cancel'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid cancellation path')
                return self.send(agent().cancel(parts[2]))
            if self.path == '/api/search-sessions':
                return self.send(tools().create(body.get('target'), body.get('parent_run'), body.get('budget', 6)), 201)
            if self.path.startswith('/api/search-sessions/') and self.path.endswith('/calls'):
                parts = self.path.strip('/').split('/')
                if len(parts) != 4:
                    raise ValueError('Invalid tool path')
                return self.send(tools().call(parts[2], body.get('action'), body.get('args')))
            if self.path == '/api/followups':
                with db() as c:
                    row = c.execute('SELECT result FROM runs WHERE id=?', (body.get('run_id'),)).fetchone()
                if not row or not row['result']:
                    raise ValueError('Parent run has no result')
                result = json.loads(row['result'])
                session = tools().create(result['input']['relation'], body['run_id'], body.get('budget', 6))
                target = session['target']
                anchor = target['subject'] if not target['subject'].startswith('?') else target['object']
                search = tools().call(session['id'], 'search', {'query': session['plan'][0], 'required_entity': anchor})
                if search['ok'] and search['evidence'] and search['used'] < search['budget']:
                    tools().call(session['id'], 'open', {'doc_id': search['evidence'][0]['doc_id']})
                return self.send(tools().get(session['id']), 201)
            if self.path == '/api/runs':
                if body.get('source') not in ('archive', 'live') or body.get('mode') not in ('direct', 'split'):
                    raise ValueError('Invalid source or mode')
                if body.get('case_id') not in CASES or body.get('model') not in MODELS:
                    raise ValueError('Unknown case or model')
                if body['source'] == 'live' and not inference_configuration()['configured']:
                    return self.send({'error': 'Live model is not configured'}, 409)
                if 'custom_input' in body:
                    if body['source'] != 'live':
                        raise ValueError('Custom inputs require live inference')
                    row = body['custom_input']
                    if not isinstance(row, dict) or not isinstance(row.get('relation'), dict) or not isinstance(row.get('evidence'), list):
                        raise ValueError('Custom input requires relation and evidence')
                    if not row['evidence'] or len({e['id'] for e in row['evidence']}) != len(row['evidence']):
                        raise ValueError('Evidence IDs must be nonempty and unique')
                if body.get('retrieval_session'):
                    if body['source'] != 'live':
                        raise ValueError('Collected evidence requires new inference')
                    # Reconstruct on server; never accept a browser's altered evidence or target.
                    collected = tools().collected_input(body['retrieval_session'])
                    if body.get('custom_input') and body['custom_input'] != collected:
                        raise ValueError('Collected input does not match the retrieval session')
                    body['custom_input'] = collected
                rid = uuid.uuid4().hex[:16]
                with db() as c:
                    c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)', (rid, 'queued', time.time(), json.dumps(body), None, None))
                POOL.submit(execute, rid, body)
                return self.send({'id': rid, 'state': 'queued'}, 202)
            if self.path == '/api/reviews':
                if body.get('verdict') not in ('entailed', 'missing', 'contradicted', 'ambiguous'):
                    raise ValueError('Invalid review label')
                note = body.get('note', '').strip()
                if not note or len(note) > 4000:
                    raise ValueError('Review rationale must be 1–4000 characters')
                with db() as c:
                    row = c.execute('SELECT state FROM runs WHERE id=?', (body.get('run_id'),)).fetchone()
                    if not row or row['state'] != 'completed':
                        raise ValueError('Review requires a completed run')
                    rid = uuid.uuid4().hex
                    c.execute('INSERT INTO reviews VALUES (?,?,?,?,?)', (rid, body['run_id'], body['verdict'], note, time.time()))
                return self.send({'id': rid}, 201)
            self.send({'error': 'Not found'}, 404)
        except RetrievalError as exc:
            return self.send({'error': str(exc), 'code': exc.code}, 404 if exc.code == 'unknown_session' else 409)
        except VerificationConflict as exc:
            self.send({'error': str(exc), 'code': 'request_key_conflict'}, 409)
        except VerificationUnavailable as exc:
            self.send({'error': str(exc), 'code': 'model_not_configured'}, 409)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    try:
        init()
    except Exception:
        server.server_close()
        raise
    print(f'Evidence Verifier: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()
