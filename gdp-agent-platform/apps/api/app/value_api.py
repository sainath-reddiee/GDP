"""The value ledger (services/value): what the platform's work is worth, in hours, money and repair time.

Read only, governed like the cost pages (AUDIT.VIEW). Effort baselines are edited through PUT /api/config/platform
with key VALUE_BASELINES (CONFIG.EDIT); every change is a new version of the setting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import perf as _perf
from app.db import Db
from app.main import current_db
from services.value import report as value_report

router = APIRouter()


@router.get("/api/value")
def value(days: int = Query(30, ge=7, le=365), db: Db = Depends(current_db)):
    """Hours saved per activity (measured counts x baseline minutes), net value after AI cost, the weekly trend,
    repair times (fixed automatically vs by a person) and the knowledge score."""
    return value_report.report(db, days)


@router.get("/api/value/summary")
@_perf.cached("value-summary", 300)
def value_summary(days: int = Query(30, ge=7, le=365), db: Db = Depends(current_db)):
    """The compact tile for the Command Center and My Work."""
    return value_report.summary(db, days)
