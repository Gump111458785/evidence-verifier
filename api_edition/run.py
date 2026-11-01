"""Pure-API launcher. --check performs no network calls and requires no ML packages."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from provider import APIProvider,load_config,gateway,search_handler

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path(__file__).with_name('config.example.json'));p.add_argument('--check',action='store_true');p.add_argument('--port',type=int,default=8790);a=p.parse_args()
    provider=APIProvider(load_config(a.config))
    if a.check:
        print(json.dumps(provider.status(),ensure_ascii=False,indent=2));return
    if importlib.util.find_spec('jsonschema') is None:
        raise SystemExit('Pure API runtime needs jsonschema; install requirements.txt in your own virtual environment. No torch or model weights required.')
    # Isolate this process from legacy local-model/agent environment configuration.
    for k in list(os.environ):
        if k.startswith(('EV_MODEL_','EV_VERIFIER_','EV_AGENT_','EV_PLANNER_')):os.environ.pop(k)
    os.environ['EV_EDITION']='pure_api'
    os.environ['EV_API_PROFILE_STATUS']=json.dumps(provider.status())
    bridge,secret=gateway(provider);url='http://127.0.0.1:'+str(bridge.server_port)+'/v1'
    if provider.ready('extractor') and provider.ready('verifier'):
        os.environ.update(EV_MODEL_URL=url,EV_MODEL_NAME='api-verifier',EV_MODEL_KEY=secret)
    if provider.ready('planner'):os.environ.update(EV_PLANNER_URL=url,EV_PLANNER_NAME='api-planner',EV_PLANNER_KEY=secret)
    if provider.ready('agent'):os.environ.update(EV_AGENT_URL=url,EV_AGENT_NAME='api-agent',EV_AGENT_KEY=secret)
    if provider.config.get('extension_id'):os.environ['EV_EXTENSION_ID']=provider.config['extension_id']
    sys.path.insert(0,str(ROOT/'app'))
    import server
    from retrieval import SearchSessions,RetrievalError
    def search(self,query,required_entity=None):
        try:return provider.search(query,required_entity)
        except (ValueError,RuntimeError) as e:raise RetrievalError('search_api_failure',str(e)) from None
    def identity(self):
        # No cross-run search reuse without an explicit immutable index revision.
        from engine import digest
        return digest({'provider_search':provider.config.get('search',{}),'run_namespace':search_namespace})
    search_namespace=provider.config.get('search',{}).get('revision') or secret
    SearchSessions._search=search;SearchSessions.corpus_identity=identity
    app=server.ThreadingHTTPServer(('127.0.0.1',a.port),search_handler(server.Handler,provider))
    thread=threading.Thread(target=bridge.serve_forever,daemon=True);thread.start()
    try:
        server.init();print(json.dumps({'url':'http://127.0.0.1:'+str(a.port),'profile':provider.status(),'gateway':'private_loopback','model_training':False}),flush=True);app.serve_forever()
    finally:
        app.server_close();bridge.shutdown();bridge.server_close()

if __name__=='__main__':main()
