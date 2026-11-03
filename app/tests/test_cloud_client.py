import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'sdk/src'))
from evidence_verifier.client import VerifierClient,VerifierError

class FakeClient(VerifierClient):
    def __init__(self,**kwargs):super().__init__(**kwargs);self.calls=[];self.path='/api/verifications/id/advance'
    def _request(self,path,body=None,**kwargs):
        self.calls.append((path,body))
        if body is None:return {'id':'id','state':'queued','advance_path':self.path}
        return {'id':'id','state':'completed','result':{'status':'ok','verdict':'missing','scope':'declared_relations_only'}}

class ClientCloudTests(unittest.TestCase):
    def test_wait_does_not_silently_enable_paid_cloud_steps(self):
        c=FakeClient()
        with self.assertRaises(VerifierError):c.wait('id',timeout=1)
        self.assertEqual(c.calls,[('/api/verifications/id',None)])
    def test_explicit_opt_in_progresses_one_step(self):
        c=FakeClient(advance_cloud_jobs=True)
        self.assertEqual(c.wait('id',timeout=1)['state'],'completed')
        self.assertEqual(c.calls,[('/api/verifications/id',None),('/api/verifications/id/advance',{})])
    def test_server_cannot_redirect_client_to_arbitrary_mutation(self):
        c=FakeClient(advance_cloud_jobs=True);c.path='/api/unrelated-mutation'
        with self.assertRaises(VerifierError):c.wait('id',timeout=1)
        self.assertEqual(len(c.calls),1)

if __name__=='__main__':unittest.main()
