"""Explicit ideal-flow teaching scenario. No model inference or benchmark claims."""
from relations import evaluate, validate_plan
from engine import digest
from planning import validate_proposal
from repair import missing_queries

SCENARIO_ID = 'designer-education'


def catalog():
    return [{'id': SCENARIO_ID, 'name': '找到设计师真正的教育经历',
             'description': '初始证据只说明任职，补充取证后完成设计师与教育经历的关系链。',
             'source': 'ideal_scenario', 'model_results': 'assumed'}]


def scenario(sid):
    if sid != SCENARIO_ID:
        raise ValueError('Unknown ideal scenario')
    evidence = [
        {'id': 'E1', 'title': 'Meridian Engine', 'text': 'The Meridian Engine was designed by Nora Hale.'},
        {'id': 'E2', 'title': 'Nora Hale · employment', 'text': 'Nora Hale worked at Pine Institute as an engineer.'},
        {'id': 'E3', 'title': 'Nora Hale · education', 'text': 'Nora Hale attended Lake University as a student.'},
    ]
    plan = validate_plan([
        {'subject': 'Meridian Engine', 'predicate': 'designed_by', 'object': '?designer'},
        {'subject': '?designer', 'predicate': 'educated_at', 'object': 'Lake University'},
    ])
    question = 'Which university did the designer of the Meridian Engine attend?'
    phrase = 'designer of the Meridian Engine'
    # Authored teaching proposal, not a generated model output. The same position
    # and role checks are used when a live proposal is eventually connected.
    proposal = validate_proposal({
        'relations': [dict(subject=r['subject'], predicate=r['predicate'], object=r['object'], time_scope='any') for r in plan],
        'clauses': [
            {'text': phrase, 'start': question.index(phrase), 'end': question.index(phrase)+len(phrase),
             'kind': 'relation', 'relation_indices': [0], 'reason': 'Bind the designer of the named work.'},
            {'text': question, 'start': 0, 'end': len(question), 'kind': 'relation', 'relation_indices': [1],
             'reason': 'Verify the candidate university for the same designer, not an employer.'},
        ], 'coverage': 'complete', 'rationale': 'Authored ideal proposal; no automatic semantic coverage claim.'
    }, question, 'Lake University')

    def assumed_fact(eid, subject, predicate, obj):
        source = next(e for e in evidence if e['id'] == eid)
        def locate(value):
            start = source['text'].index(value)
            return [{'evidence_id': eid, 'start': start, 'end': start+len(value), 'text': value}]
        fact = {'subject': subject, 'object': obj, 'predicate': predicate, 'polarity': 'positive',
                'exclusive': False, 'time_scope': 'unspecified', 'subject_spans': locate(subject),
                'object_spans': locate(obj), 'quotes': [{'evidence_id': eid, 'start': 0,
                    'end': len(source['text']), 'text': source['text']}]}
        return {'fact': fact, 'verification': {'status': 'ok', 'parsed': {'verdict': 'entailed',
                   'rationale': 'Ideal teaching scenario: assumed extraction and support decision.'}},
                'source': 'assumed_model_result'}

    first = [assumed_fact('E1', 'Meridian Engine', 'designed_by', 'Nora Hale'),
             assumed_fact('E2', 'Nora Hale', 'employed_at', 'Pine Institute')]
    initial = evaluate(plan, first, evidence[:2])
    queries = missing_queries(plan, initial)
    final_facts = first + [assumed_fact('E3', 'Nora Hale', 'educated_at', 'Lake University')]
    final = evaluate(plan, final_facts, evidence)
    return {'scenario_id': sid, 'source': 'ideal_scenario', 'measured': False,
            'model_inference_performed': False, 'question': question,
            'candidate': 'Lake University', 'plan': plan, 'plan_proposal': dict(proposal, source='authored_teaching_proposal'),
            'repair_queries': queries,
            'evidence': evidence, 'facts': final_facts,
            'initial': initial, 'final': final,
            'steps': [
                {'title': '把问题转成必要关系', 'source': 'assumed_model_result', 'detail': '预设计划先找设计师，再检查同一人的受教育学校；计划生成不读取证据。'},
                {'title': '取得初始材料', 'source': 'teaching_fixture', 'detail': 'E1描述设计关系；E2描述任职关系。'},
                {'title': '独立抽取与支持性核验', 'source': 'assumed_model_result', 'detail': '演示预设两条事实分别成立；任职不转换为教育。'},
                {'title': '检查必要关系与变量', 'source': 'program', 'detail': '设计师绑定为Nora Hale，教育关系缺失。', 'result': initial['verdict']},
                {'title': '针对教育经历补充取证', 'source': 'teaching_fixture', 'detail': '程序提出查询：' + queries[0]['query'] + '。演示预设补充材料E3，原有证据继续保留。'},
                {'title': '定位引用并重新联合绑定', 'source': 'program', 'detail': '程序回放原文位置，核对同一设计师和候选大学。', 'result': final['verdict']},
            ], 'metrics': {'gpu_seconds': None, 'model_accuracy': None, 'training_gain': None},
            'input_sha256': digest({'plan': plan, 'evidence': evidence})}
