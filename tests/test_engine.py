"""Engine contract tests authored before implementation from frozen protocol v1."""
import base64
import copy
import importlib.util
import os
import platform
import sys
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

LIMITS = {"rows_per_query": 10000, "vm_steps": 2000000, "wall_seconds": 30, "evidence_bytes": 16777216}


def request(schema="CREATE TABLE t(x);", seed="INSERT INTO t VALUES(1);", sql="", invariants=None, **limits):
    return {"format": "sqlitefolio.worker.v1", "source": {"schema_sql": schema, "seed_sql": seed},
            "candidate": {"name": "candidate", "sql": sql}, "profile": {"foreign_keys": True, "transaction_mode": "autocommit"},
            "invariants": invariants or [], "limits": {**LIMITS, **limits}}


def text(value):
    return ["text", base64.b64encode(value.encode()).decode()]


def query(name, sql):
    return {"name": name, "kind": "query_preserved", "sql": sql, "order": "ordered"}


class EngineContract(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("sqlitefolio.runner"), "bounded engine runner must exist")
        from sqlitefolio.runner import run_candidate
        self.run_candidate = run_candidate

    def test_qualified_runtime_is_recorded_by_worker(self):
        result = self.run_candidate(request())
        self.assertEqual("completed", result["execution"]["status"], result)
        self.assertEqual("3.53.1", result["runtime"]["sqlite"])
        self.assertEqual("linux", result["runtime"]["platform"])
        self.assertTrue(result["runtime"]["python"].startswith("3.12."))
        self.assertTrue(result["runtime"]["sqlite_source_id"])
        self.assertTrue(result["runtime"]["compile_options"])
        self.assertEqual({"name", "runtime", "profile", "execution", "before", "observed", "reopened", "queries", "source_preserved"}, set(result))

    def test_worker_records_exact_python_build_implementation_and_machine(self):
        result = self.run_candidate(request())
        self.assertEqual(sys.version, result["runtime"].get("python_build"))
        self.assertEqual(platform.python_implementation(), result["runtime"].get("implementation"))
        self.assertEqual(platform.machine(), result["runtime"].get("machine"))

    def test_as_written_partial_autocommit_survives_sqlite_error(self):
        result = self.run_candidate(request(sql="INSERT INTO t VALUES(2); INSERT INTO absent VALUES(3); INSERT INTO t VALUES(4);"))
        self.assertEqual("sqlite_error", result["execution"]["status"])
        self.assertFalse(result["execution"]["in_transaction"])
        self.assertEqual([[['integer', '1']], [['integer', '2']]], result["reopened"]["tables"]["t"]["rows"])
        self.assertEqual("SQLITE_ERROR", result["execution"]["error"]["sqlite_name"])

    def test_open_transaction_observed_and_reopened_are_distinct(self):
        result = self.run_candidate(request(sql="BEGIN; INSERT INTO t VALUES(2);"))
        self.assertEqual("open_transaction", result["execution"]["status"])
        self.assertIsNone(result["execution"]["error"])
        self.assertTrue(result["execution"]["in_transaction"])
        self.assertEqual(2, len(result["observed"]["tables"]["t"]["rows"]))
        self.assertEqual(1, len(result["reopened"]["tables"]["t"]["rows"]))

    def test_sqlite_error_has_priority_over_open_transaction(self):
        result = self.run_candidate(request(sql="BEGIN; INSERT INTO t VALUES(2); INSERT INTO absent VALUES(3);"))
        self.assertEqual("sqlite_error", result["execution"]["status"])
        self.assertTrue(result["execution"]["in_transaction"])
        self.assertEqual(2, len(result["observed"]["tables"]["t"]["rows"]))
        self.assertEqual(1, len(result["reopened"]["tables"]["t"]["rows"]))

    def test_foreign_key_profile_and_mode_changes_are_observed(self):
        result = self.run_candidate(request(schema="CREATE TABLE p(id PRIMARY KEY); CREATE TABLE t(x REFERENCES p(id));", seed="", sql="PRAGMA foreign_keys=OFF; INSERT INTO t VALUES(99);"))
        self.assertTrue(result["execution"]["foreign_keys_start"])
        self.assertFalse(result["execution"]["foreign_keys_end"])
        self.assertFalse(result["observed"]["diagnostics"]["foreign_keys"])
        self.assertTrue(result["reopened"]["diagnostics"]["foreign_keys"])
        self.assertEqual(1, len(result["reopened"]["diagnostics"]["foreign_key_check"]["rows"]))

    def test_source_scripts_cannot_leave_transactions_open(self):
        for source in [request(schema="BEGIN; CREATE TABLE t(x);", seed=""), request(seed="BEGIN; INSERT INTO t VALUES(2);")]:
            with self.subTest(source=source["source"]):
                result = self.run_candidate(source)
                self.assertEqual("unsupported", result["execution"]["status"])
                self.assertIsNone(result["before"])

    def test_typed_values_duplicates_invalid_utf8_and_infinities_are_lossless(self):
        result = self.run_candidate(request(schema="CREATE TABLE t(a,b,c,d,e,f,g,h);", seed="INSERT INTO t VALUES(NULL,9223372036854775807,-9223372036854775808,1.5,CAST(X'80FF' AS TEXT),X'00FF',1e999,-1e999); INSERT INTO t SELECT * FROM t;"))
        row = [["null", None], ["integer", "9223372036854775807"], ["integer", "-9223372036854775808"], ["real", struct.pack(">d", 1.5).hex()], ["text", "gP8="], ["blob", "AP8="], ["real", "7ff0000000000000"], ["real", "fff0000000000000"]]
        self.assertEqual([row, row], result["before"]["tables"]["t"]["rows"])

    def test_complete_structured_schema_generated_columns_indexes_views_sequences(self):
        schema = """CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT CHECK(length(a)>0), b TEXT GENERATED ALWAYS AS (upper(a)) STORED, c TEXT GENERATED ALWAYS AS (lower(a)) VIRTUAL);
        CREATE INDEX ix ON t(length(a),a DESC) WHERE a != 'z';
        CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END;
        CREATE VIEW good AS SELECT b FROM t; CREATE VIEW bad AS SELECT missing FROM t;
        CREATE TABLE wr(a INTEGER,b TEXT,PRIMARY KEY(a,b)) WITHOUT ROWID;"""
        result = self.run_candidate(request(schema=schema, seed="INSERT INTO t(a) VALUES('AbC');"))
        snap = result["before"]
        self.assertTrue(snap["complete"], snap)
        self.assertEqual(["id", "a", "b", "c"], snap["tables"]["t"]["columns"])
        self.assertEqual([0, 0, 3, 2], [x[6] for x in snap["tables"]["t"]["column_info"]])
        self.assertEqual(1, snap["tables"]["wr"]["table_info"][4])
        self.assertEqual(-2, snap["tables"]["t"]["indexes"][0]["columns"][0][1])
        self.assertEqual([[text("t"), ["integer", "1"]]], snap["sequences"])
        self.assertTrue(snap["views"]["good"]["ok"])
        self.assertFalse(snap["views"]["bad"]["ok"])
        self.assertIsNotNone(snap["views"]["bad"]["error"])
        self.assertIn("sqlite_sequence", snap["tables"])
        self.assertEqual([[text("ok")]], snap["diagnostics"]["integrity_check"]["rows"])

    def test_query_facts_capture_all_three_phases_and_order(self):
        result = self.run_candidate(request(seed="INSERT INTO t VALUES(2),(1),(2);", sql="DELETE FROM t WHERE x=1;", invariants=[query("q", "SELECT x,typeof(x) FROM t ORDER BY rowid")]))
        phases = result["queries"]["q"]
        self.assertEqual([[["integer", "2"], text("integer")], [["integer", "1"], text("integer")], [["integer", "2"], text("integer")]], phases["before"]["rows"])
        self.assertEqual(2, len(phases["observed"]["rows"]))
        self.assertEqual(phases["observed"], phases["reopened"])
        self.assertTrue(all(x["complete"] for x in phases.values()))

    def test_failed_query_is_explicit_in_each_phase(self):
        result = self.run_candidate(request(invariants=[query("q", "SELECT missing FROM t")]))
        for fact in result["queries"]["q"].values():
            self.assertFalse(fact["complete"])
            self.assertIsNotNone(fact["error"])

    def test_invariant_dml_and_pragma_writes_cannot_mutate(self):
        for sql in ["DELETE FROM t RETURNING x", "PRAGMA foreign_keys=OFF", "CREATE TEMP TABLE intrusion(x)", "SELECT 1; SELECT 2"]:
            with self.subTest(sql=sql):
                result = self.run_candidate(request(invariants=[query("q", sql)]))
                self.assertFalse(result["queries"]["q"]["before"]["complete"])
                self.assertEqual([[["integer", "1"]]], result["reopened"]["tables"]["t"]["rows"])
                self.assertTrue(result["execution"]["foreign_keys_start"])

    def test_read_only_metadata_pragmas_are_supported(self):
        result = self.run_candidate(request(invariants=[query("q", "SELECT name FROM pragma_table_xinfo('t')"), query("p", "PRAGMA table_info(t)")]))
        self.assertEqual([[text("x")]], result["queries"]["q"]["before"]["rows"])
        self.assertTrue(result["queries"]["p"]["before"]["complete"])

    def test_forbidden_features_are_refused_before_side_effects(self):
        sqls = ["ATTACH ':memory:' AS other;", "CREATE VIRTUAL TABLE v USING fts5(a);", "CREATE TEMP TABLE v(a);", "PRAGMA writable_schema=ON;", "SELECT load_extension('anything');", "PRAGMA journal_mode=WAL;"]
        for sql in sqls:
            with self.subTest(sql=sql):
                result = self.run_candidate(request(sql=sql))
                self.assertEqual("unsupported", result["execution"]["status"], result)

    def test_vm_budget_is_resource_limit_not_sqlite_error(self):
        result = self.run_candidate(request(sql="WITH RECURSIVE r(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM r WHERE x<1000000) INSERT INTO t SELECT x FROM r;", vm_steps=1000))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertIn("VM", result["execution"]["error"]["message"])

    def test_row_limit_is_incomplete_not_empty_success(self):
        result = self.run_candidate(request(seed="INSERT INTO t VALUES(1),(2);", rows_per_query=1))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertFalse(result["before"]["complete"])
        self.assertIsNotNone(result["before"]["error"])

    def test_query_row_limit_is_incomplete(self):
        result = self.run_candidate(request(invariants=[query("q", "SELECT 1 UNION ALL SELECT 2")], rows_per_query=1))
        self.assertFalse(result["queries"]["q"]["before"]["complete"])
        self.assertEqual("resource_limit", result["queries"]["q"]["before"]["error"]["kind"])

    def test_value_size_is_checked_before_accumulating(self):
        result = self.run_candidate(request(seed="INSERT INTO t VALUES(zeroblob(65537));"))
        self.assertFalse(result["before"]["complete"])
        self.assertEqual("resource_limit", result["execution"]["status"])

    def test_evidence_budget_cannot_produce_complete_snapshot(self):
        result = self.run_candidate(request(seed="INSERT INTO t VALUES(zeroblob(4000));", evidence_bytes=4096))
        self.assertEqual("resource_limit", result["execution"]["status"])
        self.assertTrue(result["before"] is None or not result["before"]["complete"])

    def test_database_source_bytes_and_directory_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "captured.db"
            with sqlite3.connect(path) as conn:
                conn.executescript("CREATE TABLE t(x); INSERT INTO t VALUES(1);")
            original = path.read_bytes()
            req = request(sql="INSERT INTO t VALUES(2);")
            req["source"] = {"database": str(path)}
            result = self.run_candidate(req)
            self.assertEqual("completed", result["execution"]["status"], result)
            self.assertTrue(result["source_preserved"])
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(["captured.db"], sorted(p.name for p in Path(folder).iterdir()))
            self.assertEqual(2, len(result["reopened"]["tables"]["t"]["rows"]))

    def test_existing_virtual_source_is_unsupported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "captured.db"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE VIRTUAL TABLE v USING fts5(a)")
            req = request()
            req["source"] = {"database": str(path)}
            result = self.run_candidate(req)
            self.assertEqual("unsupported", result["execution"]["status"])
            self.assertFalse(result["before"]["complete"])

    def test_request_cannot_supply_worker_commands_or_callbacks(self):
        req = request()
        req["worker_command"] = ["echo", "unexpected"]
        with self.assertRaises(ValueError):
            self.run_candidate(req)

    def test_alter_table_rename_column_and_drop_preserve_supported_behavior(self):
        for sql, table, columns in [
            ("ALTER TABLE t RENAME TO u;", "u", ["x"]),
            ("ALTER TABLE t RENAME COLUMN x TO z;", "t", ["z"]),
            ("ALTER TABLE t ADD COLUMN y; ALTER TABLE t DROP COLUMN y;", "t", ["x"]),
        ]:
            with self.subTest(sql=sql):
                result = self.run_candidate(request(sql=sql))
                self.assertEqual("completed", result["execution"]["status"], result["execution"])
                self.assertEqual(columns, result["reopened"]["tables"][table]["columns"])

    def test_source_connection_pragma_changes_do_not_change_candidate_profile(self):
        result = self.run_candidate(request(seed="PRAGMA foreign_keys=OFF; INSERT INTO t VALUES(1);"))
        self.assertTrue(result["execution"]["foreign_keys_start"])
        self.assertTrue(result["before"]["diagnostics"]["foreign_keys"])
        self.assertTrue(result["observed"]["diagnostics"]["foreign_keys"])

    def test_native_migration_error_survives_lower_priority_snapshot_refusal(self):
        long_name = "z" * 129
        result = self.run_candidate(request(sql='CREATE TABLE "' + long_name + '"(x); INSERT INTO missing VALUES(1);'))
        self.assertEqual("sqlite_error", result["execution"]["status"], result["execution"])
        self.assertEqual("sqlite_error", result["execution"]["error"]["kind"])
        self.assertEqual("no such table: missing", result["execution"]["error"]["message"])
        self.assertFalse(result["observed"]["complete"])
        self.assertEqual("unsupported", result["observed"]["error"]["kind"])

    def test_same_source_has_identical_before_facts(self):
        first = self.run_candidate(request(sql="INSERT INTO t VALUES(2);"))
        second = self.run_candidate(request(sql="DELETE FROM t;"))
        self.assertEqual(first["before"], second["before"])


if __name__ == "__main__":
    unittest.main()
