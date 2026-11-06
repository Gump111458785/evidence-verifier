"""Invitation-only accounts and opaque server-side sessions for Evidence Verifier.

Public self-registration is intentionally disabled; accounts are created or managed
only by an administrator or via the server-side operator script.
"""
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import re
import secrets
import time
import uuid

from .store import CloudError, Store

SESSION_COOKIE = 'ev_session'
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
LOGIN_RATE_TTL_SECONDS = 60 * 15
LOGIN_RATE_MAX_ATTEMPTS = 12
PBKDF2_ITERATIONS = 600_000
PASSWORD_MIN = 10
PASSWORD_MAX = 128
USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{2,31}$')
EMAIL_RE = re.compile(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,24}$')
ROLES = {'admin', 'member'}
STATUSES = {'active', 'disabled'}
DUMMY_SALT = '00112233445566778899aabbccddeeff'
DUMMY_HASH = hashlib.pbkdf2_hmac(
    'sha256',
    b'ev-invitation-only-constant-time-guard',
    bytes.fromhex(DUMMY_SALT),
    PBKDF2_ITERATIONS,
).hex()


def clean_username(value):
    text = str(value or '').strip().lower()
    if not USERNAME_RE.fullmatch(text):
        raise CloudError(
            'invalid_username',
            '用户名需为 3–32 位小写字母、数字、点、下划线或连字符，且以字母或数字开头。',
            400,
        )
    return text


def clean_email(value):
    text = str(value or '').strip().lower()
    if len(text) > 160 or not EMAIL_RE.fullmatch(text):
        raise CloudError('invalid_email', '请输入有效的邮箱地址。', 400)
    return text


def clean_display_name(value, fallback=''):
    text = re.sub(r'\s+', ' ', str(value or fallback).strip())
    if not text:
        text = fallback
    if not 1 <= len(text) <= 48:
        raise CloudError('invalid_display_name', '显示名称需为 1–48 个字符。', 400)
    return text


def validate_password(password):
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise CloudError('weak_password', f'密码长度需为 {PASSWORD_MIN}–{PASSWORD_MAX} 位。', 400)
    classes = sum((
        bool(re.search(r'[a-z]', password)),
        bool(re.search(r'[A-Z]', password)),
        bool(re.search(r'\d', password)),
        bool(re.search(r'[^A-Za-z0-9]', password)),
    ))
    if classes < 2:
        raise CloudError('weak_password', '密码需至少包含字母、数字或符号中的两类字符。', 400)
    return password


def generate_temp_password():
    alphabet_upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    alphabet_lower = 'abcdefghijkmnopqrstuvwxyz'
    digits = '23456789'
    symbols = '!@#$%&*+-='
    parts = [
        secrets.choice(alphabet_upper),
        secrets.choice(alphabet_lower),
        secrets.choice(digits),
        secrets.choice(symbols),
    ]
    pool = alphabet_upper + alphabet_lower + digits
    parts.extend(secrets.choice(pool) for _ in range(12))
    secrets.SystemRandom().shuffle(parts)
    return ''.join(parts)


def hash_password(password, salt_hex=None):
    validate_password(password)
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS).hex()
    return {
        'scheme': 'pbkdf2_sha256',
        'iterations': PBKDF2_ITERATIONS,
        'salt': salt.hex(),
        'hash': digest,
    }


def verify_password(password, record):
    if not isinstance(password, str) or not isinstance(record, dict):
        return False
    try:
        salt = bytes.fromhex(str(record.get('salt') or ''))
        iterations = int(record.get('iterations') or PBKDF2_ITERATIONS)
        expected = str(record.get('hash') or '')
    except ValueError:
        return False
    if not salt or not expected or iterations < 100_000 or iterations > 2_000_000:
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations).hex()
    return hmac.compare_digest(expected, actual)


def verify_missing_password(password):
    candidate = password if isinstance(password, str) else ''
    actual = hashlib.pbkdf2_hmac(
        'sha256',
        candidate.encode('utf-8'),
        bytes.fromhex(DUMMY_SALT),
        PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(DUMMY_HASH, actual)


def public_user(user):
    if not isinstance(user, dict):
        return None
    return {
        'id': user.get('id', ''),
        'username': user.get('username', ''),
        'email': user.get('email', ''),
        'display_name': user.get('display_name') or user.get('username', ''),
        'role': user.get('role', 'member'),
        'status': user.get('status', 'active'),
        'created_at': int(user.get('created_at') or 0),
        'updated_at': int(user.get('updated_at') or 0),
        'last_login_at': int(user.get('last_login_at') or 0),
        'report_owner': f"acct:{user.get('id', '')}",
    }


def session_token_from_headers(headers):
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    raw_cookie = str(headers.get('cookie') or '')
    if raw_cookie:
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            cookie = {}
        morsel = cookie.get(SESSION_COOKIE) if hasattr(cookie, 'get') else None
        if morsel and re.fullmatch(r'[a-f0-9]{64}', morsel.value or ''):
            return morsel.value
    auth = str(headers.get('authorization') or '')
    if auth.lower().startswith('bearer '):
        candidate = auth[7:].strip()
        if re.fullmatch(r'[a-f0-9]{64}', candidate):
            return candidate
    token = str(headers.get('x-ev-session') or '').strip()
    if re.fullmatch(r'[a-f0-9]{64}', token):
        return token
    return ''


def build_session_cookie(token, max_age=SESSION_TTL_SECONDS, secure=True):
    parts = [
        f'{SESSION_COOKIE}={token}',
        f'Max-Age={int(max_age)}',
        'HttpOnly',
        'Path=/',
        'SameSite=Strict',
    ]
    if secure:
        parts.insert(3, 'Secure')
    return '; '.join(parts)


def clear_session_cookie(secure=True):
    return build_session_cookie('', max_age=0, secure=secure)


class Accounts:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock

    @property
    def _use_redis(self):
        return isinstance(self.store, Store)

    def _mem(self):
        if not hasattr(self.store, '_auth_kv'):
            self.store._auth_kv = {}
            self.store._auth_sets = {}
        return self.store._auth_kv, self.store._auth_sets

    def _key(self, kind, ident=''):
        suffix = f'{kind}:{ident}' if ident else kind
        if hasattr(self.store, 'key'):
            return self.store.key('auth', suffix)
        return f'ev:local:auth:{suffix}'

    def _kv_get(self, key):
        if self._use_redis:
            return self.store.command(['GET', key])
        kv, _ = self._mem()
        entry = kv.get(key)
        if not entry:
            return None
        val, exp = entry
        if exp and self.clock() > exp:
            kv.pop(key, None)
            return None
        return val

    def _kv_set(self, key, value, ex=None, nx=False):
        if self._use_redis:
            cmd = ['SET', key, value]
            if ex is not None:
                cmd.extend(['EX', str(int(ex))])
            if nx:
                cmd.append('NX')
            return self.store.command(cmd)
        kv, _ = self._mem()
        if nx and key in kv:
            _, exp = kv[key]
            if not exp or self.clock() <= exp:
                return None
        exp = (self.clock() + int(ex)) if ex is not None else None
        kv[key] = (value, exp)
        return 'OK'

    def _kv_del(self, key):
        if self._use_redis:
            return self.store.command(['DEL', key])
        kv, _ = self._mem()
        kv.pop(key, None)
        return 1

    def _kv_incr(self, key, ex=None):
        if self._use_redis:
            count = self.store.command(['INCR', key])
            if count == 1 and ex is not None:
                self.store.command(['EXPIRE', key, str(int(ex))])
            return count
        kv, _ = self._mem()
        now = self.clock()
        entry = kv.get(key)
        if not entry or (entry[1] and now > entry[1]):
            count = 1
            exp = (now + int(ex)) if ex is not None else None
        else:
            count = int(entry[0]) + 1
            exp = entry[1]
        kv[key] = (str(count), exp)
        return count

    def _set_add(self, key, member):
        if self._use_redis:
            return self.store.command(['SADD', key, str(member)])
        _, sets = self._mem()
        sets.setdefault(key, set()).add(str(member))
        return 1

    def _set_rem(self, key, member):
        if self._use_redis:
            return self.store.command(['SREM', key, str(member)])
        _, sets = self._mem()
        sets.setdefault(key, set()).discard(str(member))
        return 1

    def _set_members(self, key):
        if self._use_redis:
            res = self.store.command(['SMEMBERS', key])
            return res if isinstance(res, list) else []
        _, sets = self._mem()
        return list(sets.get(key, set()))

    def _get_json(self, key):
        raw = self._kv_get(key)
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise CloudError('auth_record_invalid', '账号记录损坏。', 503) from None
        return value if isinstance(value, dict) else None

    def _put_json(self, key, value, ttl=None):
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        result = self._kv_set(key, encoded, ex=ttl)
        if result != 'OK':
            raise CloudError('auth_write_failed', '账号状态保存失败。', 503)

    def _check_login_rate(self, identifier):
        digest = hashlib.sha256(str(identifier or '').strip().lower().encode('utf-8')).hexdigest()[:24]
        key = self._key('rate:login', digest)
        raw = self._kv_get(key)
        try:
            attempts = int(raw or 0)
        except ValueError:
            attempts = 0
        if attempts >= LOGIN_RATE_MAX_ATTEMPTS:
            raise CloudError('login_rate_limited', '登录尝试过于频繁，请 15 分钟后再试。', 429)
        return key

    def _record_login_failure(self, key):
        self._kv_incr(key, ex=LOGIN_RATE_TTL_SECONDS)

    def _clear_login_failures(self, key):
        self._kv_del(key)

    def get_user(self, user_id):
        if not re.fullmatch(r'usr_[a-f0-9]{20}', str(user_id or '')):
            return None
        return self._get_json(self._key('user', user_id))

    def find_user(self, identifier):
        text = str(identifier or '').strip().lower()
        if not text:
            return None
        lookup_key = self._key('email' if '@' in text else 'username', text)
        user_id = self._kv_get(lookup_key)
        if not isinstance(user_id, str):
            return None
        return self.get_user(user_id)

    def list_users(self):
        ids = self._set_members(self._key('users'))
        users = []
        for user_id in sorted(str(item) for item in ids):
            user = self.get_user(user_id)
            if user:
                users.append(public_user(user))
        users.sort(key=lambda item: (item['role'] != 'admin', item['username']))
        return users

    def create_user(self, *, username, email, password=None, display_name='', role='member', status='active', created_by='system'):
        uname = clean_username(username)
        mail = clean_email(email)
        role = str(role or 'member').strip().lower()
        status = str(status or 'active').strip().lower()
        if role not in ROLES:
            raise CloudError('invalid_role', '账号角色无效。', 400)
        if status not in STATUSES:
            raise CloudError('invalid_status', '账号状态无效。', 400)
        generated_password = None
        if password is None or str(password).strip() == '':
            generated_password = generate_temp_password()
            pwd = generated_password
        else:
            pwd = str(password)
        pwd_hash = hash_password(pwd)
        now = int(self.clock())
        user_id = f'usr_{uuid.uuid4().hex[:20]}'
        uname_key = self._key('username', uname)
        email_key = self._key('email', mail)
        if self._kv_set(uname_key, user_id, nx=True) != 'OK':
            raise CloudError('username_exists', '该用户名已存在。', 409)
        if self._kv_set(email_key, user_id, nx=True) != 'OK':
            self._kv_del(uname_key)
            raise CloudError('email_exists', '该邮箱已绑定其他受邀账号。', 409)
        record = {
            'id': user_id,
            'username': uname,
            'email': mail,
            'display_name': clean_display_name(display_name, uname),
            'role': role,
            'status': status,
            'password': pwd_hash,
            'created_at': now,
            'updated_at': now,
            'last_login_at': 0,
            'created_by': str(created_by or 'system')[:64],
            'invitation_only': True,
        }
        self._put_json(self._key('user', user_id), record)
        self._set_add(self._key('users'), user_id)
        out = public_user(record)
        if generated_password:
            out = {**out, 'generated_password': generated_password}
        return out

    def login(self, identifier, password, user_agent=''):
        ident = str(identifier or '').strip().lower()
        if not ident or not isinstance(password, str) or not password:
            raise CloudError('invalid_credentials', '请输入受邀账号用户名/邮箱和密码。', 400)
        rate_key = self._check_login_rate(ident)
        user = self.find_user(ident)
        if not user:
            verify_missing_password(password)
            self._record_login_failure(rate_key)
            raise CloudError('invalid_credentials', '账号或密码不正确，或该账号尚未获邀开通。', 401)
        if not verify_password(password, user.get('password')):
            self._record_login_failure(rate_key)
            raise CloudError('invalid_credentials', '账号或密码不正确，或该账号尚未获邀开通。', 401)
        if user.get('status') != 'active':
            raise CloudError('account_disabled', '该受邀账号已被停用，请联系管理员。', 403)
        self._clear_login_failures(rate_key)
        now = int(self.clock())
        user['last_login_at'] = now
        user['updated_at'] = now
        self._put_json(self._key('user', user['id']), user)
        token = self.create_session(user['id'], user_agent=user_agent)
        return token, public_user(user)

    def create_session(self, user_id, user_agent=''):
        token = secrets.token_hex(32)
        now = int(self.clock())
        session = {
            'id': f'ses_{uuid.uuid4().hex[:16]}',
            'user_id': user_id,
            'created_at': now,
            'expires_at': now + SESSION_TTL_SECONDS,
            'user_agent': str(user_agent or '')[:180],
        }
        self._put_json(self._key('session', token), session, ttl=SESSION_TTL_SECONDS)
        self._set_add(self._key('user_sessions', user_id), token)
        return token

    def authenticate(self, headers):
        token = session_token_from_headers(headers)
        if not token:
            return None
        session = self._get_json(self._key('session', token))
        if not session:
            return None
        now = int(self.clock())
        if int(session.get('expires_at') or 0) <= now:
            self.revoke_session(token)
            return None
        user = self.get_user(session.get('user_id'))
        if not user or user.get('status') != 'active':
            self.revoke_session(token)
            return None
        public = public_user(user)
        public['session_expires_at'] = int(session.get('expires_at') or 0)
        return public

    def require_user(self, headers):
        user = self.authenticate(headers)
        if not user:
            raise CloudError('auth_required', '请先使用受邀账号登录。', 401)
        return user

    def require_admin(self, headers):
        user = self.require_user(headers)
        if user.get('role') != 'admin':
            raise CloudError('admin_required', '需要管理员权限。', 403)
        return user

    def revoke_session(self, token):
        if not re.fullmatch(r'[a-f0-9]{64}', str(token or '')):
            return
        session = self._get_json(self._key('session', token))
        self._kv_del(self._key('session', token))
        if session and session.get('user_id'):
            self._set_rem(self._key('user_sessions', session['user_id']), token)

    def revoke_user_sessions(self, user_id):
        tokens = self._set_members(self._key('user_sessions', user_id))
        for token in tokens:
            if re.fullmatch(r'[a-f0-9]{64}', str(token)):
                self._kv_del(self._key('session', token))
        self._kv_del(self._key('user_sessions', user_id))

    def update_profile(self, user_id, *, display_name=None, email=None):
        user = self.get_user(user_id)
        if not user:
            raise CloudError('user_not_found', '账号不存在。', 404)
        if display_name is not None:
            user['display_name'] = clean_display_name(display_name, user['username'])
        if email is not None and str(email).strip().lower() != user.get('email'):
            new_email = clean_email(email)
            new_key = self._key('email', new_email)
            if self._kv_set(new_key, user['id'], nx=True) != 'OK':
                raise CloudError('email_exists', '该邮箱已绑定其他受邀账号。', 409)
            old_email = user.get('email')
            if old_email:
                self._kv_del(self._key('email', old_email))
            user['email'] = new_email
        user['updated_at'] = int(self.clock())
        self._put_json(self._key('user', user['id']), user)
        return public_user(user)

    def change_password(self, user_id, current_password, new_password, keep_user_agent=''):
        user = self.get_user(user_id)
        if not user:
            raise CloudError('user_not_found', '账号不存在。', 404)
        if not verify_password(current_password, user.get('password')):
            raise CloudError('invalid_credentials', '当前密码不正确。', 401)
        if current_password == new_password:
            raise CloudError('same_password', '新密码不能与当前密码相同。', 400)
        user['password'] = hash_password(new_password)
        user['updated_at'] = int(self.clock())
        self._put_json(self._key('user', user['id']), user)
        self.revoke_user_sessions(user['id'])
        new_token = self.create_session(user['id'], user_agent=keep_user_agent)
        return new_token, public_user(user)

    def admin_update_user(self, user_id, *, display_name=None, email=None, role=None, status=None, new_password=None, generate_password=False):
        user = self.get_user(user_id)
        if not user:
            raise CloudError('user_not_found', '目标账号不存在。', 404)
        if display_name is not None:
            user['display_name'] = clean_display_name(display_name, user['username'])
        if email is not None and str(email).strip().lower() != user.get('email'):
            next_email = clean_email(email)
            next_key = self._key('email', next_email)
            if self._kv_set(next_key, user['id'], nx=True) != 'OK':
                raise CloudError('email_exists', '该邮箱已绑定其他受邀账号。', 409)
            old_email = user.get('email')
            if old_email:
                self._kv_del(self._key('email', old_email))
            user['email'] = next_email
        if role is not None:
            next_role = str(role).strip().lower()
            if next_role not in ROLES:
                raise CloudError('invalid_role', '账号角色无效。', 400)
            user['role'] = next_role
        if status is not None:
            next_status = str(status).strip().lower()
            if next_status not in STATUSES:
                raise CloudError('invalid_status', '账号状态无效。', 400)
            user['status'] = next_status
            if next_status != 'active':
                self.revoke_user_sessions(user['id'])
        generated_password = None
        if generate_password and not new_password:
            generated_password = generate_temp_password()
            new_password = generated_password
        if new_password is not None and str(new_password) != '':
            user['password'] = hash_password(str(new_password))
            self.revoke_user_sessions(user['id'])
        user['updated_at'] = int(self.clock())
        self._put_json(self._key('user', user['id']), user)
        out = public_user(user)
        if generated_password:
            out = {**out, 'generated_password': generated_password}
        return out
