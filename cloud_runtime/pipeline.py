"""One model phase per cloud request, reusing the frozen source/binding kernel."""
import copy
import json
import jsonschema
from grounding import source_payload, materialize_all, expand_local_context, cited_evidence
from relations import evaluate, validate_plan
from planning import SYSTEM as PLAN_SYSTEM, schema as plan_schema, validate_proposal
from evpilot import relation_kernel_v7 as kernel
from .store import CloudError


def validate_input(body):
    if not isinstance(body,dict) or set(body)-{'evidence','relations','request_key','client_tag'}:
        raise CloudError('invalid_input','仅接受证据、关系和请求标识',400)
    plan=validate_plan(body.get('relations'))
    evidence=body.get('evidence')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=40:
        raise CloudError('invalid_evidence','需要1至40段原文',400)
    seen=set()
    for row in evidence:
        if not isinstance(row,dict) or set(row)!={'id','text'} or not isinstance(row['id'],str) or not row['id'] or len(row['id'])>150 or row['id'] in seen or not isinstance(row['text'],str) or not row['text'].strip():
            raise CloudError('invalid_evidence','原文标识或内容无效',400)
        seen.add(row['id'])
    if sum(len(row['text']) for row in evidence)>22000:
        raise CloudError('input_too_large','原文超过22,000字符，未截断',413)
    return {'plan':plan,'evidence':copy.deepcopy(evidence),'scope':'declared_relations_only',
            'complete_question_coverage':'not_established','parent_kind':'standalone_evidence',
            'parent_id':None,'client_tag':body.get('client_tag','cloud')}


def planner_input(body):
    if not isinstance(body,dict) or set(body)-{'question','candidate'}:
        raise CloudError('invalid_input','关系规划只接受问题与候选片段',400)
    if any(not isinstance(body.get(k),str) or not body[k].strip() for k in ('question','candidate')):
        raise CloudError('invalid_input','问题与候选不能为空',400)
    if len(body['question'])>2000 or len(body['candidate'])>1000:
        raise CloudError('input_too_large','问题或候选片段超限',413)
    return copy.deepcopy(body)


def step(job, model, access_code):
    if job['kind']=='plan':
        value={**job['input'],'predicate_definitions':kernel.CONTRACT,
               'question_source':{'text':job['input']['question'],'start':0,'end':len(job['input']['question'])}}
        reply=model.complete(PLAN_SYSTEM+' Return JSON matching this schema: '+json.dumps(plan_schema()),value,access_code)
        try:
            jsonschema.validate(reply['parsed'],plan_schema())
            proposed=validate_proposal(reply['parsed'],job['input']['question'],job['input']['candidate'])
        except (ValueError,KeyError,TypeError,jsonschema.ValidationError) as exc:
            return invalid(reply,'planning',exc)
        return {'state':'completed','result':{'status':'ok','proposal':proposed,'provenance':reply}}
    payload,cat=source_payload(job['input']['evidence'],kernel)
    if job['step']==0:
        schema=kernel.schema_for('extract',cat)
        reply=model.complete(kernel.EXTRACT_SYSTEM+' Return JSON matching: '+json.dumps(schema),payload,access_code)
        try:jsonschema.validate(reply['parsed'],schema)
        except jsonschema.ValidationError as exc:return invalid(reply,'extraction',exc)
        raw=reply['parsed']['facts']
        facts,errors=materialize_all(raw,cat,kernel)
        expansions=[]
        if errors:
            raw,expansions=expand_local_context(raw,errors,payload,cat)
            facts,errors=materialize_all(raw,cat,kernel)
        if errors:
            return {'state':'completed','result':{'status':'invalid_output','error':'原文定位未通过',
                    'grounding':{'errors':errors,'local_context_expansions':expansions},
                    'provenance':{'source':'remote_api','usage':[reply.get('usage')]}}}
        if not raw:
            final=evaluate(job['input']['plan'],[],job['input']['evidence'])
            final.update(fact_rows=[],provenance={'source':'remote_api','usage':[reply.get('usage')]})
            return {'state':'completed','result':final}
        # JSON persistence stringifies dictionary keys; retain an ordered array
        # so a fresh cloud instance can address the same fact by integer index.
        return {'state':'queued','work':{'raw':raw,'facts':[facts[i] for i in range(len(raw))],'rows':[],
                'usage':[reply.get('usage')],'grounding':{'local_context_expansions':expansions}}}
    work=copy.deepcopy(job['work']);index=len(work['rows'])
    raw,fact=work['raw'][index],work['facts'][index]
    schema=kernel.schema_for('verify',cat)
    value={'predicate_definition':kernel.CONTRACT[fact['predicate']],'fact':raw,
           'computed_object':fact['object'],'evidence':cited_evidence(payload,raw['quote_ids'])}
    reply=model.complete(kernel.VERIFY_SYSTEM+' Return JSON matching: '+json.dumps(schema),value,access_code)
    try:jsonschema.validate(reply['parsed'],schema)
    except jsonschema.ValidationError as exc:return invalid(reply,'support_verification',exc)
    work['rows'].append({'fact':fact,'raw':raw,'verification':{'status':'ok','parsed':reply['parsed']}})
    work['usage'].append(reply.get('usage'))
    if len(work['rows'])<len(work['facts']):return {'state':'queued','work':work}
    final=evaluate(job['input']['plan'],work['rows'],job['input']['evidence'])
    final.update(fact_rows=work['rows'],grounding=work['grounding'],
                 provenance={'source':'remote_api','usage':work['usage']})
    return {'state':'completed','result':final,'work':work}


def invalid(reply,stage,exc):
    return {'state':'completed','result':{'status':'invalid_output','stage':stage,
            'error':str(exc)[:1000],'raw_response':reply['parsed'],
            'provenance':{'source':'remote_api','usage':[reply.get('usage')]}}}
