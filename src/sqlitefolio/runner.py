"""Fixed worker supervision with bounded request, response, wall time and cleanup."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

from .guardrails import DEFAULT_LIMITS, Unsupported, error_fact, not_run_error
from .values import canonical_bytes, validate_cell

MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def runtime_profile():
    with closing(sqlite3.connect(":memory:")) as conn:
        source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
        options = sorted(row[0] for row in conn.execute("PRAGMA compile_options"))
    return {"python": platform.python_version(), "python_build": sys.version, "implementation": platform.python_implementation(), "machine": platform.machine(), "sqlite": sqlite3.sqlite_version, "sqlite_source_id": source_id, "compile_options": options, "platform": sys.platform}


def runtime_supported():
    return platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 12) and sqlite3.sqlite_version == "3.53.1" and platform.system() == "Linux" and platform.machine() == "x86_64"


def validate_request(request):
    if type(request) is not dict or set(request) != {"format", "source", "candidate", "profile", "invariants", "limits"}:
        raise ValueError("Worker request keys do not match protocol")
    if request["format"] != "sqlitefolio.worker.v1":
        raise ValueError("Unsupported worker protocol")
    profile = request["profile"]
    if type(profile) is not dict or set(profile) != {"foreign_keys", "transaction_mode"} or type(profile["foreign_keys"]) is not bool or profile["transaction_mode"] != "autocommit":
        raise ValueError("Invalid worker execution profile")
    limits = request["limits"]
    if type(limits) is not dict or set(limits) != set(DEFAULT_LIMITS):
        raise ValueError("Worker limits must be normalized")
    minimum = {"rows_per_query": 1, "vm_steps": 1000, "wall_seconds": 1, "evidence_bytes": 4096}
    for key, value in limits.items():
        if type(value) is not int or not minimum[key] <= value <= DEFAULT_LIMITS[key]:
            raise ValueError("Invalid worker limit: " + key)
    if limits["vm_steps"] % 1000:
        raise ValueError("VM budget must be a multiple of 1000")
    candidate = request["candidate"]
    if type(candidate) is not dict or set(candidate) != {"name", "sql"} or type(candidate["name"]) is not str or not 1 <= len(candidate["name"].encode("utf-8")) <= 128:
        raise ValueError("Invalid worker candidate")
    if type(candidate["sql"]) is not str or len(candidate["sql"].encode("utf-8")) > 1024 * 1024:
        raise ValueError("Candidate SQL exceeds worker limit")
    source = request["source"]
    if type(source) is not dict or set(source) not in ({"database"}, {"schema_sql", "seed_sql"}):
        raise ValueError("Invalid worker source")
    for key, value in source.items():
        if type(value) is not str or "\x00" in value or len(value.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Invalid source SQL/path")
    invariants = request["invariants"]
    if type(invariants) is not list or len(invariants) > 64:
        raise ValueError("Invalid invariant list")
    names = set()
    for inv in invariants:
        if type(inv) is not dict or type(inv.get("name")) is not str or not 1 <= len(inv["name"].encode("utf-8")) <= 128 or inv["name"] in names:
            raise ValueError("Invalid invariant identity")
        names.add(inv["name"])
        if inv.get("kind") in ("query_equals", "query_preserved") and (type(inv.get("sql")) is not str or len(inv["sql"].encode("utf-8")) > 65536):
            raise ValueError("Invalid invariant query SQL")
    encoded = canonical_bytes(request)
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("Worker request exceeds 32 MiB")
    return encoded


def empty_observation(request, runtime=None):
    queries = {}
    for inv in request["invariants"]:
        if inv["kind"] in ("query_equals", "query_preserved"):
            queries[inv["name"]] = {phase: {"rows": None, "error": not_run_error(), "complete": False} for phase in ("before", "observed", "reopened")}
    return {"name": request["candidate"]["name"], "runtime": runtime or runtime_profile(), "profile": request["profile"],
            "execution": {"status": "worker_error", "error": None, "in_transaction": None, "foreign_keys_start": None, "foreign_keys_end": None},
            "before": None, "observed": None, "reopened": None, "queries": queries, "source_preserved": True}


def _failure(request, kind, message):
    result = empty_observation(request)
    result["execution"]["status"] = kind
    result["execution"]["error"] = {"kind": kind, "message": message, "sqlite_code": None, "sqlite_name": None}
    return result


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate worker response key")
        out[key] = value
    return out


def _validate_response(value, request):
    """Check protocol framing/types before observations leave supervision."""
    def exact(obj, keys):
        if type(obj) is not dict or set(obj) != set(keys):
            raise ValueError("Worker response field shape mismatch")
    def flag(value, nullable=False):
        if type(value) is not bool and not (nullable and value is None):
            raise ValueError("Worker response boolean mismatch")
    def error(value):
        if value is None:
            return
        exact(value, ("kind", "message", "sqlite_code", "sqlite_name"))
        if type(value["kind"]) is not str or type(value["message"]) is not str:
            raise ValueError("Worker response error text mismatch")
        if value["sqlite_code"] is not None and type(value["sqlite_code"]) is not int:
            raise ValueError("Worker response SQLite code mismatch")
        if value["sqlite_name"] is not None and type(value["sqlite_name"]) is not str:
            raise ValueError("Worker response SQLite name mismatch")
    def rows(value, width=None):
        if type(value) is not list or len(value) > request["limits"]["rows_per_query"]:
            raise ValueError("Worker response row bounds mismatch")
        widths = set()
        for row in value:
            if type(row) is not list or len(row) > 256 or (width is not None and len(row) != width):
                raise ValueError("Worker response row width mismatch")
            widths.add(len(row))
            for cell in row:
                validate_cell(cell)
        if len(widths) > 1:
            raise ValueError("Worker response ragged rows")
    def native_rows(value, width=None):
        if type(value) is not list or len(value) > 10000:
            raise ValueError("Worker response metadata bounds mismatch")
        for row in value:
            if type(row) is not list or (width is not None and len(row) != width) or any(item is not None and type(item) not in (str,int) for item in row):
                raise ValueError("Worker response native metadata mismatch")
    exact(value, ("name", "runtime", "profile", "execution", "before", "observed", "reopened", "queries", "source_preserved"))
    if value["name"] != request["candidate"]["name"] or value["profile"] != request["profile"]:
        raise ValueError("Worker response identity mismatch")
    exact(value["runtime"], ("python", "python_build", "implementation", "machine", "sqlite", "sqlite_source_id", "compile_options", "platform"))
    if any(type(value["runtime"][key]) is not str for key in ("python", "python_build", "implementation", "machine", "sqlite", "sqlite_source_id", "platform")) or type(value["runtime"]["compile_options"]) is not list or any(type(option) is not str for option in value["runtime"]["compile_options"]):
        raise ValueError("Worker response runtime mismatch")
    execution = value["execution"]
    exact(execution, ("status", "error", "in_transaction", "foreign_keys_start", "foreign_keys_end"))
    if execution["status"] not in ("completed", "sqlite_error", "open_transaction", "resource_limit", "unsupported", "worker_error", "source_changed"):
        raise ValueError("Unknown worker execution status")
    error(execution["error"])
    for key in ("in_transaction", "foreign_keys_start", "foreign_keys_end"):
        flag(execution[key], nullable=True)
    flag(value["source_preserved"])
    expected_queries = {inv["name"] for inv in request["invariants"] if inv["kind"] in ("query_equals", "query_preserved")}
    exact(value["queries"], expected_queries)
    for phases in value["queries"].values():
        exact(phases, ("before", "observed", "reopened"))
        for fact in phases.values():
            exact(fact, ("rows", "error", "complete"))
            flag(fact["complete"]); error(fact["error"])
            if fact["rows"] is not None:
                rows(fact["rows"])
            if fact["complete"] and (fact["rows"] is None or fact["error"] is not None):
                raise ValueError("Worker response false complete query")
            if not fact["complete"] and fact["error"] is None:
                raise ValueError("Worker response incomplete query lacks error")
    for phase in ("before", "observed", "reopened"):
        snap = value[phase]
        if snap is None:
            continue
        exact(snap, ("complete", "error", "objects", "tables", "views", "diagnostics", "sequences"))
        flag(snap["complete"]); error(snap["error"])
        if snap["complete"] != (snap["error"] is None):
            raise ValueError("Worker snapshot completeness/error mismatch")
        if type(snap["objects"]) is not list or len(snap["objects"]) > 512:
            raise ValueError("Worker response schema object bounds mismatch")
        for obj in snap["objects"]:
            exact(obj, ("type", "name", "table", "sql"))
            if obj["type"] not in ("table", "index", "trigger", "view") or any(type(obj[key]) is not str for key in ("name", "table")) or (obj["sql"] is not None and type(obj["sql"]) is not str):
                raise ValueError("Worker response object metadata mismatch")
        if type(snap["tables"]) is not dict or len(snap["tables"]) > 128:
            raise ValueError("Worker response table bounds mismatch")
        for name, table in snap["tables"].items():
            exact(table, ("columns", "column_info", "table_info", "foreign_keys", "indexes", "rows"))
            if type(table["columns"]) is not list or len(table["columns"]) > 256 or any(type(col) is not str for col in table["columns"]):
                raise ValueError("Worker response columns mismatch")
            native_rows(table["column_info"], 7); native_rows([table["table_info"]], 6); native_rows(table["foreign_keys"], 8)
            if type(table["indexes"]) is not list:
                raise ValueError("Worker response index shape mismatch")
            for index in table["indexes"]:
                exact(index, ("info", "columns"))
                native_rows([index["info"]], 5); native_rows(index["columns"], 6)
            rows(table["rows"], len(table["columns"]))
        if type(snap["views"]) is not dict:
            raise ValueError("Worker response view shape mismatch")
        for view in snap["views"].values():
            exact(view, ("ok", "error")); flag(view["ok"]); error(view["error"])
        if snap["diagnostics"] or snap["complete"]:
            exact(snap["diagnostics"], ("foreign_keys", "foreign_key_check", "integrity_check"))
            flag(snap["diagnostics"]["foreign_keys"])
            for key in ("foreign_key_check", "integrity_check"):
                exact(snap["diagnostics"][key], ("rows", "error"))
                error(snap["diagnostics"][key]["error"]); rows(snap["diagnostics"][key]["rows"])
        elif type(snap["diagnostics"]) is not dict:
            raise ValueError("Worker response partial diagnostic shape mismatch")
        rows(snap["sequences"], 2)
    return value


def run_candidate(request):
    """Run one candidate sequentially in a fixed, disposable, bounded worker."""
    encoded = validate_request(request)
    if not runtime_supported():
        return _failure(request, "unsupported", "Runtime is outside qualified Linux x86_64 CPython 3.12 SQLite 3.53.1 profile")
    with tempfile.TemporaryDirectory(prefix="sqlitefolio-worker-") as workspace:
        root = Path(workspace)
        request_path, response_path = root / "request.json", root / "response.json"
        request_path.write_bytes(encoded)
        command = [sys.executable, "-I", str(Path(__file__).with_name("worker.py")), str(request_path), str(response_path), str(root / "disposable.db")]
        process = None
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=root, start_new_session=True, close_fds=True)
            try:
                process.wait(timeout=request["limits"]["wall_seconds"])
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                return _failure(request, "resource_limit", "Parent-enforced worker wall deadline exhausted")
            if process.returncode != 0:
                return _failure(request, "worker_error", "Worker exited unexpectedly (return code " + str(process.returncode) + ")")
            with response_path.open("rb") as stream:
                data = stream.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return _failure(request, "worker_error", "Worker response exceeded protocol ceiling")
            result = json.loads(data, object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite worker JSON")))
            return _validate_response(result, request)
        except (OSError, ValueError, TypeError) as exc:
            return _failure(request, "worker_error", "Worker protocol/I/O failure: " + str(exc))
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
