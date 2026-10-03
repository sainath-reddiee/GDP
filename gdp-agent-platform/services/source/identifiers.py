"""Snowflake identifier handling. Every name that reaches dynamic SQL goes through quote()."""

from __future__ import annotations

import re
from typing import Optional

UNQUOTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SOURCE_SYSTEM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_IDENTIFIER = 255


def normalize(name: str) -> str:
    """User input -> name as stored in INFORMATION_SCHEMA. Unquoted-style names resolve upper case."""
    name = (name or "").strip()
    assert 0 < len(name) <= MAX_IDENTIFIER, f"identifier must be 1-{MAX_IDENTIFIER} characters"
    if len(name) >= 2 and name[0] == name[-1] == '"':
        return name[1:-1].replace('""', '"')
    return name.upper() if UNQUOTED.match(name) else name


def quote(name: str) -> str:
    assert name and len(name) <= MAX_IDENTIFIER, "invalid identifier"
    assert "\x00" not in name, "identifier contains NUL"
    return '"' + name.replace('"', '""') + '"'


def fqn(database: str, schema: str, name: Optional[str] = None) -> str:
    parts = [database, schema] + ([name] if name is not None else [])
    return ".".join(quote(p) for p in parts)


def landing_table_name(source_system_name: str, object_name: str) -> str:
    """Deterministic landing name: <SOURCE_SYSTEM>__<OBJECT>, upper case, [A-Z0-9_] only."""
    raw = f"{source_system_name}__{object_name}".upper()
    cleaned = re.sub(r"[^A-Z0-9_]", "_", raw)
    if not re.match(r"^[A-Z_]", cleaned):
        cleaned = "_" + cleaned
    assert len(cleaned) <= MAX_IDENTIFIER, f"landing table name too long: {cleaned[:40]}..."
    return cleaned


def format_data_type(data_type: str, char_len=None, precision=None, scale=None) -> str:
    """INFORMATION_SCHEMA.COLUMNS parts -> DDL-style type, e.g. VARCHAR(50), NUMBER(38,0)."""
    t = (data_type or "").upper()
    if t == "TEXT" and char_len is not None:
        return f"VARCHAR({int(char_len)})"
    if t == "NUMBER" and precision is not None:
        return f"NUMBER({int(precision)},{int(scale or 0)})"
    return t
