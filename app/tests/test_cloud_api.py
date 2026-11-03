import copy
import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'app'),str(ROOT)]
from cloud_runtime.api import route
from cloud_runtime.store import CloudError
from test_cloud_pipeline import FakeModel,TEXT
from test_cloud_jobs import Memory
from engine import CASES

class Storage(Memory):
    configured=True
    def __init__(self):super().__init__();self.indices={}
    def index(self,owner,ident,created):self.indices.setdefault(owner,{})[ident]=created
    def page(self,owner,offset=0,limit=20,kind='verification'):
        ids=sorted(self.indices.get(owner+':'+kind,{}),key=self.indices.get(owner+':'+kind,{}).get,reverse=True)
        return {'rows':[self.get(owner,i) for i in ids[offset:offset+limit]],'total':len(ids),'offset':offset,'limit':limit}
    def get(self,o,i):
        if (o,i) not in self.values:raise CloudError('record_not_found','Missing',404)
        return super().get(o,i)
    def put(self,o,i,value,new=False):return super().put(o,i,json.loads(json.dumps(value)),new)
    def compare_and_set(self,o,i,previous,value):
        return super().compare_and_set(o,i,previous,json.loads(json.dumps(value)))

class CloudAPITests(unittest.TestCase):
    def setUp(self):
        self.s=Storage();self.m=FakeModel();self.env={'EV_CLOUD_ACCESS_CODE':'x'*32,'EV_DEEPSEEK_API_KEY':'fixture'}
    def call(self,method,path,body=None,auth=True):
        return route(method,path,{'Authorization':'Bearer '+'x'*32} if auth else {},body or {},self.env,self.s,self.m)
    def test_unauthorized_cannot_create_paid_work(self):
        with self.assertRaises(CloudError) as e:self.call('POST','/api/verifications',{},False)
        self.assertEqual(e.exception.status,401);self.assertEqual(self.m.calls,[])
    def test_durable_two_phase_report_reload_and_no_paid_get(self):
        body={'evidence':[{'id':'E1','text':TEXT}],
              'relations':[{'subject':'Analytical Engine','predicate':'designed_by','object':'Charles Babbage'}],
              'request_key':'one'}
        job=self.call('POST','/api/verifications',body);path='/api/verifications/'+job['id']
        self.assertEqual(self.call('GET',path)['state'],'queued');self.assertEqual(len(self.m.calls),0)
        first=self.call('POST',path+'/advance');self.assertEqual(first['state'],'queued');self.assertEqual(len(self.m.calls),1)
        final=self.call('POST',path+'/advance');self.assertEqual(final['result']['verdict'],'entailed')
        self.call('POST',path+'/advance');self.assertEqual(len(self.m.calls),2)
        self.assertEqual(self.call('GET',path),final)
        report=self.call('POST','/api/sidecar/report',{'verification_id':job['id'],'question':'Who designed the engine?','candidate':'Charles Babbage'})
        self.assertIn(TEXT,report['supplemental_prompt']);self.assertFalse(report['automatically_sent'])
        self.assertEqual(self.call('GET','/api/verifications')['total'],1)
        self.assertEqual(self.call('POST','/api/verifications',body)['id'],job['id'])
    def test_invalid_relation_not_classified_missing(self):
        with self.assertRaises(ValueError):self.call('POST','/api/verifications',{'relations':[{'subject':'A','object':'B','predicate':'invented'}],'evidence':[{'id':'e','text':'A B'}]})
        self.assertEqual(self.m.calls,[])
    def test_archived_run_never_calls_model(self):
        job=self.call('POST','/api/runs',{'case_id':next(iter(CASES)),'model':'qwen7b','mode':'split','source':'archive'})
        final=self.call('POST',job['advance_path'])
        self.assertEqual(final['result']['source'],'archived_v7');self.assertEqual(self.m.calls,[])
    def test_memory_curation_persists_without_local_database(self):
        self.assertEqual(self.call('GET','/api/data-candidates')['total'],0)
        exported=self.call('POST','/api/data-exports')
        self.assertEqual(exported['manifest']['rows'],0)
        restored=self.call('GET','/api/data-exports/'+exported['manifest']['id'])
        self.assertEqual(restored,exported);self.assertEqual(self.m.calls,[])
    def test_wrong_family_does_not_expose_record(self):
        job=self.call('POST','/api/runs',{'case_id':next(iter(CASES)),'model':'qwen7b','mode':'split','source':'archive'})
        with self.assertRaises(CloudError):self.call('GET','/api/verifications/'+job['id'])

if __name__=='__main__':unittest.main()
