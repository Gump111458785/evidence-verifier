"""EdgeOne Python 3.10 handler; does not start a server or worker thread."""
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import json
import re
import sys
sys.path.insert(0,str(Path(__file__).parent/'evlib'))
from cloud_runtime.api import route
from cloud_runtime.auth import build_session_cookie,clear_session_cookie
from cloud_runtime.store import CloudError

EXT_ORIGIN_RE=re.compile(r'^chrome-extension://[a-p]{32}$')


class handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def _cors_origin(self):
        origin=(self.headers.get('Origin') or '').strip()
        return origin if EXT_ORIGIN_RE.fullmatch(origin) else None
    def do_OPTIONS(self):
        origin=self._cors_origin()
        self.send_response(204)
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Content-Type, Authorization')
            self.send_header('Access-Control-Max-Age','600')
            self.send_header('Vary','Origin')
        self.send_header('Content-Length','0')
        self.end_headers()
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def dispatch(self):
        cookie_hdr=None
        origin=self._cors_origin()
        try:
            raw=self.headers.get('Content-Length','0')
            length=int(raw)
            if length<0 or length>200000:raise CloudError('input_too_large','请求材料超限',413)
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise CloudError('invalid_request','请求体须为JSON对象',400)
            value=route(self.command,self.path,dict(self.headers.items()),body)
            if isinstance(value,dict):
                value=dict(value)
                token=value.pop('_set_cookie',None)
                cleared=value.pop('_clear_cookie',False)
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
