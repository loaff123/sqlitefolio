"""Lossless SQLite storage-class values and canonical protocol JSON."""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import struct

MAX_VALUE_BYTES = 65_536


class SQLiteText(bytes):
    """TEXT bytes distinguished from BLOB bytes at sqlite3's driver boundary."""


class ValueLimitExceeded(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def encode_cell(value):
    """Encode a value fetched with text_factory=SQLiteText without coercion."""
    if value is None:
        return ["null", None]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        if math.isnan(value):
            raise ValueError("SQLite returned an unrepresentable NaN")
        return ["real", struct.pack(">d", value).hex()]
    if isinstance(value, (bytes, str)):
        is_text = isinstance(value, (SQLiteText, str))
        data = value.encode("utf-8") if isinstance(value, str) else value
        if len(data) > MAX_VALUE_BYTES:
            raise ValueLimitExceeded("SQLite value exceeds 65536 bytes")
        return ["text" if is_text else "blob", base64.b64encode(data).decode("ascii")]
    raise ValueError("Unsupported SQLite value type")


def encode_row(row):
    return [encode_cell(value) for value in row]


def validate_cell(cell):
    """Validate one exact protocol cell, returning it or raising ValueError."""
    if type(cell) is not list or len(cell) != 2 or type(cell[0]) is not str:
        raise ValueError("Typed value must be a two-item array")
    kind, value = cell
    if kind == "null":
        if value is not None:
            raise ValueError("NULL value must be null")
    elif kind == "integer":
        if type(value) is not str or not re.fullmatch(r"(?:0|-?[1-9][0-9]*)", value) or not -(1 << 63) <= int(value) < (1 << 63):
            raise ValueError("INTEGER must be canonical signed i64 decimal")
    elif kind == "real":
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{16}", value):
            raise ValueError("REAL must be 16 lowercase hexadecimal characters")
        if math.isnan(struct.unpack(">d", bytes.fromhex(value))[0]):
            raise ValueError("NaN expectations are not supported by SQLite")
    elif kind in ("text", "blob"):
        if type(value) is not str:
            raise ValueError("TEXT/BLOB must be canonical base64")
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("TEXT/BLOB must be canonical base64") from exc
        if base64.b64encode(data).decode("ascii") != value:
            raise ValueError("TEXT/BLOB must be canonical base64")
        if len(data) > MAX_VALUE_BYTES:
            raise ValueError("Typed value exceeds 65536 bytes")
    else:
        raise ValueError("Unknown typed value storage class")
    return cell
