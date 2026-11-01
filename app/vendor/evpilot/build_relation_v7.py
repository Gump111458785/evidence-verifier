import json
from pathlib import Path
from .common import read_jsonl, write_jsonl, file_hash
from .relation_kernel_v7 import CONTRACT, EXTRACT_SYSTEM, VERIFY_SYSTEM, DIRECT_SYSTEM


def main():
    dest = Path('data/relation-kernel-v7')
    dest.mkdir(exist_ok=False)
    rows = list(read_jsonl('data/relation-kernel-v6/inputs.jsonl'))
    expected = list(read_jsonl('data/relation-kernel-v6/expected.jsonl'))
    for e in expected:
        e['suite'] = 'regression80'
    def add(id, subject, pred, object, st, ot, text, verdict, facts, time_scope=None):
        relation = dict(subject=subject, predicate=pred, object=object, subject_type=st, object_type=ot)
        if time_scope:
            relation['time_scope'] = time_scope
        id = 'probe-' + id
        evidence = [{'id': 'E'+str(i+1), 'text': t} for i, t in enumerate(text)]
        rows.append(dict(id=id, relation=relation, evidence=evidence))
        if time_scope == 'current' and verdict == 'entailed':
            for fact in facts:
                fact['allow_current'] = True
        expected.append(dict(id=id, suite='new_probes12', verdict=verdict, entailed=verdict=='entailed',
                             bindings={}, required_evidence=['E1'] if verdict in ['entailed', 'contradicted'] else [],
                             facts=facts, critical_negative=verdict!='entailed', source='constructed development probe, not human gold'))
    def f(s,p,o,ev='E1',polarity='positive',exclusive=False):
        return dict(subject=s,predicate=p,object=o,polarity=polarity,evidence=[ev],exclusive=exclusive)
    add('current-positive','Ira','employed_at','Havi University','person','organization',
        ['Ira currently works at Havi University.'],'entailed',[f('Ira','employed_at','Havi University')],'current')
    add('current-missing','Ira','employed_at','Havi University','person','organization',
        ['Ira worked at Havi University until 2012.'],'missing',[f('Ira','employed_at','Havi University')],'current')
    add('education-positive','Ari','educated_at','Novi College','person','organization',
        ['Ari studied at Novi College as a student.'],'entailed',[f('Ari','educated_at','Novi College')])
    add('not-province','Ruli','in_province','Western Province','place','province',
        ['Ruli is in the Western Region.'],'missing',[f('Ruli','in_region','Western Region')])
    add('child-not-exclusive','Tena','has_child','Rovi','person','person',
        ['Tena has a child named Deli.'],'missing',[f('Tena','has_child','Deli')])
    add('conflicting-dates','Viri plant','opened_in_year','2001','organization','date',
        ['One source states that Viri plant opened in 2001.','Another source states that Viri plant opened in 2002.'],
        'ambiguous',[f('Viri plant','opened_in_year','2001'),f('Viri plant','opened_in_year','2002','E2')])
    add('conflicting-education','Ari','educated_at','Novi College','person','organization',
        ['One source states that Ari studied at Novi College.','Another source states that Ari never studied at Novi College.'],
        'ambiguous',[f('Ari','educated_at','Novi College'),f('Ari','educated_at','Novi College','E2','negative')])
    add('year-subtraction','Pori plant','opened_in_year','1984','organization','date',
        ['Pori plant opened ten years before the 1994 flood.'],'entailed',[f('Pori plant','opened_in_year','1984')])
    add('absence-not-mentioned','Sali','has_no_teams','true','place','boolean',
        ['Sali has a railway station.'],'missing',[])
    add('explicit-negative','Ari','educated_at','Novi College','person','organization',
        ['Ari never studied at Novi College.'],'contradicted',[f('Ari','educated_at','Novi College',polarity='negative')])
    add('title-article','The Gift','has_author','Mavi','work','person',
        ['The Gift was written by Mavi.'],'entailed',[f('The Gift','has_author','Mavi')])
    add('place-article','The Hague','houses','Beri Museum','place','organization',
        ['Beri Museum is located in The Hague.'],'entailed',[f('The Hague','houses','Beri Museum')])
    assert len(rows)==92
    targets = {r['id']: r['relation']['predicate'] for r in rows}
    for e in expected:
        e['target_predicate'] = targets[e['id']]
    write_jsonl(dest/'inputs.jsonl',rows)
    write_jsonl(dest/'expected.jsonl',expected)
    (dest/'prompts.json').write_text(json.dumps(dict(contract=CONTRACT,extract=EXTRACT_SYSTEM,verify=VERIFY_SYSTEM,direct=DIRECT_SYSTEM),indent=2))
    plan={'scope':'80 unchanged development regressions + 12 new constructed probes; not independent validation',
          'gates':{'regression80':'joint>=72; positive>=36; negative>=36; variable=16; invalid=0; critical false accept=0',
                   'probes12':'all 12 joint correct, no invalid outputs'},
          'changes':['Branch-specific endpoints remove unused calculation fields and duplicate result generation.',
                     'Single quote list; source positions derived programmatically.',
                     'Bounded leading-article normalization retains raw strings and spans.',
                     'Occupational work-at explicitly counts as employment; role directions spelled out.'],
          'models':['qwen14b','qwen7b','llama8b'],
          'stop_rule':'No edits to frozen expectations or scoring after results; no training. Full70 conditional on split gates.'}
    (dest/'plan.json').write_text(json.dumps(plan,indent=2))
    paths=list(dest.glob('*'))+[Path(x) for x in ['evpilot/relation_kernel_v7.py','evpilot/build_relation_v7.py','evpilot/report_relation_v7.py',
        'evpilot/run_relation_v7_suite.py','tests/test_relation_kernel_v7.py','evpilot/relation_kernel_v6.py','evpilot/report_relation_v6.py',
        'evpilot/relation_kernel_v5.py','evpilot/judge_v3_1.py','evpilot/common.py','data/relation-kernel-v6/inputs.jsonl','data/relation-kernel-v6/expected.jsonl']]
    (dest/'freeze.json').write_text(json.dumps({'files':{str(p):file_hash(p) for p in paths}},indent=2))
    print(json.dumps({'frozen_items':len(rows),'regression':80,'probes':12}))


if __name__=='__main__':main()
