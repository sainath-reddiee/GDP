"""The value ledger's arithmetic (pure): hours saved per activity, net value, weekly trend, repair times and the
knowledge score.

Two kinds of evidence are kept apart on purpose:
  hours saved   effort a person did not spend: measured counts x assumed minutes (services.value.baselines)
  repair time   how long a failure stayed broken (MTTR), measured from incident history alone
They are never converted into each other: downtime avoided is not engineer time saved.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from services.value.baselines import ACTIVITIES


def _credit(kind: Optional[str], credit: Dict[str, float]) -> float:
    return float(credit.get(str(kind or "APPROVED").upper(), 1.0))


def entries(counts: Dict[str, Optional[List[Dict[str, Any]]]], base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per activity: {activity, label, group, unit, available, count, credited, minutes_each, hours,
    count_basis, minutes_basis, source}. counts[activity] is a list of {week, kind, n}, or None when its source could
    not be read (the activity then shows as not available rather than as zero)."""
    out = []
    for key, meta in ACTIVITIES.items():
        act = base["activities"][key]
        rows = counts.get(key)
        minutes = float(act["minutes"])
        if rows is None:
            out.append({"activity": key, "label": meta["label"], "group": meta["group"], "unit": meta["unit"],
                        "available": False, "count": None, "credited": None, "minutes_each": minutes, "hours": None,
                        "count_basis": "measured", "minutes_basis": _basis(act), "source": meta["source"]})
            continue
        count = sum(int(r.get("n") or 0) for r in rows)
        credited = sum(int(r.get("n") or 0) * _credit(r.get("kind"), base["credit"]) for r in rows)
        out.append({"activity": key, "label": meta["label"], "group": meta["group"], "unit": meta["unit"],
                    "available": True, "count": count, "credited": round(credited, 2), "minutes_each": minutes,
                    "hours": round(credited * minutes / 60, 1), "count_basis": "measured",
                    "minutes_basis": _basis(act), "source": meta["source"]})
    return out


def _basis(act: Dict[str, Any]) -> str:
    return "admin set" if act.get("basis") == "edited" else "assumed default"


def net(hours: float, rate_usd: float, credits: Optional[float], credit_price_usd: Optional[float]) -> Dict[str, Any]:
    """{hours, value_usd, ai_credits, ai_cost_usd, net_usd, needs_price}. Without a credit price the AI cost cannot be
    put in money, so no net figure is given (needs_price), never a guess."""
    value = round(float(hours) * float(rate_usd), 2)
    cost = None if credit_price_usd is None or credits is None else round(float(credits) * float(credit_price_usd), 2)
    return {"hours": round(float(hours), 1), "value_usd": value, "ai_credits": None if credits is None else round(credits, 3),
            "ai_cost_usd": cost, "net_usd": None if cost is None else round(value - cost, 2),
            "needs_price": credit_price_usd is None}


def weekly(counts: Dict[str, Optional[List[Dict[str, Any]]]], base: Dict[str, Any],
           cost_rows: Optional[List[Dict[str, Any]]], credit_price_usd: Optional[float]) -> List[Dict[str, Any]]:
    """[{week, hours, ai_credits, ai_cost_usd}] oldest first: hours from every activity, AI credits from COST_USAGE."""
    weeks: Dict[str, Dict[str, Any]] = {}
    for key, rows in counts.items():
        if not rows or key not in base["activities"]:
            continue
        minutes = float(base["activities"][key]["minutes"])
        for r in rows:
            w = weeks.setdefault(str(r.get("week"))[:10], {"hours": 0.0, "ai_credits": 0.0})
            w["hours"] += int(r.get("n") or 0) * _credit(r.get("kind"), base["credit"]) * minutes / 60
    for r in cost_rows or []:
        w = weeks.setdefault(str(r.get("week"))[:10], {"hours": 0.0, "ai_credits": 0.0})
        w["ai_credits"] += float(r.get("credits") or 0)
    out = []
    for week in sorted(weeks):
        credits = weeks[week]["ai_credits"]
        out.append({"week": week, "hours": round(weeks[week]["hours"], 1), "ai_credits": round(credits, 3),
                    "ai_cost_usd": None if credit_price_usd is None else round(credits * credit_price_usd, 2)})
    return out


# ---------------------------------------------------------------- repair time (MTTR), from incident history

def _dt(value: Any) -> Optional[datetime]:
    from services.ops.normalize import ts

    iso = ts(value)
    return datetime.fromisoformat(iso) if iso else None


def _detail(value: Any) -> Dict[str, Any]:
    import json

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def episodes(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each time an incident was open: {incident_id, fingerprint, start, end, kind, minutes}. An episode starts at
    'opened' or 'reopened' (at the failure time the event records) and ends at the next 'resolved'. kind is AUTO when a
    known fix repaired it (minutes to the auto_fixed event), MANUAL when a person resolved it, SELF when a later run
    succeeded on its own. The incident row keeps only the latest occurrence; the timeline keeps them all."""
    out: List[Dict[str, Any]] = []
    current: Dict[str, Dict[str, Any]] = {}
    ordered = sorted(events, key=lambda e: (str(e.get("incident_id")), str(_dt(e.get("created_at")) or "")))
    for e in ordered:
        iid, kind = str(e.get("incident_id")), str(e.get("kind") or "")
        at = _dt(e.get("created_at"))
        if at is None:
            continue
        if kind in ("opened", "reopened"):
            start = _dt(_detail(e.get("detail")).get("at")) or at
            current[iid] = {"incident_id": iid, "fingerprint": e.get("fingerprint"), "start": start, "auto": None}
        elif kind == "auto_fixed" and iid in current and current[iid]["auto"] is None:
            current[iid]["auto"] = at
        elif kind == "resolved" and iid in current:
            ep = current.pop(iid)
            if ep["auto"] is not None:
                kind_, end = "AUTO", ep["auto"]
            elif _detail(e.get("detail")).get("auto"):
                kind_, end = "SELF", at
            else:
                kind_, end = "MANUAL", at
            minutes = max(0.0, (end - ep["start"]).total_seconds() / 60)
            out.append({"incident_id": iid, "fingerprint": ep["fingerprint"], "start": ep["start"].isoformat(),
                        "end": end.isoformat(), "kind": kind_, "minutes": round(minutes, 1)})
    for ep in current.values():   # still open but already repaired by a known fix
        if ep["auto"] is not None:
            minutes = max(0.0, (ep["auto"] - ep["start"]).total_seconds() / 60)
            out.append({"incident_id": ep["incident_id"], "fingerprint": ep["fingerprint"],
                        "start": ep["start"].isoformat(), "end": ep["auto"].isoformat(), "kind": "AUTO",
                        "minutes": round(minutes, 1)})
    return out


def _side(values: List[float]) -> Dict[str, Any]:
    return {"n": len(values), "median_min": round(statistics.median(values), 1) if values else None}


def _compare(label: str, a_label: str, a: List[float], b_label: str, b: List[float], min_samples: int) -> Dict[str, Any]:
    left, right = _side(a), _side(b)
    measured = left["n"] >= min_samples and right["n"] >= min_samples
    change = None
    if measured and right["median_min"]:
        change = round(100 * (left["median_min"] - right["median_min"]) / right["median_min"], 1)
    return {"label": label, a_label: left, b_label: right, "measured": measured, "change_pct": change}


def mttr(eps: List[Dict[str, Any]], window_start: Optional[datetime], cutover: Optional[datetime],
         min_samples: int) -> List[Dict[str, Any]]:
    """Three comparisons of median repair time, each 'measured' only when both sides have min_samples episodes:
      1. fixed automatically vs resolved by a person, in the window;
      2. after vs before the first automatic fix (the platform's own history; incidents only exist since Airflow was
         connected, so 'before' means before automatic resolution, not before the platform);
      3. like for like: for failures that were resolved by a person and later fixed automatically, the median of each
         failure's automatic repairs vs its manual ones (the strongest evidence: the same failure both ways)."""
    def start(e):
        return _dt(e["start"])

    window = [e for e in eps if window_start is None or (start(e) and start(e) >= window_start)]
    first = _compare("Fixed automatically vs resolved by a person", "automatic",
                     [e["minutes"] for e in window if e["kind"] == "AUTO"], "manual",
                     [e["minutes"] for e in window if e["kind"] == "MANUAL"], min_samples)
    if cutover is not None:
        before = [e["minutes"] for e in eps if e["kind"] == "MANUAL" and start(e) and start(e) < cutover]
        after = [e["minutes"] for e in eps if e["kind"] in ("AUTO", "MANUAL") and start(e) and start(e) >= cutover]
    else:
        before, after = [], []
    second = _compare("After vs before automatic resolution", "after", after, "before", before, min_samples)
    second["cutover"] = cutover.isoformat() if cutover else None
    by_fp: Dict[str, Dict[str, List[float]]] = {}
    for e in eps:
        if e.get("fingerprint") and e["kind"] in ("AUTO", "MANUAL"):
            by_fp.setdefault(str(e["fingerprint"]), {"AUTO": [], "MANUAL": []})[e["kind"]].append(e["minutes"])
    pairs = [(statistics.median(v["AUTO"]), statistics.median(v["MANUAL"])) for v in by_fp.values()
             if v["AUTO"] and v["MANUAL"]]
    third = _compare("Same failure, fixed automatically vs by a person", "automatic", [p[0] for p in pairs],
                     "manual", [p[1] for p in pairs], max(1, min(min_samples, 3)))
    third["failures"] = len(pairs)
    return [first, second, third]


# ---------------------------------------------------------------- the knowledge score

def _ratio(num: Any, den: Any) -> Optional[float]:
    try:
        num, den = float(num or 0), float(den or 0)
    except (TypeError, ValueError):
        return None
    return round(num / den, 3) if den > 0 else None


def knowledge(now: Dict[str, Any], before: Dict[str, Any], weights: Dict[str, float]) -> Dict[str, Any]:
    """{score, previous_score, components: {name: {value, previous, numerator, denominator}}, counts} where every
    component is a measured ratio:
      auto_fix_share  failures fixed by a known fix / failures handled
      dag_coverage    active DAGs with a profile in Knowledge / active DAGs
      fix_coverage    failing error signatures that have a known fix / failing error signatures"""
    def comps(c: Dict[str, Any]) -> Dict[str, Optional[float]]:
        return {"auto_fix_share": _ratio(c.get("auto_fixed"), c.get("failures")),
                "dag_coverage": _ratio(c.get("profiled_dags"), c.get("active_dags")),
                "fix_coverage": _ratio(c.get("covered_signatures"), c.get("signatures"))}

    def score(values: Dict[str, Optional[float]]) -> Optional[int]:
        known = {k: v for k, v in values.items() if v is not None}
        if not known:
            return None
        total = sum(weights.get(k, 0) for k in known)
        return round(100 * sum(weights.get(k, 0) * v for k, v in known.items()) / total) if total else None

    current, previous = comps(now), comps(before)
    names = {"auto_fix_share": ("auto_fixed", "failures"), "dag_coverage": ("profiled_dags", "active_dags"),
             "fix_coverage": ("covered_signatures", "signatures")}
    return {"score": score(current), "previous_score": score(previous),
            "components": {k: {"value": current[k], "previous": previous[k], "numerator": now.get(n),
                               "denominator": now.get(d)} for k, (n, d) in names.items()},
            "counts": {k: now.get(k) for k in ("knowledge_items", "knowledge_new", "known_fixes", "runbooks")}}
