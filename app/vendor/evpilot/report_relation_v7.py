"""Score frozen diagnostics; oracle data is loaded only here, never by inference."""
import collections
import json
from pathlib import Path
from .common import read_jsonl, file_hash


def fact_correct(f, gold):
    for g in gold:
        keys = ['subject', 'predicate', 'object', 'polarity']
        if all(str(f.get(k, '')).strip().casefold() == str(g[k]).strip().casefold() for k in keys):
            refs = {q['evidence_id'] for q in f.get('quotes', [])}
            if set(g['evidence']) <= refs and not (f.get('exclusive') and not g['exclusive']):
                if f['predicate'] == 'employed_at' and f.get('time_scope') == 'current' and not g.get('allow_current'):
                    continue  # fixtures say worked/formerly employed, never current
                return True
    return False


def evaluate(rows, expected, mode):
    assert len(rows) == len(expected) and {r['id'] for r in rows} == set(expected)
    details, confusion = [], collections.Counter()
    for r in rows:
        g = expected[r['id']]
        p = r.get('prediction', {})
        verdict = p.get('verdict', r['status'])
        confusion[(g['verdict'], verdict)] += 1
        valid = r['status'] == 'ok'
        label = valid and verdict == g['verdict']
        bindings = not g['entailed'] or p.get('bindings') == g['bindings']
        quotes = set(g['required_evidence']) <= {q['evidence_id'] for q in p.get('quotes', [])}
        grounded = g['verdict'] not in ('entailed', 'contradicted') or (
            bool(p.get('matched_facts')) and all(fact_correct(f, g['facts']) for f in p['matched_facts']))
        joint = label and bindings and quotes and grounded
        causes = set()
        extraction = r.get('facts', [])
        rejected_correct = sum(fact_correct(x['fact'], g['facts']) and x.get('verification', {}).get('parsed', {}).get('verdict') != 'entailed' for x in extraction)
        accepted_incorrect = sum(not fact_correct(x['fact'], g['facts']) and x.get('verification', {}).get('parsed', {}).get('verdict') == 'entailed' for x in extraction)
        if not joint:
            if r.get('errors'):
                causes.update(e['stage'] for e in r['errors'])
            if r.get('error_stage'):
                causes.add(r['error_stage'])
            if not valid and not causes:
                causes.add('invalid_output')
            if accepted_incorrect or rejected_correct:
                causes.add('support')
            if mode == 'split' and g['entailed']:
                relevant = [x for x in extraction if x['fact']['predicate'] == g['target_predicate'] and fact_correct(x['fact'], g['facts'])]
                if not relevant:
                    causes.add('extraction')
            if not bindings:
                causes.add('matching')
            if any(f.get('calculation', {}).get('kind', 'none') != 'none' and not fact_correct(f, g['facts']) for f in p.get('matched_facts', [])):
                causes.add('calculation')
            if not causes:
                causes.add('support' if mode == 'direct' else 'extraction_or_matching')
        details.append({'id': r['id'], 'gold': g['verdict'], 'prediction': verdict,
                        'label_correct': label, 'bindings_correct': bindings, 'evidence_correct': quotes,
                        'fact_roles_correct': grounded, 'joint_correct': joint, 'positive': g['entailed'],
                        'variable': bool(g['bindings']), 'critical_false_accept': g['critical_negative'] and verdict == 'entailed',
                        'invalid': not valid, 'error_categories': sorted(causes),
                        'rejected_correct_facts': rejected_correct, 'accepted_incorrect_facts': accepted_incorrect})
    counts = {'joint': sum(d['joint_correct'] for d in details),
              'positive': sum(d['joint_correct'] and d['positive'] for d in details),
              'negative': sum(d['joint_correct'] and not d['positive'] for d in details),
              'variables': sum(d['joint_correct'] and d['variable'] for d in details),
              'invalid': sum(d['invalid'] for d in details),
              'critical_false_accept': sum(d['critical_false_accept'] for d in details),
              'accepted_incorrect_facts_row_weighted': sum(d['accepted_incorrect_facts'] for d in details),
              'rejected_correct_facts_row_weighted': sum(d['rejected_correct_facts'] for d in details)}
    checks = {'joint': counts['joint'] >= 72, 'positive': counts['positive'] >= 36,
              'negative': counts['negative'] >= 36, 'variables': counts['variables'] == 16,
              'invalid': counts['invalid'] == 0, 'critical': counts['critical_false_accept'] == 0}
    return {'counts': counts, 'checks': checks, 'passed': all(checks.values()),
            'confusion': [{'gold': a, 'prediction': b, 'count': n} for (a, b), n in sorted(confusion.items())],
            'error_categories_overlap': dict(collections.Counter(c for d in details for c in d['error_categories'])),
            'details': details}


def main():
    data = Path('data/relation-kernel-v7')
    freeze = json.loads((data / 'freeze.json').read_text())
    for p, sha in freeze['files'].items():
        assert file_hash(p) == sha, p
    expected = {r['id']: r for r in read_jsonl(data / 'expected.jsonl')}
    results = {}
    for run in sorted(Path('runs').glob('relation-kernel-v7-*')):
        if not run.is_dir():
            continue
        result = {}
        for mode in ['direct', 'split']:
            path = run / (mode + '.predictions.jsonl')
            if path.exists():
                rows = list(read_jsonl(path))
                assert len(rows) == 92 and {r['id'] for r in rows} == set(expected)
                baseline = {k:v for k,v in expected.items() if v['suite']=='regression80'}
                probes = {k:v for k,v in expected.items() if v['suite']=='new_probes12'}
                base_result = evaluate([r for r in rows if r['id'] in baseline], baseline, mode)
                probe_result = evaluate([r for r in rows if r['id'] in probes], probes, mode)
                probe_result['checks'] = {'all_correct':probe_result['counts']['joint']==12,'valid':probe_result['counts']['invalid']==0}
                probe_result['passed'] = all(probe_result['checks'].values())
                result[mode] = {'regression80':base_result,'probes12':probe_result,'passed':base_result['passed'] and probe_result['passed']}
        if result:
            (run / 'evaluation.json').write_text(json.dumps(result, indent=2))
            results[run.name] = result
        elif (run / 'failure.json').exists():
            results[run.name] = {'failure': json.loads((run / 'failure.json').read_text())}
    summary = {'scope': '80 development regressions + 12 constructed probes; not independent validation',
               'results': results, 'eligible_split_runs': [k for k, v in results.items() if v.get('split', {}).get('passed')]}
    Path('artifacts/relation-kernel-v7-evaluation.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: {mode: {'counts80': r['regression80']['counts'], 'counts12':r['probes12']['counts'],'passed':r['passed']} for mode, r in v.items() if mode in ['direct','split']} for k,v in results.items()}, indent=2))


if __name__ == '__main__':
    main()
