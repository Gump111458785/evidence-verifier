"""Persistent audit browsing; classification is independent of execution completion."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verification_api import VerificationService, VerificationRequestError

class AuditHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'audit.sqlite'
        with self.db() as c:
            c.execute('CREATE TABLE relation_checks(id TEXT PRIMARY KEY,state TEXT,created REAL,input TEXT,result TEXT,error TEXT)')
            c.execute('CREATE TABLE events(run_id TEXT,stage TEXT,state TEXT,payload TEXT,at REAL)')
        self.service = VerificationService(self.db)
        self.service.init()
    def tearDown(self): self.tmp.cleanup()
    def db(self):
        c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c
    def add(self, key, created, state='completed', result=None, standalone=True):
        spec={'plan':[{'subject':'Machine','predicate':'designed_by','object':'Nora'}], 'evidence':[{'id':'E','text':'source'}]}
        with self.db() as c:
            c.execute('INSERT INTO relation_checks VALUES(?,?,?,?,?,?)',(key,state,created,json.dumps(spec),json.dumps(result) if result is not None else None,None))
            if standalone: c.execute('INSERT INTO verification_requests VALUES(?,?,?,?)',(key,key,'hash','hash'))
    def test_paging_counts_only_standalone_audits_and_preserves_saved_time(self):
        self.add('old',10,result={'status':'ok','verdict':'missing'})
        self.add('new',20,result={'status':'ok','verdict':'entailed','provenance':{'source':'remote_api'}})
        self.add('archive',30,standalone=False)
        p=self.service.summaries(limit=1)
        self.assertEqual(p['total'],2);self.assertEqual(p['rows'][0]['id'],'new')
        self.assertEqual(p['rows'][0]['created'],20);self.assertEqual(p['rows'][0]['source'],'remote_api')
        self.assertEqual(self.service.summaries(offset=1,limit=1)['rows'][0]['id'],'old')
    def test_failed_or_canceled_tasks_cannot_inherit_an_old_supported_verdict(self):
        for i,state in enumerate(('failed','canceled','running','cancel_requested')):
            self.add(state,i,state,{'status':'ok','verdict':'entailed'})
        self.assertTrue(all(r['verdict'] is None for r in self.service.summaries()['rows']))
    def test_completed_invalid_output_is_not_missing_or_supported(self):
        self.add('invalid',1,result={'status':'invalid_output'})
        self.assertEqual(self.service.summaries()['rows'][0]['verdict'],'invalid_output')
    def test_latest_repair_is_visible_without_overwriting_initial_judgment(self):
        self.add('check',1,result={'status':'ok','verdict':'missing'})
        with self.db() as c:
            c.execute('CREATE TABLE repair_jobs(id TEXT,parent_check_id TEXT,state TEXT,created REAL,result TEXT)')
            c.execute('INSERT INTO repair_jobs VALUES(?,?,?,?,?)',('success','check','completed',2,json.dumps({'final':{'status':'ok','verdict':'entailed'}})))
        row=self.service.summaries()['rows'][0]
        self.assertEqual(row['verdict'],'missing')
        self.assertEqual(row['latest_repair']['verdict'],'entailed')
        with self.db() as c:
            c.execute('INSERT INTO repair_jobs VALUES(?,?,?,?,?)',('new-failure','check','failed',3,json.dumps({'final':{'status':'ok','verdict':'entailed'}})))
        row=self.service.summaries()['rows'][0]
        self.assertEqual(row['latest_repair']['id'],'new-failure')
        self.assertIsNone(row['latest_repair']['verdict'])

    def test_empty_page_and_invalid_pagination_do_not_change_records(self):
        self.assertEqual(self.service.summaries()['rows'],[])
        for params in ({'offset':-1},{'limit':0},{'limit':101},{'offset':True}):
            with self.assertRaises(VerificationRequestError): self.service.summaries(**params)
        self.assertEqual(self.service.summaries()['total'],0)

if __name__=='__main__':unittest.main()
