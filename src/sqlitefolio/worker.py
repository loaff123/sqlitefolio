"""Internal fixed subprocess entry point; never accepts custom code or commands."""
from __future__ import annotations

# Direct isolated execution also works from an installed wheel or extracted sdist.
if __package__ in (None, ""):
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import stat
import sys
import time

from sqlitefolio.guardrails import EvidenceBudget, Guard, MAX_DB_BYTES, ResourceLimit, Unsupported, error_fact
from sqlitefolio.runner import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, empty_observation, runtime_profile, runtime_supported, validate_request
from sqlitefolio.snapshot import observe, query_fact
from sqlitefolio.values import SQLiteText, canonical_bytes


def process_limits():
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DB_BYTES, MAX_DB_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _connection(path, request, deadline, *, readonly=False):
    target = Path(path).absolute().as_uri() + "?mode=ro&immutable=1" if readonly else str(path)
    conn = sqlite3.connect(target, uri=readonly, isolation_level=None, timeout=0)
    conn.text_factory = SQLiteText
    conn.enable_load_extension(False)
    # Fixed internal setup precedes any trusted schema/seed/candidate SQL.
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=" + ("ON" if request["profile"]["foreign_keys"] else "OFF"))
    if not readonly:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        conn.execute("PRAGMA max_page_count=" + str(MAX_DB_BYTES // page_size))
    return conn, Guard(conn, request["limits"], deadline)


def _source_identity(path):
    path = Path(path).absolute()
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise Unsupported("Symlink source path is unsupported")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Unsupported("Source must be a regular singly-linked capture")
    if info.st_size > 16 * 1024 * 1024:
        raise ResourceLimit("Source database exceeds 16 MiB")
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise Unsupported("Database sidecars are unsupported")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise Unsupported("Source capture changed while opening")
        data = bytearray()
        while chunk := os.read(fd, min(65536, 16 * 1024 * 1024 + 1 - len(data))):
            data.extend(chunk)
            if len(data) > 16 * 1024 * 1024:
                raise ResourceLimit("Source database exceeds 16 MiB")
        after = os.fstat(fd)
        if opened != after:
            # st_atime may change from the read; compare identity/content fields.
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise Unsupported("Source capture changed during read")
        if data[:16] != b"SQLite format 3\x00" or len(data) < 100:
            raise Unsupported("Source is not a SQLite database")
        if data[18] != 1 or data[19] != 1:
            raise Unsupported("Only DELETE-journal source databases are supported")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, hashlib.sha256(data).hexdigest())
    finally:
        os.close(fd)


def _set_failure(result, error, *, status=None):
    status = status or error["kind"]
    priority = {"completed": 0, "unsupported": 2, "open_transaction": 3,
                "sqlite_error": 3, "resource_limit": 4, "source_changed": 4,
                "worker_error": 5}
    current = result["execution"]
    # An untouched empty response uses worker_error as its unavailable sentinel.
    current_priority = (-1 if current["status"] == "worker_error" and current["error"] is None
                        else priority[current["status"]])
    if priority[status] < current_priority:
        return
    current["status"] = status
    current["error"] = error


def _capture_queries(conn, request, phase, result, guard, budget):
    for inv in request["invariants"]:
        if inv["kind"] in ("query_equals", "query_preserved"):
            fact = query_fact(conn, inv["sql"], guard, budget)
            result["queries"][inv["name"]][phase] = fact
            if fact["error"] and fact["error"]["kind"] == "worker_error":
                _set_failure(result, fact["error"])
            elif fact["error"] and fact["error"]["kind"] == "resource_limit":
                # query_equals needs only after-state results. Its baseline is
                # recorded faithfully but is not a required check prerequisite.
                if not (phase == "before" and inv["kind"] == "query_equals"):
                    _set_failure(result, fact["error"])


def _capture_phase(conn, request, phase, result, guard, budget):
    result[phase] = observe(conn, guard, budget)
    if result[phase]["error"] and result[phase]["error"]["kind"] in ("resource_limit", "unsupported", "worker_error"):
        _set_failure(result, result[phase]["error"])
    _capture_queries(conn, request, phase, result, guard, budget)


def execute(request, database):
    result = empty_observation(request, runtime_profile())
    if not runtime_supported():
        _set_failure(result, error_fact(Unsupported("Runtime is not qualified")))
        return result
    deadline = time.monotonic() + request["limits"]["wall_seconds"]
    budget = EvidenceBudget(request["limits"])
    conn = None
    guard = None
    identity = None
    source_path = request["source"].get("database")
    try:
        if source_path is not None:
            identity = _source_identity(source_path)
            copy_source = source_path
        else:
            copy_source = Path(database).with_name("constructed-source.db")
            conn, guard = _connection(copy_source, request, deadline)
            for key in ("schema_sql", "seed_sql"):
                with guard.phase():
                    conn.executescript(request["source"][key])
                if conn.in_transaction:
                    raise Unsupported("Source " + key + " must finish with no open transaction")
            conn.close()
            conn = None
        conn, guard = _connection(database, request, deadline)
        source, source_guard = _connection(copy_source, request, deadline, readonly=True)
        try:
            def backup_progress(status, remaining, total):
                guard.check_deadline()
                if total > MAX_DB_BYTES // 512:
                    raise ResourceLimit("Source backup page budget exceeded")
            source.backup(conn, pages=64, progress=backup_progress, sleep=0)
        finally:
            source.close()
        # Backup replaces database page size, so reset the fixed page ceiling.
        conn.set_authorizer(None)
        try:
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            conn.execute("PRAGMA max_page_count=" + str(MAX_DB_BYTES // page_size))
        finally:
            conn.set_authorizer(guard.authorize)
        result["execution"]["status"] = "completed"
        result["execution"]["foreign_keys_start"] = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        _capture_phase(conn, request, "before", result, guard, budget)
        if not result["before"]["complete"]:
            if result["execution"]["status"] == "completed":
                _set_failure(result, result["before"]["error"])
            return result
        try:
            with guard.phase():
                conn.executescript(request["candidate"]["sql"])
            if conn.in_transaction and result["execution"]["status"] == "completed":
                result["execution"]["status"] = "open_transaction"
        except Exception as exc:
            _set_failure(result, error_fact(exc, guard))
        result["execution"]["in_transaction"] = conn.in_transaction
        result["execution"]["foreign_keys_end"] = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        _capture_phase(conn, request, "observed", result, guard, budget)
        conn.close()
        conn = None
        conn, guard = _connection(database, request, deadline)
        _capture_phase(conn, request, "reopened", result, guard, budget)
    except Exception as exc:
        _set_failure(result, error_fact(exc, guard))
        if conn is not None:
            try:
                result["execution"]["in_transaction"] = conn.in_transaction
                result["execution"]["foreign_keys_end"] = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()
        if source_path is not None and identity is not None:
            try:
                result["source_preserved"] = _source_identity(source_path) == identity
            except Exception:
                result["source_preserved"] = False
            if not result["source_preserved"]:
                _set_failure(result, {"kind": "source_changed", "message": "Owned source capture changed during execution", "sqlite_code": None, "sqlite_name": None})
    return result


def _bounded_result(result, request):
    encoded = canonical_bytes(result)
    if len(encoded) > request["limits"]["evidence_bytes"]:
        # Required failure framing is exempt from a lowered observation cap.
        fallback = empty_observation(request, result["runtime"])
        fallback["source_preserved"] = result["source_preserved"]
        fallback["execution"] = dict(result["execution"])
        _set_failure(fallback, error_fact(ResourceLimit("Final canonical raw evidence byte budget exceeded")))
        encoded = canonical_bytes(fallback)
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ResourceLimit("Worker response exceeds 20 MiB")
    return encoded


def main():
    process_limits()
    if len(sys.argv) != 4:
        raise SystemExit(2)
    request_path, response_path, database = map(Path, sys.argv[1:])
    with request_path.open("rb") as stream:
        data = stream.read(MAX_REQUEST_BYTES + 1)
    if len(data) > MAX_REQUEST_BYTES:
        raise SystemExit(2)
    request = json.loads(data)
    validate_request(request)
    result = execute(request, database)
    encoded = _bounded_result(result, request)
    with response_path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()


if __name__ == "__main__":
    main()
