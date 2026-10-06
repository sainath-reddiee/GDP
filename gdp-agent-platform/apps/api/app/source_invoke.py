"""Run source onboarding handlers in the API process (EXECUTE AS CALLER semantics).

Catalog endpoints already use the FastAPI session role. Snowflake procedures deployed as
EXECUTE AS OWNER ignore that role; dev mode and AIP_SOURCE_AS_CALLER=1 invoke the same
Python handlers on the active connector session instead.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from snowflake.connector.cursor import SnowflakeCursor

from app.db import AUTH_MODE, Db

USE_CALLER = os.environ.get("AIP_SOURCE_AS_CALLER", "1" if AUTH_MODE == "dev" else "0").lower() in (
    "1",
    "true",
    "yes",
)

# The API connection binds with %s (pyformat); the Snowpark handlers bind with ?. Switching the
# shared connection would break concurrent API requests, so force qmark only for queries issued
# on the thread that is running a handler.
_handler_thread = threading.local()
_original_execute = SnowflakeCursor.execute


def _execute(self, command, params=None, *args, **kwargs):
    if getattr(_handler_thread, "qmark", False):
        kwargs.setdefault("_force_qmark_paramstyle", True)
    return _original_execute(self, command, params, *args, **kwargs)


SnowflakeCursor.execute = _execute


@contextmanager
def _snowpark(db: Db) -> Iterator[Any]:
    from snowflake.snowpark import Session
    from snowflake.snowpark import session as snowpark_session

    session = Session.builder.configs({"connection": db.conn}).create()
    _handler_thread.qmark = True
    try:
        yield session
    finally:
        _handler_thread.qmark = False
        # Session.close() would also close the API's shared connection; just unregister it.
        snowpark_session._remove_session(session)


def invoke_source(db: Db, handler: Callable[..., Any], *args: Any) -> Any:
    with _snowpark(db) as session:
        return handler(session, *args)
