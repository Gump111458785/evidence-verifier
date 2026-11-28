"""Short-lived, account-free access to the competition review workflow."""
import hashlib
import hmac
import re
import secrets
import time

from .store import CloudError

ORIGIN = 'https://trisight-sansheng-laed2yludt9.qoder.website'
ALLOWED_ORIGINS = frozenset({ORIGIN, 'https://trisight-o4mn67jtib8.qoder.zone'})
SESSION_PATH = '/api/competition/session'
TTL_SECONDS = 24 * 60 * 60
TOKEN_RE = re.compile(r'guest\.([a-f0-9]{32})\.([0-9]{1,12})\.([a-f0-9]{64})')
RECORD_RE = re.compile(r'/api/assist/[a-f0-9]{24}')
ACTION_RE = re.compile(r'/api/assist/[a-f0-9]{24}/(?:advance|cancel|feedback)')


def review_route(method, path):
    if method == 'GET':
        return path == '/api/assist' or bool(RECORD_RE.fullmatch(path))
    if method == 'POST':
        return path == '/api/assist' or bool(ACTION_RE.fullmatch(path))
    return False


def browser_route(method, path):
    return (review_route(method, path)
            or (method == 'GET' and path in ('/api/health', '/api/library'))
            or (method == 'POST' and path == SESSION_PATH))


def _signature(secret, payload):
    return hmac.new(secret.encode(), ('trisight-competition-v1:' + payload).encode(), hashlib.sha256).hexdigest()


def issue(secret):
    expires = int(time.time()) + TTL_SECONDS
    payload = f'guest.{secrets.token_hex(16)}.{expires}'
    return {'guest_token': payload + '.' + _signature(secret, payload), 'expires_at': expires}


def owner_from_token(secret, token):
    match = TOKEN_RE.fullmatch(token)
    if len(secret) < 24 or not match:
        raise CloudError('guest_session_invalid', '访客会话无效，请刷新参赛页面。', 401)
    ident, expires, signature = match.groups()
    if int(expires) <= int(time.time()) or not hmac.compare_digest(signature, _signature(secret, token.rsplit('.', 1)[0])):
        raise CloudError('guest_session_invalid', '访客会话已失效，请刷新参赛页面。', 401)
    return 'competition-' + ident
