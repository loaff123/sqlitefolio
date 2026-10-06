"""Native guardrails for trusted-input accidental-misuse containment."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from .values import ValueLimitExceeded, canonical_bytes

DEFAULT_LIMITS = {"rows_per_query": 10_000, "vm_steps": 2_000_000, "wall_seconds": 30, "evidence_bytes": 16_777_216}
MAX_DB_BYTES = 32 * 1024 * 1024


class ResourceLimit(RuntimeError):
    pass


class Unsupported(RuntimeError):
    pass


def error_fact(exc, guard=None):
    if isinstance(exc, (ResourceLimit, ValueLimitExceeded, MemoryError)):
        kind = "resource_limit"
    elif isinstance(exc, Unsupported):
        kind = "unsupported"
    elif isinstance(exc, sqlite3.Error):
        if guard is not None and guard.exhausted:
            return error_fact(ResourceLimit(guard.exhausted))
        if guard is not None and guard.denied:
            return error_fact(Unsupported(guard.denied))
        if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_NOMEM, sqlite3.SQLITE_FULL, sqlite3.SQLITE_TOOBIG):
            kind = "resource_limit"
        else:
            kind = "sqlite_error"
    else:
        kind = "worker_error"
    return {"kind": kind, "message": str(exc), "sqlite_code": getattr(exc, "sqlite_errorcode", None), "sqlite_name": getattr(exc, "sqlite_errorname", None)}


def not_run_error():
    return {"kind": "not_run", "message": "Phase was not run", "sqlite_code": None, "sqlite_name": None}


class EvidenceBudget:
    def __init__(self, limits):
        self.limit = limits["evidence_bytes"]
        self.rows_per_query = limits["rows_per_query"]
        self.bytes = 0
        self.rows = 0

    def add(self, fact, *, row=False):
        size = len(canonical_bytes(fact)) + 1
        if self.bytes + size > self.limit:
            raise ResourceLimit("Canonical evidence byte budget exceeded")
        if row and self.rows >= 100_000:
            raise ResourceLimit("Total observed row budget exceeded")
        self.bytes += size
        self.rows += int(row)


READ_PRAGMAS = frozenset({"table_info", "table_xinfo", "table_list", "foreign_key_list", "index_list", "index_info", "index_xinfo", "foreign_key_check", "integrity_check", "quick_check", "foreign_keys", "defer_foreign_keys", "user_version", "schema_version", "application_id", "page_count", "page_size", "freelist_count", "encoding", "journal_mode", "compile_options", "function_list", "collation_list"})
ARGUMENT_PRAGMAS = frozenset({"table_info", "table_xinfo", "table_list", "foreign_key_list", "index_list", "index_info", "index_xinfo", "foreign_key_check", "integrity_check", "quick_check"})
SET_PRAGMAS = frozenset({"foreign_keys", "defer_foreign_keys"})
DENIED_FUNCTIONS = frozenset({"load_extension", "readfile", "writefile", "edit", "fsdir"})
WRITE_ACTIONS = frozenset(getattr(sqlite3, name) for name in (
    "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW", "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_DELETE", "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE", "SQLITE_CREATE_VTABLE", "SQLITE_DROP_VTABLE", "SQLITE_TRANSACTION", "SQLITE_SAVEPOINT"))
TEMP_ACTIONS = frozenset(getattr(sqlite3, name) for name in ("SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW"))


class Guard:
    def __init__(self, conn, limits, deadline):
        self.conn = conn
        self.limits = limits
        self.deadline = deadline
        self.readonly = False
        self.steps = 0
        self.exhausted = None
        self.denied = None
        conn.enable_load_extension(False)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DB_BYTES)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.set_authorizer(self.authorize)
        conn.set_progress_handler(self.progress, 1000)

    def reject(self, reason):
        self.denied = reason
        return sqlite3.SQLITE_DENY

    def authorize(self, action, arg1, arg2, database, trigger):
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return self.reject("ATTACH/DETACH and external database files are unsupported")
        if action in (sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE):
            return self.reject("Virtual/shadow tables are unsupported")
        if action in TEMP_ACTIONS or (database == "temp" and action in WRITE_ACTIONS and not (action == sqlite3.SQLITE_UPDATE and arg1 in ("sqlite_temp_master", "sqlite_temp_schema"))):
            return self.reject("Temporary objects are unsupported")
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
            return self.reject("Extensions and external-file SQL functions are unsupported")
        if action == sqlite3.SQLITE_PRAGMA:
            name = (arg1 or "").lower()
            if name not in READ_PRAGMAS:
                return self.reject("Unsupported PRAGMA: " + name)
            if arg2 is not None and name not in ARGUMENT_PRAGMAS:
                if self.readonly or name not in SET_PRAGMAS:
                    return self.reject("PRAGMA setter is not permitted: " + name)
        if self.readonly and action in WRITE_ACTIONS:
            return self.reject("Invariant and observation SQL must be read-only")
        return sqlite3.SQLITE_OK

    def progress(self):
        self.steps += 1000
        if self.steps >= self.limits["vm_steps"]:
            self.exhausted = "VM instruction budget exhausted"
        elif time.monotonic() >= self.deadline:
            self.exhausted = "Worker wall deadline exhausted"
        return int(self.exhausted is not None)

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise ResourceLimit("Worker wall deadline exhausted")

    @contextmanager
    def phase(self, *, readonly=False):
        previous = self.readonly
        self.readonly = readonly
        self.steps = 0
        self.exhausted = None
        self.denied = None
        try:
            self.check_deadline()
            yield
        finally:
            self.readonly = previous
