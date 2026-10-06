import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sqlitefolio.publish import publish, PublicationError


def contender(root, barrier, queue, marker):
    barrier.wait()
    try:
        publish({'packet.json':marker.encode(),'inputs/x.sql':b'SELECT 1;'},Path(root)/'packet')
        queue.put('won')
    except PublicationError:
        queue.put('lost')


class PublicationTests(unittest.TestCase):
    def test_atomically_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'packet';publish({'packet.json':b'{}','inputs/x.sql':b'SELECT 1;'},p)
            self.assertEqual((p/'inputs/x.sql').read_bytes(),b'SELECT 1;')
            self.assertEqual([v.name for v in Path(d).iterdir()],['packet'])
    def test_no_clobber_empty_or_nonempty(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'packet';p.mkdir()
            with self.assertRaises(PublicationError):publish({'x':b'bad'},p)
            self.assertEqual(list(p.iterdir()),[])
            (p/'original').write_bytes(b'old')
            with self.assertRaises(PublicationError):publish({'x':b'bad'},p)
            self.assertEqual((p/'original').read_bytes(),b'old')
    def test_true_concurrent_noreplace_races(self):
        for _ in range(3):
            with tempfile.TemporaryDirectory() as d:
                ctx=multiprocessing.get_context('fork');barrier=ctx.Barrier(2);queue=ctx.Queue()
                workers=[ctx.Process(target=contender,args=(d,barrier,queue,str(i))) for i in range(2)]
                for w in workers:w.start()
                for w in workers:w.join(10);self.assertFalse(w.is_alive());self.assertEqual(w.exitcode,0)
                self.assertEqual(sorted([queue.get(timeout=2),queue.get(timeout=2)]),['lost','won'])
                self.assertIn((Path(d)/'packet'/'packet.json').read_bytes(),(b'0',b'1'))
                self.assertEqual(len(list(Path(d).iterdir())),1)
    def test_partial_write_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('sqlitefolio.publish.os.write',side_effect=OSError('simulated write failure')):
                with self.assertRaises(PublicationError) as got:publish({'packet.json':b'{}'},Path(d)/'packet')
            self.assertFalse(got.exception.published);self.assertEqual(list(Path(d).iterdir()),[])
    def test_after_rename_fsync_failure_truthful(self):
        with tempfile.TemporaryDirectory() as d:
            original=os.fsync
            def fail_parent(fd):
                if os.readlink('/proc/self/fd/'+str(fd))==d:raise OSError('simulated parent fsync failure')
                original(fd)
            with patch('sqlitefolio.publish.os.fsync',side_effect=fail_parent):
                with self.assertRaises(PublicationError) as got:publish({'packet.json':b'{}'},Path(d)/'packet')
            self.assertTrue(got.exception.published);self.assertTrue((Path(d)/'packet'/'packet.json').is_file())
