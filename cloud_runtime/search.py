"""Durable search/open/find sessions; only search contacts the fixed provider.

Source bodies are retained exactly. Open/find select explicit spans; a search
preview is never admitted to the verifier as an opened source.
"""
import copy
import json
import math
import re
import time
import urllib.request
from urllib.parse import urlsplit
from retrieval import SearchSessions, RELATION_TERMS
from .jobs import Jobs
from .store import CloudError, authorize


class Search:
    def __init__(self,store,env,transport=None):
        self.store,self.env,self.transport=store,env,transport

    def create(self,owner,budget=6,target=None,parent_run=None):
        if type(budget) is not int or not 1<=budget<=12:
            raise CloudError('invalid_budget','工具预算须为1至12',400)
        plan=[]
        if target:
            if target.get('predicate') not in RELATION_TERMS:raise CloudError('unsupported_relation','关系不支持',400)
            names=[target[k] for k in ('subject','object') if not target[k].startswith('?')]
            if not names:raise CloudError('unbound_target','缺少可检索实体',400)
            plan=[' '.join(names+[RELATION_TERMS[target['predicate']]])]
        rec=Jobs(self.store).create(owner,'session',{'budget':budget,'target':target,'parent_run':parent_run})
        rec.update(state='active',used=0,budget=budget,target=target,parent_run=parent_run,
                   plan=plan,documents=[],calls=[],corpus_sha256='deepseek-search-raw-v1')
        self.store.put(owner,rec['id'],rec)
        return rec

    def query(self,query,code,anchor=None):
        authorize(self.env,code)
        if not isinstance(query,str) or not query.strip() or len(query)>128:
            raise CloudError('invalid_query','DeepSeek查询须为1至128字符，未自动截断',400)
        if anchor is not None and (not isinstance(anchor,str) or not anchor.strip() or len(anchor)>100):
            raise CloudError('invalid_anchor','检索锚点无效',400)
        query=('"'+anchor+'" ' if anchor else '')+query
        if len(query)>128:raise CloudError('invalid_query','加上实体后的查询超过128字符',400)
        key=self.env.get('EV_DEEPSEEK_API_KEY')
        if not key:raise CloudError('model_not_configured','搜索密钥尚未配置')
        self.store.reserve()
        try:
            body={'query':query,'count':2,'need_content':True}
            if self.transport:reply=self.transport(body)
            else:
                req=urllib.request.Request('https://api.deepseek.com/v1/tools/search',data=json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*a,**k):raise ValueError('Redirect disabled')
                with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as r:raw=r.read(1_500_001)
                if len(raw)>1_500_000:raise ValueError('Oversized search response')
                reply=json.loads(raw)
            rows=reply['results']
            if not isinstance(rows,list) or len(rows)>2:raise ValueError('Invalid search response')
            docs=[]
            for row in rows:
                url,text=row.get('url'),row.get('content')
                if not isinstance(url,str) or urlsplit(url).scheme!='https' or not isinstance(text,str) or not text.strip():continue
                if len(text)>200000:raise ValueError('Oversized source; not truncated')
                score=row.get('rerank_score',0)
                if type(score) not in (int,float) or not math.isfinite(score):score=0
                docs.append({'doc_id':url,'title':row.get('title') or url,'text':text,'score':score,'opened':False})
            return docs
        except Exception:
            raise CloudError('search_failure','搜索未返回有效原文；未自动重试，调用预留仍保留',502) from None

    def call(self,owner,ident,action,args,code):
        if action not in ('search','open','find') or not isinstance(args,dict):raise CloudError('invalid_action','工具参数无效',400)
        session=self.store.get(owner,ident)
        if session.get('kind')!='session':raise CloudError('wrong_record','不是取证会话',400)
        if session['state']=='closed' or session['used']>=session['budget']:raise CloudError('tool_budget_exhausted','工具预算已用完',429)
        # Permanent claim prevents concurrent or repeated unknown supplier calls.
        claim=self.store.key('tool',owner+':'+ident+':'+str(session['used']))
        if self.store.command(['SET',claim,'claimed','NX'])!='OK':
            raise CloudError('tool_result_unknown','本步骤已提交或结果未知；未自动重试',409)
        before=copy.deepcopy(session);started=time.monotonic()
        try:
            if action=='search':
                docs=self.query(args.get('query'),code,args.get('required_entity'))
                for doc in docs:
                    if not any(d['doc_id']==doc['doc_id'] for d in session['documents']):session['documents'].append(doc)
                result={'ok':True,'evidence':[dict(SearchSessions.evidence(d,0,min(650,len(d['text']))),score=d['score']) for d in docs]}
            else:
                doc=next((d for d in session['documents'] if d['doc_id']==args.get('doc_id')),None)
                if not doc:raise CloudError('unknown_handle','须先在本会话搜索该文档',400)
                if action=='open':
                    doc['opened']=True
                    result={'ok':True,'evidence':[SearchSessions.evidence(doc,0,min(2400,len(doc['text'])))]}
                else:
                    keyword=args.get('keyword')
                    if not doc['opened']:raise CloudError('not_opened','须先打开原文',400)
                    if not isinstance(keyword,str) or not 1<=len(keyword)<=100:raise CloudError('invalid_keyword','关键词须为1至100字符',400)
                    hits=list(re.finditer(re.escape(keyword),doc['text'],re.I))[:5]
                    result={'ok':True,'evidence':[SearchSessions.evidence(doc,max(0,h.start()-100),min(len(doc['text']),h.end()+250)) for h in hits],'matches':len(hits)}
        except CloudError as exc:
            result={'ok':False,'error':exc.code,'message':str(exc),'evidence':[]}
        session['used']+=1
        session['state']='exhausted' if session['used']==session['budget'] else 'active'
        result.update(used=session['used'],budget=session['budget'],cached=False)
        session['calls'].append({'action':action,'args':args,'result':result,'created':time.time(),
                                 'latency_ms':round((time.monotonic()-started)*1000,3),'cached':False})
        if not self.store.compare_and_set(owner,ident,before,session):raise CloudError('session_conflict','会话状态冲突，未重复执行')
        return result

    @staticmethod
    def evidence(session):
        rows={}
        for call in session['calls']:
            if call['action'] in ('open','find') and call['result']['ok']:
                rows.update({e['id']:{'id':e['id'],'text':e['text']} for e in call['result']['evidence']})
        return list(rows.values())
