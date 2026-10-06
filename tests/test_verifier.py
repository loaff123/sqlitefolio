"""Verifier tests use independently authored protocol facts, not producer helpers."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

LIMITS = {'rows_per_query':10000,'vm_steps':2000000,'wall_seconds':30,'evidence_bytes':16777216}
RUNTIME = {'python':'3.12.12','sqlite':'3.53.1','sqlite_source_id':'2026-05-05 source-id','compile_options':['THREADSAFE=1'],'platform':'linux','python_build':'3.12.12 test build','implementation':'CPython','machine':'x86_64'}
PROFILE = {'foreign_keys':True,'transaction_mode':'autocommit'}

def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def digest(x): return hashlib.sha256(x).hexdigest()
def txt(x): return ['text',base64.b64encode(x.encode()).decode()]
def integer(n): return ['integer',str(n)]
def err(kind='sqlite_error'): return {'kind':kind,'message':'test error','sqlite_code':None,'sqlite_name':None}
def manifest(checks=None):
    return {'format':'sqlitefolio.input.v1','scenario':'sample','source':{'schema':'inputs/schema.sql','seed':'inputs/seed.sql'},'profile':copy.deepcopy(PROFILE),'candidates':[{'name':'change','sql':'inputs/candidate-1.sql'}],'invariants':checks or [],'limits':copy.deepcopy(LIMITS)}
def table(columns=('x',), rows=None):
    return {'columns':list(columns),'column_info':[[i,c,'',0,None,0,0] for i,c in enumerate(columns)],'table_info':['main','t','table',len(columns),0,0],'foreign_keys':[],'indexes':[],'rows':rows if rows is not None else [[integer(1)],[integer(1)]]}
def snap(columns=('x',),rows=None):
    return {'complete':True,'error':None,'objects':[{'type':'table','name':'t','table':'t','sql':'CREATE TABLE t('+','.join(columns)+')'}],'tables':{'t':table(columns,rows)},'views':{},'diagnostics':{'foreign_key_check':{'rows':[],'error':None},'integrity_check':{'rows':[[txt('ok')]],'error':None},'foreign_keys':True},'sequences':[]}
def candidate(before=None,after=None,queries=None):
    before=before or snap(); after=after or copy.deepcopy(before)
    return {'name':'change','runtime':copy.deepcopy(RUNTIME),'profile':copy.deepcopy(PROFILE),'execution':{'status':'completed','error':None,'in_transaction':False,'foreign_keys_start':True,'foreign_keys_end':True},'before':before,'observed':after,'reopened':copy.deepcopy(after),'queries':queries or {},'source_preserved':True}
def fact(rows): return {'rows':rows,'error':None,'complete':True}

class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('sqlitefolio.verify'),'independent verifier must exist')
        from sqlitefolio.verify import reconstruct,verify_packet
        self.reconstruct=reconstruct; self.verify=verify_packet

    def test_reconstructs_duplicate_projection_counts_and_every_summary_field(self):
        result=self.reconstruct(manifest(),[candidate(after=snap(rows=[[integer(1)]]))])[0]
        self.assertEqual(set(result),{'name','status','execution','complete','observed_checks','reopened_checks','observed_changes','reopened_changes'})
        self.assertEqual(result['status'],'pass')
        self.assertEqual(result['observed_changes']['tables']['t'],{'kind':'projection','common_columns':['x'],'columns_added':[],'columns_removed':[],'rows_before':2,'rows_after':1,'projected_added':0,'projected_removed':1,'metadata_changed':False})

    def test_no_common_columns_never_claims_row_loss(self):
        result=self.reconstruct(manifest(),[candidate(after=snap(('renamed',),[[integer(1)]]))])[0]
        delta=result['observed_changes']['tables']['t']
        self.assertEqual(delta['common_columns'],[])
        self.assertIsNone(delta['projected_removed']); self.assertIsNone(delta['projected_added'])

    def test_query_boolean_does_not_use_python_truthiness(self):
        check={'name':'boolean','kind':'query_equals','sql':'SELECT x FROM t','order':'ordered','expected':[[integer(1)]],'boolean':True}
        q={'boolean':{phase:fact([[txt('yes')]]) for phase in ['before','observed','reopened']}}
        result=self.reconstruct(manifest([check]),[candidate(queries=q)])[0]
        self.assertEqual(result['observed_checks'],[{'name':'boolean','status':'error'}])
        self.assertNotEqual(result['status'],'pass')

    def test_query_preserved_bag_is_typed_multiset(self):
        check={'name':'bag','kind':'query_preserved','sql':'SELECT x FROM t','order':'bag'}
        q={'bag':{'before':fact([[integer(1)],[integer(1)],[txt('1')]]),'observed':fact([[txt('1')],[integer(1)],[integer(1)]]),'reopened':fact([[integer(1)],[txt('1')]])}}
        result=self.reconstruct(manifest([check]),[candidate(queries=q)])[0]
        self.assertEqual(result['observed_checks'],[{'name':'bag','status':'pass'}]); self.assertEqual(result['reopened_checks'],[{'name':'bag','status':'fail'}])
        self.assertEqual(result['status'],'failed')

    def test_incomplete_snapshot_has_no_numeric_changes(self):
        after=snap(); after.update(complete=False,error=err('resource_limit'))
        raw=candidate(after=after); raw['execution'].update(status='resource_limit',error=err('resource_limit'))
        result=self.reconstruct(manifest(),[raw])[0]
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['observed_changes'],{'complete':False,'objects_added':None,'objects_removed':None,'objects_changed':None,'tables':{}})

    def test_view_and_native_diagnostic_errors_never_pass(self):
        for mode in ('view','fk','integrity'):
            raw=candidate()
            for phase in ('observed','reopened'):
                s=raw[phase]
                if mode=='view':
                    s['objects'].append({'type':'view','name':'v','table':'v','sql':'CREATE VIEW v AS SELECT missing FROM t'})
                    s['views']['v']={'ok':False,'error':err()}
                elif mode=='fk': s['diagnostics']['foreign_key_check']['rows']=[[txt('t'),integer(1),txt('p'),integer(0)]]
                else: s['diagnostics']['integrity_check']['rows']=[[txt('broken')]]
            self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'failed',mode)

    def test_rejects_raw_shape_and_typed_cell_forgeries(self):
        mutations=[lambda x:x.update(unknown=True),lambda x:x['before'].update(complete=1),lambda x:x['before']['tables']['t']['rows'][0].__setitem__(0,['integer','01']),lambda x:x['before']['tables']['t']['rows'][0].__setitem__(0,['real','7ff8000000000000']),lambda x:x['before']['tables']['t']['rows'][0].__setitem__(0,['blob','Zh==']),lambda x:x['execution'].update(status='completed',error=err()),lambda x:x['before']['tables']['t']['column_info'][0].__setitem__(0,True),lambda x:x['before']['tables']['t'].update(rows=[[]]),lambda x:x['runtime'].update(sqlite='3.50.0')]
        for change in mutations:
            raw=candidate(); change(raw)
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): self.reconstruct(manifest(),[raw])

    def test_missing_query_phase_rejected(self):
        check={'name':'q','kind':'query_preserved','sql':'SELECT 1','order':'ordered'}
        with self.assertRaises(ValueError): self.reconstruct(manifest([check]),[candidate(queries={'q':{'observed':fact([]),'reopened':fact([])}})])

    def test_before_profile_runtime_group_identity(self):
        inp=manifest(); inp['candidates'].append({'name':'other','sql':'inputs/candidate-2.sql'})
        for field in ('before','profile','runtime'):
            a=candidate(); b=candidate(); b['name']='other'
            if field=='before': b['before']['tables']['t']['rows']=[]
            if field=='profile': b['profile']['foreign_keys']=False
            if field=='runtime': b['runtime']['python']='3.12.99'
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.reconstruct(inp,[a,b])

    def test_malformed_packet_returns_controlled_failure(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'packet'; p.mkdir()
            for payload in (b'{',b'[]',b'{"format":"x","format":"y"}',b'{"x":NaN}',b'{"x":1e999}'):
                (p/'packet.json').write_bytes(payload)
                out=self.verify(p)
                self.assertEqual(out['valid'],False); self.assertEqual(out['passing'],False); self.assertEqual(out['exit_code'],6)


class VerifierBoundaryTests(unittest.TestCase):
    setUp=VerifierTests.setUp
    def test_open_transaction_null_error_is_valid_nonpassing(self):
        raw=candidate(); raw['execution'].update(status='open_transaction',error=None,in_transaction=True)
        self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'execution_error')

    def test_empty_partial_diagnostics_is_valid_incomplete(self):
        raw=candidate(); raw['before'].update(complete=False,error=err('resource_limit'),diagnostics={},objects=[],tables={})
        raw['observed']=None; raw['reopened']=None
        raw['execution'].update(status='resource_limit',error=err('resource_limit'))
        self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'incomplete')

    def test_evidence_limit_enforced_with_minimal_error_envelope_exception(self):
        inp=manifest(); inp['limits']['evidence_bytes']=4096
        raw=candidate()
        for phase in ('before','observed','reopened'):
            raw[phase]['tables']['t']['rows']=[[txt('a'*2000)]]
        with self.assertRaises(ValueError): self.reconstruct(inp,[raw])
        raw.update(before=None,observed=None,reopened=None)
        raw['execution'].update(status='resource_limit',error=err('resource_limit'))
        self.assertEqual(self.reconstruct(inp,[raw])[0]['status'],'incomplete')

    def write_packet(self, root, raw=None, inp=None, original=None):
        from sqlitefolio.report import render
        from sqlitefolio.verify import _LIMITATIONS
        root.mkdir(); (root/'inputs').mkdir()
        inp=inp or manifest(); original=original or inp
        data={'inputs/original-manifest.json':canon(original),'inputs/schema.sql':b'CREATE TABLE t(x);','inputs/seed.sql':b'INSERT INTO t VALUES(1),(1);','inputs/candidate-1.sql':b''}
        for name,blob in data.items(): (root/name).write_bytes(blob)
        candidate_data=raw or candidate()
        source_digest=digest(canon({'schema_sha256':digest(data['inputs/schema.sql']),'seed_sha256':digest(data['inputs/seed.sql'])}))
        packet={'format':'sqlitefolio.packet.v1','input':inp,'input_sha256':digest(canon(inp)),'files':{name:digest(blob) for name,blob in data.items()},'source':{'kind':'sql','sha256':source_digest,'snapshot_sha256':digest(canon(candidate_data['before'])),'included':False},'candidates':[candidate_data],'summary':self.reconstruct(inp,[candidate_data]),'limitations':list(_LIMITATIONS)}
        (root/'manifest.json').write_bytes(canon(inp)); (root/'packet.json').write_bytes(canon(packet)); (root/'report.html').write_text(render(packet),encoding='utf8')
        return packet

    def rewrite_packet(self,root,packet):
        from sqlitefolio.report import render
        (root/'packet.json').write_bytes(canon(packet)); (root/'report.html').write_text(render(packet),encoding='utf8')

    def test_complete_packet_passes_structural_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; self.write_packet(root)
            self.assertEqual(self.verify(root)['exit_code'],0,self.verify(root))

    def test_rehashed_false_summary_fails_despite_matching_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            packet['summary'][0]['observed_changes']['tables']['t']['projected_removed']=1
            self.rewrite_packet(root,packet)
            result=self.verify(root); self.assertFalse(result['valid']); self.assertIn('summary',result['message'])

    def test_boolean_number_summary_substitution_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            packet['summary'][0]['complete']=1; self.rewrite_packet(root,packet)
            self.assertFalse(self.verify(root)['valid'])

    def test_rehashed_original_manifest_semantics_must_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            original=manifest(); original['scenario']='different'
            blob=canon(original); (root/'inputs/original-manifest.json').write_bytes(blob)
            packet['files']['inputs/original-manifest.json']=digest(blob); self.rewrite_packet(root,packet)
            self.assertFalse(self.verify(root)['valid'])

    def test_report_and_manifest_bytes_are_exact(self):
        for filename in ('report.html','manifest.json'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder)/'packet'; self.write_packet(root)
                with (root/filename).open('ab') as out: out.write(b'\n')
                self.assertFalse(self.verify(root)['valid'],filename)

    def test_extra_directory_file_symlink_and_hardlink_rejected(self):
        for mode in ('directory','file','symlink','hardlink'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder)/'packet'; self.write_packet(root)
                if mode=='directory': (root/'inputs/extra').mkdir()
                elif mode=='file': (root/'extra.txt').write_text('x')
                elif mode=='symlink':
                    (root/'inputs/seed.sql').unlink(); (root/'inputs/seed.sql').symlink_to(root/'inputs/schema.sql')
                else: os.link(root/'inputs/seed.sql',Path(folder)/'linked.sql')
                self.assertFalse(self.verify(root)['valid'],mode)

    def test_partial_error_packet_valid_but_never_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; raw=candidate(); raw.update(before=None,observed=None,reopened=None)
            raw['execution'].update(status='worker_error',error=err('worker_error'),in_transaction=None,foreign_keys_start=None,foreign_keys_end=None)
            self.write_packet(root,raw=raw)
            result=self.verify(root); self.assertTrue(result['valid'],result); self.assertFalse(result['passing']); self.assertEqual(result['exit_code'],5)

    def test_sql_packet_cannot_claim_included_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            (root/'sample.db').write_bytes(b'fake'); packet['files']['sample.db']=digest(b'fake'); packet['source']['included']=True
            self.rewrite_packet(root,packet)
            self.assertFalse(self.verify(root)['valid'])

class VerifierAdditionalTests(unittest.TestCase):
    setUp=VerifierTests.setUp
    write_packet=VerifierBoundaryTests.write_packet
    rewrite_packet=VerifierBoundaryTests.rewrite_packet

    def test_long_exact_native_error_message_is_allowed_within_evidence_cap(self):
        raw=candidate(); raw['execution'].update(status='sqlite_error',error=err())
        raw['execution']['error']['message']='no such column: '+('long_identifier_'*5000)
        self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'execution_error')

    def test_lexical_symlink_component_is_rejected_even_before_dotdot(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); root=base/'packet'; self.write_packet(root)
            (base/'real').mkdir(); (base/'link').symlink_to(base/'real',target_is_directory=True)
            self.assertFalse(self.verify(base/'link/../packet')['valid'])

    def test_incomplete_metadata_does_not_require_complete_object_correspondence(self):
        raw=candidate(); raw['before'].update(complete=False,error=err('resource_limit'),objects=[])
        raw['execution'].update(status='resource_limit',error=err('resource_limit'))
        self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'incomplete')

    def test_original_repeated_filename_must_retain_identical_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            original=manifest(); original['source']={'schema':'same.sql','seed':'same.sql'}
            root=Path(folder)/'packet'; self.write_packet(root,original=original)
            self.assertFalse(self.verify(root)['valid'])

    def test_complete_baselines_agree_even_with_missing_failure_baseline(self):
        inp=manifest(); inp['candidates'].append({'name':'other','sql':'inputs/candidate-2.sql'})
        a=candidate(); b=candidate(); b['name']='other'; b.update(before=None,observed=None,reopened=None)
        b['execution'].update(status='worker_error',error=err('worker_error'),in_transaction=None,foreign_keys_start=None,foreign_keys_end=None)
        out=self.reconstruct(inp,[a,b]); self.assertEqual([r['status'] for r in out],['pass','internal_error'])

    def test_all_nonquery_invariants_reconstructed_from_facts(self):
        checks=[{'name':'count','kind':'row_count','table':'t','expected':'baseline'},
                {'name':'explicit','kind':'row_count','table':'t','expected':2},
                {'name':'present','kind':'object_present','object_type':'table','object':'t'},
                {'name':'absent','kind':'object_absent','object_type':'view','object':'v'},
                {'name':'fk','kind':'no_foreign_key_violations'}, {'name':'integrity','kind':'integrity_ok'}]
        out=self.reconstruct(manifest(checks),[candidate()])[0]
        self.assertEqual(out['observed_checks'],[{'name':c['name'],'status':'pass'} for c in checks])
        self.assertEqual(out['reopened_checks'],out['observed_checks'])

    def test_query_equals_ignores_baseline_errors_and_required_notrun_is_incomplete(self):
        check={'name':'new','kind':'query_equals','sql':'SELECT new FROM t','order':'ordered','expected':[[integer(1)]]}
        q={'new':{'before':{'rows':None,'error':err(),'complete':False},'observed':fact([[integer(1)]]),'reopened':fact([[integer(1)]])}}
        raw=candidate(queries=q); self.assertEqual(self.reconstruct(manifest([check]),[raw])[0]['status'],'pass')
        q['new']['reopened']={'rows':None,'error':err('not_run'),'complete':False}
        self.assertEqual(self.reconstruct(manifest([check]),[raw])[0]['status'],'incomplete')

    def test_db_digest_is_declared_without_sample_and_checked_with_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            inp=manifest(); inp['source']={'database':'sample.db'}
            for name in ('inputs/schema.sql','inputs/seed.sql'):
                (root/name).unlink(); del packet['files'][name]
            original=copy.deepcopy(inp); original['source']={'database':'user-source.db'}
            (root/'inputs/original-manifest.json').write_bytes(canon(original))
            packet['files']['inputs/original-manifest.json']=digest(canon(original))
            packet.update(input=inp,input_sha256=digest(canon(inp)))
            packet['source'].update(kind='database',sha256=digest(b'original'))
            (root/'manifest.json').write_bytes(canon(inp)); self.rewrite_packet(root,packet)
            self.assertTrue(self.verify(root)['valid'],self.verify(root))
            (root/'sample.db').write_bytes(b'wrong'); packet['files']['sample.db']=digest(b'wrong'); packet['source']['included']=True
            self.rewrite_packet(root,packet); self.assertFalse(self.verify(root)['valid'])
            packet['source']['sha256']=digest(b'wrong'); self.rewrite_packet(root,packet)
            self.assertTrue(self.verify(root)['valid'],self.verify(root))


class VerifierFinalContractTests(unittest.TestCase):
    setUp=VerifierTests.setUp

    def test_execution_kind_and_status_must_be_consistent(self):
        for status in ('sqlite_error','resource_limit','unsupported','worker_error','source_changed','open_transaction'):
            raw=candidate(); raw['execution'].update(status=status,error=err('different'),in_transaction=status=='open_transaction')
            with self.subTest(status=status):
                with self.assertRaises(ValueError): self.reconstruct(manifest(),[raw])

    def test_row_count_expectation_may_exceed_observation_cap(self):
        checks=[{'name':'impossible','kind':'row_count','table':'t','expected':10001}]
        out=self.reconstruct(manifest(checks),[candidate()])[0]
        self.assertEqual(out['observed_checks'],[{'name':'impossible','status':'fail'}])
        self.assertEqual(out['status'],'failed')

    def test_metadata_rows_count_toward_total_observation_budget(self):
        raw=candidate(); columns=[f'c{i}' for i in range(256)]
        for phase in ('before','observed','reopened'):
            snapshot=raw[phase]; snapshot['objects']=[]; snapshot['tables']={}
            for i in range(128):
                name=f't{i:03d}'
                item=table(columns,[[integer(0) for _ in columns] for _ in range(4)])
                item['table_info'][1]=name
                snapshot['tables'][name]=item
                snapshot['objects'].append({'type':'table','name':name,'table':name,'sql':'CREATE TABLE '+name+'('+','.join(columns)+')'})
        with self.assertRaises(ValueError): self.reconstruct(manifest(),[raw])

    def test_native_metadata_strings_are_not_typed_cell_payloads(self):
        raw=candidate()
        for phase in ('before','observed','reopened'):
            raw[phase]['tables']['t']['column_info'][0][4]="'" + ('a'*70000) + "'"
        self.assertEqual(self.reconstruct(manifest(),[raw])[0]['status'],'pass')

    def test_expanded_runtime_identity_is_required(self):
        for field in ('python_build','implementation','machine'):
            raw=candidate(); del raw['runtime'][field]
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.reconstruct(manifest(),[raw])
        for field,value in (('implementation','PyPy'),('machine','aarch64')):
            raw=candidate(); raw['runtime'][field]=value
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.reconstruct(manifest(),[raw])

class VerifierCheckedByteTests(unittest.TestCase):
    setUp=VerifierTests.setUp
    write_packet=VerifierBoundaryTests.write_packet

    def test_success_returns_digests_of_exact_verified_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; packet=self.write_packet(root)
            packet_bytes=json.dumps(packet,indent=3,ensure_ascii=True).encode()+b'\n'
            (root/'packet.json').write_bytes(packet_bytes)
            report_bytes=(root/'report.html').read_bytes()
            result=self.verify(root)
            self.assertTrue(result['valid'],result)
            self.assertEqual(result.get('packet_sha256'),digest(packet_bytes))
            self.assertEqual(result.get('report_sha256'),digest(report_bytes))
            self.assertNotEqual(result['packet_sha256'],digest(canon(packet)))

    def test_checked_byte_digests_do_not_reread_after_validation(self):
        from unittest.mock import patch
        import sqlitefolio.verify as verifier
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'packet'; self.write_packet(root)
            expected={name:digest((root/name).read_bytes()) for name in ('packet.json','report.html')}
            original_read=verifier._read
            reads=[]
            def read_then_replace(path,maximum):
                data=original_read(path,maximum)
                if path.name in expected:
                    reads.append(path.name)
                    path.write_bytes(b'UNVERIFIED REPLACEMENT')
                return data
            with patch('sqlitefolio.verify._read',side_effect=read_then_replace):
                result=self.verify(root)
            self.assertTrue(result['valid'],result)
            self.assertEqual(reads.count('packet.json'),1)
            self.assertEqual(reads.count('report.html'),1)
            self.assertEqual(result.get('packet_sha256'),expected['packet.json'])
            self.assertEqual(result.get('report_sha256'),expected['report.html'])

class VerifierMinimalEnvelopeTests(unittest.TestCase):
    setUp=VerifierTests.setUp

    def envelope(self,status):
        checks=[{'name':f'q{i:02d}','kind':'query_preserved','sql':'SELECT 777','order':'ordered'} for i in range(64)]
        inp=manifest(checks); inp['limits']['evidence_bytes']=4096
        raw=candidate(); raw.update(before=None,observed=None,reopened=None)
        raw['execution'].update(status=status,error=err(status))
        raw['source_preserved']=status!='source_changed'
        raw['queries']={check['name']:{phase:{'rows':None,'error':err('not_run'),'complete':False} for phase in ('before','observed','reopened')} for check in checks}
        self.assertGreater(len(canon(raw)),4096)
        return inp,raw

    def test_higher_priority_failure_envelope_is_reviewable_above_lowered_limit(self):
        for status,expected in (('worker_error','internal_error'),('source_changed','incomplete')):
            inp,raw=self.envelope(status)
            with self.subTest(status=status):
                self.assertEqual(self.reconstruct(inp,[raw])[0]['status'],expected)

    def test_exception_does_not_permit_collected_facts_above_limit(self):
        for status in ('worker_error','source_changed','resource_limit'):
            for change in ('snapshot','query'):
                inp,raw=self.envelope(status)
                if change=='snapshot': raw['before']=snap()
                else: raw['queries']['q00']['observed']=fact([])
                with self.subTest(status=status,change=change):
                    with self.assertRaises(ValueError): self.reconstruct(inp,[raw])

    def test_exception_does_not_cover_lower_priority_execution_error(self):
        inp,raw=self.envelope('sqlite_error')
        with self.assertRaises(ValueError): self.reconstruct(inp,[raw])

if __name__=='__main__': unittest.main()
