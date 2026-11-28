"""Fixed low-cost model; paid calls require auth and a durable reservation."""
import json
import re
import urllib.error
import urllib.request
from .store import CloudError, authorize

MODEL = 'deepseek-flash'
MAX_INPUT_BYTES = 40000
MAX_OUTPUT_TOKENS = 2400
MAX_REPLY_BYTES = 512000


def _parse_json_object(content):
    if not isinstance(content, str):
        raise ValueError('Empty model content')
    text = content.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Strip markdown code fences if present
    fenced = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        obj = json.loads(fenced)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start, end = fenced.find('{'), fenced.rfind('}')
    if start >= 0 and end > start:
        obj = json.loads(fenced[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError('Expected JSON object')


class DeepSeek:
    def __init__(self, env, store, transport=None):
        self.env, self.store, self.transport = env, store, transport

    def _http_post(self, body, key, timeout=28):
        req = urllib.request.Request(
            'https://api.deepseek.com/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key},
        )
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError('Redirect disabled')
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
            raw = r.read(MAX_REPLY_BYTES + 1)
        if len(raw) > MAX_REPLY_BYTES:
            raise ValueError('Response too large')
        return json.loads(raw)

    def complete(self, system, payload, access_code):
        authorize(self.env, access_code)
        key = self.env.get('EV_DEEPSEEK_API_KEY', '')
        if not key:
            raise CloudError('model_not_configured', '尚未配置DeepSeek服务端密钥')
        messages = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
        if len(json.dumps(messages, ensure_ascii=False).encode()) > MAX_INPUT_BYTES:
            raise CloudError('input_too_large', '模型输入超限，未截断也未调用模型', 413)
        body = {'model': MODEL, 'temperature': 0, 'max_tokens': MAX_OUTPUT_TOKENS,
                'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'}, 'messages': messages}
        reserved = self.store.reserve()
        detail = ''
        http_code = None
        try:
            if self.transport:
                reply = self.transport(body)
                choice = reply['choices'][0]
                if choice['message'].get('refusal'):
                    raise ValueError('Model refusal')
                if choice.get('finish_reason') not in ('stop', 'length'):
                    raise ValueError('Non-final supplier response')
                result = _parse_json_object(choice['message']['content'])
            else:
                def call_and_parse(req_body, timeout_s):
                    raw_reply = self._http_post(req_body, key, timeout=timeout_s)
                    ch = raw_reply['choices'][0]
                    if ch['message'].get('refusal'):
                        raise ValueError('Model refusal')
                    if ch.get('finish_reason') not in ('stop', 'length'):
                        raise ValueError('Non-final supplier response')
                    return raw_reply, _parse_json_object(ch['message']['content'])
                try:
                    reply, result = call_and_parse(body, 12)
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as first_err:
                    http_code = getattr(first_err, 'code', None)
                    detail = f' ({type(first_err).__name__}{" " + str(http_code) if http_code is not None else ""})'
                    if http_code in (401, 402) or getattr(self.store, 'competition_owner', None):
                        raise
                    fallback_body = {
                        'model': self.env.get('EV_DEEPSEEK_FALLBACK_MODEL', 'deepseek-chat'),
                        'temperature': 0,
                        'max_tokens': MAX_OUTPUT_TOKENS,
                        'response_format': {'type': 'json_object'},
                        'messages': messages,
                    }
                    reply, result = call_and_parse(fallback_body, 14)
            return {'parsed': result, 'usage': reply.get('usage'),
                    'source': 'remote_api', 'provider_model': reply.get('model', MODEL), 'reservation': reserved}
        except Exception as exc:
            err_code = getattr(exc, 'code', http_code)
            if err_code == 402:
                raise CloudError('model_failure', 'DeepSeek API 账户余额不足（HTTP 402），请联系管理员充值或更换服务端密钥', 502) from None
            if err_code == 401:
                raise CloudError('model_failure', 'DeepSeek API 服务端密钥无效（HTTP 401），请联系管理员检查配置', 502) from None
            if not self.transport and not detail:
                code_str = f' HTTP {exc.code}' if hasattr(exc, 'code') else ''
                detail = f'（{type(exc).__name__}{code_str}）'
            raise CloudError('model_failure', '模型未返回有效结果；未自动重试，调用预留仍保留' + detail, 502) from None
