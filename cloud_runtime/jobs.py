"""Request-driven job progression, persisted before and after every step.

One POST advances at most one bounded model/tool call. GET never spends money.
A permanent per-step claim prevents billing the same uncertain step twice, even
if an instance is lost or the browser repeats a POST. Stale uncertain work fails;
it is not silently retried. Records and claims are owner-scoped.
"""
import copy
import time
import uuid
from .store import CloudError


class Jobs:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def create(self, owner, kind, payload, request_key=None):
        if request_key is not None and (not isinstance(request_key,str) or not 1<=len(request_key)<=100):
            raise CloudError('invalid_request_key','请求标识无效',400)
        # The immutable input hash must be checked by the caller on a resumed key.
        import hashlib,json
        ident=hashlib.sha256((owner+':'+kind+':'+request_key).encode()).hexdigest()[:24] if request_key else uuid.uuid4().hex[:24]
        value={'id':ident,'kind':kind,'state':'queued','created':self.clock(),
               'input':payload,'step':0,'result':None,'error':None,'events':[]}
        try:self.store.put(owner,ident,value,new=True)
        except CloudError as exc:
            if exc.code!='request_exists':raise
            previous=self.get(owner,ident)
            if previous['input']!=payload or previous['kind']!=kind:
                raise CloudError('request_conflict','相同标识已有不同输入',409)
            self.store.index(owner+':'+kind,ident,previous['created'])
            return previous
        self.store.index(owner+':'+kind,ident,value['created'])
        return value

    def get(self, owner, ident):
        job=self.store.get(owner,ident)
        if job['state']=='running' and self.clock()>job.get('step_started',0)+90:
            previous=copy.deepcopy(job)
            job={**job,'state':'failed','error':'步骤观察超时，供应商结果未知；为避免重复扣费未自动重试'}
            if not self.store.compare_and_set(owner,ident,previous,job):
                return self.store.get(owner,ident)
        return job

    def advance(self, owner, ident, perform):
        job=self.get(owner,ident)
        if job['state'] in ('completed','failed','canceled'):return job
        if job['state']=='running':return job
        step=job['step']
        claim=self.store.key('step',owner+':'+ident+':'+str(step))
        result=self.store.command(['SET',claim,uuid.uuid4().hex,'NX'])
        if result is None:
            raise CloudError('step_already_claimed','步骤已提交或结果未知，请恢复观察；未重复执行',409)
        if result!='OK':raise CloudError('claim_failed','无法确认持久化执行权，未调用模型')
        previous=copy.deepcopy(job)
        job.update(state='running',step_started=self.clock())
        if not self.store.compare_and_set(owner,ident,previous,job):
            return self.get(owner,ident)
        running=copy.deepcopy(job)
        try:
            updates=perform(job)
            job.update(updates)
            job['step']=step+1
            if job['state'] not in ('queued','completed','failed'):
                raise ValueError('Invalid step transition')
        except Exception as exc:
            job.update(state='failed',error=str(exc) if isinstance(exc,CloudError) else '核验步骤失败，未自动重试',
                       error_code=exc.code if isinstance(exc,CloudError) else 'step_runtime_failure',
                       error_type=type(exc).__name__)
        if not self.store.compare_and_set(owner,ident,running,job):
            raise CloudError('transition_conflict','结果保存冲突，未覆盖原记录')
        return job

    def cancel(self, owner, ident):
        job=self.get(owner,ident)
        if job['state']=='running':
            raise CloudError('step_in_progress','当前短请求正在执行；停止观察不会撤销已提交的供应商调用',409)
        if job['state'] not in ('completed','failed','canceled'):
            previous=copy.deepcopy(job)
            job.update(state='canceled',error='Canceled by caller')
            if not self.store.compare_and_set(owner,ident,previous,job):
                return self.get(owner,ident)
        return job
