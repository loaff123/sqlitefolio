"""Independent facts oracle for trusted original fixtures, using only stdlib sqlite3.

Never imports the assessed prototype. Scripts execute as written on a disposable
backup with executescript(), with no injected BEGIN/COMMIT/ROLLBACK. Facts are read
before cleanup; a second connection establishes committed visibility. Finally
closing a connection naturally rolls back any still-open migration transaction.
This is a trusted-fixture runner, not a security sandbox for untrusted SQL.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


def query(connection, sql):
    try:
        return {"rows": [list(row) for row in connection.execute(sql).fetchall()]}
    except sqlite3.Error as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def facts(connection):
    objects = query(connection, "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")["rows"]
    result = {"objects": objects, "tables": {}, "views": {},
              "foreign_key_check": query(connection, "PRAGMA foreign_key_check"),
              "integrity_check": query(connection, "PRAGMA integrity_check")}
    for kind, name, _, _ in objects:
        if kind == "table":
            columns = query(connection, "PRAGMA table_info(" + quoted(name) + ")")
            rows = query(connection, "SELECT * FROM " + quoted(name))
            if "rows" in rows:
                rows["rows"].sort(key=lambda row: json.dumps(row, sort_keys=True))
            result["tables"][name] = {"columns": columns, **rows}
        elif kind == "view":
            result["views"][name] = query(connection, "SELECT * FROM " + quoted(name) + " LIMIT 0")
    return result


def run_case(path, predicted):
    case = json.loads(path.read_text())
    with tempfile.TemporaryDirectory(prefix="migration-oracle-") as temp:
        source_path = Path(temp) / "source.sqlite"
        target_path = Path(temp) / "rehearsal.sqlite"
        source = sqlite3.connect(source_path)
        source.execute(f"PRAGMA foreign_keys={int(case['foreign_keys'])}")
        source.executescript(case["schema_sql"])
        source.executescript(case["seed_sql"])
        source.commit()
        before = facts(source)
        target = sqlite3.connect(target_path)
        source.backup(target)
        source.close()
        target.execute(f"PRAGMA foreign_keys={int(case['foreign_keys'])}")
        error = None
        try:
            target.executescript(case["migration_sql"])
        except sqlite3.Error as exc:
            error = {"message": str(exc), "type": type(exc).__name__}
        in_transaction = target.in_transaction
        status = "error" if error else "open_transaction" if in_transaction else "applied"
        result = {"id": case["id"], "status": status, "error": error,
                  "in_transaction": in_transaction,
                  "foreign_keys_end": bool(target.execute("PRAGMA foreign_keys").fetchone()[0]),
                  "before": before, "live": facts(target), "checks": []}
        for check in case["checks"]:
            observed = query(target, check["sql"])
            result["checks"].append({"name": check["name"], **observed,
                                     "passed": observed.get("rows") == check["expect_rows"] and "error" not in observed})
        persisted = sqlite3.connect(target_path)
        result["persisted"] = facts(persisted)
        result["observations"] = [{"name": item["name"], "connection": item["connection"],
            **query(target if item["connection"] == "live" else persisted, item["sql"])}
            for item in predicted["observations"]]
        persisted.close()
        target.close()
        final = sqlite3.connect(target_path)
        result["after_close"] = facts(final)
        final.close()
        result["fixture_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result


def compare(actual, expected):
    failures = []
    for key in ("status", "in_transaction", "foreign_keys_end"):
        if actual[key] != expected[key]:
            failures.append(f"{key}: {actual[key]!r} != {expected[key]!r}")
    if expected["error_contains"]:
        if expected["error_contains"] not in (actual["error"] or {}).get("message", ""):
            failures.append("Migration error did not match prediction")
    elif actual["error"]:
        failures.append("Unexpected migration error")
    for got, want in zip(actual["checks"], expected["check_outcomes"], strict=True):
        if got["name"] != want["name"] or got["passed"] != want["passed"]:
            failures.append("Check verdict mismatch: " + want["name"])
        if "actual_rows" in want and got.get("rows") != want["actual_rows"]:
            failures.append("Check rows mismatch: " + want["name"])
        if "error_contains" in want and want["error_contains"] not in got.get("error", ""):
            failures.append("Check error mismatch: " + want["name"])
    for got, want in zip(actual["observations"], expected["observations"], strict=True):
        if "expect_rows" in want and got.get("rows") != want["expect_rows"]:
            failures.append("Observation rows mismatch: " + want["name"])
        if "error_contains" in want and want["error_contains"] not in got.get("error", ""):
            failures.append("Observation error mismatch: " + want["name"])
    if actual["persisted"] != actual["after_close"]:
        failures.append("Closing the live connection changed committed facts")
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads((args.fixtures / "expected.json").read_text())["cases"]
    results = []
    for case_id, predicted in expected.items():
        result = run_case(args.fixtures / (case_id + ".json"), predicted)
        result["prediction_failures"] = compare(result, predicted)
        results.append(result)
        print(case_id, result["status"], "transaction_open=" + str(result["in_transaction"]), "prediction_failures=" + str(result["prediction_failures"]))
    report = {"sqlite_version": sqlite3.sqlite_version, "case_count": len(results),
              "all_predictions_match": all(not result["prediction_failures"] for result in results),
              "cases": results}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if report["all_predictions_match"] else 1)


if __name__ == "__main__":
    main()
