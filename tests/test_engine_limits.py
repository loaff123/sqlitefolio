"""Boundary and native supervisor regressions; production behavior is exercised."""
import base64
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from test_engine import LIMITS, request, query
from sqlitefolio.runner import run_candidate
from sqlitefolio.values import canonical_bytes, validate_cell


class EngineLimitContract(unittest.TestCase):
    def test_runtime_profile_closes_its_native_connection(self):
        from sqlitefolio.runner import runtime_profile
        connection = sqlite3.connect(":memory:")
        try:
            with mock.patch("sqlitefolio.runner.sqlite3.connect", return_value=connection):
                profile = runtime_profile()
            self.assertEqual("3.53.1", profile["sqlite"])
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        finally:
            connection.close()

    def test_exact_row_and_value_boundaries_succeed(self):
        result = run_candidate(request(seed="INSERT INTO t VALUES(zeroblob(65536));", rows_per_query=1))
        self.assertEqual("completed", result["execution"]["status"])
        self.assertTrue(result["reopened"]["complete"])
        self.assertEqual(65536, len(base64.b64decode(result["before"]["tables"]["t"]["rows"][0][0][1])))

    def test_wide_without_rowid_expression_index_metadata_is_complete(self):
        columns = ["c%d" % i for i in range(256)]
        schema = "CREATE TABLE t(" + ",".join(col + " INTEGER" for col in columns) + ",PRIMARY KEY(" + ",".join(columns[:128]) + ")) WITHOUT ROWID;"
        schema += "CREATE INDEX ix ON t(" + ",".join(col + "+1" for col in columns) + ");"
        result = run_candidate(request(schema=schema, seed=""))
        self.assertEqual("completed", result["execution"]["status"], result["execution"])
        self.assertTrue(result["before"]["complete"])
        index = next(index for index in result["before"]["tables"]["t"]["indexes"] if index["info"][1] == "ix")
        self.assertEqual(384, len(index["columns"]))

    def test_table_limit_prevents_complete_baseline(self):
        schema = ";".join("CREATE TABLE t%d(x)" % i for i in range(129))
        result = run_candidate(request(schema=schema, seed=""))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertFalse(result["before"]["complete"])

    def test_schema_object_limit_prevents_complete_baseline(self):
        schema = "CREATE TABLE t(x);" + ";".join("CREATE INDEX i%d ON t(x)" % i for i in range(512))
        result = run_candidate(request(schema=schema, seed=""))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertFalse(result["before"]["complete"])

    def test_all_phase_total_row_cap_is_enforced(self):
        schema = ";".join("CREATE TABLE t%d(x)" % i for i in range(4))
        seed = "WITH RECURSIVE r(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM r WHERE x<10000) INSERT INTO t0 SELECT x FROM r;" + ";".join("INSERT INTO t%d SELECT x FROM t0" % i for i in range(1,4))
        result = run_candidate(request(schema=schema, seed=seed))
        self.assertTrue(result["before"]["complete"])
        self.assertTrue(result["observed"]["complete"])
        self.assertFalse(result["reopened"]["complete"])
        self.assertEqual("resource_limit", result["execution"]["status"])

    def test_parent_wall_deadline_is_observed_and_workspace_cleaned(self):
        with tempfile.TemporaryDirectory() as owned_parent:
            with mock.patch.object(tempfile, "tempdir", owned_parent):
                start = time.monotonic()
                result = run_candidate(request(sql="SELECT randomblob(30000000);" * 200, wall_seconds=1))
                elapsed = time.monotonic() - start
            self.assertEqual("resource_limit", result["execution"]["status"], result["execution"])
            self.assertLess(elapsed, 4)
            self.assertEqual([], list(Path(owned_parent).iterdir()))

    def test_source_schema_is_guarded_before_application_statements(self):
        result = run_candidate(request(schema="ATTACH ':memory:' AS forbidden; CREATE TABLE t(x);", seed=""))
        self.assertEqual("unsupported", result["execution"]["status"])
        self.assertIsNone(result["before"])

    def test_all_required_failure_query_frames_survive_small_budget(self):
        inv = [query("query_%02d_" % i + "x"*50, "SELECT zeroblob(4000)") for i in range(64)]
        result = run_candidate(request(invariants=inv, evidence_bytes=4096))
        self.assertEqual(64, len(result["queries"]))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertLess(len(canonical_bytes(result)), 20*1024*1024)
        for phases in result["queries"].values():
            self.assertEqual({"before", "observed", "reopened"}, set(phases))

    def test_unexpected_death_is_worker_error_not_guessed_resource(self):
        real_popen = subprocess.Popen
        def launch_then_kill(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            process.kill()
            return process
        with mock.patch("sqlitefolio.runner.subprocess.Popen", side_effect=launch_then_kill):
            result = run_candidate(request())
        self.assertEqual("worker_error", result["execution"]["status"])

    def test_nested_protocol_corruption_is_worker_error(self):
        real_popen = subprocess.Popen
        def launch_then_corrupt(command, **kwargs):
            process = real_popen(command, **kwargs)
            process.wait()
            response = Path(command[4])
            data = json.loads(response.read_text())
            data["execution"] = {"status": "completed"}
            response.write_text(json.dumps(data))
            return process
        with mock.patch("sqlitefolio.runner.subprocess.Popen", side_effect=launch_then_corrupt):
            result = run_candidate(request())
        self.assertEqual("worker_error", result["execution"]["status"])

    def test_native_process_resource_limits_are_installed(self):
        import os
        import sys
        code = "from sqlitefolio.worker import process_limits; import resource,json; process_limits(); print(json.dumps([resource.getrlimit(x) for x in [resource.RLIMIT_AS,resource.RLIMIT_FSIZE,resource.RLIMIT_CPU,resource.RLIMIT_NOFILE,resource.RLIMIT_CORE]]))"
        result = subprocess.run([sys.executable, "-c", code], check=True, stdout=subprocess.PIPE, text=True)
        self.assertEqual([[536870912]*2, [33554432]*2, [35]*2, [64]*2, [0]*2], json.loads(result.stdout))

    def test_typed_value_validation_rejects_ambiguous_encodings(self):
        for cell in [["integer", "01"], ["integer", "-0"], ["integer", str(1<<63)], ["real", "7ff8000000000000"], ["blob", "AB=="], ["text", "***"], ["null", False], ["other", "x"]]:
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                validate_cell(cell)

    def test_source_sidecar_or_hardlink_is_refused(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE t(x)")
            req = request()
            req["source"] = {"database": str(path)}
            sidecar = Path(str(path) + "-journal")
            sidecar.touch()
            self.assertEqual("unsupported", run_candidate(req)["execution"]["status"])
            sidecar.unlink()
            os.link(path, Path(directory)/"alias.db")
            self.assertEqual("unsupported", run_candidate(req)["execution"]["status"])


if __name__ == "__main__":
    unittest.main()
