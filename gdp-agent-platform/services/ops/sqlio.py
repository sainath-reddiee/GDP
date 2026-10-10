"""Two small adapters so ops code runs the same in the API, the worker and Snowpark procedures.

The OPS modules talk to a Db (the API's connection wrapper: query/execute/execute_count with %s parameters and lower
case column names). The shared AI and knowledge helpers (services.common.llm, services.common.audit,
services.knowledge.writer, services.code.context) take a Snowpark-style session: session.sql(sql, params=[...])
.collect() with ? parameters and rows keyed by upper case column names.

DbSession wraps a Db as such a session (no Snowpark needed, and no change to the connection's parameter style, which
other API requests share). SessionDb is the other way round, for code handed a real Snowpark session.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


class Row(dict):
    """A result row like Snowpark's: row["NAME"], row[0] and row.as_dict()."""

    def __init__(self, data: Dict[str, Any]):
        super().__init__(data)
        self._values = list(data.values())

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self)


def to_pyformat(sql: str, has_params: bool) -> str:
    """'?' binds to '%s' (and literal '%' doubled) when there are parameters; unchanged otherwise."""
    if not has_params:
        return sql
    return sql.replace("%", "%%").replace("?", "%s")


def to_qmark(sql: str) -> str:
    return sql.replace("%%", "\x00").replace("%s", "?").replace("\x00", "%")


class _Result:
    def __init__(self, db: Any, sql: str, params: Optional[Sequence[Any]]):
        self.db, self.sql, self.params = db, sql, list(params or [])

    def collect(self) -> List[Row]:
        if self.params:
            found = self.db.query(to_pyformat(self.sql, True), tuple(self.params))
        else:
            found = self.db.query(self.sql)
        return [Row({str(k).upper(): v for k, v in (r or {}).items()}) for r in (found or [])]


class DbSession:
    """A Db seen as a Snowpark session (only .sql(...).collect() is used by the shared helpers)."""

    def __init__(self, db: Any):
        self.db = db

    def sql(self, sql: str, params: Optional[Sequence[Any]] = None) -> _Result:
        return _Result(self.db, sql, params)


class SessionDb:
    """A Snowpark session seen as a Db: %s binds, lower case column names."""

    def __init__(self, session: Any, user: str = "system"):
        self.session, self.user = session, user

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        found = self.session.sql(to_qmark(sql), params=list(params or [])).collect()
        return [{str(k).lower(): v for k, v in (r.as_dict() if hasattr(r, "as_dict") else dict(r)).items()} for r in found]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self.session.sql(to_qmark(sql), params=list(params or [])).collect()

    def execute_count(self, sql: str, params: Sequence[Any] = ()) -> int:
        found = self.session.sql(to_qmark(sql), params=list(params or [])).collect()
        if not found:
            return 0
        first = found[0].as_dict() if hasattr(found[0], "as_dict") else dict(found[0])
        total = 0
        for key, value in first.items():
            if "multi-joined" in str(key).lower():
                continue
            try:
                total += int(value or 0)
            except (TypeError, ValueError):
                pass
        return total


def as_db(source: Any) -> Any:
    """A Db-like object from a Db or a Snowpark session."""
    if hasattr(source, "query") and hasattr(source, "execute_count"):
        return source
    return SessionDb(source)


def as_session(source: Any) -> Any:
    """A Snowpark-like session from a Db or a session."""
    if hasattr(source, "sql") and not hasattr(source, "query"):
        return source
    if isinstance(source, SessionDb):
        return source.session
    return DbSession(source)
