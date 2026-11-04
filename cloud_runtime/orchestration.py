"""Resumable cloud composition. Each advance performs at most one supplier call."""
import copy
import json
import time
import jsonschema
from agent import SYSTEM as AGENT_SYSTEM
from engine import CASES, replay, check_spans
from repair import missing_queries, merge_evidence
from relations import evaluate, validate_plan
from grounding import source_payload
from evpilot import relation_kernel_v7 as kernel
from .pipeline import step as verify_step, validate_input, planner_input
from .jobs import Jobs
from .search import Search
from .store import CloudError

FAMILIES={'verification':'verifications','plan':'plan-proposals','agent':'agent-jobs',
          'repair':'repair-jobs','workflow':'workflows','run':'runs','check':'relation-checks'}


class Orchestrator:
    def __init__(self,store,env,model,code,owner):
        self.store,self.env,self.model,self.code,self.owner=store,env,model,code,owner
        self.jobs=Jobs(store);self.search=Search(store,env)

    def get(self,ident):return self.jobs.get(self.owner,ident)
    def create(self,kind,payload,key=None):return self.jobs.create(self.owner,kind,payload,key)
    def advance(self,ident):return self.jobs.advance(self.owner,ident,self.step)
    def event(self,kind,payload):return {'kind':kind,'payload':payload,'created':time.time()}

    def agent(self,question,budget):
        if not isinstance(question,str) or not 1<=len(question.strip())<=2000:
            raise CloudError('invalid_question','问题须为1至2,000字符',400)
        session=self.search.create(self.owner,budget)
        job=self.create('agent',{'question':question.strip(),'session_id':session['id'],'budget':budget})
        job.update(question=question.strip(),session_id=session['id'],config={'model':'deepseek-flash',
                   'source':'remote_api','tool_budget':budget})
        self.store.put(self.owner,job['id'],job)
        return job

    def repair(self,parent_id,budget=6,rounds=2):
        if type(budget) is not int or not 2<=budget<=12 or type(rounds) is not int or not 1<=rounds<=2:
            raise CloudError('invalid_budget','补搜预算须为2至12，轮次为1或2',400)
        parent=self.get(parent_id)
        if parent['kind'] not in ('verification','check') or parent['state']!='completed' or parent['result'].get('status')!='ok' or parent['result'].get('verdict')!='missing':
            raise CloudError('invalid_parent','补搜需要有效的证据缺失核验记录',400)
        session=self.search.create(self.owner,budget)
        job=self.create('repair',{'parent_check_id':parent_id,'plan':parent['input']['plan'],
                 'evidence':parent['input']['evidence'],'additional_tool_budget':budget,
                 'max_rounds':rounds,'initial_tool_calls':None,'session_id':session['id']})
        job.update(parent_check_id=parent_id,session_id=session['id'],
                   result={'initial':parent['result'],'final':parent['result'],'evidence':parent['input']['evidence'],
                           'rounds':[],'stop_reason':None,'source':'live_retrieval_repair','model_inference_performed':False},
                   work={'phase':'search','gap_index':0,'round':0,'child':None})
        self.store.put(self.owner,job['id'],job);return job

    def workflow(self,body):
        q=body.get('question');budget=body.get('budget',6)
        if not isinstance(q,str) or not 1<=len(q.strip())<=2000 or type(budget) is not int or not 4<=budget<=12:
            raise CloudError('invalid_input','完整任务须有问题和4至12次工具预算',400)
        job=self.create('workflow',{'question':q.strip(),'total_tool_budget':budget,'search_budget_cap':budget-2,
                        'services':{'source':'remote_api'}},body.get('request_key'))
        return job

    def step(self,job):
        kind=job['kind']
        if kind in ('verification','plan','check'):return verify_step(job,self.model,self.code)
        if kind=='agent':return self.agent_step(job)
        if kind=='repair':return self.repair_step(job)
        if kind=='workflow':return self.workflow_step(job)
        if kind=='run':return self.run_step(job)
        raise CloudError('unsupported_job','任务类型不支持',400)

    def agent_step(self,job):
        work=copy.deepcopy(job.get('work') or {'messages':[],'pending':None,'turn':0,'usage':[]})
        events=list(job['events']);session=self.get(job['session_id'])
        if work['pending']:
            action=work['pending'];result=self.search.call(self.owner,session['id'],action['action'],action.get('args'),self.code)
            events.append(self.event('tool_observation',{'action':action,'result':result}))
            work['messages'].append({'action':action,'observation':result});work['pending']=None
            return {'state':'queued','work':work,'events':events}
        if work['turn']>job['input']['budget']:
            raise CloudError('turn_limit','模型超过动作轮次上限，未继续调用',429)
        reply=self.model.complete(AGENT_SYSTEM,{'question':job['question'],'history':work['messages'],
                                   'remaining_tools':session['budget']-session['used']},self.code)
        action=reply['parsed'];name=action.get('action')
        events.append(self.event('model_response',{'raw':json.dumps(action,ensure_ascii=False),'usage':reply.get('usage')}))
        work['usage'].append(reply.get('usage'));work['turn']+=1
        if name in ('answer','abstain'):
            evidence=self.search.evidence(session);known={e['id']:e for e in evidence};cites=action.get('citations',[])
            if not isinstance(action.get('reason'),str) or not action['reason'].strip():raise CloudError('invalid_model_action','模型缺少简短说明',502)
            if name=='answer' and (not isinstance(action.get('answer'),str) or not action['answer'].strip() or not isinstance(cites,list) or not cites or any(not isinstance(x,str) or x not in known for x in cites)):
                raise CloudError('invalid_citation','候选答案引用了未打开的证据或缺少引用',502)
            result={'answer':action.get('answer') if name=='answer' else None,'abstained':name=='abstain',
                    'reason':action['reason'],'evidence':[known[x] for x in dict.fromkeys(cites)] if name=='answer' else [],
                    'usage':{'model_calls':len(work['usage']),'supplier_usage':work['usage']},
                    'source':'remote_api','support_status':'not_independently_verified','stop_reason':name}
            events.append(self.event('final_candidate',result))
            return {'state':'completed','work':work,'result':result,'events':events}
        if name not in ('search','open','find') or not isinstance(action.get('args'),dict):raise CloudError('invalid_model_action','模型动作无效',502)
        if session['used']>=session['budget']:raise CloudError('tool_budget_exhausted','模型在预算用完后仍要求工具',429)
        work['pending']=action
        return {'state':'queued','work':work,'events':events}

    def repair_step(self,job):
        work=copy.deepcopy(job['work']);result=copy.deepcopy(job['result'])
        session=self.get(job['session_id']);gaps=[g for g in missing_queries(job['input']['plan'],result['final']) if g['query']]
        if work['phase']=='verify':
            child=self.get(work['child'])
            if child['state']=='queued':child=self.advance(child['id'])
            if child['state'] in ('failed','canceled'):raise CloudError('repair_verification_failed',child.get('error') or '重核验失败',502)
            if child['state']!='completed':return {'state':'queued','work':work,'result':result}
            result['final']=child['result'];result['rounds'].append({'verification_id':child['id'],'result':child['result']})
            result['model_inference_performed']=True
            if child['result'].get('status')!='ok':
                result['stop_reason']='invalid_output';return {'state':'completed','result':result,'work':work}
            work.update(phase='search',gap_index=0,round=work['round']+1,child=None)
            # Finish this HTTP step after its final model call. A second repair
            # round starts in a later POST, never a model call plus search here.
            return {'state':'queued','work':work,'result':result}
        if result['final'].get('verdict')!='missing' or work['round']>=job['input']['max_rounds']:
            result['stop_reason']='relation_supported' if result['final'].get('verdict')=='entailed' else 'round_limit'
            return {'state':'completed','result':result,'work':work}
        if work['phase']=='open':
            self.search.call(self.owner,session['id'],'open',{'doc_id':work['doc_id']},self.code)
            work.update(phase='search',gap_index=work['gap_index']+1)
            return {'state':'queued','work':work,'result':result}
        if work['gap_index']<len(gaps) and session['budget']-session['used']>=2:
            gap=gaps[work['gap_index']]
            observation=self.search.call(self.owner,session['id'],'search',{'query':gap['query']},self.code)
            if not observation['ok']:raise CloudError('repair_search_failed',observation.get('message','补搜失败'),502)
            if observation['evidence']:work.update(phase='open',doc_id=observation['evidence'][0]['doc_id'])
            else:work['gap_index']+=1
            return {'state':'queued','work':work,'result':result}
        evidence=merge_evidence(result['evidence'],self.search.evidence(session))
        if evidence==result['evidence']:
            result['stop_reason']='no_new_evidence';return {'state':'completed','work':work,'result':result}
        result['evidence']=evidence
        child=self.create('verification',validate_input({'relations':job['input']['plan'],'evidence':evidence}))
        work.update(phase='verify',child=child['id'])
        return {'state':'queued','work':work,'result':result}

    def workflow_step(self,job):
        work=copy.deepcopy(job.get('work') or {'phase':'search','children':{}})
        phase=work['phase'];ids=work['children']
        result=copy.deepcopy(job.get('result') or {'current_stage':phase,'outcome':'pending','candidate':None,
                               'evidence':[],'complete_question_coverage':'not_established','answer_correctness':None})
        if phase not in ids:
            if phase=='search':child=self.agent(job['input']['question'],job['input']['search_budget_cap'])
            elif phase=='planning':child=self.create('plan',planner_input({'question':job['input']['question'],'candidate':result['candidate']}))
            elif phase=='verification':
                proposal=self.get(ids['planning'])['result']['proposal']
                if not proposal['relations']:
                    result['outcome']='no_supported_plan';return {'state':'completed','work':work,'result':result}
                child=self.create('verification',validate_input({'relations':proposal['relations'],'evidence':result['evidence']}))
            else:
                used=self.get(self.get(ids['search'])['session_id'])['used']
                child=self.repair(ids['verification'],job['input']['total_tool_budget']-used,1)
            ids[phase]=child['id']
            return {'state':'queued','work':work,'result':result}
        child=self.get(ids[phase])
        if child['state']=='queued':child=self.advance(child['id'])
        if child['state'] in ('failed','canceled'):raise CloudError('child_failed',child.get('error') or '子任务失败',502)
        if child['state']!='completed':return {'state':'queued','work':work,'result':result}
        if phase=='search':
            if child['result']['abstained']:
                result.update(outcome='search_abstained');return {'state':'completed','work':work,'result':result}
            result.update(candidate=child['result']['answer'],evidence=child['result']['evidence']);work['phase']='planning'
        elif phase=='planning':
            if child['result'].get('status')!='ok':
                result['outcome']='invalid_output';return {'state':'completed','work':work,'result':result}
            work['phase']='verification'
        elif phase=='verification':
            checked=child['result'];result.update(initial_joint_result=checked,final_joint_result=checked)
            used=self.get(self.get(ids['search'])['session_id'])['used']
            if checked.get('status')=='ok' and checked['verdict']=='missing' and job['input']['total_tool_budget']-used>=2:work['phase']='repair'
            else:
                result['outcome']=checked.get('verdict',checked.get('status'));return {'state':'completed','work':work,'result':result}
        else:
            result.update(final_joint_result=child['result']['final'],evidence=child['result']['evidence'],
                          outcome=child['result']['final'].get('verdict',child['result']['final'].get('status')))
            return {'state':'completed','work':work,'result':result}
        result['current_stage']=work['phase']
        return {'state':'queued','work':work,'result':result}

    def run_step(self,job):
        spec=job['input'];row=spec.get('custom_input') or CASES[spec['case_id']]
        if spec['source']=='archive':
            result=replay(spec['case_id'],spec['model'],spec['mode']);result['mode']=spec['mode']
            result['span_checks']=check_spans(row,result['output'])
            return {'state':'completed','result':result}
        if spec['mode']=='direct':
            payload,cat=source_payload(row['evidence'],kernel);schema=kernel.schema_for('direct',cat)
            reply=self.model.complete(kernel.DIRECT_SYSTEM+' Return JSON matching: '+json.dumps(schema),
                                      dict(payload,target_relation=row['relation']),self.code)
            raw=reply['parsed'];jsonschema.validate(raw,schema)
            facts=[kernel.materialize_fact(f,cat) for f in raw['facts']]
            prediction=kernel.match_target(row['relation'],[(f,{'verdict':'entailed'}) for f in facts])
            if raw['verdict'] in ('entailed','contradicted') and prediction['verdict']!=raw['verdict']:
                raise CloudError('invalid_direct_output','裁判结论未被所引事实支持',502)
            if raw['verdict'] in ('missing','ambiguous'):prediction={'verdict':raw['verdict'],'bindings':{}}
            output={'status':'ok','facts':[{'fact':f} for f in facts],'prediction':prediction,'response':reply}
            return {'state':'completed','result':{'source':'remote_api','input':row,'output':output,
                    'model':'deepseek-flash','mode':'direct','span_checks':check_spans(row,output)}}
        nested={**job,'kind':'verification','input':validate_input({'relations':[row['relation']],'evidence':row['evidence']})}
        updates=verify_step(nested,self.model,self.code)
        if updates['state']=='completed':
            checked=updates['result'];facts=checked.get('fact_rows',[])
            output={'status':checked['status'],'facts':facts,'prediction':{'verdict':checked.get('verdict'),
                    'bindings':checked.get('bindings',{})},'grounding':checked.get('grounding')}
            updates['result']={'source':'remote_api','input':row,'output':output,'model':'deepseek-flash',
                      'mode':'split','span_checks':check_spans(row,output),'provenance':checked.get('provenance')}
        return updates

    def expose(self,job):
        value={k:v for k,v in job.items() if k!='work'}
        if job['state']=='failed' and job.get('work'):value['partial_artifacts']=copy.deepcopy(job['work'])
        kind=job['kind']
        if kind in FAMILIES and job['state']=='queued':value['advance_path']='/api/'+FAMILIES[kind]+'/'+job['id']+'/advance'
        if kind in ('repair','agent'):value['session']=self.get(job['session_id'])
        if kind=='plan':
            value['revisions']=[{'id':r['id'],'created':r['created'],**r['input']} for r in
                                 self.store.page(self.owner,0,100,'revision')['rows'] if r['input'].get('proposal_id')==job['id']]
        if kind=='workflow':
            ids=job.get('work',{}).get('children',{})
            value['children']={stage:self.expose(self.get(ident)) for stage,ident in ids.items()}
            used=sum(c.get('session',{}).get('used',0) for c in value['children'].values())
            value['budget']={'used':used,'limit':job['input']['total_tool_budget']}
            known=sum(self.known_model_calls(self.get(ident)) for ident in ids.values())
            value['usage']={'complete':False,'known_totals':{'model_calls':known,'cache_hits':0,'coalesced_hits':0}}
            value['result']=value['result'] or {'outcome':'pending','current_stage':'queued','candidate':None,'evidence':[]}
        if kind=='run':value.setdefault('reviews',[])
        return value

    def known_model_calls(self,job):
        result=job.get('result') or {};work=job.get('work') or {}
        if job['kind']=='agent':return len(work.get('usage',[]))
        if job['kind']=='plan':return int(bool(result.get('provenance',{}).get('usage')))
        if job['kind']=='repair':
            known=sum(len(r.get('result',{}).get('provenance',{}).get('usage',[])) for r in result.get('rounds',[]))
            if work.get('phase')=='verify' and work.get('child'):
                child=self.get(work['child'])
                known+=self.known_model_calls(child)
            return known
        usage=result.get('provenance',{}).get('usage',work.get('usage',[]))
        return len(usage) if isinstance(usage,list) else 0
