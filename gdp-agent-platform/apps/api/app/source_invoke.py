"""Run source onboarding handlers in the API process (EXECUTE AS CALLER semantics).

Catalog endpoints already use the FastAPI session role. Snowflake procedures deployed as
EXECUTE AS OWNER ignore that role; dev mode and AIP_SOURCE_AS_CALLER=1 invoke the same
Python handlers on the active connector session instead.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from app.db import AUTH_MODE, Db

USE_CALLER = os.environ.get("AIP_SOURCE_AS_CALLER", "1" if AUTH_MODE == "dev" else "0").lower() in (
    "1",
    "true",
    "yes",
)


@contextmanager
def _snowpark(db: Db) -> Iterator[Any]:
    from snowflake.snowpark import Session

    session = Session.builder.configs({"connection": db.conn}).create()
    try:
        yield session
    finally:
        session.close()


def invoke_source(db: Db, handler: Callable[..., Any], *args: Any) -> Any:
    with _snowpark(db) as session:
        return handler(session, *args)
