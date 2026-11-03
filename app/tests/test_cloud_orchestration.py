import sys
import unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'app'),str(ROOT)]
from cloud_runtime.orchestration import Orchestrator
from test_cloud_api import Storage
from test_cloud_pipeline import FakeModel,TEXT
from cloud_runtime.store import CloudError

class AgentModel(FakeModel):
    def complete(self,system,payload,code):
        if 'history' in payload:
            if not payload['history']:value={'action':'search','args':{'query':'Analytical Engine designer'}}
            elif len(payload['history'])==1:value={'action':'open','args':{'doc_id':'https://example.org/engine'}}
            else:
                eid=payload['history'][-1]['observation']['evidence'][0]['id']
                value={'action':'answer','answer':'Charles Babbage','citations':[eid],'reason':'Fixture source'}
        elif 'question_source' in payload:
            q=payload['question'];value={'relations':[{'subject':'Analytical Engine','predicate':'designed_by','object':'Charles Babbage','time_scope':'any'}],
                'clauses':[{'text':q,'start':0,'end':len(q),'kind':'relation','relation_indices':[0],'reason':'Fixture question'}],
                'coverage':'complete','rationale':'Fixture, not live result'}
        else:return super().complete(system,payload,code)
        self.calls.append(payload);return {'parsed':value,'usage':{'prompt_tokens':1,'completion_tokens':1}}

class OrchestrationTests(unittest.TestCase):
    def test_workflow_survives_json_roundtrips_and_advances_once(self):
        store=Storage();model=AgentModel();env={'EV_CLOUD_ACCESS_CODE':'x'*32};owner='owner'
        def app():return Orchestrator(store,env,model,'x'*32,owner)
        job=app().workflow({'question':'Who designed the Analytical Engine?','budget':6,'request_key':'workflow'})
        with patch('cloud_runtime.search.Search.query',return_value=[{'doc_id':'https://example.org/engine','title':'Engine','text':TEXT,'opened':False,'score':1}]):
            for _ in range(40):
                before=len(model.calls);job=app().advance(job['id'])
                self.assertLessEqual(len(model.calls)-before,1)
                if job['state'] in ('completed','failed'):break
        self.assertEqual(job['state'],'completed',job.get('error'))
        self.assertEqual(job['result']['outcome'],'entailed')
        exposed=app().expose(job)
        self.assertEqual(exposed['budget']['used'],2)
        self.assertEqual(exposed['children']['verification']['result']['verdict'],'entailed')
        self.assertEqual(len(model.calls),6)
    def test_canceled_workflow_does_not_create_children(self):
        app=Orchestrator(Storage(),{},FakeModel(),'','owner')
        job=app.workflow({'question':'Who?','budget':4})
        app.jobs.cancel('owner',job['id']);out=app.advance(job['id'])
        self.assertEqual(out['state'],'canceled');self.assertEqual(app.model.calls,[])

if __name__=='__main__':unittest.main()
