import copy
import unittest
from sqlitefolio.summary import build_summary


def snapshot(rows=None, cols=None):
    return {'complete':True,'error':None,'objects':[{'type':'table','name':'t','table':'t','sql':'CREATE TABLE t(x)'}],'tables':{'t':{'columns':cols or ['x'],'column_info':[],'table_info':[],'foreign_keys':[],'indexes':[],'rows':rows or []}},'views':{},'diagnostics':{'foreign_key_check':{'rows':[],'error':None},'integrity_check':{'rows':[[['text','b2s=']]],'error':None},'foreign_keys':True},'sequences':[]}


def raw():
    s=snapshot([[['integer','1']],[['integer','1']]])
    return {'name':'test','runtime':{},'profile':{},'execution':{'status':'completed','error':None,'in_transaction':False,'foreign_keys_start':True,'foreign_keys_end':True},'before':s,'observed':copy.deepcopy(s),'reopened':copy.deepcopy(s),'queries':{},'source_preserved':True}


class SummaryTests(unittest.TestCase):
    def test_duplicate_projection_and_change(self):
        r=raw();r['observed']['tables']['t']['rows'].pop()
        s=build_summary({'invariants':[]},[r])[0]
        self.assertEqual(s['observed_changes']['tables']['t']['projected_removed'],1)
        self.assertEqual(s['status'],'pass')
    def test_incomplete_no_loss_counts(self):
        r=raw();r['observed']['complete']=False;r['observed']['error']={'kind':'resource_limit'};r['observed']['tables']={}
        s=build_summary({'invariants':[]},[r])[0]
        self.assertEqual(s['status'],'incomplete');self.assertIsNone(s['observed_changes']['objects_removed'])
        self.assertEqual(s['observed_changes']['tables'],{})
    def test_open_transaction_never_pass(self):
        r=raw();r['execution']['status']='open_transaction';r['execution']['in_transaction']=True
        self.assertEqual(build_summary({'invariants':[]},[r])[0]['status'],'execution_error')
    def test_query_typed_bag(self):
        r=raw();fact={'complete':True,'error':None,'rows':[[['integer','1']],[['integer','2']]]}
        r['queries']={'q':{'before':fact,'observed':fact,'reopened':fact}}
        inv={'name':'q','kind':'query_equals','sql':'x','order':'bag','expected':[[['integer','2']],[['integer','1']]]}
        self.assertEqual(build_summary({'invariants':[inv]},[r])[0]['observed_checks'][0]['status'],'pass')
        inv['expected'][0]=[['real','4000000000000000']]
        self.assertEqual(build_summary({'invariants':[inv]},[r])[0]['status'],'failed')
    def test_bad_boolean_error_and_notpass(self):
        r=raw();fact={'complete':True,'error':None,'rows':[[['integer','2']]]}
        r['queries']={'q':dict.fromkeys(('before','observed','reopened'),fact)}
        inv={'name':'q','kind':'query_equals','sql':'x','order':'ordered','expected':[[['integer','1']]],'boolean':True}
        s=build_summary({'invariants':[inv]},[r])[0]
        self.assertEqual(s['observed_checks'][0]['status'],'error');self.assertNotEqual(s['status'],'pass')
    def test_added_columns_explicit(self):
        r=raw();t=r['observed']['tables']['t'];t['columns']=['x','new'];t['rows']=[[['integer','1'],['integer','7']]]*2
        s=build_summary({'invariants':[]},[r])[0]['observed_changes']['tables']['t']
        self.assertEqual(s['kind'],'projection');self.assertEqual(s['columns_added'],['new']);self.assertEqual(s['projected_added'],0)
    def test_view_error_and_fk_failure(self):
        for field in ('view','fk'):
            r=raw()
            if field=='view':r['observed']['views']['broken']={'ok':False,'error':{'kind':'sqlite_error'}}
            else:r['observed']['diagnostics']['foreign_key_check']['rows']=[[['text','dA=='],['integer','1'],['text','cA=='],['integer','0']]]
            self.assertEqual(build_summary({'invariants':[]},[r])[0]['status'],'failed')
