"""Bounded native SQLite facts, with explicit incomplete observations."""
from __future__ import annotations

import sqlite3

from .guardrails import ResourceLimit, Unsupported, error_fact, not_run_error
from .values import SQLiteText, canonical_bytes, encode_row


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def metadata(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise Unsupported("Non-UTF8 SQLite names/metadata are unsupported") from exc
    if value is None or type(value) in (str, int):
        return value
    raise Unsupported("Unsupported native metadata value")


def metadata_rows(cursor, budget, max_rows=10_000):
    rows = []
    for raw in cursor:
        if len(rows) >= max_rows:
            raise ResourceLimit("Native metadata row budget exceeded")
        row = [metadata(value) for value in raw]
        budget.add(row, row=True)
        rows.append(row)
    return rows


def typed_rows(cursor, budget, *, sort=False):
    rows = []
    for raw in cursor:
        if len(rows) >= budget.rows_per_query:
            raise ResourceLimit("Rows per table/query budget exceeded")
        row = encode_row(raw)
        budget.add(row, row=True)
        rows.append(row)
    if sort:
        rows.sort(key=canonical_bytes)
    return rows


def query_fact(conn, sql, guard, budget):
    try:
        with guard.phase(readonly=True):
            cursor = conn.execute(sql)
            if cursor.description is None:
                raise Unsupported("Invariant SQL must return query rows")
            return {"rows": typed_rows(cursor, budget), "error": None, "complete": True}
    except Exception as exc:
        return {"rows": None, "error": error_fact(exc, guard), "complete": False}


def _name(value):
    if type(value) is not str or len(value.encode("utf-8")) > 128 or "\x00" in value:
        raise Unsupported("SQLite object/column names must be UTF-8 and at most 128 bytes")
    return value


def observe(conn, guard, budget):
    snap = {"complete": False, "error": None, "objects": [], "tables": {}, "views": {}, "diagnostics": {}, "sequences": []}
    try:
        with guard.phase(readonly=True):
            fk = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            snap["diagnostics"] = {
                "foreign_key_check": {"rows": [], "error": not_run_error()},
                "integrity_check": {"rows": [], "error": not_run_error()},
                "foreign_keys": fk,
            }
            for raw in conn.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name"):
                if len(snap["objects"]) >= 512:
                    raise ResourceLimit("Schema object budget exceeded")
                vals = [metadata(value) for value in raw]
                _name(vals[1]); _name(vals[2])
                obj = dict(zip(("type", "name", "table", "sql"), vals))
                budget.add(obj)
                snap["objects"].append(obj)
            table_list = metadata_rows(conn.execute("PRAGMA main.table_list"), budget, 514)
            main_tables = {row[1]: row for row in table_list if row[0] == "main"}
            if any(row[2] in ("virtual", "shadow") for row in table_list):
                raise Unsupported("Virtual/shadow tables are unsupported")
            if any(row[0] == "temp" and row[1] != "sqlite_temp_schema" for row in table_list):
                raise Unsupported("Temporary objects are unsupported")
            table_names = [obj["name"] for obj in snap["objects"] if obj["type"] == "table"]
            if len(table_names) > 128:
                raise ResourceLimit("Observed table budget exceeded")
            for name in table_names:
                qname = quote(name)
                col_info = metadata_rows(conn.execute("PRAGMA main.table_xinfo(" + qname + ")"), budget, 256)
                columns = [_name(row[1]) for row in col_info]
                if len(columns) > 256:
                    raise ResourceLimit("Columns per table budget exceeded")
                fks = metadata_rows(conn.execute("PRAGMA main.foreign_key_list(" + qname + ")"), budget)
                idx_info = metadata_rows(conn.execute("PRAGMA main.index_list(" + qname + ")"), budget, 512)
                indexes = []
                for info in sorted(idx_info, key=lambda row: row[1]):
                    _name(info[1])
                    idx_columns = metadata_rows(conn.execute("PRAGMA main.index_xinfo(" + quote(info[1]) + ")"), budget)
                    indexes.append({"info": info, "columns": idx_columns})
                rows = typed_rows(conn.execute("SELECT " + ",".join(quote(col) for col in columns) + " FROM main." + qname), budget, sort=True)
                snap["tables"][name] = {"columns": columns, "column_info": col_info, "table_info": main_tables[name], "foreign_keys": fks, "indexes": indexes, "rows": rows}
            for obj in snap["objects"]:
                if obj["type"] != "view":
                    continue
                try:
                    conn.execute("SELECT * FROM main." + quote(obj["name"]) + " LIMIT 0")
                    fact = {"ok": True, "error": None}
                except sqlite3.Error as exc:
                    fact = {"ok": False, "error": error_fact(exc, guard)}
                    if fact["error"]["kind"] == "resource_limit":
                        raise ResourceLimit(fact["error"]["message"])
                budget.add(fact)
                snap["views"][obj["name"]] = fact
            if "sqlite_sequence" in snap["tables"]:
                snap["sequences"] = typed_rows(conn.execute("SELECT name,seq FROM main.sqlite_sequence"), budget, sort=True)
            for pragma in ("foreign_key_check", "integrity_check"):
                try:
                    rows = typed_rows(conn.execute("PRAGMA main." + pragma), budget)
                    snap["diagnostics"][pragma] = {"rows": rows, "error": None}
                except Exception as exc:
                    err = error_fact(exc, guard)
                    snap["diagnostics"][pragma] = {"rows": [], "error": err}
                    snap["error"] = snap["error"] or err
            if snap["error"] is None:
                snap["complete"] = True
    except Exception as exc:
        snap["error"] = error_fact(exc, guard)
    return snap
