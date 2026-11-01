"""Local HTTP protocol fixture. Never a real model or a benchmark result.

Runs an isolated workbench on 8791 using the real read-only paragraph corpus.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FixtureModel(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body['messages']
        if 'CANCEL_TEST' in messages[1]['content']:
            time.sleep(.5)
        observations = [json.loads(m['content'])['tool_observation'] for m in messages
                        if m['role'] == 'user' and m['content'].startswith('{"tool_observation"')]
        if not observations:
            action = {'action': 'search', 'args': {'query': 'Marie Curie'}}
        elif len(observations) == 1 and observations[0]['evidence']:
            action = {'action': 'open', 'args': {'doc_id': observations[0]['evidence'][0]['doc_id']}}
        elif len(observations) >= 2 and observations[-1]['evidence']:
            evidence = observations[-1]['evidence'][0]
            years = re.findall(r'\b\d{4}\b', evidence['text'])
            action = {'action': 'answer', 'answer': years[0] if years else 'No year found',
                      'citations': [evidence['id']], 'reason': 'Protocol fixture copies the first year from the opened passage; not a model judgment.'}
        else:
            action = {'action': 'abstain', 'reason': 'Protocol fixture found no passage'}
        data = json.dumps({'choices': [{'message': {'content': json.dumps(action)}}],
                           'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='ev-agent-fixture-') as folder:
        model = ThreadingHTTPServer(('127.0.0.1', 0), FixtureModel)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        os.environ.update(EV_DB=str(Path(folder) / 'fixture.sqlite'),
                          EV_AGENT_URL=f'http://127.0.0.1:{model.server_port}/v1',
                          EV_AGENT_NAME='protocol-fixture-http', EV_AGENT_REVISION='fixture-v1')
        import server
        server.init()
        app = ThreadingHTTPServer(('127.0.0.1', 8791), server.Handler)
        print('ISOLATED PROTOCOL FIXTURE: http://127.0.0.1:8791 (not model inference)', flush=True)
        try:
            app.serve_forever()
        finally:
            app.server_close(); model.shutdown(); model.server_close()
