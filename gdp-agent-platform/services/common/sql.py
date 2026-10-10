"""Small Snowpark helpers shared by the stage procedures."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

MAX_TEXT = 4000
INSERT_BATCH = 100


def rows(session, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    return [r.as_dict() for r in session.sql(sql, params=params or []).collect()]


def scalar(session, sql: str, params: Optional[list] = None) -> Any:
    result = session.sql(sql, params=params or []).collect()
    return result[0][0] if result else None


def clip(value: Optional[Any], limit: int = MAX_TEXT) -> str:
    return "" if value is None else str(value)[:limit]


def variant(value: Optional[Any]) -> Any:
    """VARIANT columns come back from Snowpark as JSON text."""
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def insert_rows(session, table: str, columns: Sequence[str], exprs: Sequence[str],
                values: List[Sequence[Any]]) -> None:
    """Batched INSERT ... SELECT; exprs wrap each bind, e.g. "NULLIF(?, '')::NUMBER" or "PARSE_JSON(?)".

    Values are bound as text (None -> ''): NULLIF(<number>, '') would make Snowflake cast '' to NUMBER,
    and Snowpark binds Python None as the string 'None'.
    """
    assert len(columns) == len(exprs), "columns and exprs differ in length"
    if not values:
        return
    select = "SELECT " + ", ".join(exprs)
    binds = sum(e.count("?") for e in exprs)
    for start in range(0, len(values), INSERT_BATCH):
        batch = values[start:start + INSERT_BATCH]
        for row in batch:
            assert len(row) == binds, f"{table}: row has {len(row)} values for {binds} bind placeholders"
        sql = f"INSERT INTO {table} ({', '.join(columns)}) " + " UNION ALL ".join([select] * len(batch))
        params = [_bind(v) for row in batch for v in row]
        session.sql(sql, params=params).collect()


def _bind(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def atomic(session, write: Callable[[], Any]) -> Any:
    """Run `write` in one transaction (supersede then insert must not leave a run with no current rows).
    Not for use inside a workflow transition: _apply_transition already holds the transaction there."""
    session.sql("BEGIN TRANSACTION").collect()
    try:
        result = write()
        session.sql("COMMIT").collect()
    except Exception:
        session.sql("ROLLBACK").collect()
        raise
    return result


def config_value(session, key: str, default: Any = None) -> Any:
    found = rows(session, "SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = ? AND IS_CURRENT "
                          "ORDER BY VERSION DESC LIMIT 1", [key])
    return variant(found[0]["CONFIG_VALUE"]) if found else default
