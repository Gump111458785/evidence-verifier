import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from cloud_runtime.jobs import Jobs
from cloud_runtime.store import CloudError

class Memory:
    def __init__(self):self.values={};self.claims=set()
    def index(self,*args):pass
    def key(self,*args):return ':'.join(args)
    def put(self,o,i,v,new=False):
        if new and (o,i) in self.values:raise CloudError('request_exists','exists',409)
        self.values[o,i]=copy.deepcopy(v)
    def get(self,o,i):return copy.deepcopy(self.values[o,i])
    def compare_and_set(self,o,i,previous,value):
        if self.values[o,i]!=previous:return False
        self.values[o,i]=copy.deepcopy(value);return True
    def command(self,c):
        if c[1] in self.claims:return None
        self.claims.add(c[1]);return 'OK'

class JobTests(unittest.TestCase):
    def test_get_does_not_advance_or_bill(self):
        s=Memory();j=Jobs(s);v=j.create('owner','verify',{},'same')
        self.assertEqual(j.get('owner',v['id'])['state'],'queued')
        self.assertEqual(s.claims,set())
    def test_resume_same_key_and_reject_different_input(self):
        j=Jobs(Memory());a=j.create('o','verify',{},'k');b=j.create('o','verify',{},'k')
        self.assertEqual(a['id'],b['id'])
        with self.assertRaises(CloudError):j.create('o','verify',{'x':1},'k')
    def test_completed_step_is_not_reexecuted(self):
        j=Jobs(Memory());v=j.create('o','verify',{});calls=[]
        def run(job):calls.append(1);return {'state':'completed','result':{'status':'ok'}}
        j.advance('o',v['id'],run);j.advance('o',v['id'],run)
        self.assertEqual(len(calls),1)
    def test_cancel_before_claim_prevents_supplier_execution(self):
        j=Jobs(Memory());v=j.create('o','verify',{})
        j.cancel('o',v['id'])
        out=j.advance('o',v['id'],lambda _:self.fail('paid call after cancel'))
        self.assertEqual(out['state'],'canceled')
    def test_stale_write_does_not_overwrite_newer_record(self):
        s=Memory();j=Jobs(s);v=j.create('o','verify',{})
        j.cancel('o',v['id'])
        self.assertFalse(s.compare_and_set('o',v['id'],v,{**v,'state':'running'}))
    def test_lost_instance_does_not_retry_uncertain_step(self):
        s=Memory();j=Jobs(s,clock=lambda:200);v=j.create('o','verify',{})
        v.update(state='running',step_started=1);s.put('o',v['id'],v)
        out=j.advance('o',v['id'],lambda _:self.fail('must not run'))
        self.assertEqual(out['state'],'failed')

if __name__=='__main__':unittest.main()
