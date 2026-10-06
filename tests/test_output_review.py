"""Independent, benign regressions for packet and publication boundaries.

These tests deliberately assert the frozen protocol, including cases that fail
against the implementation reviewed on 2026-10-05. No downloaded SQL, live
database, native parser investigation, or external publication is involved.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlitefolio import cli
from sqlitefolio.contract import InputError, canonical, read_json
from sqlitefolio.packet import CapacityError, rehearse, replay, run_candidate
from sqlitefolio.paths import load_inputs, sha
from sqlitefolio.publish import PublicationError, publish
from sqlitefolio.verify import verify_packet


def write_input(root, *, sql="SELECT 1;", filename="migration.sql", limits=None):
    """Use only a tiny, newly authored and quiescent SQL sample."""
    (root / "schema.sql").write_text("CREATE TABLE t(x INTEGER);", encoding="utf-8")
    (root / "seed.sql").write_text("INSERT INTO t VALUES(1),(2);", encoding="utf-8")
    (root / filename).write_text(sql, encoding="utf-8")
    manifest = {
        "format": "sqlitefolio.input.v1",
        "scenario": "output_review",
        "source": {"schema": "schema.sql", "seed": "seed.sql"},
        "profile": {"foreign_keys": True, "transaction_mode": "autocommit"},
        "candidates": [{"name": "proposal", "sql": filename}],
        "invariants": [],
    }
    if limits is not None:
        manifest["limits"] = limits
    path = root / (filename + ".json")
    path.write_bytes(canonical(manifest))
    return path


class OutputBoundaryReviewTests(unittest.TestCase):
    def test_overflowed_json_number_is_rejected_as_nonfinite(self):
        for encoded in (b'{"x":1e999}', b'{"x":-1e999}'):
            with self.subTest(encoded=encoded), self.assertRaises(InputError):
                read_json(encoded)

    def test_input_lexical_symlink_before_dotdot_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = write_input(root)
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(InputError):
                load_inputs(root / "alias" / ".." / manifest.name)

    def test_output_lexical_symlink_before_dotdot_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = write_input(root)
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(InputError):
                load_inputs(manifest, root / "alias" / ".." / "packet")
            self.assertFalse((root / "packet").exists())

    def test_stage_name_collision_never_deletes_an_unowned_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            collision = root / ".sqlitefolio-stage-review-collision"
            collision.mkdir()
            sentinel = collision / "previous-owner.txt"
            sentinel.write_bytes(b"keep existing bytes")
            with patch("sqlitefolio.publish.secrets.token_hex", return_value="review-collision"):
                with self.assertRaises(PublicationError) as raised:
                    publish({"packet.json": b"{}"}, root / "packet")
            self.assertFalse(raised.exception.published)
            self.assertTrue(sentinel.is_file(), "cleanup removed a stage this call did not create")
            self.assertEqual(sentinel.read_bytes(), b"keep existing bytes")
            self.assertFalse((root / "packet").exists())

    def test_partial_successful_writes_are_retried_to_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original_write = os.write

            def short_write(fd, data):
                return original_write(fd, data[:3])

            with patch("sqlitefolio.publish.os.write", side_effect=short_write):
                publish({"packet.json": b"0123456789abcdef"}, root / "packet")
            self.assertEqual((root / "packet" / "packet.json").read_bytes(), b"0123456789abcdef")

    def test_coordinator_refuses_accumulation_before_next_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest_path = write_input(root)
            manifest = json.loads(manifest_path.read_bytes())
            manifest["candidates"].append({"name": "second", "sql": "migration.sql"})
            manifest_path.write_bytes(canonical(manifest))
            with patch("sqlitefolio.packet.MAX_PACKET", 1024), patch("sqlitefolio.packet.run_candidate", wraps=run_candidate) as run:
                with self.assertRaises(CapacityError):
                    rehearse(manifest_path, root / "packet", trust_input=True)
            self.assertEqual(run.call_count, 1)
            self.assertFalse((root / "packet").exists())

    def test_report_capacity_failure_does_not_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = write_input(root)
            with patch("sqlitefolio.report.MAX_HTML", 1024):
                with self.assertRaises(CapacityError):
                    rehearse(manifest, root / "packet", trust_input=True)
            self.assertFalse((root / "packet").exists())

    def test_verifier_refuses_oversized_packet_before_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            packet = root / "packet.json"
            with packet.open("wb") as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
            with patch("sqlitefolio.verify.os.read", side_effect=AssertionError("oversized file was read")) as read:
                result = verify_packet(root)
            self.assertFalse(result["valid"])
            self.assertEqual(result["exit_code"], 6)
            read.assert_not_called()

    def test_inspect_does_not_emit_report_replaced_after_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = write_input(root)
            output = root / "packet"
            self.assertEqual(rehearse(manifest, output, trust_input=True)["verification"]["exit_code"], 0)
            replacement = "THIS REPORT WAS NEVER VERIFIED\n"

            def verify_then_replace(path):
                result = verify_packet(path)
                self.assertTrue(result["valid"])
                (output / "report.html").write_text(replacement, encoding="utf-8")
                return result

            emitted = []

            def capture(value, *, raw=False, fd=1):
                if fd == 1:
                    emitted.append(value)

            with patch("sqlitefolio.verify.verify_packet", side_effect=verify_then_replace), patch("sqlitefolio.cli.emit", side_effect=capture):
                cli.main(["inspect", str(output)])
            self.assertNotIn(replacement, emitted, "inspect reread and emitted unverified replacement bytes")

    def test_replay_does_not_use_binding_replaced_after_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first_manifest = write_input(root, sql="SELECT 1;", filename="first.sql")
            second_manifest = write_input(root, sql="SELECT 2;", filename="second.sql")
            output = root / "packet"
            self.assertEqual(rehearse(first_manifest, output, trust_input=True)["verification"]["exit_code"], 0)

            def verify_then_replace_binding(path):
                result = verify_packet(path)
                self.assertTrue(result["valid"])
                packet_path = output / "packet.json"
                packet = json.loads(packet_path.read_bytes())
                packet["files"]["inputs/candidate-1.sql"] = sha(b"SELECT 2;")
                packet_path.write_bytes(canonical(packet))
                return result

            with patch("sqlitefolio.verify.verify_packet", side_effect=verify_then_replace_binding):
                result = replay(output, second_manifest, trust_input=True)
            self.assertFalse(result["matched"], "replay trusted a new binding not checked against retained SQL")
            self.assertFalse(verify_packet(output)["valid"])

    def test_incomplete_report_does_not_call_its_raw_snapshots_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = write_input(root, limits={"rows_per_query": 1})
            output = root / "packet"
            result = rehearse(manifest, output, trust_input=True)
            self.assertEqual(result["verification"]["exit_code"], 4)
            text = (output / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("Underlying complete raw facts and diagnostics", text)


if __name__ == "__main__":
    unittest.main()
