import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT)]

from cloud_runtime.api import route
from cloud_runtime.store import CloudError
from test_cloud_api import Storage
from test_cloud_pipeline import FakeModel, TEXT


class CloudAuthTests(unittest.TestCase):
    def setUp(self):
        self.s = Storage()
        self.m = FakeModel()
        self.env = {'EV_CLOUD_ACCESS_CODE': 'x' * 32, 'EV_DEEPSEEK_API_KEY': 'fixture'}

    def call(self, method, path, body=None, headers=None):
        return route(method, path, headers or {}, body or {}, self.env, self.s, self.m)

    def test_public_self_registration_is_forbidden(self):
        with self.assertRaises(CloudError) as ctx:
            self.call('POST', '/api/auth/register', {'username': 'alice', 'email': 'alice@example.com', 'password': 'StrongPassword!1'})
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(ctx.exception.code, 'invitation_only')

    def test_invited_account_login_session_and_isolated_owner(self):
        created = self.call(
            'POST',
            '/api/auth/admin/users',
            {
                'username': 'reviewer01',
                'email': 'reviewer01@example.com',
                'password': 'ReviewerPass!2026',
                'display_name': 'Reviewer 01',
                'role': 'member',
            },
            headers={'Authorization': 'Bearer ' + 'x' * 32},
        )
        self.assertEqual(created['user']['username'], 'reviewer01')

        login_res = self.call('POST', '/api/auth/login', {'identifier': 'reviewer01', 'password': 'ReviewerPass!2026'})
        self.assertTrue(login_res['authenticated'])
        token = login_res['session_token']
        cookie_headers = {'Cookie': f'ev_session={token}'}

        me = self.call('GET', '/api/auth/me', headers=cookie_headers)
        self.assertTrue(me['authenticated'])
        self.assertEqual(me['user']['username'], 'reviewer01')

        # Invited user can create a verification job using session cookie without knowing EV_CLOUD_ACCESS_CODE
        body = {
            'evidence': [{'id': 'E1', 'text': TEXT}],
            'relations': [{'subject': 'Analytical Engine', 'predicate': 'designed_by', 'object': 'Charles Babbage'}],
            'request_key': 'invited-user-job',
        }
        job = self.call('POST', '/api/verifications', body, headers=cookie_headers)
        self.assertEqual(job['state'], 'queued')

        # Non-admin user cannot access admin user list
        with self.assertRaises(CloudError) as ctx:
            self.call('GET', '/api/auth/admin/users', headers=cookie_headers)
        self.assertEqual(ctx.exception.status, 403)

        # Logout revokes session
        self.call('POST', '/api/auth/logout', headers=cookie_headers)
        me_after = self.call('GET', '/api/auth/me', headers=cookie_headers)
        self.assertFalse(me_after['authenticated'])


if __name__ == '__main__':
    unittest.main()
