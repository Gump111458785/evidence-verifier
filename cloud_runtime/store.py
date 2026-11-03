"""Durable records and atomic prepaid call reservations via Redis REST.

The ledger never expires or refunds uncertain supplier calls. Fail closed on any
configuration, network, or response error. No credentials are returned or logged.
"""
import hashlib
import hmac
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class CloudError(ValueError):
    def __init__(self, code, message, status=503):
        super().__init__(message)
        self.code, self.status = code, status


RESERVE = '''
local current = redis.call('GET', KEYS[1])
if not current then
  if redis.call('EXISTS', KEYS[2]) == 1 then return -2 end
  redis.call('SET', KEYS[1], '0')
  redis.call('SET', KEYS[2], '1')
  current = '0'
end
local used = tonumber(current)
if not used or used < 0 then return -2 end
if redis.call('PTTL', KEYS[1]) ~= -1 then return -2 end
if redis.call('PTTL', KEYS[2]) ~= -1 then return -2 end
local amount = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if used + amount > cap then return -1 end
return redis.call('INCRBY', KEYS[1], amount)
'''


class Store:
    def __init__(self, env, transport=None):
        self.url = env.get('EV_REDIS_REST_URL', '').rstrip('/')
        self.token = env.get('EV_REDIS_REST_TOKEN', '')
        self.namespace = env.get('EV_CLOUD_BUDGET_ID', 'makers-z7l7raqtginj-trial-01')
        self.transport = transport

    @property
    def configured(self):
        try:
            u = urlsplit(self.url)
            return bool(u.scheme == 'https' and u.hostname and not u.username and not u.password
                        and not u.query and not u.fragment and self.token
                        and re.fullmatch(r'[A-Za-z0-9_-]{16,100}', self.namespace))
        except ValueError:
            return False

    def key(self, kind, ident=''):
        return 'ev:' + self.namespace + ':' + kind + (':' + ident if ident else '')

    def command(self, command):
        if not self.configured:
            raise CloudError('storage_not_configured', '尚未配置持久化存储，付费调用已禁用')
        try:
            if self.transport:
                reply = self.transport(command)
            else:
                req = urllib.request.Request(self.url, data=json.dumps(command).encode(),
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args, **kwargs):
                        raise ValueError('Redirect disabled')
                with urllib.request.build_opener(NoRedirect()).open(req, timeout=8) as r:
                    reply = json.loads(r.read(4_000_000))
            if not isinstance(reply, dict) or 'error' in reply or 'result' not in reply:
                raise ValueError('Invalid storage response')
            return reply['result']
        except CloudError:
            raise
        except Exception:
            raise CloudError('storage_unavailable', '持久化存储不可用，未授权新增付费调用') from None

    def reserve(self, amount_millicny=250):
        # 40 reservations × CNY .25 = CNY 10. A reservation bounds ONE HTTP call,
        # not an entire multi-call verification. IDs and cap are not client input.
        if type(amount_millicny) is not int or amount_millicny != 250:
            raise ValueError('Only the fixed audited reservation is allowed')
        result = self.command(['EVAL', RESERVE, '2', self.key('spent'), self.key('initialized'), '250', '10000'])
        if result == -1:
            raise CloudError('budget_exhausted', '本轮10元联调额度已耗尽', 429)
        if type(result) is not int or not 250 <= result <= 10000 or result % 250:
            raise CloudError('budget_unavailable', '费用账本异常，付费调用已停止')
        return {'reserved_millicny': result, 'limit_millicny': 10000}

    def get(self, owner, ident):
        raw = self.command(['GET', self.key('record', owner + ':' + ident)])
        if raw is None:
            raise CloudError('record_not_found', '记录不存在或访问码不匹配', 404)
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            raise CloudError('record_invalid', '持久化记录无效') from None

    def put(self, owner, ident, value, new=False):
        data = json.dumps(value, ensure_ascii=False)
        if len(data.encode()) > 2_000_000:
            raise CloudError('record_too_large', '审查记录超限', 413)
        command = ['SET', self.key('record', owner + ':' + ident), data]
        if new:
            command.append('NX')
        result = self.command(command)
        if new and result is None:
            raise CloudError('request_exists', '请求已存在，请读取原记录', 409)
        if result != 'OK':
            raise CloudError('storage_write_failed', '记录未能持久化，停止执行')


    def index(self, owner, ident, created):
        result = self.command(['ZADD', self.key('index', owner), str(created), ident])
        if type(result) is not int or result not in (0, 1):
            raise CloudError('index_write_failed', '审查索引尚未保存，停止执行')

    def page(self, owner, offset=0, limit=20, kind='verification'):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise CloudError('invalid_page', '分页范围无效', 400)
        key = self.key('index', owner + ':' + kind)
        total = self.command(['ZCARD', key])
        ids = self.command(['ZREVRANGE', key, str(offset), str(offset + limit - 1)])
        if type(total) is not int or not isinstance(ids, list):
            raise CloudError('index_unavailable', '无法读取持久化审查索引')
        return {'rows': [self.get(owner, ident) for ident in ids], 'total': total, 'offset': offset, 'limit': limit}

    def compare_and_set(self, owner, ident, expected, value):
        # Atomic record transition across cloud instances; stale observers cannot
        # overwrite a cancellation or another step's result.
        encode = lambda v: json.dumps(v, ensure_ascii=False)
        old, new = encode(expected), encode(value)
        if len(new.encode()) > 2_000_000:
            raise CloudError('record_too_large', '审查记录超限', 413)
        script = "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end redis.call('SET', KEYS[1], ARGV[2]); return 1"
        result = self.command(['EVAL', script, '1', self.key('record', owner + ':' + ident), old, new])
        if type(result) is not int or result not in (0, 1):
            raise CloudError('transition_unknown', '不能确认持久化任务状态，停止执行')
        return result == 1


def authorize(env, presented):
    expected = env.get('EV_CLOUD_ACCESS_CODE', '')
    if len(expected) < 24:
        raise CloudError('access_not_configured', '尚未配置服务端访问码')
    if not isinstance(presented, str) or not hmac.compare_digest(expected.encode(), presented.encode()):
        raise CloudError('access_denied', '访问码错误', 401)
    return hashlib.sha256(expected.encode()).hexdigest()[:32]
