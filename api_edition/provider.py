"""Remote API gateway with bounded I/O and strict, secret-free configuration."""
import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlsplit
import urllib.request
import urllib.error

ROLES = ('planner', 'extractor', 'verifier', 'agent')
ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
ROLE_FIELDS = {'base_url', 'model', 'api_key_env', 'response_format', 'send_seed', 'token_parameter', 'reasoning_effort', 'max_output_tokens'}


def endpoint_ok(value, *, source=False):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return False
    try:
        u = urlsplit(value)
        port = u.port  # Reject malformed or out-of-range ports before making requests.
        allowed = u.scheme == 'https' or not source and u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost', '::1')
        return bool(u.hostname and allowed and not u.username and not u.password
                    and (source or not u.query and not u.fragment))
    except ValueError:
        return False


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError(label + ' must be an object with supported fields only')


def _text(value, label, limit=2000):
    if not isinstance(value, str) or len(value) > limit or '\r' in value or '\n' in value:
        raise ValueError(label + ' must be a bounded single-line string')


def validate_config(value):
    _object(value, {'roles', 'search', 'timeout_seconds', 'max_response_bytes', 'extension_id', 'grounding_repair_attempts'}, 'API configuration')
    c = copy.deepcopy(value)
    if type(c.get('grounding_repair_attempts', 0)) is not int or c.get('grounding_repair_attempts', 0) not in (0, 1):
        raise ValueError('grounding_repair_attempts must be 0 or 1')
    _object(c.get('roles'), set(ROLES), 'roles')
    for role in ROLES:
        v = c['roles'].setdefault(role, {})
        _object(v, ROLE_FIELDS, role)
        for key in ('base_url', 'model', 'api_key_env'):
            _text(v.get(key, ''), role + '.' + key)
        if v.get('base_url') and not endpoint_ok(v['base_url']):
            raise ValueError('API URL must be HTTPS or loopback HTTP, without embedded credentials/query')
        if v.get('api_key_env') and not ENV_NAME.fullmatch(v['api_key_env']):
            raise ValueError('api_key_env must name an environment variable')
        if v.get('response_format', 'json_schema') not in ('json_schema', 'json_object', 'none'):
            raise ValueError('Unsupported response_format')
        if v.get('token_parameter', 'max_tokens') not in ('max_tokens', 'max_completion_tokens'):
            raise ValueError('Unsupported token parameter')
        if 'reasoning_effort' in v and v['reasoning_effort'] not in ('none', 'low', 'high', 'max'):
            raise ValueError('Unsupported reasoning_effort')
        if 'max_output_tokens' in v and (type(v['max_output_tokens']) is not int or not 128 <= v['max_output_tokens'] <= 16384):
            raise ValueError('max_output_tokens must be an integer from 128 to 16384')
        if type(v.get('send_seed', False)) is not bool:
            raise ValueError('send_seed must be boolean')
    search = c.setdefault('search', {})
    _object(search, {'endpoint', 'api_key_env', 'revision', 'max_results', 'protocol'}, 'search')
    for key in ('endpoint', 'api_key_env', 'revision'):
        _text(search.get(key, ''), 'search.' + key)
    if search.get('protocol', 'generic') not in ('generic', 'deepseek'):
        raise ValueError('Unsupported search protocol')
    if search.get('endpoint') and not endpoint_ok(search['endpoint']):
        raise ValueError('Search endpoint must be HTTPS or loopback HTTP')
    if search.get('api_key_env') and not ENV_NAME.fullmatch(search['api_key_env']):
        raise ValueError('Search api_key_env must name an environment variable')
    if type(search.get('max_results', 3)) is not int or not 1 <= search.get('max_results', 3) <= 5:
        raise ValueError('max_results must be 1–5')
    timeout, limit = c.get('timeout_seconds', 90), c.get('max_response_bytes', 2000000)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 180:
        raise ValueError('timeout_seconds must be a finite number within 1–180')
    if type(limit) is not int or not 1024 <= limit <= 4000000:
        raise ValueError('max_response_bytes must be an integer within 1024–4000000')
    extension = c.get('extension_id', '')
    _text(extension, 'extension_id', 32)
    if extension and not re.fullmatch(r'[a-p]{32}', extension):
        raise ValueError('extension_id must be the installed Chrome extension ID')
    return c


def load_config(path):
    return validate_config(json.loads(Path(path).read_text()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Provider redirect rejected; credentials were not forwarded')


class APIProvider:
    def __init__(self, config):
        self.config = validate_config(config)
        self._run_namespace = secrets.token_hex(16)

    def ready(self, role):
        v = self.config['roles'].get(role, {})
        return bool(v.get('base_url') and v.get('model') and os.environ.get(v.get('api_key_env', '')))

    def search_ready(self):
        s = self.config['search']
        return bool(s.get('endpoint') and os.environ.get(s.get('api_key_env', '')))

    def status(self):
        return {'edition': 'pure_api', 'local_model_loading': False, 'training_enabled': False,
                'roles': {r: {'configured': self.ready(r), 'model': self.config['roles'][r].get('model', '')} for r in ROLES},
                'search_configured': self.search_ready(), 'paid_api_calls_performed_by_check': False}

    def search_identity(self):
        s = self.config['search']
        identity = {'config': s, 'namespace': s.get('revision') or self._run_namespace,
                    'credential_sha256': hashlib.sha256(os.environ.get(s.get('api_key_env', ''), '').encode()).hexdigest()}
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def post(self, url, body, key):
        request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        limit = self.config.get('max_response_bytes', 2000000)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.config.get('timeout_seconds', 90)) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('Provider HTTP ' + str(exc.code) + '; response body withheld; no automatic retry') from None
        except (OSError, TimeoutError):
            raise RuntimeError('Provider transport failure; no automatic retry') from None
        if len(data) > limit:
            raise ValueError('Provider response exceeds size limit')
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Provider response is not valid JSON') from None
        if not isinstance(result, dict):
            raise ValueError('Provider response must be a JSON object')
        return result

    def complete(self, role, body):
        if not self.ready(role):
            raise ValueError('API role not configured: ' + role)
        if not isinstance(body, dict) or not isinstance(body.get('messages'), list):
            raise ValueError('Completion request requires a messages array')
        v, payload = self.config['roles'][role], copy.deepcopy(body)
        payload.update(model=v['model'], stream=False)
        if 'reasoning_effort' in v:
            payload['reasoning_effort'] = v['reasoning_effort']
        if 'max_output_tokens' in v:
            payload['max_tokens'] = v['max_output_tokens']
        if not v.get('send_seed', False):
            payload.pop('seed', None)
        if v.get('token_parameter') == 'max_completion_tokens':
            if 'max_tokens' not in payload:
                raise ValueError('Missing max_tokens for token parameter mapping')
            payload['max_completion_tokens'] = payload.pop('max_tokens')
        rf = payload.get('response_format', {})
        if not isinstance(rf, dict) or not isinstance(rf.get('json_schema', {}), dict):
            raise ValueError('Invalid structured response format')
        schema, mode = rf.get('json_schema', {}).get('schema'), v.get('response_format', 'json_schema')
        if mode != 'json_schema':
            payload.pop('response_format', None)
            if mode == 'json_object':
                payload['response_format'] = {'type': 'json_object'}
            payload['messages'].insert(0, {'role': 'system', 'content': 'Return one JSON object only. Required output schema: ' + json.dumps(schema or {'type': 'object'})})
        reply = self.post(v['base_url'].rstrip('/') + '/chat/completions', payload, os.environ[v['api_key_env']])
        if not isinstance(reply, dict) or not isinstance(reply.get('choices'), list) or not reply['choices']:
            raise ValueError('Provider response lacks choices')
        choice = reply['choices'][0]
        message = choice.get('message') if isinstance(choice, dict) else None
        if isinstance(choice, dict) and choice.get('finish_reason') in ('length', 'content_filter'):
            raise ValueError('Provider completion is truncated or filtered; check the configured output token budget')
        if not isinstance(message, dict) or message.get('refusal'):
            raise ValueError('Provider returned refused or invalid completion')
        if not isinstance(message.get('content'), str) or not message['content'].strip():
            raise ValueError('Provider returned empty/non-text completion')
        reply.update(ev_api_role=role, ev_api_model=v['model'])
        return reply

    def search(self, query, required_entity=None):
        if not self.search_ready():
            raise ValueError('Search API not configured; no diagnostic-corpus fallback')
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('Query must contain 1–500 characters')
        if required_entity is not None and (not isinstance(required_entity, str) or not required_entity.strip() or len(required_entity) > 300):
            raise ValueError('Invalid entity anchor')
        search = self.config['search']
        query = (('"' + required_entity + '" ') if required_entity else '') + query
        deepseek = search.get('protocol') == 'deepseek'
        if deepseek and len(query) > 128:
            raise ValueError('DeepSeek query exceeds 128 characters; no silent truncation')
        payload = ({'query': query, 'count': search.get('max_results', 3), 'need_content': True} if deepseek
                   else {'query': query, 'max_results': search.get('max_results', 3), 'include_raw_content': True})
        reply = self.post(search['endpoint'], payload, os.environ[search['api_key_env']])
        if not isinstance(reply, dict) or not isinstance(reply.get('results'), list) or len(reply['results']) > search.get('max_results', 3):
            raise ValueError('Search API results invalid or oversized')
        docs, seen = [], set()
        for row in reply['results']:
            if not isinstance(row, dict):
                raise ValueError('Search result must be an object')
            url, text = row.get('url'), row.get('content' if deepseek else 'raw_content')
            if not endpoint_ok(url, source=True) or not isinstance(text, str) or not text.strip():
                continue  # A snippet is not a source body.
            if url in seen:
                continue
            if len(text) > 200000:
                raise ValueError('Source text exceeds limit; no silent raw-content truncation')
            title, score = row.get('title') or url, row.get('rerank_score' if deepseek else 'score', 0)
            if not isinstance(title, str) or type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError('Invalid search title or score')
            seen.add(url)
            docs.append({'doc_id': url, 'title': title, 'text': text, 'score': score})
        return docs


def gateway(provider):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, code):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            authorization = self.headers.get('Authorization', '')
            try:
                authorized = hmac.compare_digest(authorization, 'Bearer ' + token)
            except TypeError:
                authorized = False
            if not authorized:
                return self.respond({'error': {'message': 'Gateway authorization required', 'type': 'unauthorized'}}, 401)
            if self.path != '/v1/chat/completions':
                return self.respond({'error': {'message': 'Unsupported gateway path', 'type': 'not_found'}}, 404)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 200000:
                    raise ValueError('Request exceeds gateway limit')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or not isinstance(body.get('messages'), list):
                    raise ValueError('Gateway body requires an object and messages array')
                rf = body.get('response_format', {})
                if not isinstance(rf, dict) or not isinstance(rf.get('json_schema', {}), dict):
                    raise ValueError('Invalid response_format')
                name = rf.get('json_schema', {}).get('name')
                if name is not None and not isinstance(name, str):
                    raise ValueError('Invalid schema name')
                role = {'extract': 'extractor', 'grounding_repair': 'extractor', 'verify': 'verifier', 'direct': 'verifier', 'relation_proposal': 'planner'}.get(name)
                if role is None:
                    if body.get('model') != 'api-agent':
                        raise ValueError('Unknown gateway role')
                    role = 'agent'
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                return self.respond({'error': {'message': str(exc), 'type': 'invalid_request'}}, 400)
            try:
                return self.respond(provider.complete(role, body), 200)
            except Exception as exc:
                return self.respond({'error': {'message': str(exc), 'type': 'api_gateway_error'}}, 502)

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler), token


def search_handler(base, provider):
    """Keep the workbench on API retrieval; reject cross-site requests before billing."""
    class Handler(base):
        def do_GET(self):
            path = urlsplit(self.path)
            if path.path != '/api/search':
                return super().do_GET()
            origin = self.headers.get('Origin')
            extension = provider.config.get('extension_id')
            allowed = {'http://' + self.headers.get('Host', '')}
            if extension:
                allowed.add('chrome-extension://' + extension)
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' or origin and origin not in allowed:
                return self.send({'error': 'Cross-site API search rejected'}, 403)
            try:
                return self.send(provider.search(parse_qs(path.query).get('q', [''])[0]))
            except (ValueError, RuntimeError) as exc:
                return self.send({'error': str(exc), 'code': 'search_api_failure'}, 409)
    return Handler
