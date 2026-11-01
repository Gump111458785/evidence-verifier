import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from provider import APIProvider,load_config,gateway

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'TEST_REMOTE_KEY':'fixture-secret'});self.env.start()
        self.c={'roles':{r:{'base_url':'https://api.example.invalid/v1','model':r+'-remote','api_key_env':'TEST_REMOTE_KEY','response_format':'json_schema'} for r in ['planner','extractor','verifier','agent']},'search':{'endpoint':'https://search.example.invalid/search','api_key_env':'TEST_REMOTE_KEY','max_results':3}}
    def tearDown(self):self.env.stop()
    def test_placeholder_check_makes_no_network_call(self):
        c=load_config(Path(__file__).resolve().parents[1]/'config.example.json');p=APIProvider(c)
        with patch.object(p,'post',side_effect=AssertionError('No calls permitted')):self.assertFalse(any(r['configured'] for r in p.status()['roles'].values()))
    def test_candidate_blind_messages_preserved(self):
        p=APIProvider(self.c);body={'model':'alias','seed':42,'max_tokens':20,'messages':[{'role':'system','content':'extract only evidence'},{'role':'user','content':'{"evidence":["source only"]}'}],'response_format':{'type':'json_schema','json_schema':{'schema':{'type':'object'}}}}
        with patch.object(p,'post',return_value={'choices':[{'message':{'content':'{}'}}]}) as send:
            p.complete('extractor',body)
        sent=send.call_args.args[1];self.assertEqual(sent['messages'],body['messages']);self.assertNotIn('seed',sent);self.assertEqual(sent['model'],'extractor-remote')
    def test_json_object_mode_keeps_schema_and_no_candidate(self):
        self.c['roles']['verifier']['response_format']='json_object';p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'choices':[{'message':{'content':'{}'}}]}) as send:p.complete('verifier',{'model':'x','messages':[],'response_format':{'json_schema':{'schema':{'required':['verdict']}}}})
        sent=send.call_args.args[1];self.assertEqual(sent['response_format'],{'type':'json_object'});self.assertIn('verdict',sent['messages'][0]['content'])
    def test_unconfigured_is_not_fake_success(self):
        self.c['roles']['extractor']['base_url']=''
        with self.assertRaisesRegex(ValueError,'not configured'):APIProvider(self.c).complete('extractor',{})
    def test_refusal_is_failure(self):
        p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'choices':[{'message':{'content':None,'refusal':'no'}}]}):
            with self.assertRaises(ValueError):p.complete('verifier',{'messages':[]})
    def test_snippet_is_not_source(self):
        p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'results':[{'url':'https://example.org/page','content':'snippet only'}]}):self.assertEqual(p.search('question'),[])
    def test_raw_content_has_source_url(self):
        p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'results':[{'url':'https://example.org/article?id=1','raw_content':'real source body'}]}):
            result=p.search('school','Nora');self.assertEqual(result[0]['doc_id'],'https://example.org/article?id=1')
    def test_search_no_local_fallback(self):
        self.c['search']['endpoint']=''
        with self.assertRaisesRegex(ValueError,'no diagnostic'):APIProvider(self.c).search('question')
    def test_secrets_rejected_in_config_and_insecure_url(self):
        for bad in [{'roles':{'extractor':{'api_key':'never-inline'}}},{'roles':{'verifier':{'base_url':'http://external.example/v1'}}}]:
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'config.json';path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):load_config(path)


class GatewayTests(unittest.TestCase):
    def test_gateway_auth_and_role_dispatch_over_loopback(self):
        import threading
        import urllib.request
        import urllib.error
        c={'roles':{}}
        p=APIProvider(c);server,token=gateway(p)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)+'/v1/chat/completions'
        body=json.dumps({'model':'api-verifier','messages':[], 'response_format':{'json_schema':{'name':'extract'}}}).encode()
        try:
            request=urllib.request.Request(url,data=body,headers={'Authorization':'Bearer '+token})
            with patch.object(p,'complete',return_value={'choices':[{'message':{'content':'{}'}}]}) as complete:
                with urllib.request.urlopen(request) as response:self.assertEqual(response.status,200)
                self.assertEqual(complete.call_args.args[0],'extractor')
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url,data=body))
        finally:server.shutdown();server.server_close();thread.join(timeout=2)



class SearchPageTests(unittest.TestCase):
    def handler(self):
        from provider import search_handler
        from unittest.mock import Mock
        class Base:
            def send(self,value,status=200):return status,value
        p=Mock();p.config={};h=search_handler(Base,p)();h.path='/api/search?q=Nora';h.headers={'Host':'127.0.0.1:8790'}
        return h,p
    def test_search_page_does_not_read_diagnostic_corpus(self):
        h,p=self.handler();p.search.side_effect=ValueError('Search API not configured');code,value=h.do_GET();self.assertEqual(code,409);p.search.assert_called_once_with('Nora')
    def test_cross_site_cannot_trigger_paid_search(self):
        h,p=self.handler();h.headers['Sec-Fetch-Site']='cross-site';self.assertEqual(h.do_GET()[0],403);p.search.assert_not_called()

if __name__=='__main__':unittest.main()
