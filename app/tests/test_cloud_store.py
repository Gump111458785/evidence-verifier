import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud_runtime.store import Store, CloudError, authorize


class CloudStoreTests(unittest.TestCase):
    def env(self):
        return {'EV_REDIS_REST_URL':'https://redis.example.org', 'EV_REDIS_REST_TOKEN':'fixture',
                'EV_CLOUD_BUDGET_ID':'test-budget-20261103', 'EV_CLOUD_ACCESS_CODE':'a'*24}

    def test_missing_store_fails_closed(self):
        with self.assertRaises(CloudError) as e:Store({}).reserve()
        self.assertEqual(e.exception.code,'storage_not_configured')

    def test_failed_or_corrupt_ledger_never_allows_call(self):
        for value in ({'error':'offline'}, {'result':-2}, {'result':True}, {'result':0}, {'result':10001}):
            with self.subTest(value=value),self.assertRaises(CloudError):
                Store(self.env(),lambda _:value).reserve()

    def test_budget_exhaustion_is_separate(self):
        with self.assertRaises(CloudError) as e:Store(self.env(),lambda _: {'result':-1}).reserve()
        self.assertEqual((e.exception.code,e.exception.status),('budget_exhausted',429))

    def test_atomic_reservation_uses_shared_nonexpiring_keys(self):
        calls=[]
        store=Store(self.env(),lambda c:(calls.append(c) or {'result':250}))
        self.assertEqual(store.reserve()['reserved_millicny'],250)
        self.assertEqual(calls[0][0],'EVAL')
        self.assertEqual(calls[0][-2:],['250','10000'])
        self.assertIn("PTTL", calls[0][1])
        self.assertIn('INCRBY',calls[0][1])

    def test_records_scoped_to_access_identity_and_new_request_not_overwritten(self):
        calls=[];store=Store(self.env(),lambda c:(calls.append(c) or {'result':None}))
        with self.assertRaises(CloudError) as e:store.put('owner','id',{},new=True)
        self.assertEqual(e.exception.status,409)
        self.assertEqual(calls[0][-1],'NX')
        with self.assertRaises(CloudError):store.get('other','id')
        self.assertNotEqual(calls[0][1],calls[1][1])

    def test_access_code_required_without_exposing_value(self):
        with self.assertRaises(CloudError):authorize(self.env(),'bad')
        self.assertEqual(len(authorize(self.env(),'a'*24)),32)
        with self.assertRaises(CloudError):authorize({},'a'*24)


if __name__=='__main__':unittest.main()
