"""Joint variable binding over independently verified facts. Not a question-plan oracle."""
import copy
import re
from engine import check_spans, digest
from evpilot import relation_kernel_v7 as kernel


class PlanError(ValueError):
    pass


def validate_plan(relations):
    if not isinstance(relations, list) or not 1 <= len(relations) <= 8:
        raise PlanError('A plan must contain 1–8 declared relations')
    plan, variables = [], {}
    for index, item in enumerate(relations):
        if not isinstance(item, dict) or item.get('predicate') not in kernel.TYPES:
            raise PlanError('Unsupported relation at index ' + str(index))
        r = copy.deepcopy(item)
        if set(r) - {'subject', 'object', 'predicate', 'subject_type', 'object_type', 'time_scope'}:
            raise PlanError('Unsupported relation fields; do not silently ignore qualifiers')
        st, ot = kernel.TYPES[r['predicate']]
        r.setdefault('subject_type', st); r.setdefault('object_type', ot)
        for role in ('subject', 'object'):
            value = r.get(role)
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise PlanError('Relation endpoints must be strings of 1–300 characters')
            if value.startswith('?'):
                if not re.fullmatch(r'\?[A-Za-z][A-Za-z0-9_]*', value):
                    raise PlanError('Variable must look like ?person or ?place')
                entity_type = r[role + '_type']
                if value in variables and variables[value] != entity_type:
                    raise PlanError('Variable has incompatible roles/types: ' + value)
                variables[value] = entity_type
        try:
            kernel.validate_target(r)
        except Exception as exc:
            raise PlanError(str(exc)) from exc
        plan.append(r)
    return plan


def normalize(r):
    r = dict(r)
    for role in ('subject', 'object'):
        if not r[role].startswith('?'):
            r[role] = kernel.canonical_entity(r[role], r[role + '_type'])
    return r


def evaluate(relations, rows, evidence):
    plan = validate_plan(relations)
    # A source/format failure cannot be laundered into "missing evidence".
    checks = check_spans({'evidence': evidence}, {'facts': rows})
    if any(not x['valid'] for x in checks):
        return {'status': 'invalid_output', 'error': 'Source position replay failed', 'span_checks': checks}
    verified = []
    for row in rows:
        fact, response = row.get('fact'), row.get('verification', {})
        if not fact or response.get('status') != 'ok' or response.get('parsed', {}).get('verdict') not in kernel.VERDICTS:
            return {'status': 'invalid_output', 'error': 'A fact lacks a valid independent verification'}
        if not all(fact.get(k) for k in ('quotes', 'subject_spans', 'object_spans')):
            return {'status': 'invalid_output', 'error': 'A fact lacks complete source positions'}
        verified.append((fact, response['parsed']))
    local = [kernel.match_target(r, verified) for r in plan]
    paths = [({'bindings': {}, 'facts': []})]
    for r in plan:
        next_paths = {}
        for path in paths:
            for fact, check in verified:
                if check['verdict'] != 'entailed' or fact['polarity'] != 'positive':
                    continue
                bindings = kernel.match_fact(normalize(r), fact)
                if bindings is None or any(v in path['bindings'] and not kernel.same(path['bindings'][v], value)
                                           for v, value in bindings.items()):
                    continue
                combined = dict(path['bindings'], **bindings)
                key = digest({k: v.casefold().strip() for k, v in combined.items()})
                next_paths.setdefault(key, {'bindings': combined, 'facts': path['facts'] + [fact]})
                if len(next_paths) > 256:
                    return {'status': 'binding_limit', 'error': 'More than 256 binding candidates; no silent pruning'}
        paths = list(next_paths.values())
        if not paths:
            break
    accepted, conflict = [], False
    for path in paths:
        grounded = []
        for r in plan:
            bound = dict(r)
            for role in ('subject', 'object'):
                bound[role] = path['bindings'].get(r[role], r[role])
            grounded.append(kernel.match_target(bound, verified))
        if all(x['verdict'] == 'entailed' for x in grounded):
            accepted.append(path)
        else:
            conflict = True
    if len(accepted) == 1 and not conflict:
        verdict, bindings, proof = 'entailed', accepted[0]['bindings'], accepted[0]['facts']
    else:
        verdict = ('ambiguous' if accepted or conflict or any(x['verdict'] == 'ambiguous' for x in local)
                   else 'contradicted' if any(x['verdict'] == 'contradicted' for x in local) else 'missing')
        bindings, proof = {}, []
    return {'status': 'ok', 'verdict': verdict, 'bindings': bindings, 'proof': proof,
            'relation_results': local, 'span_checks': checks, 'scope': 'declared_relations_only',
            'plan_sha256': digest(plan), 'complete_question_coverage': 'not_established',
            'binding_gap': not paths and all(x['verdict'] in ('entailed', 'ambiguous') for x in local)}
