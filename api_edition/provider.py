"""Remote API gateway. No model SDK, torch, weight loading or training."""
import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit
import urllib.request
import urllib.error

ROLES = ('planner','extractor','verifier','agent')


def endpoint_ok(value):
    u = urlsplit(value)
    return bool(u.netloc and not u.username and not u.password and not u.query and not u.fragment
                and (u.scheme == 'https' or u.scheme == 'http' and u.hostname in ('127.0.0.1','localhost','::1')))


def load_config(path):
    c=json.loads(Path(path).read_text())
    if set(c)-{'roles','search','timeout_seconds','max_response_bytes','extension_id'}:
        raise ValueError('Unknown API configuration field')
    if not isinstance(c.get('roles'),dict) or set(c['roles'])-set(ROLES):
        raise ValueError('Configure planner/extractor/verifier/agent roles')
    for role in ROLES:
        v=c['roles'].setdefault(role,{})
        if set(v)-{'base_url','model','api_key_env','response_format','send_seed','token_parameter'}:
            raise ValueError('Unknown role configuration; keep secrets only in environment variables')
        if v.get('base_url') and not endpoint_ok(v['base_url']):
            raise ValueError('API URL must be HTTPS (HTTP allowed only on loopback), without embedded credentials/query')
        if v.get('response_format','json_schema') not in ('json_schema','json_object','none'):
            raise ValueError('Unsupported response_format')
        if v.get('token_parameter','max_tokens') not in ('max_tokens','max_completion_tokens'):
            raise ValueError('Unsupported token parameter')
        if v.get('api_key_env') and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',v['api_key_env']):
            raise ValueError('api_key_env must name an environment variable')
    s=c.setdefault('search',{})
    if set(s)-{'endpoint','api_key_env','revision','max_results'}:
        raise ValueError('Unknown search configuration')
    if s.get('endpoint') and not endpoint_ok(s['endpoint']):
        raise ValueError('Search endpoint must be HTTPS or loopback HTTP')
    if type(s.get('max_results',3)) is not int or not 1<=s.get('max_results',3)<=5:
        raise ValueError('max_results must be 1–5')
    if not 1<=float(c.get('timeout_seconds',90))<=180 or not 1024<=int(c.get('max_response_bytes',2000000))<=4000000:
        raise ValueError('Invalid timeout/response limit')
    if c.get('extension_id') and not re.fullmatch(r'[a-p]{32}',c['extension_id']):
        raise ValueError('extension_id must be the installed Chrome extension ID')
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeError('Provider redirect rejected; credentials were not forwarded')


class APIProvider:
    def __init__(self,config):self.config=config
    def ready(self,role):
        v=self.config['roles'].get(role,{})
        return bool(v.get('base_url') and v.get('model') and v.get('api_key_env') and os.environ.get(v['api_key_env']))
    def status(self):
        return {'edition':'pure_api','local_model_loading':False,'training_enabled':False,
                'roles':{r:{'configured':self.ready(r),'model':self.config['roles'].get(r,{}).get('model','')} for r in ROLES},
                'search_configured':self.search_ready(),'paid_api_calls_performed_by_check':False}
    def search_ready(self):
        s=self.config.get('search',{});return bool(s.get('endpoint') and s.get('api_key_env') and os.environ.get(s['api_key_env']))
    def post(self,url,body,key):
        request=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        limit=int(self.config.get('max_response_bytes',2000000))
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=float(self.config.get('timeout_seconds',90))) as response:
                data=response.read(limit+1)
        except urllib.error.HTTPError as e:
            raise RuntimeError('Provider HTTP '+str(e.code)+'; response body withheld; no automatic retry') from None
        except (OSError,TimeoutError):
            raise RuntimeError('Provider transport failure; no automatic retry') from None
        if len(data)>limit:raise ValueError('Provider response exceeds size limit')
        try:return json.loads(data)
        except (ValueError,UnicodeDecodeError):raise ValueError('Provider response is not valid JSON') from None
    def complete(self,role,body):
        if not self.ready(role):raise ValueError('API role not configured: '+role)
        v=self.config['roles'][role];payload=copy.deepcopy(body);payload['model']=v['model'];payload['stream']=False
        if not v.get('send_seed',False):payload.pop('seed',None)
        if v.get('token_parameter')=='max_completion_tokens':payload['max_completion_tokens']=payload.pop('max_tokens')
        rf=payload.get('response_format',{});schema=rf.get('json_schema',{}).get('schema')
        mode=v.get('response_format','json_schema')
        if mode!='json_schema':
            payload.pop('response_format',None)
            if mode=='json_object':payload['response_format']={'type':'json_object'}
            payload['messages']=list(payload['messages'])
            payload['messages'].insert(0,{'role':'system','content':'Return one JSON object only. Required output schema: '+json.dumps(schema or {'type':'object'})})
        response=self.post(v['base_url'].rstrip('/')+'/chat/completions',payload,os.environ[v['api_key_env']])
        if not isinstance(response,dict) or not isinstance(response.get('choices'),list) or not response['choices']:
            raise ValueError('Provider response lacks choices')
        content=response['choices'][0].get('message',{}).get('content')
        if not isinstance(content,str) or not content.strip():raise ValueError('Provider returned empty/refused/non-text completion')
        # Each existing stage still validates JSON/schema and source positions locally.
        response['ev_api_role']=role;response['ev_api_model']=v['model']
        return response
    def search(self,query,required_entity=None):
        if not self.search_ready():raise ValueError('Search API not configured; no diagnostic-corpus fallback')
        if not isinstance(query,str) or not 1<=len(query)<=500:raise ValueError('Query must contain 1–500 characters')
        if required_entity and (not isinstance(required_entity,str) or len(required_entity)>300):raise ValueError('Invalid entity anchor')
        s=self.config['search'];q=(('"'+required_entity+'" ') if required_entity else '')+query
        reply=self.post(s['endpoint'],{'query':q,'max_results':s.get('max_results',3),'include_raw_content':True},os.environ[s['api_key_env']])
        results=reply.get('results')
        if not isinstance(results,list) or len(results)>s.get('max_results',3):raise ValueError('Search API results invalid or oversized')
        docs=[];seen=set()
        for r in results:
            url=r.get('url','');text=r.get('raw_content')
            u=urlsplit(url)
            if u.scheme!='https' or not u.netloc or u.username or u.password or not isinstance(text,str) or not text.strip():
                continue  # Snippet-only results are never promoted to evidence.
            if url in seen:continue
            if len(text)>200000:raise ValueError('Source text exceeds limit; no silent raw-content truncation')
            seen.add(url);docs.append({'doc_id':url,'title':str(r.get('title') or url),'text':text,'score':r.get('score',0)})
        return docs


def gateway(provider):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            try:
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):raise ValueError('Gateway authorization required')
                if self.path!='/v1/chat/completions':raise ValueError('Unsupported gateway path')
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=200000:raise ValueError('Request exceeds gateway limit')
                body=json.loads(self.rfile.read(n));name=body.get('response_format',{}).get('json_schema',{}).get('name')
                role={'extract':'extractor','verify':'verifier','direct':'verifier','relation_proposal':'planner'}.get(name)
                if role is None:
                    if body.get('model')!='api-agent':raise ValueError('Unknown gateway role')
                    role='agent'
                result=provider.complete(role,body);code=200
            except Exception as e:
                result={'error':{'message':str(e),'type':'api_gateway_error'}};code=502
            data=json.dumps(result,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    return server,token


def search_handler(base,provider):
    """Keep the workbench search page on API retrieval too; block cross-site billing."""
    from urllib.parse import parse_qs
    class Handler(base):
        def do_GET(self):
            path=urlsplit(self.path)
            if path.path!='/api/search':return super().do_GET()
            origin=self.headers.get('Origin')
            allowed_extension='chrome-extension://'+provider.config.get('extension_id','')
            if self.headers.get('Sec-Fetch-Site')=='cross-site' or origin and origin not in ('http://'+self.headers.get('Host',''),allowed_extension):
                return self.send({'error':'Cross-site API search rejected'},403)
            try:return self.send(provider.search(parse_qs(path.query).get('q',[''])[0]))
            except (ValueError,RuntimeError) as e:return self.send({'error':str(e),'code':'search_api_failure'},409)
    return Handler
