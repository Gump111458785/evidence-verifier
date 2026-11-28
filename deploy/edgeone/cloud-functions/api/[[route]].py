"""EdgeOne Python 3.10 handler; does not start a server or worker thread."""
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import json
import re
import sys
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).parent/'evlib'))
from cloud_runtime.api import route
from cloud_runtime.auth import build_session_cookie,clear_session_cookie
from cloud_runtime.competition import ORIGIN as COMPETITION_ORIGIN,browser_route
from cloud_runtime.store import CloudError

EXT_ORIGIN_RE=re.compile(r'^chrome-extension://[a-p]{32}$')


def cors_policy(origin, method, path):
    if EXT_ORIGIN_RE.fullmatch(origin):
        return origin, 'GET, POST, OPTIONS'
    if origin != COMPETITION_ORIGIN:
        return None, None
    if browser_route(method,urlsplit(path).path):
        return origin, method
    return None, None


class handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_OPTIONS(self):
        origin,methods=cors_policy((self.headers.get('Origin') or '').strip(),
                                   self.headers.get('Access-Control-Request-Method',''),self.path)
        self.send_response(204)
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Access-Control-Allow-Methods',methods)
            self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization')
            self.send_header('Access-Control-Max-Age','600')
        self.send_header('Vary','Origin, Access-Control-Request-Method')
        self.send_header('Content-Length','0')
        self.end_headers()
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def dispatch(self):
        cookie_hdr=None
        request_origin=(self.headers.get('Origin') or '').strip()
        origin,_=cors_policy(request_origin,self.command,self.path)
        try:
            if request_origin == COMPETITION_ORIGIN and not origin:
                raise CloudError('origin_route_denied','参赛站点未开放此接口',403)
            raw=self.headers.get('Content-Length','0')
            length=int(raw)
            if length<0 or length>200000:raise CloudError('input_too_large','请求材料超限',413)
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise CloudError('invalid_request','请求体须为JSON对象',400)
            headers=dict(self.headers.items())
            if origin == COMPETITION_ORIGIN:
                # The competition site uses a scoped bearer token, independent of cookies.
                headers={k:v for k,v in headers.items() if k.lower() not in ('cookie','x-ev-session')}
            value=route(self.command,self.path,headers,body)
            if isinstance(value,dict):
                value=dict(value)
                token=value.pop('_set_cookie',None)
                cleared=value.pop('_clear_cookie',False)
                if origin != COMPETITION_ORIGIN:
                    if token:cookie_hdr=build_session_cookie(token,secure=True)
                    elif cleared:cookie_hdr=clear_session_cookie(secure=True)
            status=200
        except CloudError as exc:
            value={'code':exc.code,'error':str(exc)};status=exc.status
        except (ValueError,TypeError,KeyError):
            value={'code':'invalid_request','error':'请求或输出格式无效'};status=400
        except Exception:
            value={'code':'cloud_failure','error':'云端处理失败；未自动重试'};status=500
        data=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
        self.send_header('Vary','Origin')
        if cookie_hdr:self.send_header('Set-Cookie',cookie_hdr)
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
