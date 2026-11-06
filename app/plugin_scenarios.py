"""Authored LLM sidecar examples; assumed model facts, real source replay and joins."""
from engine import digest
from relations import evaluate, validate_plan
from repair import missing_queries


def fact(evidence, eid, subject, predicate, obj):
    source = next(e for e in evidence if e['id'] == eid)
    def span(text):
        start = source['text'].index(text)
        return [{'evidence_id': eid, 'start': start, 'end': start+len(text), 'text': text}]
    value = {'subject': subject, 'object': obj, 'predicate': predicate, 'polarity': 'positive',
             'exclusive': False, 'time_scope': 'unspecified', 'subject_spans': span(subject),
             'object_spans': span(obj), 'quotes': [{'evidence_id': eid, 'start': 0,
                 'end': len(source['text']), 'text': source['text']}]}
    return {'fact': value, 'verification': {'status': 'ok', 'parsed': {'verdict': 'entailed',
            'rationale': 'Authored ideal plugin example: extraction and support are assumed.'}},
            'source': 'assumed_model_result'}


def catalog():
    examples = []
    for sid, name, explanation, middle_text, middle_subject, middle_predicate, middle_object in [
        ('employment-education', '任职不等于就读', '相关证据可以解释任职，却没有建立受教育关系。',
         'Nora Hale worked at Pine Institute as an engineer.', 'Nora Hale', 'employed_at', 'Pine Institute'),
        ('entity-binding', '两条真关系，也可能接错人', '设计者是Nora，教育证据说的是Leo；单条关系成立不代表链条成立。',
         'Leo Marsh attended Lake University as a student.', 'Leo Marsh', 'educated_at', 'Lake University'),
    ]:
        evidence = [
            {'id': 'E1', 'title': '作品与设计者', 'text': 'The Meridian Engine was designed by Nora Hale.'},
            {'id': 'E2', 'title': '容易混淆的相关材料', 'text': middle_text},
            {'id': 'E3', 'title': '补充教育证据', 'text': 'Nora Hale attended Lake University as a student.'},
        ]
        plan = validate_plan([
            {'subject': 'Meridian Engine', 'predicate': 'designed_by', 'object': '?designer'},
            {'subject': '?designer', 'predicate': 'educated_at', 'object': 'Lake University'},
        ])
        initial_facts = [fact(evidence, 'E1', 'Meridian Engine', 'designed_by', 'Nora Hale'),
                         fact(evidence, 'E2', middle_subject, middle_predicate, middle_object)]
        final_facts = initial_facts + [fact(evidence, 'E3', 'Nora Hale', 'educated_at', 'Lake University')]
        initial = evaluate(plan, initial_facts, evidence[:2])
        final = evaluate(plan, final_facts, evidence)
        examples.append({'id': sid, 'name': name, 'explanation': explanation,
                         'question': 'Which university did the designer of the Meridian Engine attend?',
                         'candidate': 'The designer of the Meridian Engine attended Lake University.',
                         'plan': plan, 'evidence': evidence, 'initial': initial, 'final': final,
                         'queries': missing_queries(plan, initial),
                         'initial_evidence_ids': ['E1', 'E2'], 'final_evidence_ids': ['E1', 'E2', 'E3'],
                         'input_sha256': digest({'plan': plan, 'evidence': evidence})})
    clean_evidence = [
        {'id': 'E1', 'title': '作品与设计者', 'text': 'The Meridian Engine was designed by Nora Hale.'},
        {'id': 'E2', 'title': '校友就读档案', 'text': 'Nora Hale attended Lake University as a student.'},
    ]
    clean_plan = validate_plan([
        {'subject': 'Meridian Engine', 'predicate': 'designed_by', 'object': '?designer'},
        {'subject': '?designer', 'predicate': 'educated_at', 'object': 'Lake University'},
    ])
    clean_facts = [
        fact(clean_evidence, 'E1', 'Meridian Engine', 'designed_by', 'Nora Hale'),
        fact(clean_evidence, 'E2', 'Nora Hale', 'educated_at', 'Lake University'),
    ]
    clean_eval = evaluate(clean_plan, clean_facts, clean_evidence)
    examples.append({
        'id': 'verified-clean',
        'name': '证据充分 · 安静通过并推荐追问（演示）',
        'explanation': '当所有声明关系均获原文直接支持且实体绑定一致时，插件保持轻量不打扰，并提供高价值追问建议（演示）。',
        'question': 'Which university did the designer of the Meridian Engine attend?',
        'candidate': 'The designer of the Meridian Engine, Nora Hale, attended Lake University.',
        'plan': clean_plan,
        'evidence': clean_evidence,
        'initial': clean_eval,
        'final': clean_eval,
        'queries': [],
        'followup_questions': [
            'Nora Hale 在 Lake University 就读期间主修什么专业方向？',
            'Meridian Engine 的设计是否应用了她在 Lake University 时期的实验成果？',
        ],
        'initial_evidence_ids': ['E1', 'E2'],
        'final_evidence_ids': ['E1', 'E2'],
        'input_sha256': digest({'plan': clean_plan, 'evidence': clean_evidence}),
    })
    return {'source': 'ideal_plugin_demo', 'model_inference_performed': False, 'measured': False,
            'model_results': 'authored_assumptions', 'program_steps': ['source_span_replay', 'joint_variable_binding', 'gap_query_templates'],
            'scope': 'declared_relations_only', 'cases': examples}
