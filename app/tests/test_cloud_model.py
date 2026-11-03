import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from cloud_runtime.model import DeepSeek
from cloud_runtime.store import CloudError

class Budget:
    def __init__(self):self.calls=0
    def reserve(self):self.calls+=1;return {'reserved_millicny':self.calls*250}

class CloudModelTests(unittest.TestCase):
    def env(self):return {'EV_DEEPSEEK_API_KEY':'fixture-not-real','EV_CLOUD_ACCESS_CODE':'a'*24}
    def test_auth_and_input_reject_before_any_paid_reservation(self):
        for code,payload in [('wrong',{}),('a'*24,{'long':'x'*40001})]:
            b=Budget();m=DeepSeek(self.env(),b,lambda _:self.fail('network forbidden'))
            with self.assertRaises(CloudError):m.complete('rules',payload,code)
            self.assertEqual(b.calls,0)
    def test_missing_key_rejects_without_reserving(self):
        b=Budget()
        with self.assertRaises(CloudError):DeepSeek({'EV_CLOUD_ACCESS_CODE':'a'*24},b).complete('',{},'a'*24)
        self.assertEqual(b.calls,0)
    def test_timeout_reserves_once_and_never_retries(self):
        b=Budget();calls=[]
        def fail(body):calls.append(body);raise TimeoutError()
        with self.assertRaises(CloudError):DeepSeek(self.env(),b,fail).complete('',{},'a'*24)
        self.assertEqual((b.calls,len(calls)),(1,1))
    def test_success_marks_real_provider_and_limits(self):
        b=Budget();calls=[]
        def reply(body):
            calls.append(body);return {'choices':[{'finish_reason':'stop','message':{'content':'{"verdict":"missing"}'}}]}
        out=DeepSeek(self.env(),b,reply).complete('',{},'a'*24)
        self.assertEqual(out['parsed']['verdict'],'missing')
        self.assertEqual(calls[0]['max_tokens'],2400)
        self.assertEqual(calls[0]['thinking']['type'],'disabled')
        self.assertEqual(out['source'],'remote_api')
    def test_invalid_output_is_failure_not_missing(self):
        b=Budget()
        with self.assertRaises(CloudError) as e:DeepSeek(self.env(),b,lambda _:{}).complete('',{},'a'*24)
        self.assertEqual(e.exception.code,'model_failure')
        self.assertEqual(b.calls,1)

if __name__=='__main__':unittest.main()
