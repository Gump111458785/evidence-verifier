"""Fixed low-cost model; paid calls require auth and a durable reservation."""
import json
import urllib.request
from .store import CloudError, authorize

MODEL = 'deepseek-flash'
MAX_INPUT_BYTES = 40000
MAX_OUTPUT_TOKENS = 2400
MAX_REPLY_BYTES = 512000


class DeepSeek:
    def __init__(self, env, store, transport=None):
        self.env, self.store, self.transport = env, store, transport

    def complete(self, system, payload, access_code):
        authorize(self.env, access_code)
        key = self.env.get('EV_DEEPSEEK_API_KEY','')
        if not key:
            raise CloudError('model_not_configured','尚未配置DeepSeek服务端密钥')
        messages=[{'role':'system','content':system},
                  {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
        if len(json.dumps(messages,ensure_ascii=False).encode())>MAX_INPUT_BYTES:
            raise CloudError('input_too_large','模型输入超限，未截断也未调用模型',413)
        body={'model':MODEL,'temperature':0,'max_tokens':MAX_OUTPUT_TOKENS,
              'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'messages':messages}
        reserved=self.store.reserve()
        # No retry and no refund after reservation. Even timeouts may be billed.
        try:
            if self.transport:
                reply=self.transport(body)
            else:
                req=urllib.request.Request('https://api.deepseek.com/chat/completions',
                    data=json.dumps(body,ensure_ascii=False).encode(),
                    headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self,*args,**kwargs):raise ValueError('Redirect disabled')
                with urllib.request.build_opener(NoRedirect()).open(req,timeout=35) as r:
                    raw=r.read(MAX_REPLY_BYTES+1)
                if len(raw)>MAX_REPLY_BYTES:raise ValueError('Response too large')
                reply=json.loads(raw)
            choice=reply['choices'][0]
            if choice.get('finish_reason')!='stop' or choice['message'].get('refusal'):
                raise ValueError('Non-final supplier response')
            result=json.loads(choice['message']['content'])
            if not isinstance(result,dict):raise ValueError('Expected JSON object')
            return {'parsed':result,'usage':reply.get('usage'),
                    'source':'remote_api','provider_model':reply.get('model',MODEL),'reservation':reserved}
        except Exception:
            raise CloudError('model_failure','模型未返回有效结果；未自动重试，调用预留仍保留',502) from None
