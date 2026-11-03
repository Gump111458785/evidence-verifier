"""Reuse CPU curation rules in memory; persist typed table rows atomically.

No local database file, SQL supplied by clients, background task or model call.
The complete bounded workspace is versioned via Redis compare-and-set.
"""
import copy
from contextlib import contextmanager
import json
import re
import sqlite3
from curation import CurationService,text_hash
from engine import digest
from .store import CloudError

TABLES=('data_candidates','data_reviews','data_exports')

def dispatch(method,path,query,body,app):
    ident='curation-workspace-v1'
    try:before=app.store.get(app.owner,ident)
    except CloudError as exc:
        if exc.code!='record_not_found':raise
        initial={'tables':{t:[] for t in TABLES}}
        try:app.store.put(app.owner,ident,initial,new=True)
        except CloudError as other:
            if other.code!='request_exists':raise
        before=app.store.get(app.owner,ident)
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    @contextmanager
    def connections():
        with db:yield db
    service=CurationService(connections);service.init()
    try:
        for table in TABLES:
            for row in before['tables'][table]:
                db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',row)
        db.commit()
        if method=='GET':
            if path=='/api/data-candidates':
                rows=service.summaries();offset=max(0,int(query.get('offset',['0'])[0]))
                return {'rows':rows[offset:offset+50],'total':len(rows),'offset':offset,'limit':50,
                        'counts':{'pending':sum(r['decision']=='pending' for r in rows),'included':sum(r['decision']=='include' for r in rows),'training_source':sum(r['usage']=='train_candidate' for r in rows)}}
            if path=='/api/data-exports':return [json.loads(r[0]) for r in db.execute('SELECT manifest FROM data_exports ORDER BY created DESC LIMIT 100')]
            m=re.fullmatch('/api/data-candidates/([a-f0-9]{16})',path)
            if m:return service.get(m[1])
            m=re.fullmatch('/api/data-exports/([a-f0-9]{16})',path)
            if m:return service.exported(m[1])
        if method=='POST':
            if path=='/api/data-candidates':result={'ids':service.add(body.get('records'))}
            elif path=='/api/data-exports':result=service.export()
            elif path in ('/api/data-candidates/from-check','/api/data-candidates/from-run'):
                check=path.endswith('from-check');parent=app.get(body.get('check_id' if check else 'run_id'))
                if parent['state']!='completed':raise CloudError('invalid_parent','任务尚未完成',400)
                if check:
                    evidence=parent['input']['evidence'];facts=parent['result'].get('fact_rows',[])
                else:
                    if parent['result']['mode']!='split':raise CloudError('invalid_parent','直接裁判不能作为独立抽取标签',400)
                    evidence=parent['result']['input']['evidence'];facts=parent['result']['output'].get('facts',[])
                source={'dataset':'unclassified_runtime','split':'unknown',
                        'source_group':'runtime-evidence-'+digest(sorted(text_hash(e['text']) for e in evidence)),
                        'license':'retain_original_source_terms','source_url':'cloud-record:'+parent['id'],'origin':'verifier_trace'}
                if not check and parent['result']['source']=='archived_v7':source.update(dataset='frozen_v7_diagnostic',split='development')
                result=service.collect_trace(evidence,facts,body.get('task'),body.get('fact_index'),source,
                        {'source':'cloud_runtime','record_id':parent['id'],'scope':'pending_curation_not_gold'})
            else:
                m=re.fullmatch('/api/data-candidates/([a-f0-9]{16})/reviews',path)
                if not m:raise CloudError('route_not_found','接口不存在',404)
                result=service.review(m[1],body)
            after={'tables':{t:[list(r) for r in db.execute('SELECT * FROM '+t)] for t in TABLES}}
            if not app.store.compare_and_set(app.owner,ident,before,after):raise CloudError('curation_conflict','样本版本已变化；请重新读取，不会覆盖新版本',409)
            return result
        raise CloudError('route_not_found','接口不存在',404)
    finally:db.close()
