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
    def test_reasoning_effort_is_opt_in_and_forwarded(self):
        self.c['roles']['verifier']['reasoning_effort']='none'
        p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'choices':[{'message':{'content':'{}'}}]}) as send:
            p.complete('verifier',{'messages':[]})
            self.assertEqual(send.call_args.args[1]['reasoning_effort'],'none')
        self.c['roles']['verifier']['reasoning_effort']='invalid'
        with self.assertRaisesRegex(ValueError,'reasoning_effort'):APIProvider(self.c)
    def test_deepseek_search_maps_native_body_and_preserves_source(self):
        self.c['search']['protocol']='deepseek'
        p=APIProvider(self.c)
        reply={'results':[{'url':'https://example.org/page','content':'Full original source.',
                           'snippet':'Short summary','rerank_score':0.75}]}
        with patch.object(p,'post',return_value=reply) as send:
            docs=p.search('education','Nora')
        self.assertEqual(send.call_args.args[1],{'query':'"Nora" education','count':3,'need_content':True})
        self.assertEqual(docs[0]['text'],'Full original source.')
        self.assertEqual(docs[0]['score'],0.75)
    def test_deepseek_missing_body_and_oversized_anchored_query(self):
        self.c['search']['protocol']='deepseek';p=APIProvider(self.c)
        with patch.object(p,'post',return_value={'results':[{'url':'https://example.org','snippet':'not evidence','summary':'not evidence'}]}):
            self.assertEqual(p.search('question'),[])
        with patch.object(p,'post') as send:
            with self.assertRaisesRegex(ValueError,'128'):p.search('x'*125,'Nora')
            send.assert_not_called()
        self.c['search']['protocol']='unknown'
        with self.assertRaisesRegex(ValueError,'protocol'):APIProvider(self.c)
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



class StrictConfigurationTests(unittest.TestCase):
    def test_bad_types_are_clear_validation_errors(self):
        from provider import validate_config
        cases = [[], None, {'roles':[]}, {'roles':{'verifier':None}}, {'roles':{},'search':[]},
                 {'roles':{},'timeout_seconds':True}, {'roles':{},'timeout_seconds':float('nan')},
                 {'roles':{},'max_response_bytes':2048.5}, {'roles':{},'extension_id':123},
                 {'roles':{'extractor':{'send_seed':'false'}}},
                 {'roles':{},'search':{'api_key_env':'bad name'}},
                 {'roles':{'verifier':{'base_url':'https://example.org:99999/v1'}}}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):validate_config(value)

    def test_configuration_is_copied(self):
        config={'roles':{}}
        p=APIProvider(config);config['roles']['verifier']={'model':'changed'}
        self.assertFalse(p.ready('verifier'));self.assertNotIn('model',p.config['roles']['verifier'])

    def test_identity_separates_credentials_and_unversioned_runs(self):
        c={'roles':{},'search':{'endpoint':'https://search.example.invalid','api_key_env':'TEST_CACHE_ACCOUNT','revision':'index-v1'}}
        with patch.dict(os.environ,{'TEST_CACHE_ACCOUNT':'account-one'}):
            p=APIProvider(c);first=p.search_identity()
            self.assertEqual(first,APIProvider(c).search_identity())
        with patch.dict(os.environ,{'TEST_CACHE_ACCOUNT':'account-two'}):self.assertNotEqual(first,p.search_identity())
        c['search'].pop('revision')
        self.assertNotEqual(APIProvider(c).search_identity(),APIProvider(c).search_identity())


class ProviderOutputTests(unittest.TestCase):
    setUp = ProviderTests.setUp
    tearDown = ProviderTests.tearDown
    def test_malformed_choices_and_truncation_fail_closed(self):
        p=APIProvider(self.c)
        replies=[[], {'choices':[None]}, {'choices':[{'message':[]}]},
                 {'choices':[{'message':{'content':'{}'},'finish_reason':'length'}]},
                 {'choices':[{'message':{'content':'{}','refusal':'blocked'}}]}]
        for reply in replies:
            with self.subTest(reply=reply),patch.object(p,'post',return_value=reply),self.assertRaises(ValueError):
                p.complete('verifier',{'messages':[]})

    def test_search_rejects_non_object_rows_and_non_finite_scores(self):
        p=APIProvider(self.c)
        for row in [None, {'url':'https://example.org','raw_content':'source','score':float('nan')}]:
            with patch.object(p,'post',return_value={'results':[row]}),self.assertRaises(ValueError):p.search('question')


class RuntimeIsolationTests(unittest.TestCase):
    def test_environment_cannot_inherit_local_model_or_extension_settings(self):
        from runtime import configure_environment
        with patch.dict(os.environ,{'EV_MODEL_URL':'http://localhost:8000/v1','EV_MODEL_KEY':'old-local-key',
                 'EV_EXTENSION_ID':'b'*32,'EV_ROUTING_POLICY':'invalid','EV_DB':'keep-runtime.sqlite'}):
            configure_environment(APIProvider({'roles':{}}),'http://127.0.0.1:1234/v1','gateway-secret')
            self.assertNotIn('EV_MODEL_URL',os.environ);self.assertNotIn('EV_MODEL_KEY',os.environ)
            self.assertNotIn('EV_EXTENSION_ID',os.environ);self.assertNotIn('EV_ROUTING_POLICY',os.environ)
            self.assertEqual(os.environ['EV_DB'],'keep-runtime.sqlite')

    def test_api_search_injection_does_not_mutate_research_class(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'app'))
        from retrieval import SearchSessions,RetrievalError
        from runtime import search_sessions
        original_search,original_identity=SearchSessions._search,SearchSessions.corpus_identity
        p=APIProvider({'roles':{}});tools=search_sessions(p,lambda:None,Path('unused.sqlite'))
        with self.assertRaises(RetrievalError):tools._search('query')
        self.assertIs(SearchSessions._search,original_search);self.assertIs(SearchSessions.corpus_identity,original_identity)
        self.assertEqual(tools.corpus_identity(),p.search_identity())



class GatewayFailureTests(unittest.TestCase):
    def test_auth_and_malformed_requests_never_reach_provider(self):
        import threading
        import urllib.request
        import urllib.error
        p=APIProvider({'roles':{}});server,token=gateway(p)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        cases=[('/v1/chat/completions',{},'',401),('/wrong',{},token,404),
               ('/v1/chat/completions',[],token,400),
               ('/v1/chat/completions',{'messages':[],'response_format':{'json_schema':{'name':[]}}},token,400)]
        try:
            with patch.object(p,'complete',side_effect=AssertionError('No provider calls')) as call:
                for path,body,key,expected in cases:
                    request=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key})
                    with self.subTest(expected=expected),self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code,expected)
                call.assert_not_called()
        finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
