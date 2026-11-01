"""Append-only human curation and frozen evidence-only SFT exports.

Import metadata is a declaration, not an authenticated data license or gold label.
Nothing in this module calls a model, downloads data, or starts training.
"""
import copy
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import time
import uuid

from engine import CASES, digest
from evpilot import relation_kernel_v7 as kernel


class CurationError(ValueError):
    pass


def text_hash(text):
    return digest(' '.join(text.casefold().split()))


def source_usage(source):
    if source['dataset'].casefold().replace('-', '').replace('_', '').replace(' ', '') in ('2wiki', '2wikimultihopqa'):
        return 'holdout'
    if source['split'] == 'fixture' or source['origin'] == 'synthetic_fixture':
        return 'fixture'
    return 'train_candidate' if source['split'] == 'train' else 'development_only'


def normalize_candidate(value):
    if not isinstance(value, dict) or set(value) != {'task', 'source', 'evidence', 'raw_fact', 'suggestion'}:
        raise CurationError('Expected task, source, evidence, raw_fact and suggestion')
    if value['task'] not in ('extract', 'verify'):
        raise CurationError('Only extract and verify tasks are supported')
    source = value['source']
    fields = {'dataset', 'split', 'source_group', 'license', 'source_url', 'origin'}
    if not isinstance(source, dict) or set(source) != fields:
        raise CurationError('Source requires dataset/split/source_group/license/source_url/origin')
    if any(not isinstance(source[k], str) or not source[k].strip() or len(source[k]) > 1000 for k in fields):
        raise CurationError('Source metadata must be nonempty text within 1000 characters')
    if source['split'] not in ('train', 'dev', 'test', 'development', 'unknown', 'fixture'):
        raise CurationError('Invalid declared source split')
    if source['origin'] not in ('gold_supporting_facts', 'agent_trajectory', 'verifier_trace', 'synthetic_hard_negative', 'manual', 'synthetic_fixture'):
        raise CurationError('Invalid source origin')
    evidence = value['evidence']
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 40:
        raise CurationError('Need 1–40 evidence passages')
    ids, cleaned = set(), []
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'].strip() or len(item['id']) > 150 or item['id'] in ids:
            raise CurationError('Evidence IDs must be unique nonempty strings')
        if not isinstance(item.get('text'), str) or not item['text'].strip():
            raise CurationError('Evidence text must be nonempty')
        ids.add(item['id']); cleaned.append({'id': item['id'], 'text': item['text']})
    if sum(len(e['text']) for e in cleaned) > 22000:
        raise CurationError('Evidence exceeds 22000 characters; no silent truncation')
    raw_fact = value['raw_fact']
    if (value['task'] == 'verify' and not isinstance(raw_fact, dict)) or (value['task'] == 'extract' and raw_fact is not None):
        raise CurationError('Verify needs a raw fact; extract requires raw_fact=null')
    if not isinstance(value['suggestion'], dict):
        raise CurationError('Model suggestion must be a JSON object, not a gold label')
    source = {k: v.strip() for k, v in source.items()}
    record = {'task': value['task'], 'source': source, 'evidence': cleaned,
              'raw_fact': copy.deepcopy(raw_fact), 'suggestion': copy.deepcopy(value['suggestion'])}
    record.update(usage=source_usage(source), evidence_sha256=digest(cleaned),
                  source_group=source['source_group'], suggestion_is_gold=False)
    return record


def review_answer(task, value):
    if not isinstance(value, dict):
        raise CurationError('Reviewed answer must be a JSON object')
    if task == 'extract':
        if set(value) != {'facts'} or not isinstance(value['facts'], list) or len(value['facts']) > 40 or any(not isinstance(f, dict) for f in value['facts']):
            raise CurationError('Extraction answer must contain a facts array of at most 40 objects')
    elif (set(value) != {'verdict', 'rationale'} or value['verdict'] not in kernel.VERDICTS
          or not isinstance(value['rationale'], str) or not value['rationale'].strip() or len(value['rationale']) > 4000):
        raise CurationError('Verification answer needs a four-way verdict and rationale')
    return copy.deepcopy(value)


def sft_row(candidate, review):
    """Full schema/provenance replay uses the existing real-inference dependency."""
    if importlib.util.find_spec('jsonschema') is None:
        raise CurationError('Existing jsonschema dependency is unavailable for full source replay')
    import jsonschema
    payload, cat = kernel.extraction_payload(candidate['evidence'])
    answer = review_answer(candidate['task'], review['answer'])
    if candidate['task'] == 'extract':
        for raw in answer['facts']:
            kernel.materialize_fact(raw, cat)
        system = kernel.EXTRACT_SYSTEM
    else:
        fact = kernel.materialize_fact(candidate['raw_fact'], cat)
        payload = {'predicate_definition': kernel.CONTRACT[fact['predicate']], 'fact': candidate['raw_fact'],
                   'computed_object': fact['object'], 'evidence': payload['evidence']}
        system = kernel.VERIFY_SYSTEM
    # A cited span can be real even when its proposed relationship is wrong.
    # Support decisions remain human judgments, not a byproduct of this replay.
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}]
    return {'id': candidate['id'] + '-' + review['id'], 'split': 'train', 'task': candidate['task'],
            'source_group': candidate['source_group'], 'label_source': 'human_verified', 'messages': messages,
            'curation': {'candidate_id': candidate['id'], 'review_id': review['id'],
                         'reviewer': review['reviewer'], 'review_note': review['note'],
                         'messages_sha256': digest(messages), 'source': candidate['source'],
                         'evidence': candidate['evidence'], 'evidence_sha256': digest(candidate['evidence']),
                         'parent_trace': candidate['provenance'],
                         'source_replay': 'passed', 'semantic_label': 'human_declared_not_independent_gold'}}


class CurationService:
    def __init__(self, connections, development_corpus=None):
        self.connections, self.development_corpus = connections, development_corpus

    def init(self):
        with self.connections() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS data_candidates(
              id TEXT PRIMARY KEY,created REAL,fingerprint TEXT UNIQUE,input TEXT,provenance TEXT);
            CREATE TABLE IF NOT EXISTS data_reviews(
              id TEXT PRIMARY KEY,candidate_id TEXT,created REAL,input TEXT);
            CREATE INDEX IF NOT EXISTS data_review_versions ON data_reviews(candidate_id,created DESC);
            CREATE TABLE IF NOT EXISTS data_exports(
              id TEXT PRIMARY KEY,created REAL,manifest TEXT,rows TEXT);
            ''')

    def add(self, values, provenance=None):
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise CurationError('Import needs 1–100 candidate records per request')
        # Validate the whole batch before opening the write transaction.
        cleaned = [normalize_candidate(value) for value in values]
        ids = []
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            for record in cleaned:
                fingerprint = digest(record)
                old = c.execute('SELECT id FROM data_candidates WHERE fingerprint=?', (fingerprint,)).fetchone()
                cid = old['id'] if old else uuid.uuid4().hex[:16]
                if not old:
                    c.execute('INSERT INTO data_candidates VALUES (?,?,?,?,?)',
                              (cid, time.time(), fingerprint, json.dumps(record), json.dumps(provenance or {'source': 'user_import_declared'})))
                ids.append(cid)
        return ids

    def get(self, cid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM data_candidates WHERE id=?', (cid,)).fetchone()
            reviews = c.execute('SELECT * FROM data_reviews WHERE candidate_id=? ORDER BY created,rowid', (cid,)).fetchall()
        if not row:
            raise CurationError('Unknown data candidate')
        record = dict(row, input=json.loads(row['input']), provenance=json.loads(row['provenance']),
                      reviews=[dict(r, input=json.loads(r['input'])) for r in reviews])
        record['latest_review_id'] = reviews[-1]['id'] if reviews else None
        record['decision'] = record['reviews'][-1]['input']['decision'] if reviews else 'pending'
        record['quoted_evidence'] = kernel.extraction_payload(record['input']['evidence'])[0]['evidence']
        return record

    def list(self):
        # Exports freeze effective review versions; old versions remain in get().
        result = []
        with self.connections() as c:
            for row in c.execute('''SELECT d.id,d.input,d.provenance,r.id AS review_id,r.input AS review_input
                FROM data_candidates d LEFT JOIN data_reviews r ON r.rowid=(
                  SELECT v.rowid FROM data_reviews v WHERE v.candidate_id=d.id ORDER BY v.created DESC,v.rowid DESC LIMIT 1)
                ORDER BY d.created DESC'''):
                review = {'id': row['review_id'], 'input': json.loads(row['review_input'])} if row['review_id'] else None
                result.append({'id': row['id'], 'input': json.loads(row['input']),
                               'provenance': json.loads(row['provenance']), 'reviews': [review] if review else [],
                               'decision': review['input']['decision'] if review else 'pending'})
        return result

    def summaries(self):
        result = []
        with self.connections() as c:
            for row in c.execute('''SELECT d.id,d.created,d.input,
                (SELECT r.input FROM data_reviews r WHERE r.candidate_id=d.id ORDER BY r.created DESC,r.rowid DESC LIMIT 1) AS latest
                FROM data_candidates d ORDER BY d.created DESC'''):
                value = json.loads(row['input'])
                review = json.loads(row['latest']) if row['latest'] else None
                result.append({'id': row['id'], 'created': row['created'], 'task': value['task'],
                               'source': value['source'], 'usage': value['usage'],
                               'decision': review['decision'] if review else 'pending'})
        return result

    def review(self, cid, value):
        if not isinstance(value, dict) or set(value) != {'base_review_id', 'reviewer', 'note', 'decision', 'answer'}:
            raise CurationError('Review needs base_review_id, reviewer, note, decision and answer')
        for field, limit in (('reviewer', 100), ('note', 2000)):
            if not isinstance(value[field], str) or not 1 <= len(value[field].strip()) <= limit:
                raise CurationError('Review needs a nonempty ' + field)
        if value['decision'] not in ('include', 'exclude'):
            raise CurationError('Review decision must be include or exclude')
        candidate = self.get(cid)
        answer = review_answer(candidate['input']['task'], value['answer']) if value['decision'] == 'include' else value['answer']
        if not isinstance(answer, dict):
            raise CurationError('Answer must remain a JSON object')
        snapshot = dict(value, answer=answer, semantic_review='human_declared', authenticated_identity=False)
        rid = uuid.uuid4().hex[:16]
        with self.connections() as c:
            c.execute('BEGIN IMMEDIATE')
            last = c.execute('SELECT id FROM data_reviews WHERE candidate_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (cid,)).fetchone()
            if value['base_review_id'] != (last['id'] if last else None):
                raise CurationError('Review version changed; reload before saving a new revision')
            c.execute('INSERT INTO data_reviews VALUES (?,?,?,?)', (rid, cid, time.time(), json.dumps(snapshot)))
        return self.get(cid)

    def collect_trace(self, evidence, facts, task, fact_index, source, provenance):
        if task not in ('extract', 'verify'):
            raise CurationError('Only extract or verify can be collected')
        if task == 'verify':
            if type(fact_index) is not int or not 0 <= fact_index < len(facts):
                raise CurationError('Invalid fact index')
            raw = facts[fact_index].get('raw')
            suggestion = facts[fact_index].get('verification', {}).get('parsed', {})
            if raw is None:
                raise CurationError('This record has no replayable raw fact')
        else:
            if any('raw' not in f for f in facts):
                raise CurationError('One or more facts lack their raw extraction form')
            raw, suggestion = None, {'facts': [f['raw'] for f in facts]}
        ids = self.add([{'task': task, 'source': source, 'evidence': evidence,
                         'raw_fact': raw, 'suggestion': suggestion}], provenance)
        return self.get(ids[0])

    def from_run(self, rid, task, fact_index=None):
        with self.connections() as c:
            row = c.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
        if not row or row['state'] != 'completed':
            raise CurationError('Collect from a completed split verifier run')
        parent = json.loads(row['result'])
        if parent['mode'] != 'split':
            raise CurationError('Direct judgments are not independent extraction labels')
        facts, evidence = parent['output'].get('facts', []), parent['input']['evidence']
        diagnostic_hashes = {text_hash(e['text']) for case in CASES.values() for e in case['evidence']}
        diagnostic = (parent['source'] == 'archived_v7' or parent['input']['id'] in CASES
                      or any(text_hash(e['text']) in diagnostic_hashes for e in evidence))
        source = {'dataset': 'frozen_v7_diagnostic' if diagnostic else 'unclassified_runtime',
                  'split': 'development' if diagnostic else 'unknown',
                  'source_group': 'runtime-evidence-' + digest(sorted(text_hash(e['text']) for e in evidence)),
                  'license': 'retain_original_source_terms', 'source_url': 'local-run:' + rid,
                  'origin': 'verifier_trace'}
        if str(parent['model']).startswith('protocol-fixture'):
            source.update(dataset='protocol_fixture', split='fixture', origin='synthetic_fixture')
        return self.collect_trace(evidence, facts, task, fact_index, source,
                                  {'source': 'server_runtime', 'run_id': rid, 'result_sha256': digest(parent),
                                   'model': parent['model'], 'model_revision': parent.get('model_revision'),
                                   'scope': 'pending_curation_not_gold'})

    def from_check(self, cid, task, fact_index=None):
        with self.connections() as c:
            row = c.execute('SELECT * FROM relation_checks WHERE id=?', (cid,)).fetchone()
        if not row or row['state'] != 'completed':
            raise CurationError('Collect from a completed joint relation check')
        parent, result = json.loads(row['input']), json.loads(row['result'])
        facts = result.get('fact_rows', parent.get('archived_output', {}).get('facts'))
        if facts is None:
            raise CurationError('This older check lacks retained facts; submit its plan again')
        evidence = parent['evidence']
        provenance = {'source': 'server_joint_check', 'check_id': cid, 'input_sha256': digest(parent),
                      'result_sha256': digest(result), 'verifier': result.get('provenance'),
                      'scope': 'pending_curation_not_gold'}
        source = {'dataset': 'unclassified_runtime', 'split': 'unknown',
                  'source_group': 'runtime-evidence-' + digest(sorted(text_hash(e['text']) for e in evidence)),
                  'license': 'retain_original_source_terms', 'source_url': 'local-check:' + cid, 'origin': 'verifier_trace'}
        if parent['parent_kind'] == 'agent_job':
            with self.connections() as c:
                job = c.execute('SELECT a.config,s.corpus_sha256 FROM agent_jobs a JOIN search_sessions s ON a.session_id=s.id WHERE a.id=?', (parent['parent_id'],)).fetchone()
            source['origin'] = 'agent_trajectory'
            if job:
                provenance['agent_job_id'] = parent['parent_id']
                provenance['corpus_sha256'] = job['corpus_sha256']
                if json.loads(job['config']).get('source') == 'protocol_fixture':
                    source.update(dataset='protocol_fixture', split='fixture', origin='synthetic_fixture')
                elif self.development_corpus:
                    manifest = Path(self.development_corpus).with_name('corpus-manifest.json')
                    if manifest.is_file() and json.loads(manifest.read_text())['sha256'] == job['corpus_sha256']:
                        source.update(dataset='MuSiQue', split='dev')
        else:
            diagnostic = {text_hash(e['text']) for case in CASES.values() for e in case['evidence']}
            if any(text_hash(e['text']) in diagnostic for e in evidence):
                source.update(dataset='frozen_v7_diagnostic', split='development')
        return self.collect_trace(evidence, facts, task, fact_index, source, provenance)

    def export(self):
        records = self.list()
        excluded, candidates = [], []
        restricted_groups, restricted_text = set(), set()
        # Exact normalized paragraph overlap only, not semantic contamination detection.
        for case in CASES.values():
            restricted_text.update(text_hash(e['text']) for e in case['evidence'])
        corpus_sha = None
        if self.development_corpus and Path(self.development_corpus).is_file():
            path = Path(self.development_corpus)
            sha = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    sha.update(chunk)
            corpus_sha = sha.hexdigest()
            expected = json.loads(path.with_name('corpus-manifest.json').read_text())['sha256']
            if corpus_sha != expected:
                raise CurationError('Bundled development corpus identity changed; review its source before export')
            with sqlite3.connect('file:' + str(path.resolve()) + '?mode=ro', uri=True) as c:
                for (text,) in c.execute('SELECT text FROM docs'):
                    restricted_text.add(text_hash(text))
        for row in records:
            value = row['input']
            if value['usage'] != 'train_candidate':
                restricted_groups.add(value['source_group'])
                restricted_text.update(text_hash(e['text']) for e in value['evidence'])
        for row in records:
            value = row['input']
            reason = None
            if row['decision'] != 'include':
                reason = 'not_human_included'
            elif value['usage'] != 'train_candidate':
                reason = 'non_training_source'
            elif value['source_group'] in restricted_groups:
                reason = 'source_group_overlaps_non_training'
            elif any(text_hash(e['text']) in restricted_text for e in value['evidence']):
                reason = 'exact_evidence_overlap_non_training'
            if reason:
                excluded.append({'candidate_id': row['id'], 'reason': reason})
                continue
            review = row['reviews'][-1]
            try:
                prepared = sft_row(dict(value, id=row['id'], provenance=row['provenance']), dict(review['input'], id=review['id']))
                candidates.append(prepared)
            except Exception as exc:
                excluded.append({'candidate_id': row['id'], 'reason': 'source_or_schema_replay_failed', 'detail': str(exc)[:1000]})
        groups = {}
        for row in candidates:
            key = digest([row['task'], row['messages'][:2]])
            groups.setdefault(key, []).append(row)
        accepted = []
        for rows in groups.values():
            if len({digest(r['messages'][2]) for r in rows}) > 1:
                excluded.extend({'candidate_id': r['curation']['candidate_id'], 'reason': 'conflicting_labels_for_same_input'} for r in rows)
            else:
                accepted.append(rows[0])
                excluded.extend({'candidate_id': r['curation']['candidate_id'], 'reason': 'duplicate_training_input'} for r in rows[1:])
        output = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in accepted)
        manifest = {'id': uuid.uuid4().hex[:16], 'created': time.time(), 'rows': len(accepted),
                    'groups': len({r['source_group'] for r in accepted}), 'tasks': dict(Counter(r['task'] for r in accepted)),
                    'origins': dict(Counter(r['curation']['source']['origin'] for r in accepted)),
                    'verify_labels': dict(Counter(json.loads(r['messages'][2]['content'])['verdict'] for r in accepted if r['task'] == 'verify')),
                    'jsonl_sha256': hashlib.sha256(output.encode()).hexdigest(),
                    'development_corpus_sha256': corpus_sha,
                    'source_schema_sha256': digest(kernel.CONTRACT), 'excluded': excluded,
                    'overlap_scope': 'declared_source_groups_and_exact_normalized_paragraphs',
                    'label_scope': 'single_user_curation_not_independent_double_annotation',
                    'training_executed': False, 'ready_for_preflight': bool(accepted)}
        with self.connections() as c:
            c.execute('INSERT INTO data_exports VALUES (?,?,?,?)', (manifest['id'], manifest['created'], json.dumps(manifest), output))
        return {'manifest': manifest, 'jsonl': output}

    def exported(self, eid):
        with self.connections() as c:
            row = c.execute('SELECT * FROM data_exports WHERE id=?', (eid,)).fetchone()
        if not row:
            raise CurationError('Unknown dataset export')
        return {'manifest': json.loads(row['manifest']), 'jsonl': row['rows']}
