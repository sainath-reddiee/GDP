"""The value ledger report: hours saved per activity, net value after AI cost, the weekly trend, repair times and the
knowledge score, for one window. Read only; every part degrades to 'not available' on its own."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from services.value import baselines, ledger, sources

MTTR_LOOKBACK_DAYS = 365   # the 'before automatic resolution' side needs history older than the window


def _config(db: Any, key: str) -> Dict[str, Any]:
    """{value, version, changed_by, changed_at} of the newest current PLATFORM_CONFIG row, or {} when unset."""
    try:
        found = db.query("""SELECT CONFIG_VALUE, VERSION, CREATED_BY, CREATED_AT FROM CORE.PLATFORM_CONFIG
                             WHERE CONFIG_KEY = %s AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1""", (key,))
    except Exception:
        return {}
    if not found:
        return {}
    value = found[0].get("config_value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return {"value": value, "version": found[0].get("version"), "changed_by": found[0].get("created_by"),
            "changed_at": str(found[0].get("created_at") or "") or None}


def _price(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def report(db: Any, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    days = max(7, min(int(days or 30), 365))
    now = now or datetime.now(timezone.utc)
    stored = _config(db, baselines.KEY)
    base = baselines.effective(stored.get("value"))
    price = _price(_config(db, "CREDIT_PRICE_USD").get("value"))

    exclude = base["exclude"]
    confirmed = bool(base["confirmed"])
    counts = sources.activity_counts(db, days, exclude)
    rows = ledger.entries(counts, base)
    hours = sum(r["hours"] or 0 for r in rows)
    cost = sources.cost_rows(db, days)
    credits = None if cost is None else sum(r["credits"] for r in cost)
    trend = ledger.weekly(counts, base, cost, price)

    events = sources.incident_events(db, max(days, MTTR_LOOKBACK_DAYS), exclude)
    cut = ledger._dt(sources.cutover(db, exclude))
    repair = (ledger.mttr(ledger.episodes(events), now - timedelta(days=days), cut, int(base["min_samples"]))
              if events is not None else None)

    current = sources.knowledge_counts(db, days, 0, exclude)
    previous = sources.knowledge_counts(db, 2 * days, days, exclude)
    know = (ledger.knowledge(current, previous or {}, base["knowledge_weights"]) if current is not None else None)

    by_group: Dict[str, float] = {}
    for r in rows:
        by_group[r["group"]] = round(by_group.get(r["group"], 0) + (r["hours"] or 0), 1)
    return {"window_days": days, "generated_at": now.isoformat(timespec="seconds"),
            "activities": rows, "hours_by_group": by_group,
            "value": ledger.net(hours, base["blended_rate_usd"], credits, price, confirmed),
            "weekly": trend, "repair_time": repair, "knowledge": know,
            "baselines": {"blended_rate_usd": base["blended_rate_usd"], "rate_basis": base["rate_basis"],
                          "credit": base["credit"], "min_samples": base["min_samples"],
                          "activities": base["activities"], "version": stored.get("version"),
                          "changed_by": stored.get("changed_by"), "changed_at": stored.get("changed_at"),
                          "confirmed": confirmed, "basis_note": base["basis_note"],
                          # who confirmed: the person who saved the confirmed version
                          "confirmed_by": stored.get("changed_by") if confirmed else None,
                          "confirmed_at": stored.get("changed_at") if confirmed else None},
            # what was left out of every count, so a reader knows what the numbers cover
            "scope": {"excluded_run_environments": exclude["run_environments"],
                      "excluded_airflow_envs": exclude["airflow_envs"],
                      "always_counted": ["case_triage"], "ai_cost": "all AI usage, including excluded activity"},
            "credit_price_usd": price}


def summary(db: Any, days: int = 30) -> Dict[str, Any]:
    """The compact tile: hours, net value, auto-fix share, knowledge score and the weekly hours."""
    full = report(db, days)
    know = full.get("knowledge") or {}
    share = ((know.get("components") or {}).get("auto_fix_share") or {}).get("value")
    return {"window_days": full["window_days"], "value": full["value"], "auto_fix_share": share,
            "knowledge_score": know.get("score"), "previous_knowledge_score": know.get("previous_score"),
            "weekly_hours": [w["hours"] for w in full["weekly"]][-12:]}
