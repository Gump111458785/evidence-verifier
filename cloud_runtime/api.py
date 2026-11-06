"""Same-origin cloud API, durable private tasks and public labelled examples."""
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from engine import CASES,MODELS
from relations import evaluate,validate_plan
from scenarios import scenario
from sidecar import report
from .auth import Accounts,session_token_from_headers
from .store import Store,CloudError,authorize
from .model import DeepSeek
from .pipeline import validate_input,planner_input
from .orchestration import Orchestrator,FAMILIES


def _require_admin_or_operator(headers,env,accounts):
    user=accounts.authenticate(headers)
    if user:
        if user.get('role')!='admin':raise CloudError('admin_required','需要管理员权限。',403)
        return user
    hdr={k.lower():v for k,v in (headers or {}).items()}.get('authorization','')
    if hdr.startswith('Bearer '):
        authorize(env,hdr[7:].strip())
        return {'id':'operator','username':'operator','role':'admin'}
    raise CloudError('auth_required','请先使用管理员账号登录。',401)


def route(method,path,headers,body,env=None,store=None,model=None):
    env=os.environ if env is None else env
    store=store or Store(env);model=model or DeepSeek(env,store)
    accounts=Accounts(store)
    url=urlsplit(path);path=url.path;query=parse_qs(url.query)
    configured=bool(env.get('EV_DEEPSEEK_API_KEY') and store.configured and len(env.get('EV_CLOUD_ACCESS_CODE',''))>=24)
    if method=='GET' and path=='/api/health':
        return {'ok':True,'edition':'pure_api','cloud_mode':'durable_step_jobs','live_configured':configured,
                'invitation_only':True,'auth_mode':'invitation_account',
                'planner':{'configured':configured},'agent':{'configured':configured,'model':'deepseek-flash'},
                'workflow':{'configured':configured,'search_model':'deepseek-flash','planner_model':'deepseek-flash','verifier_model':'deepseek-flash','source':'remote_api'},
                'search_configured':configured,'model':'deepseek-flash','trial_budget_cny':10,
                'corpus_docs':0,'corpus_scope':'api_search_raw_content_not_local_index','no_automatic_retries':True}
    if method=='GET' and path=='/api/inference-status':
        return {'configured':False,'edition':'pure_api','reason':'云端调用远程API，不报告本地GPU负载'}
    if method=='GET' and path=='/api/auth/me':
        user=accounts.authenticate(headers) if (store.configured or hasattr(store,'_auth_kv')) else None
        return {'authenticated':bool(user),'invitation_only':True,'registration_enabled':False,
                'auth_required':True,'legacy_access_code_supported':True,'user':user}
    if method=='POST' and path=='/api/auth/register':
        raise CloudError('invitation_only','当前仅向受邀账号开放登录，不支持公开自助注册。请联系管理员开通账号。',403)
    if method=='POST' and path=='/api/auth/login':
        identifier=(body or {}).get('identifier') or (body or {}).get('username') or (body or {}).get('email') or ''
        password=(body or {}).get('password') or ''
        ua={k.lower():v for k,v in (headers or {}).items()}.get('user-agent','')
        token,user=accounts.login(identifier,password,user_agent=ua)
        return {'authenticated':True,'invitation_only':True,'user':user,'session_token':token,'_set_cookie':token}
    if method=='POST' and path=='/api/auth/logout':
        token=session_token_from_headers(headers)
        if token:accounts.revoke_session(token)
        return {'authenticated':False,'logged_out':True,'_clear_cookie':True}
    if method=='POST' and path=='/api/auth/profile':
        current=accounts.require_user(headers)
        updated=accounts.update_profile(current['id'],display_name=(body or {}).get('display_name'),email=(body or {}).get('email'))
        return {'authenticated':True,'user':updated}
    if method=='POST' and path=='/api/auth/password':
        current=accounts.require_user(headers)
        ua={k.lower():v for k,v in (headers or {}).items()}.get('user-agent','')
        token,updated=accounts.change_password(current['id'],(body or {}).get('current_password') or '',(body or {}).get('new_password') or '',keep_user_agent=ua)
        return {'authenticated':True,'user':updated,'password_updated':True,'session_token':token,'_set_cookie':token}
    if method=='GET' and path=='/api/auth/admin/users':
        _require_admin_or_operator(headers,env,accounts)
        return {'users':accounts.list_users(),'invitation_only':True}
    if method=='POST' and path=='/api/auth/admin/users':
        admin=_require_admin_or_operator(headers,env,accounts)
        created=accounts.create_user(
            username=(body or {}).get('username'),
            email=(body or {}).get('email'),
            password=(body or {}).get('password'),
            display_name=(body or {}).get('display_name') or '',
            role=(body or {}).get('role') or 'member',
            status=(body or {}).get('status') or 'active',
            created_by=admin.get('username','admin'),
        )
        return {'user':created,'generated_password':created.get('generated_password')}
    m_admin=re.fullmatch(r'/api/auth/admin/users/(usr_[a-f0-9]{20})',path)
    if method=='POST' and m_admin:
        _require_admin_or_operator(headers,env,accounts)
        updated=accounts.admin_update_user(
            m_admin[1],
            display_name=(body or {}).get('display_name'),
            email=(body or {}).get('email'),
            role=(body or {}).get('role'),
            status=(body or {}).get('status'),
            new_password=(body or {}).get('new_password'),
            generate_password=bool((body or {}).get('generate_password')),
        )
        return {'user':updated,'generated_password':updated.get('generated_password')}
    if method=='GET':
        snapshots=Path(__file__).with_name('public-data.json')
        if snapshots.exists():
            data=json.loads(snapshots.read_text())
            if path in data:return data[path]
    if method=='POST' and path=='/api/scenarios/designer-education/run':
        value=scenario('designer-education');value['demonstration_id']=value['input_sha256'][:24];return value
    session_user=accounts.authenticate(headers) if (store.configured or hasattr(store,'_auth_kv')) else None
    if session_user:
        expected=env.get('EV_CLOUD_ACCESS_CODE','')
        if len(expected)<24:raise CloudError('access_not_configured','尚未配置服务端访问码')
        code=expected
        owner=hashlib.sha256(('ev-acct:'+session_user['id']).encode()).hexdigest()[:32]
    else:
        code={k.lower():v for k,v in (headers or {}).items()}.get('authorization','')
        if not code.startswith('Bearer '):raise CloudError('access_denied','真实操作与私人记录需要受邀账号登录或访问凭证',401)
        code=code[7:].strip();owner=authorize(env,code)
    app=Orchestrator(store,env,model,code,owner)
    if method=='GET' and path=='/api/budget':
        spent=store.command(['GET',store.key('spent')])
        return {'reserved_millicny':int(spent or 0),'limit_millicny':10000,'refunds':False,'scope':'all_instances_all_restarts'}
    if method=='POST' and path=='/api/sidecar/report':
        check=app.get(body.get('verification_id'));repair=app.get(body['repair_id']) if body.get('repair_id') else None
        return report(check,body.get('question'),body.get('candidate'),repair)
    if path.startswith('/api/data-'):
        from .curation import dispatch
        return dispatch(method,path,query,body,app)
    if method=='GET' and path=='/api/verifications':
        page=store.page(owner,int(query.get('offset',['0'])[0]),int(query.get('limit',['20'])[0]))
        rows=[];repairs=store.page(owner,0,100,'repair')['rows']
        for rec in page['rows']:
            first=rec['input']['plan'][0];result=rec.get('result') or {}
            latest=next((r for r in repairs if r['parent_check_id']==rec['id']),None)
            rows.append({'id':rec['id'],'state':rec['state'],'created':rec['created'],
                         'title':first['subject']+' → '+first['predicate']+' → '+first['object'],
                         'relations':len(rec['input']['plan']),'evidence':len(rec['input']['evidence']),
                         'verdict':result.get('verdict') if result.get('status')=='ok' else None,
                         'source':'remote_api','error':rec.get('error'),
                         'latest_repair':{'id':latest['id'],'state':latest['state'],'verdict':(latest.get('result') or {}).get('final',{}).get('verdict')} if latest else None})
        return {**page,'rows':rows}
    if method=='GET' and path in ['/api/'+f for f in FAMILIES.values()]+['/api/search-sessions']:
        family=path.split('/')[-1];kind=next((k for k,v in FAMILIES.items() if v==family),'session')
        rows=store.page(owner,0,100,kind)['rows']
        for field in ('parent_kind','parent_id','parent_check_id'):
            if field in query:rows=[r for r in rows if r.get(field,r['input'].get(field))==query[field][0]]
        return [dict(app.expose(r),**({'question':r['input']['question']} if kind=='workflow' else {})) for r in rows]
    if method=='POST' and path in ('/api/sidecar/plans','/api/plan-proposals'):
        if not configured:raise CloudError('model_not_configured','模型或持久化存储尚未配置')
        payload=planner_input({k:body.get(k) for k in ('question','candidate')})
        if path=='/api/plan-proposals':
            parent=app.get(body.get('parent_id'))
            if parent['state']!='completed':raise CloudError('invalid_parent','父任务未完成',400)
            kind=body.get('parent_kind')
            if kind=='agent_job' and parent['kind']=='agent' and not parent['result']['abstained']:
                payload=planner_input({'question':parent['question'],'candidate':parent['result']['answer']})
            elif kind!='verifier_run' or parent['kind']!='run' or parent['result']['mode']!='split':
                raise CloudError('invalid_parent','关系规划来源不符',400)
            payload.update(parent_kind=body.get('parent_kind'),parent_id=parent['id'])
        return app.expose(app.create('plan',payload))
    if method=='POST' and path=='/api/verifications':
        if not configured:raise CloudError('model_not_configured','模型或持久化存储尚未配置')
        return app.expose(app.create('verification',validate_input(body),body.get('request_key')))
    if method=='POST' and path=='/api/repair-jobs':return app.expose(app.repair(body.get('parent_check_id'),body.get('budget',6),body.get('rounds',2)))
    if method=='POST' and path=='/api/agent-jobs':return app.expose(app.agent(body.get('question'),body.get('budget',6)))
    if method=='POST' and path=='/api/workflows':return app.expose(app.workflow(body))
    if method=='POST' and path=='/api/search-sessions':return app.search.create(owner,body.get('budget',6),body.get('target'),body.get('parent_run'))
    if method=='POST' and path=='/api/runs':
        if body.get('source') not in ('archive','live') or body.get('mode') not in ('direct','split') or body.get('case_id') not in CASES:
            raise CloudError('invalid_input','来源、模式或历史样本无效',400)
        allowed=MODELS if body['source']=='archive' else MODELS+['cloud-flash','deepseek-flash']
        if body.get('model') not in allowed:raise CloudError('invalid_model','模型选择无效',400)
        spec={k:copy.deepcopy(body[k]) for k in ('case_id','model','mode','source')}
        if spec['source']=='live':
            spec['model']='deepseek-flash'
            row=body.get('custom_input') or CASES[spec['case_id']]
            if body.get('retrieval_session'):row=collected(app,body['retrieval_session'])
            value=validate_input({'evidence':row.get('evidence'),'relations':[row.get('relation')]})
            spec['custom_input']={'id':row.get('id','cloud-input'),'relation':value['plan'][0],'evidence':value['evidence']}
        return app.expose(app.create('run',spec))
    if method=='POST' and path=='/api/relation-checks':
        plan=validate_plan(body.get('relations'));parent=app.get(body.get('parent_id'))
        if parent['state']!='completed':raise CloudError('invalid_parent','父任务未完成',400)
        if body.get('plan_revision_id'):
            revision=app.get(body['plan_revision_id'])
            frozen=revision['input']['snapshot']
            if revision['kind']!='revision' or frozen['parent_id']!=parent['id'] or frozen['relations']!=plan:raise CloudError('revision_conflict','关系修订与当前输入不符',409)
        if parent['kind']=='run':
            result=parent['result']
            if result['mode']!='split':raise CloudError('invalid_parent','仅拆分核验事实可回放',400)
            evidence=result['input']['evidence'];facts=result['output'].get('facts',[])
            spec=validate_input({'evidence':evidence,'relations':plan})
            spec.update(parent_kind=body.get('parent_kind'),parent_id=parent['id'],archived_output=result['output'],parent_provenance={'source':result['source']})
            rec=app.create('check',spec);rec.update(state='completed',result=evaluate(plan,facts,evidence))
            rec['result']['fact_rows']=facts;store.put(owner,rec['id'],rec)
        elif parent['kind']=='agent' and parent['result'] and not parent['result']['abstained']:
            spec=validate_input({'evidence':parent['result']['evidence'],'relations':plan})
            spec.update(parent_kind='agent_job',parent_id=parent['id'])
            rec=app.create('check',spec)
        else:raise CloudError('invalid_parent','父任务没有可核验的独立事实',400)
        return app.expose(rec)
    if method=='POST' and path=='/api/reviews':
        job=app.get(body.get('run_id'))
        if job['kind']!='run' or job['state']!='completed' or body.get('verdict') not in ('entailed','missing','ambiguous','contradicted') or not isinstance(body.get('note'),str) or not 1<=len(body['note'])<=4000:
            raise CloudError('invalid_review','复核输入无效',400)
        previous=copy.deepcopy(job);job.setdefault('reviews',[]).append({'verdict':body['verdict'],'note':body['note'],'created':time.time()})
        if not store.compare_and_set(owner,job['id'],previous,job):raise CloudError('review_conflict','复核版本已变化，请重新读取',409)
        return {'id':job['id']}
    m=re.fullmatch(r'/api/plan-proposals/([a-f0-9]{24})/revisions',path)
    if method=='POST' and m:
        parent=app.get(m[1]);relations=validate_plan(body.get('relations'))
        if parent['kind']!='plan':raise CloudError('wrong_record','不是关系草稿',400)
        rec=app.create('revision',{'proposal_id':parent['id'],'snapshot':{'relations':relations,'parent_kind':parent['input'].get('parent_kind'),'parent_id':parent['input'].get('parent_id'),'note':body.get('note'),'user_edited':True}})
        return {'id':rec['id'],**rec['input']}
    m=re.fullmatch(r'/api/plan-revisions/([a-f0-9]{24})',path)
    if method=='GET' and m:
        rec=app.get(m[1]);return {'id':rec['id'],**rec['input']}
    m=re.fullmatch(r'/api/search-sessions/([a-f0-9]{24})(/calls|/input)?',path)
    if m:
        ident,action=m.groups()
        if method=='GET' and not action:return app.get(ident)
        if method=='GET' and action=='/input':return collected(app,ident)
        if method=='POST' and action=='/calls':return app.search.call(owner,ident,body.get('action'),body.get('args'),code)
    m=re.fullmatch(r'/api/('+ '|'.join(FAMILIES.values()) +r')/([a-f0-9]{24})(/advance|/cancel)?',path)
    if m:
        family,ident,action=m.groups();job=app.get(ident)
        if FAMILIES.get(job['kind'])!=family:raise CloudError('wrong_record','记录类型不符',404)
        if method=='GET' and not action:return app.expose(job)
        if method=='POST' and action=='/advance':return app.expose(app.advance(ident))
        if method=='POST' and action=='/cancel':return app.expose(app.jobs.cancel(owner,ident))
    if method=='POST' and path=='/api/followups':
        parent=app.get(body.get('run_id'))
        if parent['kind']!='run' or parent['state']!='completed':raise CloudError('invalid_parent','需要完整历史记录',400)
        return app.search.create(owner,body.get('budget',6),parent['result']['input']['relation'],parent['id'])
    raise CloudError('route_not_found','接口不存在',404)


def collected(app,ident):
    session=app.get(ident)
    if session.get('kind')!='session':raise CloudError('wrong_record','不是取证会话',400)
    evidence=app.search.evidence(session)
    if not session.get('target') or not evidence:raise CloudError('no_opened_evidence','需要目标关系及已打开的原文',400)
    if session.get('parent_run'):
        from repair import merge_evidence
        parent=app.get(session['parent_run'])
        evidence=merge_evidence(parent['result']['input']['evidence'],evidence)
    return {'id':'search-'+ident,'relation':session['target'],'evidence':evidence,'retrieval_session':ident}
