"""Archived experiment playback and optional live vLLM adapter. No simulated inference."""
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from inference import configuration as inference_configuration, router as inference_router, estimate_work
from grounding import REPAIR_SYSTEM, source_payload, materialize_all, expand_local_context, revision_schema, apply_revision, cited_evidence

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'vendor'))
ARCHIVE = json.loads((ROOT / 'data/archive.json').read_text())
CASES = {r['id']: r for r in ARCHIVE['inputs']}
MODELS = ['qwen7b', 'llama8b', 'qwen14b']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def replay(case_id, model, mode):
    result = ARCHIVE['models'][model][mode][case_id]
    return {'source': 'archived_v7', 'input': CASES[case_id], 'output': result,
            'archive_sha256': ARCHIVE['source_sha256'],
            'scope': ARCHIVE['scope'], 'model': model, 'mode': mode}


def check_spans(row, result):
    """Replay every archived citation against its original evidence; no semantic claim."""
    sources = {e['id']: e['text'] for e in row['evidence']}
    checked = []
    for f in result.get('facts', []):
        fact = f.get('fact', {})
        for field in ['subject_spans', 'object_spans', 'quotes']:
            for span in fact.get(field, []):
                source = sources.get(span.get('evidence_id'), '')
                valid = (type(span.get('start')) is int and type(span.get('end')) is int
                         and 0 <= span['start'] < span['end'] <= len(source)
                         and source[span['start']:span['end']] == span.get('text'))
                checked.append(dict(span, field=field, valid=valid))
    return checked


def live(row, mode, cache_get, cache_put, event):
    # Uses the original frozen kernel, including typed schema, source replay and arithmetic.
    import jsonschema
    from evpilot import relation_kernel_v7 as kernel
    routing_config = inference_configuration()
    if not routing_config['configured']:
        raise ValueError(routing_config['error'] or 'Verifier model is not configured')
    endpoints = routing_config['urls']
    model = routing_config['model']
    kernel.validate_target(row['relation'])
    payload, cat = source_payload(row['evidence'], kernel)
    repair_attempts = os.environ.get('EV_GROUNDING_REPAIR_ATTEMPTS', '0')
    if repair_attempts not in ('0', '1'):
        raise ValueError('Grounding repair attempts must be 0 or 1')
    version = digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (ROOT / 'vendor/evpilot').glob('*.py')})
    # An endpoint/model name can survive weight updates. Without a pinned revision,
    # isolate the cache to this execution rather than silently using stale weights.
    model_revision = routing_config['revision'] or None
    cache_namespace = model_revision or 'unversioned-run-' + uuid.uuid4().hex
    adapter_version = digest({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                              for name in ('engine.py', 'grounding.py')})
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'cache_hits': 0, 'model_calls': 0, 'coalesced_hits': 0}
    usage_complete = True

    def call(stage, value):
        schema = (revision_schema(cat, len(value['hypotheses']), kernel) if stage == 'grounding_repair'
                  else kernel.schema_for(stage, cat))
        system = {'extract': kernel.EXTRACT_SYSTEM, 'verify': kernel.VERIFY_SYSTEM,
                  'direct': kernel.DIRECT_SYSTEM, 'grounding_repair': REPAIR_SYSTEM}[stage]
        key = digest([sorted(endpoints), model, cache_namespace, adapter_version, version, stage, system, value, schema, 42, 0])
        cached = cache_get(key)
        if cached is not None:
            usage['cache_hits'] += 1
            event(stage, 'cache_hit', {'key': key})
            return cached
        body = {'model': model, 'temperature': 0, 'seed': 42,
                'max_tokens': 2400 if stage != 'verify' else 400,
                'messages': [{'role': 'system', 'content': system},
                             {'role': 'user', 'content': json.dumps(value)}],
                'response_format': {'type': 'json_schema', 'json_schema':
                                    {'name': stage, 'strict': True, 'schema': schema}}}
        headers = {'Content-Type': 'application/json'}
        if os.environ.get('EV_MODEL_KEY'):
            headers['Authorization'] = 'Bearer ' + os.environ['EV_MODEL_KEY']
        event(stage, 'running', {})
        def perform(endpoint):
            nonlocal usage_complete
            request = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions',
                                             data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=180) as response:
                reply = json.load(response)
            usage['model_calls'] += 1
            reported = reply.get('usage') if isinstance(reply.get('usage'), dict) else {}
            for k in ['prompt_tokens', 'completion_tokens']:
                if type(reported.get(k)) is int and reported[k] >= 0:
                    usage[k] += reported[k]
                else:
                    usage_complete = False
            raw = reply['choices'][0]['message']['content']
            try:
                parsed = json.loads(raw)
                jsonschema.validate(parsed, schema)
            except (ValueError, TypeError, jsonschema.ValidationError) as exc:
                event(stage, 'invalid_output', {'raw': raw, 'error': str(exc)[:1000], 'usage': reply.get('usage', {})})
                raise ValueError('Invalid model output at ' + stage) from exc
            record = {'status': 'ok', 'raw': raw, 'parsed': parsed, 'usage': reply.get('usage', {})}
            if os.environ.get('EV_EDITION')=='pure_api':
                record.update(api_role=reply.get('ev_api_role'),provider_model=reply.get('ev_api_model'))
            # Persist validation before releasing followers: no gap between single-flight and cache.
            cache_put(key, record)
            return record
        record, routing = inference_router().run(key, estimate_work(body), perform)
        if routing['coalesced']:
            usage['coalesced_hits'] += 1
        record['routing'] = routing
        event(stage, 'coalesced' if routing['coalesced'] else 'completed', record)
        return record

    if mode == 'direct':
        response = call('direct', dict(payload, target_relation=row['relation']))
        raw = response['parsed']
        result = {'id': row['id'], 'status': 'ok', 'response': response, 'raw_prediction': raw}
        try:
            fs = [kernel.materialize_fact(f, cat) for f in raw['facts']]
            prediction = kernel.match_target(row['relation'], [(f, {'verdict': 'entailed'}) for f in fs])
            if raw['verdict'] in ('entailed', 'contradicted') and prediction['verdict'] != raw['verdict']:
                raise ValueError('direct verdict not backed by its supplied facts')
            if raw['verdict'] in ('missing', 'ambiguous'):
                prediction = {'verdict': raw['verdict'], 'bindings': {}, 'quotes': [], 'matched_facts': []}
            result['prediction'] = prediction
            result['facts'] = [{'fact': f} for f in fs]
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
            result.update(status='invalid_output', errors=[{'stage': 'matching', 'error': str(exc)}])
    else:
        extracted = call('extract', payload)  # No question, candidate or target relation.
        raw_facts = extracted['parsed']['facts']
        materialized, grounding_errors = materialize_all(raw_facts, cat, kernel)
        grounding = {'policy': 'preserve_hypotheses_single_source_revision',
                     'initial_errors': grounding_errors, 'repair_attempted': False,
                     'repaired_fact_indices': [], 'local_context_expansions': []}
        if grounding_errors and repair_attempts == '1':
            raw_facts, expansions = expand_local_context(raw_facts, grounding_errors, payload, cat)
            grounding['local_context_expansions'] = expansions
            materialized, grounding_errors = materialize_all(raw_facts, cat, kernel)
        if grounding_errors and repair_attempts == '1':
            grounding['repair_attempted'] = True
            event('grounding', 'needs_revision', {'errors': grounding_errors})
            revision = call('grounding_repair', dict(payload, hypotheses=raw_facts, errors=grounding_errors))
            grounding['revision'] = revision
            try:
                revised = apply_revision(raw_facts, revision['parsed']['facts'], grounding_errors, cat)
                grounding['repaired_fact_indices'] = [i for i, (a, b) in enumerate(zip(raw_facts, revised)) if a != b]
                raw_facts = revised
                materialized, grounding_errors = materialize_all(raw_facts, cat, kernel)
            except (ValueError, KeyError, TypeError) as exc:
                grounding_errors = [{'stage': 'grounding_repair', 'error': str(exc)[:1000]}]
        grounding['final_errors'] = grounding_errors
        event('grounding', 'rejected' if grounding_errors else 'completed', grounding)
        facts, errors = [], []
        # Preflight the whole extraction before spending on semantic verification.
        errors.extend(grounding_errors)
        for index, raw in enumerate(raw_facts if not errors else []):
            try:
                fact = materialized[index]
                check = call('verify', {'predicate_definition': kernel.CONTRACT[fact['predicate']],
                                       'fact': raw, 'computed_object': fact['object'],
                                       'evidence': cited_evidence(payload, raw['quote_ids'])})
                facts.append({'fact': fact, 'raw': raw, 'verification': check})
            except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
                errors.append({'index': index, 'error': str(exc)[:1000]})
        result = {'id': row['id'], 'status': 'invalid_output' if errors else 'ok',
                  'facts': facts, 'errors': errors, 'grounding': grounding}
        if not errors:
            result['prediction'] = kernel.match_target(row['relation'],
                [(f['fact'], f['verification']['parsed']) for f in facts])
    return {'source': 'remote_api' if os.environ.get('EV_EDITION')=='pure_api' else 'live_vllm', 'input': row, 'output': result, 'model': model,
            'mode': mode, 'usage': usage, 'usage_complete': usage_complete, 'kernel_sha256': version,
            'adapter_sha256': adapter_version,
            'model_revision': model_revision, 'cache_scope': 'pinned_revision' if model_revision else 'execution_only'}
