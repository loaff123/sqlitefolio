import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from sqlitefolio.contract import InputError, read_json, validate_manifest
from sqlitefolio.paths import load_inputs


def manifest():
    return {"format":"sqlitefolio.input.v1","scenario":"demo","source":{"schema":"schema.sql","seed":"seed.sql"},"profile":{"foreign_keys":True,"transaction_mode":"autocommit"},"candidates":[{"name":"safe","sql":"migration.sql"}],"invariants":[]}


class ContractTests(unittest.TestCase):
    def test_valid_defaults(self):
        m=validate_manifest(manifest())
        self.assertEqual(m['limits']['rows_per_query'],10000)
    def test_duplicate_keys(self):
        with self.assertRaises(InputError):read_json(b'{"format":1,"format":2}')
    def test_invalid_utf8_nonfinite(self):
        for data in (b'\xff',b'{"x":NaN}',b'{"x":Infinity}'):
            with self.subTest(data=data),self.assertRaises(InputError):read_json(data)
    def test_unknown_nested(self):
        for target in ('top','profile','source','candidate','invariant'):
            m=manifest()
            if target=='top':m['typo']=0
            elif target=='candidate':m['candidates'][0]['typo']=0
            elif target=='invariant':m['invariants']=[{'name':'x','kind':'integrity_ok','typo':0}]
            else:m[target]['typo']=0
            with self.subTest(target=target),self.assertRaises(InputError):validate_manifest(m)
    def test_duplicate_names_and_source_choice(self):
        m=manifest();m['candidates']*=2
        with self.assertRaises(InputError):validate_manifest(m)
        m=manifest();m['source']['database']='x.db'
        with self.assertRaises(InputError):validate_manifest(m)
    def test_counts_and_limits(self):
        for value in (True,-1,10001,'3'):
            m=manifest();m['limits']={'rows_per_query':value}
            with self.subTest(value=value),self.assertRaises(InputError):validate_manifest(m)
        m=manifest();m['invariants']=[{'kind':'row_count','name':'n','table':'t','expected':True}]
        with self.assertRaises(InputError):validate_manifest(m)
    def test_typed_expectations(self):
        for cell in (['integer',True],['integer','01'],['integer','9223372036854775808'],['text','!!!'],['real','7ff8000000000000'],['null','null']):
            m=manifest();m['invariants']=[{'kind':'query_equals','name':'q','sql':'SELECT 1','order':'bag','expected':[[cell]]}]
            with self.subTest(cell=cell),self.assertRaises(InputError):validate_manifest(m)
    def test_boolean_contract(self):
        m=manifest();m['invariants']=[{'kind':'query_equals','name':'q','sql':'SELECT 1','order':'ordered','expected':[[['integer','2']]],'boolean':True}]
        with self.assertRaises(InputError):validate_manifest(m)
    def test_unequal_width_paths(self):
        for p in ('../x','/x','https://a','a//b','a\\b','$HOME/a','a/./b'):
            m=manifest();m['source']['schema']=p
            with self.subTest(p=p),self.assertRaises(InputError):validate_manifest(m)


class PathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for name,content in [('schema.sql','CREATE TABLE t(x);'),('seed.sql','INSERT INTO t VALUES(1);'),('migration.sql','SELECT 1;')]:
            (self.root/name).write_text(content)
        self.path=self.root/'input.json';self.path.write_text(json.dumps(manifest()))
    def tearDown(self):self.tmp.cleanup()
    def test_preserves_and_normalizes(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        bundle=load_inputs(self.path,self.root/'packet')
        self.assertEqual(bundle.manifest['source'],{'schema':'inputs/schema.sql','seed':'inputs/seed.sql'})
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        self.assertTrue(bundle.unchanged())
    def test_no_clobber_source_or_ancestor(self):
        for dest in (self.root,self.path,self.root/'schema.sql'):
            with self.subTest(dest=dest),self.assertRaises(InputError):load_inputs(self.path,dest)
    def test_hardlink_and_symlink(self):
        link=self.root/'linked';os.link(self.root/'schema.sql',link)
        with self.assertRaises(InputError):load_inputs(self.path,self.root/'out')
        link.unlink();link.symlink_to(self.root/'schema.sql')
        m=manifest();m['source']['schema']='linked';self.path.write_text(json.dumps(m))
        with self.assertRaises(InputError):load_inputs(self.path,self.root/'out')
    def test_symlink_output_ancestor_and_nonexistent_parent(self):
        (self.root/'alias').symlink_to(self.root,target_is_directory=True)
        for dest in (self.root/'alias'/'out',self.root/'missing'/'out'):
            with self.subTest(dest=dest),self.assertRaises(InputError):load_inputs(self.path,dest)
        self.assertFalse((self.root/'missing').exists())
    def test_source_changes_detected(self):
        b=load_inputs(self.path);(self.root/'seed.sql').write_text('SELECT 2;')
        self.assertFalse(b.unchanged())
    def test_missing_and_sql_utf8(self):
        (self.root/'seed.sql').unlink()
        with self.assertRaises(InputError):load_inputs(self.path)
        (self.root/'seed.sql').write_bytes(b'\xff')
        with self.assertRaises(InputError):load_inputs(self.path)
    def test_database_sidecar_and_header(self):
        import sqlite3
        db=self.root/'sample.db';c=sqlite3.connect(db);c.execute('create table t(x)');c.close()
        m=manifest();m['source']={'database':'sample.db'};self.path.write_text(json.dumps(m))
        b=load_inputs(self.path);self.assertEqual(b.source_kind,'database')
        (self.root/'sample.db-wal').write_bytes(b'')
        with self.assertRaises(InputError):load_inputs(self.path)

class CountExpectationTests(unittest.TestCase):
    def test_expected_count_above_observation_bound_is_valid_false_check(self):
        m=manifest();m['invariants']=[{'name':'large expected','kind':'row_count','table':'t','expected':10001}]
        self.assertEqual(validate_manifest(m)['invariants'][0]['expected'],10001)
