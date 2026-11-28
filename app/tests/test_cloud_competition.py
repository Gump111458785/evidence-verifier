import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from test_cloud_api import Storage
from test_cloud_pipeline import FakeModel
from cloud_runtime.api import route
from cloud_runtime.competition import ORIGIN, SESSION_PATH, TTL_SECONDS
from cloud_runtime.store import CloudError, Store


class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.env = {'EV_CLOUD_ACCESS_CODE': 'x' * 32, 'EV_DEEPSEEK_API_KEY': 'fixture'}
        self.store = Storage()
        self.model = FakeModel()

    def call(self, method, path, token='', body=None, origin=ORIGIN):
        headers = {'Origin': origin}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return route(method, path, headers, body or {}, self.env, self.store, self.model)

    def test_guests_can_start_without_account_and_cannot_read_other_guest(self):
        owner = self.call('POST', SESSION_PATH)['guest_token']
        stranger = self.call('POST', SESSION_PATH)['guest_token']
        body = {'question': 'When?', 'answer': 'It opened in 1889.', 'modes': ['predict']}
        job = self.call('POST', '/api/assist', owner, body)
        path = '/api/assist/' + job['id']
        self.assertEqual(self.call('GET', path, owner)['state'], 'queued')
        with self.assertRaises(CloudError) as denied:
            self.call('GET', path, stranger)
        self.assertEqual(denied.exception.status, 404)
        self.assertEqual(self.model.calls, [])

    def test_guest_tokens_cannot_access_admin_or_legacy_routes(self):
        token = self.call('POST', SESSION_PATH)['guest_token']
        for path in ('/api/auth/admin/users', '/api/budget', '/api/verifications'):
            with self.assertRaises(CloudError) as denied:
                self.call('GET', path, token, origin='')
            self.assertEqual(denied.exception.code, 'guest_scope_denied')
        with self.assertRaises(CloudError) as denied:
            self.call('GET', '/api/assist', 'x' * 32)
        self.assertEqual(denied.exception.status, 401)

    def test_wrong_origin_and_expired_token(self):
        with self.assertRaises(CloudError):
            self.call('POST', SESSION_PATH, origin='https://other.qoder.zone')
        with patch('cloud_runtime.competition.time.time', return_value=1000):
            token = self.call('POST', SESSION_PATH)['guest_token']
        with patch('cloud_runtime.competition.time.time', return_value=1000 + TTL_SECONDS):
            with self.assertRaises(CloudError):
                self.call('GET', '/api/assist', token)

    def test_guest_reservation_is_bounded_before_shared_reservation(self):
        calls = []

        def transport(command):
            calls.append(command)
            return {'result': -1}

        store = Store({'EV_REDIS_REST_URL': 'https://redis.example.com', 'EV_REDIS_REST_TOKEN': 'fixture'}, transport)
        store.competition_owner = 'test-guest'
        with self.assertRaises(CloudError) as denied:
            store.reserve()
        self.assertEqual(denied.exception.code, 'guest_budget_exhausted')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ['250', '1250'])

    def test_browser_cors_only_allows_the_competition_routes(self):
        path = Path(__file__).resolve().parents[2] / 'deploy/edgeone/cloud-functions/api/[[route]].py'
        spec = importlib.util.spec_from_file_location('test_edgeone_route', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.cors_policy(ORIGIN, 'POST', SESSION_PATH), (ORIGIN, 'POST'))
        self.assertEqual(module.cors_policy(ORIGIN, 'GET', '/api/assist?limit=10'), (ORIGIN, 'GET'))
        self.assertEqual(module.cors_policy(ORIGIN, 'GET', '/api/auth/admin/users'), (None, None))
        self.assertEqual(module.cors_policy('https://other.qoder.zone', 'POST', SESSION_PATH), (None, None))
