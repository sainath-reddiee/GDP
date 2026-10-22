"""Datadog for Operations (services/ops/datadog.py): the connection, an incident's logs, and monitors now.

The keys live only in the API host's environment (DATADOG_SITE, DATADOG_API_KEY, DATADOG_APP_KEY, DATADOG_APP_URL);
no endpoint accepts or returns one. What the platform does with Datadog is the DATADOG platform setting, saved through
PUT /api/config/platform (CONFIG.EDIT).

Governance (services.governance.policy): reads need OPS.VIEW, the connection test INTEGRATION.MANAGE (like the
Airflow test), and checking monitors now OPS.OPERATE (like polling Airflow now). Calls run on the caller's session.
Every failure comes back as a message the Admin page can show (failed(): Snowflake privileges, Datadog, the host),
never as a bare 500.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import Db, SnowflakeSessionError
from app.main import current_db
from services.ops import datadog as dd
from services.ops.redact import redact

router = APIRouter()


def failed(what: str, exc: Exception) -> HTTPException:
    """A readable HTTP error for an unexpected failure: 403 for a Snowflake privilege, 502 for Datadog, else 400."""
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, dd.DatadogError):
        return HTTPException(502 if exc.kind in ("unavailable", "throttled") else 400, f"{what}: {exc}")
    text = redact(str(exc))[:300]
    if "insufficient privileges" in text.lower() or "not authorized" in text.lower():
        return HTTPException(403, f"{what}: your Snowflake role lacks a privilege it needs ({text})")
    return HTTPException(400, f"{what}: {type(exc).__name__}: {text}")


@router.get("/api/ops/datadog")
def datadog_status(db: Db = Depends(current_db)):
    """Whether the host has the keys (never the keys), and the saved setting."""
    try:
        return {"connection": dd.public(dd.config_from_env()), "setting": dd.setting(db)}
    except SnowflakeSessionError:
        raise
    except Exception as exc:
        raise failed("Reading the Datadog status failed", exc) from exc


@router.post("/api/ops/datadog/test")
def datadog_test(db: Db = Depends(current_db)):
    """The API key, then a log search and a monitor list with the application key."""
    cfg = dd.config_from_env()
    if cfg["problems"]:
        return {"ok": False, "connection": dd.public(cfg), "api_key": False, "logs": None, "monitors": None,
                "errors": {"setup": "; ".join(cfg["problems"])}}
    try:
        value = dd.setting(db)
        sample = dd.scoped_query(value.get("log_query") or "", {}, dd.scope(value, cfg)) or "*"
        result = dd.Client(cfg).check(sample)
    except SnowflakeSessionError:
        raise
    except Exception as exc:
        raise failed("The Datadog test failed", exc) from exc
    ok = bool(result["api_key"]) and not result["errors"]
    return {"ok": ok, "connection": dd.public(cfg), **result, "query": sample}


@router.get("/api/ops/incidents/{incident_id}/datadog-logs")
def incident_datadog_logs(incident_id: str, minutes: Optional[int] = Query(None, ge=1, le=1440),
                          db: Db = Depends(current_db)):
    """The incident's Datadog logs, newest first, with a link to the same search in Datadog."""
    from services.ops.incidents import SqlStore

    incident = SqlStore(db).get(incident_id)
    if not incident:
        raise HTTPException(404, f"Incident {incident_id} not found")
    value = dd.setting(db)
    if not value.get("enabled"):
        return {"enabled": False, "logs": [], "error": None}
    try:
        found = dd.incident_logs(incident, value, minutes=minutes)
    except dd.DatadogError as exc:
        return {"enabled": True, "logs": [], "error": str(exc), "kind": exc.kind}
    except Exception as exc:   # shown in the logs panel, the rest of the incident page is unaffected
        return {"enabled": True, "logs": [], "error": failed("Datadog logs could not be read", exc).detail}
    return {"enabled": True, **found, "error": None}


@router.post("/api/ops/datadog/monitors/check")
def datadog_monitors_now(db: Db = Depends(current_db)):
    """Read the monitors now (the worker does it every two minutes): open and resolve their incidents."""
    from services.ops.datadog_monitors import DatadogStore, process

    try:
        return process(DatadogStore(db))
    except SnowflakeSessionError:
        raise
    except Exception as exc:
        raise failed("Checking the Datadog monitors failed", exc) from exc
