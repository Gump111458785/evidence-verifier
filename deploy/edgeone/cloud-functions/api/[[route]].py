"""EdgeOne Python 3.10 handler; does not start a server or worker thread."""
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).parent/'evlib'))
from cloud_runtime.api import route
from cloud_runtime.store import CloudError

class handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def dispatch(self):
        try:
            raw=self.headers.get('Content-Length','0')
            length=int(raw)
            if length<0 or length>200000:raise CloudError('input_too_large','请求材料超限',413)
            body=json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body,dict):raise CloudError('invalid_request','请求体须为JSON对象',400)
            value=route(self.command,self.path,dict(self.headers.items()),body)
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
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
