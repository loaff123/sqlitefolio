import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from sqlitefolio.packet import rehearse


def write_input(root, sql='ALTER TABLE t ADD COLUMN y INTEGER DEFAULT 7;'):
    (root/'schema.sql').write_text('CREATE TABLE t(x INTEGER);')
    (root/'seed.sql').write_text('INSERT INTO t VALUES(1),(1);')
    (root/'migration.sql').write_text(sql)
    m={'format':'sqlitefolio.input.v1','scenario':'journey','source':{'schema':'schema.sql','seed':'seed.sql'},'profile':{'foreign_keys':True,'transaction_mode':'autocommit'},'candidates':[{'name':'proposal','sql':'migration.sql'}],'invariants':[{'name':'count','kind':'row_count','table':'t','expected':'baseline'}]}
    path=root/'input.json';path.write_text(json.dumps(m));return path


class CLIPacketTests(unittest.TestCase):
    def invoke(self,*args):
        return subprocess.run([sys.executable,'-m','sqlitefolio',*map(str,args)],capture_output=True,timeout=45)
    def test_full_sql_journey(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=write_input(root);out=root/'packet'
            result=self.invoke('rehearse',manifest,'--out',out,'--trust-input')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.invoke('verify',out).returncode,0)
            inspected=self.invoke('inspect',out);self.assertEqual(inspected.returncode,0,inspected.stderr)
            self.assertIn(b'SQLiteFolio',inspected.stdout)
            self.assertEqual(self.invoke('replay',out,'--manifest',manifest,'--trust-input').returncode,0)
            self.assertFalse((out/'sample.db').exists())
    def test_requires_explicit_trust(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=write_input(root)
            r=self.invoke('rehearse',manifest,'--out',root/'packet')
            self.assertEqual(r.returncode,2);self.assertFalse((root/'packet').exists())
    def test_open_transaction_and_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=write_input(root,'BEGIN; DELETE FROM t;')
            before={p.name:p.read_bytes() for p in root.iterdir()}
            r=self.invoke('rehearse',manifest,'--out',root/'packet','--trust-input')
            self.assertEqual(r.returncode,3,r.stderr)
            self.assertTrue(all((root/n).read_bytes()==v for n,v in before.items()))
            p=json.loads((root/'packet'/'packet.json').read_bytes())
            self.assertEqual(len(p['candidates'][0]['observed']['tables']['t']['rows']),0)
            self.assertEqual(len(p['candidates'][0]['reopened']['tables']['t']['rows']),2)
    def test_replay_changed_sql(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=write_input(root);out=root/'packet'
            self.assertEqual(self.invoke('rehearse',m,'--out',out,'--trust-input').returncode,0)
            (root/'migration.sql').write_text('DELETE FROM t;')
            self.assertEqual(self.invoke('replay',out,'--manifest',m,'--trust-input').returncode,6)
    def test_closed_stdout_keeps_published_packet(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=write_input(root);out=root/'packet'
            proc=subprocess.Popen([sys.executable,'-m','sqlitefolio','rehearse',str(m),'--out',str(out),'--trust-input'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            proc.stdout.close();error=proc.stderr.read();proc.stderr.close();proc.wait(timeout=45)
            self.assertEqual(proc.returncode,7,error);self.assertTrue((out/'packet.json').exists())
    def test_database_inclusion_opt_in(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);m=write_input(root);database=root/'sample.db'
            conn=sqlite3.connect(database);conn.executescript('CREATE TABLE t(x);INSERT INTO t VALUES(1);');conn.close()
            document=json.loads(m.read_text());document['source']={'database':'sample.db'};m.write_text(json.dumps(document));before=database.read_bytes()
            out=root/'packet'
            self.assertEqual(self.invoke('rehearse',m,'--out',out,'--trust-input','--include-sample').returncode,0)
            self.assertEqual((out/'sample.db').read_bytes(),before);self.assertEqual(database.read_bytes(),before)
            self.assertEqual(self.invoke('replay',out,'--manifest',m,'--trust-input').returncode,0)
