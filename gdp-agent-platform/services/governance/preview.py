"""What a change request will change, before and after, for the Approvals page.

A queued request stores the original call (method, path and JSON body). Showing that JSON makes an approver read
the API; this module turns it into labelled rows instead: for the store edits it knows (an STTM edit, a Data
Quality schedule, a run's check decisions) the current state beside the requested one, and for any other call its
fields and query parameters. Pure: the API passes a loader that reads the current state (see `build`).

Every preview has the same shape, rendered by apps/web/app/approvals/request-preview.tsx:

  {"kind": STTM_EDIT | DQ_SCHEDULE | DQ_SCHEDULE_DELETE | RUN_CHECK_DECISIONS | GENERIC,
   "headline": one line, "warnings": [text], "link": {"href", "label"} or None,
   "sections": [{"title", "rows": [{"label", "before", "after", "change": added|removed|changed|set, "note"}]}]}
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

MAX_ROWS = 60
MAX_TEXT = 400

STTM_EDIT = re.compile(r"^/api/hub/sttm/([^/]+)/edit$")
DQ_SCHEDULE = re.compile(r"^/api/dq/schedules$")
RUN_CHECKS = re.compile(r"^/api/dq/run-checks/decisions$")

LINE_FIELDS = ("source_table", "source_column", "mapping_type", "transformation", "target_datatype", "business_rule",
               "filter_rule")


def text(value: Any) -> Optional[str]:
    """A value as an approver reads it: scalars as is, lists and objects as compact JSON, clipped."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
            out = ", ".join(str(v) for v in value)
        else:
            out = json.dumps(value, default=str, separators=(", ", ": "))
    else:
        out = str(value)
    return out if len(out) <= MAX_TEXT else out[:MAX_TEXT - 1] + "…"


def row(label: str, before: Any = None, after: Any = None, change: Optional[str] = None, note: Any = None) -> Dict[str, Any]:
    b, a = text(before), text(after)
    if change is None:
        change = "added" if b is None and a is not None else "removed" if a is None and b is not None else "changed"
    return {"label": label, "before": b, "after": a, "change": change, "note": text(note)}


def split(path: str) -> tuple:
    """(path without the query, {query parameter: value})."""
    parts = urlsplit(path or "")
    return parts.path, dict(parse_qsl(parts.query, keep_blank_values=True))


def kind_for(method: str, path: str) -> str:
    method, bare = str(method or "").upper(), split(path)[0]
    if method == "POST" and STTM_EDIT.match(bare):
        return "STTM_EDIT"
    if DQ_SCHEDULE.match(bare):
        return "DQ_SCHEDULE" if method == "PUT" else "DQ_SCHEDULE_DELETE" if method == "DELETE" else "GENERIC"
    if method == "POST" and RUN_CHECKS.match(bare):
        return "RUN_CHECK_DECISIONS"
    return "GENERIC"


def _flatten(value: Any, prefix: str, out: List[Dict[str, Any]]) -> None:
    if len(out) >= MAX_ROWS:
        return
    if isinstance(value, dict) and value and len(json.dumps(value, default=str)) > 120:
        for k, v in value.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value) and len(value) <= 20:
        for i, v in enumerate(value):
            _flatten(v, f"{prefix}[{i + 1}]", out)
    else:
        out.append(row(prefix.replace("_", " ") or "body", after=value, change="set"))


def generic(method: str, path: str, payload: Any) -> Dict[str, Any]:
    """Any other call: its query parameters and body fields as labelled rows."""
    bare, query = split(path)
    sections = []
    if query:
        sections.append({"title": "Parameters", "rows": [row(k.replace("_", " "), after=v, change="set") for k, v in query.items()]})
    fields: List[Dict[str, Any]] = []
    if payload not in (None, {}, []):
        _flatten(payload if isinstance(payload, (dict, list)) else {"value": payload}, "", fields)
    if fields:
        more = len(fields) >= MAX_ROWS
        sections.append({"title": "Requested values" + (" (first 60)" if more else ""), "rows": fields})
    return {"kind": "GENERIC", "headline": f"{str(method).upper()} {bare}", "warnings": [] if sections else
            ["This call has no body or parameters; approving it runs it as it is."], "link": None, "sections": sections}


# ---------------------------------------------------------------- STTM edit


def _line_text(line: Dict[str, Any]) -> str:
    source = ".".join(str(p) for p in (line.get("source_table"), line.get("source_column")) if p) or "no source"
    how = line.get("transformation") or str(line.get("mapping_type") or "").lower() or "direct"
    return f"{source} ({how})"


def _joins_text(design: Dict[str, Any]) -> Optional[str]:
    graph = (design or {}).get("join_graph") or {}
    joins = graph.get("joins") or []
    return "; ".join(f"{str(j.get('join_type') or 'LEFT').upper()} {j.get('right_table')} ON {j.get('condition')}"
                     for j in joins) or None


def sttm_preview(base: Dict[str, Any], latest: Dict[str, Any], payload: Dict[str, Any], pending: bool) -> Dict[str, Any]:
    """An STTM store edit against the version it was made on: lines edited, added and removed, design and join plan."""
    from services.catalog import sttm as cat_sttm

    warnings: List[str] = []
    if pending and int(latest.get("version") or 0) != int(payload.get("base_version") or 0):
        warnings.append(f"Version {latest.get('version')} was saved after this request was made on v{payload.get('base_version')}. "
                        "Approving it will fail; reject it and ask for the change to be made again on the latest version.")
    target = latest.get("target_entity") or base.get("target_entity") or "mapping"
    link = {"href": f"/dashboard/sttm/{latest.get('catalog_id') or base.get('catalog_id')}", "label": "Open in STTM Store"}
    plan_payload = {k: v for k, v in payload.items() if k in ("edits", "add_lines", "remove_lines", "design", "joins")}
    try:
        plan = cat_sttm.plan_edit(base, plan_payload, {})
    except (AssertionError, ValueError) as exc:
        out = generic("POST", "", payload)
        out.update(kind="STTM_EDIT", headline=f"STTM edit of {target}", link=link,
                   warnings=warnings + [f"This change no longer applies to v{base.get('version')}: {exc}"])
        return out
    d = plan["diff"]
    sections = []
    if d["changed"]:
        sections.append({"title": "Lines changed", "rows": [
            row(f"{c['target_column']} · {f.replace('_', ' ')}", c["from"].get(f), c["to"].get(f), "changed")
            for c in d["changed"] for f in c["fields"]][:MAX_ROWS]})
    if d["added"]:
        sections.append({"title": "Lines added", "rows": [row(l["target_column"], None, _line_text(l), "added",
                                                              l.get("business_rule")) for l in d["added"]][:MAX_ROWS]})
    if d["removed"]:
        sections.append({"title": "Lines removed", "rows": [row(l["target_column"], _line_text(l), None, "removed")
                                                            for l in d["removed"]][:MAX_ROWS]})
    old_design, new_design = dict(base.get("table_design") or {}), dict(plan["design"] or {})
    if payload.get("design"):
        keys = [k for k in payload["design"] if k not in ("join_graph",)]
        rows = [row(k.replace("_", " "), old_design.get(k), new_design.get(k)) for k in keys
                if text(old_design.get(k)) != text(new_design.get(k))]
        if rows:
            sections.append({"title": "Table design", "rows": rows})
    if payload.get("joins"):
        rows = []
        if text(old_design.get("driving_table")) != text(new_design.get("driving_table")):
            rows.append(row("driving table", old_design.get("driving_table"), new_design.get("driving_table")))
        if _joins_text(old_design) != _joins_text(new_design):
            rows.append(row("joins", _joins_text(old_design), _joins_text(new_design)))
        sections.append({"title": "Join plan", "rows": rows or [row("joins", _joins_text(old_design), _joins_text(new_design),
                                                                     "set", "the join plan is unchanged")]})
    if payload.get("note"):
        sections.append({"title": "Requester's note", "rows": [row("note", after=payload["note"], change="set")]})
    return {"kind": "STTM_EDIT", "headline": f"STTM edit of {target} on v{base.get('version')}: {d['summary']}",
            "warnings": warnings, "link": link, "sections": sections}


# ---------------------------------------------------------------- Data Quality schedule


def schedule_preview(method: str, path: str, payload: Any, current: Optional[Dict[str, Any]],
                     active_checks: Optional[int]) -> Dict[str, Any]:
    _, query = split(path)
    table = (payload or {}).get("table") if isinstance(payload, dict) else None
    table = table or query.get("table") or "the table"
    before = (current or {}).get("cron")
    link = {"href": "/dashboard/dq-store", "label": "Open Data Quality Store"}
    if str(method).upper() == "DELETE":
        warnings = [] if current else ["This table has no schedule now; approving the request will fail."]
        return {"kind": "DQ_SCHEDULE_DELETE", "headline": f"Stop the scheduled checks of {table}", "warnings": warnings,
                "link": link, "sections": [{"title": "Schedule", "rows": [row("cron", before, None, "removed",
                                                                           "the Snowflake task is dropped")]}]}
    after = str((payload or {}).get("cron") or "").strip()
    warnings = []
    if active_checks == 0:
        warnings.append("The table has no active check now; approving the request will fail.")
    if before and before == after:
        warnings.append("The table already runs on this schedule.")
    return {"kind": "DQ_SCHEDULE", "headline": f"Schedule the checks of {table}", "warnings": warnings, "link": link,
            "sections": [{"title": "Schedule", "rows": [row("cron", before, after, None if before != after else "set",
                                                             f"{active_checks} active check(s)" if active_checks else None)]}]}


# ---------------------------------------------------------------- run check decisions


def _check_text(e: Dict[str, Any]) -> str:
    definition = e.get("definition") if e.get("definition") is not None else e.get("check_definition")
    parts = [str(e.get("status") or "").lower(), str(e.get("severity") or "")]
    if definition:
        parts.append(text(definition) or "")
    return ", ".join(p for p in parts if p)


def decisions_preview(payload: Dict[str, Any], current: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Each decision beside the check as it is now (status, severity, definition)."""
    decisions = (payload or {}).get("decisions") or []
    run_id = str((payload or {}).get("run_id") or "")
    rows, missing = [], 0
    for d in decisions[:MAX_ROWS]:
        e = current.get(str(d.get("expectation_id"))) or {}
        if not e:
            missing += 1
        label = " · ".join(str(p) for p in (e.get("target_column") or "table", e.get("check_type")) if p) \
            + f" ({str(d.get('expectation_id'))[:8]})"
        after = [str(d.get("decision") or "").lower(), str(d.get("severity") or e.get("severity") or "")]
        if d.get("definition") is not None:
            after.append(text(d["definition"]) or "")
        note = "; ".join(p for p in (d.get("justification"), f"requirement: {d['requirement']}" if d.get("requirement") else None) if p)
        rows.append(row(label, _check_text(e) if e else None, ", ".join(p for p in after if p), "changed" if e else "set", note))
    counts: Dict[str, int] = {}
    for d in decisions:
        counts[str(d.get("decision") or "?").lower()] = counts.get(str(d.get("decision") or "?").lower(), 0) + 1
    warnings = [f"{missing} check(s) were not found on the run; they may have been replaced since."] if missing else []
    return {"kind": "RUN_CHECK_DECISIONS",
            "headline": f"{len(decisions)} check decision(s) on run {run_id[:8]}: "
                        + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())),
            "warnings": warnings, "link": {"href": f"/runs/{run_id}", "label": "Open run"} if run_id else None,
            "sections": [{"title": "Decisions", "rows": rows}] if rows else []}


# ---------------------------------------------------------------- entry point


class Loader:
    """What `build` reads; the API implements it with its connector. Each method may raise; the preview then
    falls back to the request's fields."""

    def sttm_version(self, catalog_id: str, version: Optional[int] = None) -> Dict[str, Any]: ...
    def dq_schedule(self, table: str) -> Optional[Dict[str, Any]]: ...
    def dq_active_checks(self, table: str) -> int: ...
    def run_checks(self, run_id: str, ids: List[str]) -> Dict[str, Dict[str, Any]]: ...


def build(request: Dict[str, Any], load: Loader) -> Dict[str, Any]:
    method, path, payload = request.get("method") or "", request.get("path") or "", request.get("payload")
    pending = request.get("status") == "PENDING"
    kind = kind_for(method, path)
    try:
        if kind == "STTM_EDIT" and isinstance(payload, dict):
            catalog_id = STTM_EDIT.match(split(path)[0]).group(1)
            latest = load.sttm_version(catalog_id)
            base = load.sttm_version(catalog_id, int(payload.get("base_version") or latest["version"]))
            return sttm_preview(base, latest, payload, pending)
        if kind in ("DQ_SCHEDULE", "DQ_SCHEDULE_DELETE"):
            table = (payload or {}).get("table") if isinstance(payload, dict) else None
            table = table or split(path)[1].get("table") or ""
            active = load.dq_active_checks(table) if kind == "DQ_SCHEDULE" and pending else None
            return schedule_preview(method, path, payload, load.dq_schedule(table), active)
        if kind == "RUN_CHECK_DECISIONS" and isinstance(payload, dict):
            ids = [str(d.get("expectation_id")) for d in payload.get("decisions") or [] if d.get("expectation_id")]
            return decisions_preview(payload, load.run_checks(str(payload.get("run_id") or ""), ids))
    except Exception as exc:  # the current state could not be read: still show what was asked
        out = generic(method, path, payload)
        out["warnings"] = [f"The current state could not be read ({str(exc)[:200]}); showing the requested values only."]
        return out
    return generic(method, path, payload)


def result_summary(result: Any) -> List[Dict[str, Any]]:
    """The applied call's response as a few labelled rows (top-level values only)."""
    body = (result or {}).get("body") if isinstance(result, dict) else None
    if not isinstance(body, dict):
        return [row("response", after=body, change="set")] if body not in (None, "") else []
    out = []
    for k, v in body.items():
        if isinstance(v, (dict, list)) and len(json.dumps(v, default=str)) > 160:
            out.append(row(k.replace("_", " "), after=f"{len(v)} item(s)" if isinstance(v, list) else "(details)", change="set"))
        else:
            out.append(row(k.replace("_", " "), after=v, change="set"))
        if len(out) >= 12:
            break
    return out
