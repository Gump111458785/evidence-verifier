import sys
import json
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'app'));sys.path.insert(0,str(ROOT))
from cloud_runtime.pipeline import step,validate_input

TEXT='Charles Babbage designed the Analytical Engine.'
class FakeModel:
    def __init__(self):self.calls=[]
    def complete(self,system,payload,code):
        self.calls.append(payload)
        if 'predicate_definitions' in payload:
            q=payload['evidence'][0]['segments'][0]['quote_id']
            value={'facts':[{'subject':'Analytical Engine','predicate':'designed_by',
                    'object':{'kind':'literal','text':'Charles Babbage'},'polarity':'positive',
                    'qualifier':'none','quote_ids':[q]}]}
        else:value={'verdict':'entailed','rationale':'Controlled local fixture'}
        return {'parsed':value,'usage':{'prompt_tokens':1,'completion_tokens':1}}

class PipelineTests(unittest.TestCase):
    def test_real_kernel_with_predefined_supplier_contract(self):
        spec=validate_input({'evidence':[{'id':'E1','text':TEXT}],
                 'relations':[{'subject':'Analytical Engine','predicate':'designed_by','object':'Charles Babbage'}]})
        job={'kind':'verification','step':0,'input':spec};model=FakeModel()
        first=step(job,model,'fixture')
        self.assertEqual(first['state'],'queued')
        self.assertNotIn('question',model.calls[0]);self.assertNotIn('candidate',model.calls[0])
        job.update(first);job['step']=1
        job=json.loads(json.dumps(job))  # a distinct cloud instance reads JSON
        final=step(job,model,'fixture')
        self.assertEqual(final['result']['verdict'],'entailed')
        self.assertEqual(len(model.calls),2)
        self.assertEqual(final['result']['proof'][0]['quotes'][0]['text'],TEXT)
        self.assertNotIn('target_relation',model.calls[1])

if __name__=='__main__':unittest.main()

class OutputClassificationTests(unittest.TestCase):
    def test_invalid_json_schema_is_not_missing_evidence(self):
        class BadModel:
            def complete(self,*args):return {'parsed':{'guess':'unsupported-format'},'usage':{}}
        spec=validate_input({'evidence':[{'id':'E1','text':TEXT}],
                 'relations':[{'subject':'Analytical Engine','predicate':'designed_by','object':'Charles Babbage'}]})
        result=step({'kind':'verification','step':0,'input':spec},BadModel(),'fixture')
        self.assertEqual(result['result']['status'],'invalid_output')
        self.assertNotIn('verdict',result['result'])
        self.assertEqual(result['result']['raw_response'],{'guess':'unsupported-format'})
