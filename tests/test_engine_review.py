"""Independent review regressions using original, trusted benign SQL only."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from sqlitefolio.packet import rehearse
from sqlitefolio.runner import run_candidate
from sqlitefolio.summary import build_summary, exit_code
from sqlitefolio.values import encode_row
from sqlitefolio.worker import execute, _bounded_result
from sqlitefolio.contract import validate_manifest
from sqlitefolio.verify import reconstruct


LIMITS = {"rows_per_query": 1, "vm_steps": 2000000,
          "wall_seconds": 30, "evidence_bytes": 16777216}
QUERY = "SELECT x FROM t UNION ALL SELECT x FROM t WHERE x=1"


def request(kind="query_preserved", *, sql="UPDATE t SET x=2; BEGIN;"):
    invariant = {"name": "q", "kind": kind, "sql": QUERY, "order": "ordered"}
    if kind == "query_equals":
        invariant["expected"] = [[["integer", "2"]]]
    return {"format": "sqlitefolio.worker.v1",
            "source": {"schema_sql": "CREATE TABLE t(x);",
                       "seed_sql": "INSERT INTO t VALUES(1);"},
            "candidate": {"name": "candidate", "sql": sql},
            "profile": {"foreign_keys": True, "transaction_mode": "autocommit"},
            "invariants": [invariant], "limits": dict(LIMITS)}


class EngineIndependentReview(unittest.TestCase):
    def test_baseline_limit_cannot_leave_open_transaction_with_an_error(self):
        raw = run_candidate(request())
        self.assertTrue(raw["execution"]["in_transaction"])
        self.assertTrue(all(raw[p]["complete"] for p in ("before", "observed", "reopened")))
        self.assertEqual("resource_limit", raw["queries"]["q"]["before"]["error"]["kind"])
        self.assertTrue(raw["queries"]["q"]["observed"]["complete"])
        self.assertTrue(raw["queries"]["q"]["reopened"]["complete"])
        self.assertEqual("resource_limit", raw["execution"]["status"])
        self.assertEqual(raw["execution"]["status"], raw["execution"]["error"]["kind"])

    def test_optional_query_equals_baseline_limit_does_not_block_pass(self):
        req = request("query_equals", sql="UPDATE t SET x=2;")
        raw = run_candidate(req)
        summary = build_summary({"invariants": req["invariants"]}, [raw])[0]
        self.assertFalse(raw["queries"]["q"]["before"]["complete"])
        self.assertEqual("resource_limit", raw["queries"]["q"]["before"]["error"]["kind"])
        self.assertEqual([{"name": "q", "status": "pass"}], summary["observed_checks"])
        self.assertEqual([{"name": "q", "status": "pass"}], summary["reopened_checks"])
        self.assertEqual("completed", raw["execution"]["status"])
        self.assertEqual("pass", summary["status"])

    def test_unexpected_query_observation_failure_remains_internal_error(self):
        req = request(sql="")
        req["invariants"][0]["sql"] = "SELECT 777"

        def fail_one_query_row(row):
            if row == (777,):
                raise RuntimeError("synthetic observation failure")
            return encode_row(row)

        with tempfile.TemporaryDirectory() as directory, mock.patch(
                "sqlitefolio.snapshot.encode_row", side_effect=fail_one_query_row):
            raw = execute(req, Path(directory) / "disposable.db")
        self.assertTrue(all(raw[p]["complete"] for p in ("before", "observed", "reopened")))
        self.assertEqual("worker_error", raw["queries"]["q"]["observed"]["error"]["kind"])
        summary = build_summary({"invariants": req["invariants"]}, [raw])
        self.assertEqual("worker_error", raw["execution"]["status"])
        self.assertEqual("internal_error", summary[0]["status"])
        self.assertEqual(5, exit_code(summary))

    def test_internal_error_minimal_envelope_survives_lowered_evidence_cap(self):
        req = request(sql="")
        req["limits"]["evidence_bytes"] = 4096
        req["invariants"] = [{"name": "q%02d" % i, "kind": "query_preserved",
                              "sql": "SELECT 777", "order": "ordered"}
                             for i in range(64)]

        def fail_one_query_row(row):
            if row == (777,):
                raise RuntimeError("synthetic observation failure")
            return encode_row(row)

        with tempfile.TemporaryDirectory() as directory, mock.patch(
                "sqlitefolio.snapshot.encode_row", side_effect=fail_one_query_row):
            raw = json.loads(_bounded_result(execute(req, Path(directory) / "disposable.db"), req))
        self.assertEqual("worker_error", raw["execution"]["status"])
        self.assertTrue(all(raw[p] is None for p in ("before", "observed", "reopened")))
        self.assertGreater(len(json.dumps(raw)), 4096)
        manifest = validate_manifest({
            "format": "sqlitefolio.input.v1", "scenario": "review_boundary",
            "source": {"schema": "schema.sql", "seed": "seed.sql"},
            "candidates": [{"name": "candidate", "sql": "migration.sql"}],
            "profile": req["profile"], "invariants": req["invariants"],
            "limits": req["limits"]})
        summary = reconstruct(manifest, [raw])
        self.assertEqual("internal_error", summary[0]["status"])
        self.assertEqual(5, exit_code(summary))

    def test_baseline_limit_open_transaction_packet_verifies_as_nonpassing(self):
        req = request()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.sql").write_text(req["source"]["schema_sql"])
            (root / "seed.sql").write_text(req["source"]["seed_sql"])
            (root / "migration.sql").write_text(req["candidate"]["sql"])
            manifest = {"format": "sqlitefolio.input.v1", "scenario": "review_boundary",
                        "source": {"schema": "schema.sql", "seed": "seed.sql"},
                        "candidates": [{"name": "candidate", "sql": "migration.sql"}],
                        "profile": req["profile"], "invariants": req["invariants"],
                        "limits": req["limits"]}
            (root / "manifest.json").write_text(json.dumps(manifest))
            result = rehearse(root / "manifest.json", root / "review", trust_input=True)
            self.assertTrue(result["verification"]["valid"], result["verification"])
            self.assertEqual(4, result["verification"]["exit_code"])


if __name__ == "__main__":
    unittest.main()
