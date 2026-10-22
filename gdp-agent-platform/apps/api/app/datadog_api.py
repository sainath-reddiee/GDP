"""Datadog for Operations (services/ops/datadog.py): the connection, an incident's logs, and monitors now.

The keys live only in the API host's environment (DATADOG_SITE, DATADOG_API_KEY, DATADOG_APP_KEY, DATADOG_APP_URL);
no endpoint accepts or returns one. What the platform does with Datadog is the DATADOG platform setting, saved through
PUT /api/config/platform (CONFIG.EDIT).

Governance (services.governance.policy): reads need OPS.VIEW, the connection test INTEGRATION.MANAGE (like the
Airflow test), and checking monitors now OPS.OPERATE (like polling Airflow now). Calls run on the caller's session.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import Db
from app.main import current_db
from services.ops import datadog as dd

router = APIRouter()


@router.get("/api/ops/datadog")
def datadog_status(db: Db = Depends(current_db)):
    """Whether the host has the keys (never the keys), and the saved setting."""
    return {"connection": dd.public(dd.config_from_env()), "setting": dd.setting(db)}


@router.post("/api/ops/datadog/test")
def datadog_test(db: Db = Depends(current_db)):
    """The API key, then a log search and a monitor list with the application key."""
    cfg = dd.config_from_env()
    if cfg["problems"]:
        return {"ok": False, "connection": dd.public(cfg), "api_key": False, "logs": None, "monitors": None,
                "errors": {"setup": "; ".join(cfg["problems"])}}
    value = dd.setting(db)
    sample = dd.scoped_query(value.get("log_query") or "", {}, dd.scope(value, cfg)) or "*"
    result = dd.Client(cfg).check(sample)
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
    return {"enabled": True, **found, "error": None}


@router.post("/api/ops/datadog/monitors/check")
def datadog_monitors_now(db: Db = Depends(current_db)):
    """Read the monitors now (the worker does it every two minutes): open and resolve their incidents."""
    from services.ops.datadog_monitors import DatadogStore, process

    try:
        return process(DatadogStore(db))
    except dd.DatadogError as exc:
        raise HTTPException(502 if exc.kind in ("unavailable", "throttled") else 400, str(exc)) from exc
