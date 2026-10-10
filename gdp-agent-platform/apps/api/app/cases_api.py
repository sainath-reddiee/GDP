"""Cases: one record per reported problem from any source (Jira, an incident, a failing QA test or data quality check,
or "Report a problem" in the app), tracked from report to verified fix (PR Q1). Storage is the CASES schema (V034);
the workflow is services/cases (rules, service, store).

Governance (services.governance.policy): reads are open to everyone signed in and filtered by domain visibility
(services/governance/domains.py; a case the caller cannot see answers 404). Writes need CASE.WORK. The status route
also needs CASE.RESOLVE, checked here, to move a case to VERIFIED, RESOLVED, CLOSED or DUPLICATE; merging needs
CASE.RESOLVE. Opening from Jira needs JIRA.READ (the issue is read as the caller); opening from an incident needs
OPS.VIEW. Descriptions, comments and page context are redacted (services/ops/redact.py) before they are stored.
Calls run on the caller's session, so the CASES.DOMAIN_SCOPE row policy applies to every read as well.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.db import Db
from app.main import _config, current_db
from services.cases import rules
from services.cases import service as svc
from services.cases.store import CASE_SELECT, SqlStore
from services.governance import domains
from services.ops.redact import redact, redact_payload

router = APIRouter()
LIST_LIMIT = 200
Kind = Literal["DATA_BUG", "CODE_BUG", "DATA_QUALITY", "PIPELINE", "QUESTION"]
Severity = Literal["P1", "P2", "P3", "P4"]
Source = Literal["APP_REPORT", "JIRA", "INCIDENT", "QA_FAILURE", "DQ_FAILURE"]
LinkKind = Literal["JIRA", "INCIDENT", "QA_RESULT", "DQ_RESULT", "RUN", "PR", "KNOWLEDGE", "CASE"]


# ---------------------------------------------------------------- plumbing

def _who(db: Db) -> Dict[str, Any]:
    """The caller's user, app roles and privileges. Before governance is deployed (V020) everyone holds everything,
    as in the middleware."""
    from app.governance import identity, ready

    try:
        if ready(db):
            who = identity(db)
            return {"user": who["user"], "roles": set(who["roles"]), "privileges": set(who["privileges"])}
    except Exception:
        pass
    return {"user": str(db.user or "").upper(), "roles": set(), "privileges": {"*"}}


def _holds(who: Dict[str, Any], privilege: str) -> bool:
    return privilege in who["privileges"] or "*" in who["privileges"]


def _need(who: Dict[str, Any], privilege: str, what: str) -> None:
    if not _holds(who, privilege):
        raise HTTPException(403, f"Not allowed: {what} needs the {privilege} privilege.")


def _can_see(db: Db, who: Dict[str, Any], domain_id: Optional[str]) -> bool:
    return domains.can_see(db, who["user"], domain_id, who["privileges"], who["roles"])


def _http(exc: svc.CaseError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _cfg(db: Db) -> Dict[str, Any]:
    value = _config(db, "CASES", {}) or {}
    return value if isinstance(value, dict) else {}


def _jira_site(db: Db) -> Optional[str]:
    try:
        from app import jira_api

        return str(jira_api._config(db).get("site_url") or "").rstrip("/") or None
    except Exception:
        return None


def _csv(value: Optional[str], allowed: tuple, name: str) -> List[str]:
    items = [v.strip().upper() for v in (value or "").split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise HTTPException(422, f"{name} must be among {', '.join(allowed)}")
    return items


def case_out(r: Dict[str, Any]) -> Dict[str, Any]:
    """The list shape. number is the display id (CASE-<n>); number_value is the plain integer."""
    return {"case_id": r["case_id"], "number": rules.case_ref(r.get("case_number")),
            "number_value": int(r["case_number"]) if r.get("case_number") is not None else None,
            "domain_id": r.get("domain_id"), "domain_name": r.get("domain_name"), "title": r.get("title"),
            "kind": r.get("kind"), "source": r.get("source"), "status": r.get("status"), "severity": r.get("severity"),
            "assignee": r.get("assignee"), "team_id": r.get("team_id"), "sla_due_at": _iso(r.get("sla_due_at")),
            "sla_breached": bool(r.get("sla_breached")), "target_table_id": r.get("target_table_id"),
            "target_fqn": r.get("target_fqn"), "run_id": r.get("run_id"), "ai_summary": r.get("ai_summary"),
            "opened_by": r.get("opened_by"), "opened_at": _iso(r.get("opened_at")), "updated_at": _iso(r.get("updated_at")),
            "links_count": int(r.get("links_count") or 0)}


def _models(value: Any) -> List[str]:
    from app.main import _json

    value = _json(value) if isinstance(value, str) else value
    return [str(v) for v in value] if isinstance(value, list) else []


def case_detail_out(r: Dict[str, Any]) -> Dict[str, Any]:
    from app.main import _json

    ai = _json(r.get("ai")) if isinstance(r.get("ai"), str) else r.get("ai")
    page = _json(r.get("page_context")) if isinstance(r.get("page_context"), str) else r.get("page_context")
    return {**case_out(r), "description": r.get("description"), "models": _models(r.get("models")),
            "repo_id": r.get("repo_id"), "fingerprint": r.get("fingerprint"), "duplicate_of": r.get("duplicate_of"),
            "resolution": r.get("resolution"), "ai": ai, "source_ref": r.get("source_ref"), "page_context": page,
            "resolved_by": r.get("resolved_by"), "resolved_at": _iso(r.get("resolved_at")),
            "closed_at": _iso(r.get("closed_at")), "reopened": int(r.get("reopened") or 0)}


def _visible_case(db: Db, who: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    found = SqlStore(db).get(case_id)
    if not found or not _can_see(db, who, found.get("domain_id")):
        raise HTTPException(404, "Case not found")
    return found


# ---------------------------------------------------------------- scope: domain, table and run

def _table(db: Db, target_table_id: str) -> Dict[str, Any]:
    found = db.query("""SELECT TARGET_TABLE_ID, DOMAIN_ID, TARGET_DATABASE || '.' || TARGET_SCHEMA || '.' || TARGET_TABLE AS FQN
                          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = %s""", (target_table_id,))
    if not found:
        raise HTTPException(400, f"Unknown target table {target_table_id}")
    return found[0]


def _table_by_name(db: Db, name: Optional[str], domain_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A registered table from a name (DB.SCHEMA.TABLE, SCHEMA.TABLE or TABLE); only an unambiguous match."""
    parts = [p.strip().strip('"').upper() for p in str(name or "").split(".") if p.strip()]
    if not parts or len(parts) > 3:
        return None
    cols = ["UPPER(TARGET_TABLE)", "UPPER(TARGET_SCHEMA)", "UPPER(TARGET_DATABASE)"][:len(parts)]
    where = [f"{c} = %s" for c in cols]
    params: List[Any] = list(reversed(parts))
    if domain_id:
        where.append("DOMAIN_ID = %s")
        params.append(domain_id)
    try:
        found = db.query(f"""SELECT TARGET_TABLE_ID, DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY
                              WHERE {' AND '.join(where)} AND COALESCE(ACTIVE_FLAG, TRUE) LIMIT 2""", tuple(params))
    except Exception:
        return None
    return found[0] if len(found) == 1 else None


def _run_domain(db: Db, run_id: str) -> Optional[str]:
    found = db.query("SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (run_id,))
    if not found:
        raise HTTPException(400, f"Unknown run {run_id}")
    return found[0].get("domain_id")


def _scope(db: Db, who: Dict[str, Any], domain_id: Optional[str], target_table_id: Optional[str],
           run_id: Optional[str], table_name: Optional[str] = None) -> Dict[str, Any]:
    """{domain_id, target_table_id, run_id}: the domain given, else the target table's, else the run's, else GENERAL.
    404 when the caller cannot see the domain."""
    inferred = None
    if target_table_id:
        inferred = _table(db, target_table_id).get("domain_id")
    elif table_name:
        found = _table_by_name(db, table_name)
        if found:
            target_table_id, inferred = found["target_table_id"], found.get("domain_id")
    if run_id:
        run_domain = _run_domain(db, run_id)
        inferred = inferred or run_domain
    if domain_id and domain_id not in domains.domain_names(db):
        raise HTTPException(404, "Domain not found")
    domain_id = domain_id or inferred or domains.general_domain_id(db)
    if not _can_see(db, who, domain_id):
        raise HTTPException(404, "Domain not found")
    return {"domain_id": domain_id, "target_table_id": target_table_id, "run_id": run_id}


def _open(db: Db, who: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    try:
        case, created, duplicate_of = svc.open_case(SqlStore(db), spec, who["user"],
                                                    lambda d: _can_see(db, who, d), _cfg(db))
    except svc.CaseError as exc:
        raise _http(exc) from None
    out: Dict[str, Any] = {"case": case_detail_out(case), "created": created}
    if duplicate_of:
        out["duplicate_of"] = duplicate_of
    return out


# ---------------------------------------------------------------- settings (PR Q2), registered before /api/cases/{case_id}

class CaseSettingsIn(BaseModel):
    auto_triage: Optional[bool] = None
    auto_triage_severities: Optional[List[Severity]] = Field(default=None, max_length=4)
    sla_hours: Optional[Dict[str, int]] = None


def _settings_out(db: Db) -> Dict[str, Any]:
    from services.cases.triage import settings_from

    out = settings_from(_cfg(db))
    try:
        seen = db.query("SELECT MAX(LAST_RUN_AT) AS AT FROM OPS.JOB_LEASE WHERE JOB_NAME = 'worker'")
        out["worker_seen_at"] = _iso(seen[0].get("at")) if seen else None
    except Exception:
        out["worker_seen_at"] = None
    return out


@router.get("/api/cases/settings")
def get_case_settings(db: Db = Depends(current_db)):
    """{auto_triage, auto_triage_severities, sla_hours:{P1..P4}, worker_seen_at} (CORE.PLATFORM_CONFIG key CASES)."""
    return _settings_out(db)


@router.put("/api/cases/settings")
def put_case_settings(body: CaseSettingsIn, db: Db = Depends(current_db)):
    """Change the fields given (INTEGRATION.MANAGE). sla_hours: hours per severity, 1 to 8760."""
    from app.main import _set_config

    current = dict(_cfg(db))
    given = body.model_dump(exclude_unset=True)
    if given.get("auto_triage") is not None:
        current["auto_triage"] = bool(given["auto_triage"])
    if given.get("auto_triage_severities") is not None:
        wanted = set(given["auto_triage_severities"])
        current["auto_triage_severities"] = [s for s in rules.SEVERITIES if s in wanted]
    if given.get("sla_hours") is not None:
        hours = {}
        for key, value in given["sla_hours"].items():
            key = str(key).upper()
            if key not in rules.SEVERITIES:
                raise HTTPException(422, "sla_hours keys must be among P1, P2, P3, P4")
            if not 1 <= int(value) <= 24 * 365:
                raise HTTPException(422, "sla_hours values must be between 1 and 8760 hours")
            hours[key] = int(value)
        current["sla_hours"] = {**rules.sla_hours(current), **hours}
    _set_config(db, "CASES", current, "Cases: AI auto triage (on, severities) and SLA hours per severity")
    return _settings_out(db)


# ---------------------------------------------------------------- list, summary, detail

@router.get("/api/cases")
def list_cases(status: Optional[str] = Query(default=None, max_length=200), severity: Optional[str] = Query(default=None, max_length=40),
               kind: Optional[str] = Query(default=None, max_length=200), domain_id: Optional[str] = Query(default=None, max_length=36),
               mine: bool = False, team_id: Optional[str] = Query(default=None, max_length=64),
               q: Optional[str] = Query(default=None, max_length=200), sla: Optional[Literal["breached"]] = None,
               limit: int = Query(default=50, ge=1, le=LIST_LIMIT), offset: int = Query(default=0, ge=0),
               db: Db = Depends(current_db)):
    """Cases the caller may see, most severe first. status is a comma list (default: the open statuses; any: all)."""
    who = _who(db)
    visible = domains.visible_domain_ids(db, who["user"], who["privileges"], who["roles"])
    where, params = [], []
    cond, cparams = domains.visibility_sql("C.DOMAIN_ID", visible)
    where.append(cond)
    params += list(cparams)
    if (status or "").strip().lower() != "any":
        statuses = _csv(status, rules.STATUSES, "status") or list(rules.OPEN_STATUSES)
        where.append(f"C.STATUS IN ({', '.join(['%s'] * len(statuses))})")
        params += statuses
    for column, value, allowed, name in (("C.SEVERITY", severity, rules.SEVERITIES, "severity"),
                                         ("C.KIND", kind, rules.KINDS, "kind")):
        items = _csv(value, allowed, name)
        if items:
            where.append(f"{column} IN ({', '.join(['%s'] * len(items))})")
            params += items
    for column, value in (("C.DOMAIN_ID", domain_id), ("C.TEAM_ID", team_id)):
        if value:
            where.append(f"{column} = %s")
            params.append(value)
    if mine:
        where.append("UPPER(C.ASSIGNEE) = %s")
        params.append(who["user"])
    if sla == "breached":
        where.append(f"C.STATUS IN ({', '.join(['%s'] * len(rules.OPEN_STATUSES))}) AND C.SLA_DUE_AT < CURRENT_TIMESTAMP()")
        params += list(rules.OPEN_STATUSES)
    if q and q.strip():
        text = q.strip()
        number = text.upper().removeprefix("CASE-")
        like = f"%{text}%"
        if number.isdigit() and text.upper().startswith("CASE-"):
            where.append("C.CASE_NUMBER = %s")   # CASE-<n> finds exactly that case (merge resolves it this way)
            params.append(int(number))
        elif number.isdigit():
            where.append("(C.TITLE ILIKE %s OR C.SOURCE_REF ILIKE %s OR C.CASE_NUMBER = %s)")
            params += [like, like, int(number)]
        else:
            where.append("(C.TITLE ILIKE %s OR C.SOURCE_REF ILIKE %s)")
            params += [like, like]
    clause = " WHERE " + " AND ".join(where)
    rows = db.query(CASE_SELECT + clause + f" ORDER BY C.SEVERITY, C.OPENED_AT DESC LIMIT {int(limit)} OFFSET {int(offset)}",
                    tuple(params))
    total = db.query("SELECT COUNT(*) AS N FROM CASES.CASE_RECORD C" + clause, tuple(params))
    return {"cases": [case_out(r) for r in rows], "total": int(total[0]["n"] or 0) if total else 0}


@router.get("/api/cases/summary")
def cases_summary(db: Db = Depends(current_db)):
    """{open, mine, p1_open, sla_breached, by_status} over the cases the caller may see."""
    who = _who(db)
    visible = domains.visible_domain_ids(db, who["user"], who["privileges"], who["roles"])
    cond, cparams = domains.visibility_sql("C.DOMAIN_ID", visible)
    opened = ", ".join(f"'{s}'" for s in rules.OPEN_STATUSES)   # constants
    rows = db.query(f"""SELECT C.STATUS, COUNT(*) AS N,
                               COUNT_IF(C.STATUS IN ({opened}) AND UPPER(C.ASSIGNEE) = %s) AS MINE,
                               COUNT_IF(C.STATUS IN ({opened}) AND C.SEVERITY = 'P1') AS P1,
                               COUNT_IF(C.STATUS IN ({opened}) AND C.SLA_DUE_AT < CURRENT_TIMESTAMP()) AS BREACHED
                          FROM CASES.CASE_RECORD C WHERE {cond} GROUP BY C.STATUS""", (who["user"], *cparams))
    by_status = {s: 0 for s in rules.STATUSES}
    out = {"open": 0, "mine": 0, "p1_open": 0, "sla_breached": 0}
    for r in rows:
        n = int(r.get("n") or 0)
        by_status[str(r.get("status"))] = by_status.get(str(r.get("status")), 0) + n
        if r.get("status") in rules.OPEN_STATUSES:
            out["open"] += n
        out["mine"] += int(r.get("mine") or 0)
        out["p1_open"] += int(r.get("p1") or 0)
        out["sla_breached"] += int(r.get("breached") or 0)
    return {**out, "by_status": by_status}


@router.get("/api/cases/{case_id}")
def case_detail(case_id: str, db: Db = Depends(current_db)):
    who = _who(db)
    case = _visible_case(db, who, case_id)
    store = SqlStore(db)
    events = [{"event_id": e["event_id"], "kind": e.get("kind"), "actor": e.get("actor"), "detail": e.get("detail"),
               "created_at": _iso(e.get("created_at"))} for e in store.events(case_id)]
    links = [{"link_id": link["link_id"], "kind": link.get("kind"), "ref": link.get("ref"), "label": link.get("label"),
              "url": link.get("url"), "state": link.get("state") or "OK"} for link in store.links(case_id)]
    from services.cases.triage import artifact_out

    artifacts = [artifact_out(a) for a in store.artifacts(case_id)]
    return {"case": case_detail_out(case), "events": events, "links": links, "artifacts": artifacts}


# ---------------------------------------------------------------- create and edit

class PageContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: Optional[str] = Field(default=None, max_length=500)
    run_id: Optional[str] = Field(default=None, max_length=36)
    table: Optional[str] = Field(default=None, max_length=500)
    check_id: Optional[str] = Field(default=None, max_length=64)
    test_id: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=500)


class CaseIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20000)
    kind: Optional[Kind] = None
    severity: Optional[Severity] = None
    domain_id: Optional[str] = Field(default=None, max_length=36)
    target_table_id: Optional[str] = Field(default=None, max_length=36)
    run_id: Optional[str] = Field(default=None, max_length=36)
    source: Source = "APP_REPORT"
    source_ref: Optional[str] = Field(default=None, max_length=500)
    page_context: Optional[PageContext] = None


def _redacted(text: Optional[str], limit: int) -> Optional[str]:
    return redact(text)[:limit] if text and text.strip() else None


@router.post("/api/cases")
def create_case(body: CaseIn, db: Db = Depends(current_db)):
    """Open a case, or return the open case it duplicates (created false; duplicate_of on a fingerprint match)."""
    who = _who(db)
    page = rules.clean_page_context(body.page_context.model_dump() if body.page_context else None)
    run_id = body.run_id or (page or {}).get("run_id")
    scope = _scope(db, who, body.domain_id, body.target_table_id, run_id, (page or {}).get("table"))
    title = redact(body.title.strip())[:300]
    if len(title) < 3:
        raise HTTPException(422, "title needs at least 3 characters")
    spec = {"title": title, "description": _redacted(body.description, 20000), "kind": body.kind or "DATA_BUG",
            "severity": body.severity or rules.DEFAULT_SEVERITY, "source": body.source,
            "source_ref": (body.source_ref or "").strip() or None,
            "page_context": redact_payload(page) if page else None, **scope}
    if scope.get("run_id"):
        spec["links"] = [{"kind": "RUN", "ref": scope["run_id"], "url": rules.link_url("RUN", scope["run_id"])}]
    return _open(db, who, spec)


class CaseEdit(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20000)
    kind: Optional[Kind] = None
    severity: Optional[Severity] = None
    domain_id: Optional[str] = Field(default=None, max_length=36)
    target_table_id: Optional[str] = Field(default=None, max_length=36)
    team_id: Optional[str] = Field(default=None, max_length=64)


@router.put("/api/cases/{case_id}")
def update_case(case_id: str, body: CaseEdit, db: Db = Depends(current_db)):
    """Change the fields given (null clears target_table_id and team_id; title, kind, severity and domain cannot be
    cleared)."""
    who = _who(db)
    case = _visible_case(db, who, case_id)
    given = body.model_dump(exclude_unset=True)
    changes: Dict[str, Any] = {}
    for key in ("title", "kind", "severity"):
        if given.get(key):
            changes[key.upper()] = redact(given[key].strip())[:300] if key == "title" else given[key]
    if "description" in given:
        changes["DESCRIPTION"] = _redacted(given["description"], 20000)
    if given.get("domain_id"):
        if given["domain_id"] not in domains.domain_names(db) or not _can_see(db, who, given["domain_id"]):
            raise HTTPException(404, "Domain not found")
        changes["DOMAIN_ID"] = given["domain_id"]
    if "target_table_id" in given:
        if given["target_table_id"]:
            _table(db, given["target_table_id"])
        changes["TARGET_TABLE_ID"] = given["target_table_id"] or None
    if "team_id" in given:
        changes["TEAM_ID"] = given["team_id"] or None
    try:
        updated = svc.edit(SqlStore(db), case, changes, who["user"], _cfg(db))
    except svc.CaseError as exc:
        raise _http(exc) from None
    return {"case": case_detail_out(updated)}


class AssignIn(BaseModel):
    assignee: Optional[str] = Field(default=None, max_length=256)


@router.post("/api/cases/{case_id}/assign")
def assign_case(case_id: str, body: AssignIn, db: Db = Depends(current_db)):
    """Assign to a user (empty unassigns). The assignee must be able to see the case's domain."""
    who = _who(db)
    case = _visible_case(db, who, case_id)
    assignee = (body.assignee or "").strip().upper() or None
    if assignee and not domains.can_see(db, assignee, case.get("domain_id"), (), domains.direct_roles(db, assignee)):
        raise HTTPException(400, f"{assignee} is not a member of this case's domain, so they could not see it.")
    return {"case": case_detail_out(svc.assign(SqlStore(db), case, assignee, who["user"]))}


class StatusIn(BaseModel):
    status: Literal["NEW", "TRIAGED", "IN_PROGRESS", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED", "RESOLVED", "CLOSED", "DUPLICATE"]
    note: Optional[str] = Field(default=None, max_length=4000)
    override_reason: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/cases/{case_id}/status")
def case_status(case_id: str, body: StatusIn, db: Db = Depends(current_db)):
    """Move a case along its workflow (services/cases/rules.py lists the allowed changes)."""
    who = _who(db)
    case = _visible_case(db, who, case_id)
    try:
        updated = svc.change_status(SqlStore(db), case, body.status, who["user"], who["privileges"],
                                    _redacted(body.note, 4000), _redacted(body.override_reason, 2000))
    except svc.CaseError as exc:
        raise _http(exc) from None
    out: Dict[str, Any] = {"case": case_detail_out(updated)}
    if body.status == "RESOLVED":
        out["knowledge"] = _propose_knowledge(db, updated, who["user"])
    return out


def _propose_knowledge(db: Db, case: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
    """CASE_RESOLUTION and QA_TEST knowledge for the domain's stewards; a failure never undoes the resolve."""
    from services.cases.fixes import on_resolved
    from services.cases.triage import _ai

    try:
        return on_resolved(db, {**case, "ai": _ai(case)}, actor)
    except Exception as exc:
        try:
            SqlStore(db).event(case["case_id"], "knowledge_failed", actor,
                               {"error": redact(f"{type(exc).__name__}: {exc}")[:300]})
        except Exception:
            pass
        return None


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/api/cases/{case_id}/comment")
def case_comment(case_id: str, body: CommentIn, db: Db = Depends(current_db)):
    who = _who(db)
    case = _visible_case(db, who, case_id)
    text = _redacted(body.text, 8000)
    if not text:
        raise HTTPException(422, "text is required")
    svc.comment(SqlStore(db), case, text, who["user"])
    return {"ok": True}


class LinkIn(BaseModel):
    kind: LinkKind
    ref: str = Field(min_length=1, max_length=1000)
    label: Optional[str] = Field(default=None, max_length=500)


@router.post("/api/cases/{case_id}/links")
def case_link(case_id: str, body: LinkIn, db: Db = Depends(current_db)):
    """Link a Jira issue, incident, result, run, pull request, knowledge item or another case (stored once)."""
    who = _who(db)
    case = _visible_case(db, who, case_id)
    ref, label = body.ref.strip(), body.label
    if body.kind == "JIRA":
        from services.jira.client import check_key

        try:
            ref = check_key(ref)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    elif body.kind == "CASE":
        if ref == case_id:
            raise HTTPException(400, "A case cannot link to itself.")
        other = _visible_case(db, who, ref)
        label = label or rules.case_ref(other.get("case_number"))
    elif body.kind == "PR" and not ref.startswith("https://"):
        raise HTTPException(400, "A pull request link must be an https URL.")
    url = rules.link_url(body.kind, ref, _jira_site(db) if body.kind == "JIRA" else None)
    added = svc.add_link(SqlStore(db), case, body.kind, ref, label, url, who["user"])
    return {"added": added, "links": SqlStore(db).links(case_id)}


@router.delete("/api/cases/{case_id}/links/{link_id}")
def case_unlink(case_id: str, link_id: str, db: Db = Depends(current_db)):
    who = _who(db)
    case = _visible_case(db, who, case_id)
    try:
        svc.remove_link(SqlStore(db), case, link_id, who["user"])
    except svc.CaseError as exc:
        raise _http(exc) from None
    return {"removed": link_id}


class MergeIn(BaseModel):
    into_case_id: str = Field(min_length=1, max_length=36)


@router.post("/api/cases/{case_id}/merge")
def case_merge(case_id: str, body: MergeIn, db: Db = Depends(current_db)):
    """Mark this case a DUPLICATE of another, link both ways and move this case's links to the other one."""
    who = _who(db)
    case = _visible_case(db, who, case_id)
    into = _visible_case(db, who, body.into_case_id)
    try:
        merged = svc.merge(SqlStore(db), case, into, who["user"], who["privileges"])
    except svc.CaseError as exc:
        raise _http(exc) from None
    return {"case": case_detail_out(merged), "into": case_detail_out(SqlStore(db).get(into["case_id"]) or into)}


# ---------------------------------------------------------------- intake

class FromJiraIn(BaseModel):
    key: str = Field(min_length=3, max_length=64)


STRONG_MATCH = 60   # services.jira.triage.SCHEMA_TABLE: the ticket names SCHEMA.TABLE or more, or the table is linked


def _registry(db: Db) -> List[Dict[str, Any]]:
    """The target tables in the shape services.jira.triage.score_tables reads."""
    try:
        found = db.query("""SELECT TARGET_TABLE_ID, DOMAIN_ID, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, DESCRIPTION,
                                   COALESCE(ACTIVE_FLAG, TRUE) AS ACTIVE FROM KNOWLEDGE.TARGET_TABLE_REGISTRY LIMIT 5000""")
    except Exception:
        return []
    part = lambda v: str(v or "").strip().strip('"').upper()  # noqa: E731
    return [{"target_table_id": r["target_table_id"], "domain_id": r.get("domain_id"), "database": part(r.get("target_database")),
             "schema": part(r.get("target_schema")), "table": part(r.get("target_table")),
             "description": r.get("description") or "", "active": bool(r.get("active")), "has_sttm": False} for r in found]


def infer_table(tables: List[Dict[str, Any]], issue: Dict[str, Any], linked: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The top deterministic candidate of services.jira.triage (no AI call), only when it is strong and unambiguous."""
    from services.jira.triage import score_tables

    ranked = score_tables(tables, issue, linked)
    if not ranked or ranked[0]["score"] < STRONG_MATCH:
        return None
    if len(ranked) > 1 and ranked[1]["score"] >= ranked[0]["score"]:
        return None
    return ranked[0]


@router.post("/api/cases/from-jira")
def case_from_jira(body: FromJiraIn, db: Db = Depends(current_db)):
    """Open a case from a Jira issue, read as the caller (428 when their Jira account is not connected)."""
    from app import jira_api as jira
    from services.jira.client import JiraError, detail

    who = _who(db)
    _need(who, "JIRA.READ", "reading Jira issues")
    key = jira._key(body.key)
    client, conn, _ = jira._client(db)
    try:
        info = detail(client.issue(key), conn.get("site_url") or "")
    except JiraError as exc:
        raise jira._jira_error(exc) from None
    try:
        linked = db.query("""SELECT RUN_ID, TARGET_TABLE, TARGET_TABLE_ID FROM JIRA.ISSUE_LINK
                              WHERE ISSUE_KEY = %s AND NOT IS_DELETED""", (key,))
    except Exception:
        linked = []
    linked = [{k: v for k, v in r.items() if v} for r in linked]
    issue = {k: info.get(k) for k in ("key", "summary", "description", "environment", "comments")}
    top = infer_table(_registry(db), issue, linked)
    run_id = next((r["run_id"] for r in linked if r.get("run_id")), None)
    try:
        scope = _scope(db, who, None, top["target_table_id"] if top else None, run_id)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        scope = _scope(db, who, None, top["target_table_id"] if top else None, None)
    summary = str(info.get("summary") or "").strip()
    title = redact(summary or key)[:300]
    title = title if len(title) >= 3 else f"Jira {key}"
    links = [{"kind": "JIRA", "ref": key, "label": summary[:500] or key, "url": info.get("url")}]
    if scope.get("run_id"):
        links.append({"kind": "RUN", "ref": scope["run_id"], "url": rules.link_url("RUN", scope["run_id"])})
    spec = {"title": title, "description": _redacted(info.get("description"), 20000), "kind": "DATA_BUG",
            "severity": rules.severity_from_jira(info.get("priority")), "source": "JIRA", "source_ref": key,
            "links": links, **scope}
    out = _open(db, who, spec)
    if out["created"]:
        jira._log(db, key, "CASE_OPENED", "DONE", conn.get("cloud_id"), scope.get("run_id"),
                  {"case_id": out["case"]["case_id"], "number": out["case"]["number"]})
    out["table_match"] = ({"target_table_id": top["target_table_id"], "fqn": top["fqn"], "score": top["score"],
                           "reasons": top["reasons"]} if top else None)
    return out


class FromIncidentIn(BaseModel):
    incident_id: str = Field(min_length=1, max_length=36)


@router.post("/api/cases/from-incident")
def case_from_incident(body: FromIncidentIn, db: Db = Depends(current_db)):
    """Open a PIPELINE case from an Airflow incident; the case and the incident link both ways."""
    who = _who(db)
    _need(who, "OPS.VIEW", "reading incidents")
    found = db.query("""SELECT INCIDENT_ID, DAG_ID, TASK_ID, KIND, SEVERITY, TEAM_ID, TITLE, ERROR_EXCERPT, AI_SUMMARY, JIRA_KEY
                          FROM OPS.INCIDENT WHERE INCIDENT_ID = %s""", (body.incident_id,))
    if not found:
        raise HTTPException(404, "Incident not found")
    i = found[0]
    where = ".".join(str(x) for x in (i.get("dag_id"), i.get("task_id")) if x)
    title = redact(str(i.get("title") or f"{where} {str(i.get('kind') or '').lower()}").strip())[:300]
    title = title if len(title) >= 3 else f"Incident {body.incident_id[:8]}"
    parts = [f"Airflow incident on {where} ({i.get('kind')})."]
    if i.get("error_excerpt"):
        parts.append(f"Error:\n{i['error_excerpt']}")
    if i.get("ai_summary"):
        parts.append(f"AI summary: {i['ai_summary']}")
    links = [{"kind": "INCIDENT", "ref": i["incident_id"], "label": title,
              "url": rules.link_url("INCIDENT", i["incident_id"])}]
    if i.get("jira_key"):
        links.append({"kind": "JIRA", "ref": i["jira_key"], "label": i["jira_key"],
                      "url": rules.link_url("JIRA", i["jira_key"], _jira_site(db))})
    scope = _scope(db, who, None, None, None)
    severity = str(i.get("severity") or "").upper()
    spec = {"title": title, "description": _redacted("\n\n".join(parts), 20000), "kind": "PIPELINE",
            "severity": severity if severity in rules.SEVERITIES else rules.DEFAULT_SEVERITY, "source": "INCIDENT",
            "source_ref": i["incident_id"], "team_id": i.get("team_id"), "links": links, **scope}
    out = _open(db, who, spec)
    if out["created"]:
        try:
            from services.ops.incidents import SqlStore as IncidentStore

            IncidentStore(db).event(i["incident_id"], "case_opened", who["user"],
                                    {"case_id": out["case"]["case_id"], "number": out["case"]["number"]})
        except Exception:
            pass   # the case keeps its INCIDENT link; the incident timeline entry is best effort
    return out


class FromResultIn(BaseModel):
    qa_result_id: Optional[str] = Field(default=None, max_length=36)
    check_result_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/cases/from-result")
def case_from_result(body: FromResultIn, db: Db = Depends(current_db)):
    """Open a case from a failing QA test result (QA_FAILURE) or data quality check result (DQ_FAILURE). Counts only:
    sample rows and measured data values never go into the case."""
    if bool(body.qa_result_id) == bool(body.check_result_id):
        raise HTTPException(422, "Give exactly one of qa_result_id and check_result_id.")
    who = _who(db)
    if body.qa_result_id:
        from services.jira.triage import measured_count

        found = db.query("""SELECT RESULT_ID, RUN_ID, TEST_ID, TITLE, SEVERITY, OUTCOME, ROWS_RETURNED, MEASURED, EXPECTED,
                                   DETAIL, TARGET_TABLE_ID FROM QUALITY.QA_RESULT WHERE RESULT_ID = %s""", (body.qa_result_id,))
        if not found:
            raise HTTPException(404, "QA result not found")
        r = found[0]
        if str(r.get("outcome") or "").upper() == "PASS":
            raise HTTPException(400, "This QA test passed; there is nothing to open a case for.")
        scope = _scope(db, who, None, r.get("target_table_id"), r.get("run_id"))
        title = redact(f"QA test failed: {r.get('title') or r.get('test_id')}")[:300]
        lines = [f"QA test {r.get('test_id')} returned {r.get('outcome')}.",
                 f"Rows returned: {r.get('rows_returned') if r.get('rows_returned') is not None else 'unknown'}"]
        if measured_count(r.get("measured")):
            lines.append(f"Measured: {measured_count(r.get('measured'))}")
        if r.get("expected"):
            lines.append(f"Expected: {r['expected']}")
        if r.get("detail"):
            lines.append(f"Detail: {r['detail']}")
        source, ref, kind, link_kind = "QA_FAILURE", r["result_id"], "DATA_BUG", "QA_RESULT"
    else:
        found = db.query("""SELECT RESULT_ID, RUN_ID, TARGET_TABLE, TARGET_COLUMN, CHECK_TYPE, DIMENSION, SEVERITY, OUTCOME,
                                   FAILED_ROWS, THRESHOLD, DETAIL FROM QUALITY.CHECK_RESULT WHERE RESULT_ID = %s""",
                         (body.check_result_id,))
        if not found:
            raise HTTPException(404, "Check result not found")
        r = found[0]
        if str(r.get("outcome") or "").upper() in ("PASS", "NOT_EVALUATED"):
            raise HTTPException(400, f"This check is {r.get('outcome')}; there is nothing to open a case for.")
        run_domain = _run_domain(db, r["run_id"]) if r.get("run_id") else None
        table = _table_by_name(db, r.get("target_table"), run_domain)
        scope = _scope(db, who, None, table["target_table_id"] if table else None, r.get("run_id"))
        on = ".".join(str(x) for x in (r.get("target_table"), r.get("target_column")) if x)
        title = redact(f"Data quality check failed: {r.get('check_type') or 'check'} on {on or 'the target'}")[:300]
        lines = [f"Data quality check {r.get('check_type')} ({r.get('dimension') or 'unknown dimension'}) returned {r.get('outcome')}.",
                 f"Failing rows: {r.get('failed_rows') if r.get('failed_rows') is not None else 'unknown'}"]
        if r.get("threshold"):
            lines.append(f"Threshold: {r['threshold']}")
        if r.get("detail"):
            lines.append(f"Detail: {r['detail']}")
        source, ref, kind, link_kind = "DQ_FAILURE", r["result_id"], "DATA_QUALITY", "DQ_RESULT"
    links = [{"kind": link_kind, "ref": ref, "label": title}]
    if scope.get("run_id"):
        links.append({"kind": "RUN", "ref": scope["run_id"], "url": rules.link_url("RUN", scope["run_id"])})
    spec = {"title": title if len(title) >= 3 else "Failing result", "description": _redacted("\n".join(lines), 20000),
            "kind": kind, "severity": rules.severity_from_result(r.get("severity")), "source": source, "source_ref": ref,
            "links": links, **scope}
    return _open(db, who, spec)


# ---------------------------------------------------------------- my domains

@router.get("/api/governance/my-domains")
def my_domains(db: Db = Depends(current_db)):
    """The domains the caller may see, with their membership role (null when not a member) and whether they steward
    it; all is true for admins, who see every domain."""
    who = _who(db)
    names = domains.domain_names(db)
    visible: Optional[Set[str]] = domains.visible_domain_ids(db, who["user"], who["privileges"], who["roles"])
    roles = domains.member_domains(db, who["user"])
    ids = sorted(names) if visible is None else sorted(d for d in visible if d in names)
    return {"domains": [{"domain_id": d, "name": names[d], "role": roles.get(d),
                         "steward": domains.is_steward(db, who["user"], d, who["roles"])}
                        for d in sorted(ids, key=lambda d: names[d].upper())],
            "all": visible is None}


# ---------------------------------------------------------------- triage and fixes (PR Q2)

def _ai_http(fn):
    """Map the services' errors: TriageError keeps its status (429 with Retry-After), a missing structured answer is
    502, Snowflake errors keep their usual mapping."""
    from app.main import _snowflake_error
    from services.cases.triage import TriageError

    try:
        return fn()
    except TriageError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(exc.status, exc.message, headers=headers) from None
    except HTTPException:
        raise
    except AssertionError as exc:
        raise HTTPException(502, str(exc)[:300]) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


def _linked_jira(db: Db, who: Dict[str, Any], case_id: str) -> Optional[Dict[str, Any]]:
    """The first linked Jira issue, read with the caller's own Jira connection; None when there is no link, the caller
    has no JIRA.READ or no connection, or Jira does not answer."""
    if not _holds(who, "JIRA.READ"):
        return None
    keys = [link["ref"] for link in SqlStore(db).links(case_id) if link.get("kind") == "JIRA"]
    if not keys:
        return None
    try:
        from app import jira_api as jira
        from services.jira.client import detail

        client, conn, _ = jira._client(db)
        info = detail(client.issue(keys[0]), conn.get("site_url") or "")
        return {k: info.get(k) for k in ("key", "summary", "description", "environment", "comments", "type", "priority",
                                          "status")}
    except Exception:
        return None


class TriageIn(BaseModel):
    force: bool = False
    note: Optional[str] = Field(default=None, max_length=4000)


@router.post("/api/cases/{case_id}/triage")
def case_triage(case_id: str, body: Optional[TriageIn] = None, db: Db = Depends(current_db)):
    """{ai, artifacts, cached}: the AI triage (cached per fingerprint and context unless force); AI.USE and CASE.WORK."""
    from services.cases.triage import triage

    who = _who(db)
    _need(who, "CASE.WORK", "triaging a case")
    _visible_case(db, who, case_id)
    body = body or TriageIn()
    issue = _linked_jira(db, who, case_id)
    return _ai_http(lambda: triage(db, case_id, who["user"], force=body.force, note=body.note, jira_issue=issue,
                                   can_see=lambda d: _can_see(db, who, d)))


class CaseAskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@router.post("/api/cases/{case_id}/ask")
def case_ask(case_id: str, body: CaseAskIn, db: Db = Depends(current_db)):
    """{answer, citations:[{kind, ref, url?}]}."""
    from services.cases.triage import ask

    who = _who(db)
    _visible_case(db, who, case_id)
    issue = _linked_jira(db, who, case_id)
    return _ai_http(lambda: ask(db, case_id, body.question, who["user"], jira_issue=issue,
                                can_see=lambda d: _can_see(db, who, d)))


class DecideIn(BaseModel):
    decision: Literal["accept", "reject"]
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/cases/{case_id}/artifacts/{artifact_id}/decide")
def artifact_decide(case_id: str, artifact_id: str, body: DecideIn, db: Db = Depends(current_db)):
    """{artifact}: accept or reject a PROPOSED artifact (an invalid reproduction test cannot be accepted)."""
    from services.cases.fixes import decide

    who = _who(db)
    case = _visible_case(db, who, case_id)
    return {"artifact": _ai_http(lambda: decide(db, case, artifact_id, body.decision, who["user"], body.note))}


@router.post("/api/cases/{case_id}/artifacts/{artifact_id}/run")
def artifact_run(case_id: str, artifact_id: str, db: Db = Depends(current_db)):
    """{outcome, rows_returned, measured, detail, columns, sample, masked}: one reproduction test, read-only and
    guarded, with the caller's role; PII columns are masked in the sample."""
    from services.cases.fixes import run_repro

    who = _who(db)
    case = _visible_case(db, who, case_id)
    return _ai_http(lambda: run_repro(db, case, artifact_id, who["user"]))


class AppliedIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/cases/{case_id}/artifacts/{artifact_id}/applied")
def artifact_applied(case_id: str, artifact_id: str, body: Optional[AppliedIn] = None, db: Db = Depends(current_db)):
    """{artifact, case}: an ACCEPTED fix marked APPLIED by the person who applied it; the case moves to FIX_APPLIED."""
    from services.cases.fixes import mark_applied

    who = _who(db)
    case = _visible_case(db, who, case_id)
    out = _ai_http(lambda: mark_applied(db, case, artifact_id, who["user"], (body or AppliedIn()).note))
    return {"artifact": out["artifact"], "case": case_detail_out(out["case"])}


class PublishIn(BaseModel):
    dry_run: bool = True
    preview_token: Optional[str] = Field(default=None, max_length=4000)


@router.post("/api/cases/{case_id}/artifacts/{artifact_id}/publish")
def artifact_publish(case_id: str, artifact_id: str, body: PublishIn, db: Db = Depends(current_db),
                     x_aip_replay: Optional[str] = Header(default=None)):
    """A dry run returns {branch, base_branch, repo, path, diff, preview_token}; the real publish ({dry_run: false,
    preview_token}) opens a draft pull request from branch fix/<case number> and returns {pr_url, branch, status}."""
    from app.governance import _valid_replay
    from app.main import _publish_dbt
    from services.cases.fixes import publish

    who = _who(db)
    _need(who, "DBT.EDIT", "publishing a pull request")
    case = _visible_case(db, who, case_id)
    replay = bool(_valid_replay(x_aip_replay))
    return _ai_http(lambda: publish(db, case, artifact_id, who["user"], body.dry_run, body.preview_token, replay=replay,
                                    publisher=lambda payload: _publish_dbt(db, "", payload)))


@router.post("/api/cases/{case_id}/verify")
def case_verify(case_id: str, db: Db = Depends(current_db)):
    """{results:[{artifact_id, title, outcome}], verified, case}: runs every ACCEPTED reproduction test; all PASS moves
    the case to VERIFIED, anything else to IN_PROGRESS."""
    from services.cases.fixes import verify

    who = _who(db)
    case = _visible_case(db, who, case_id)
    out = _ai_http(lambda: verify(db, case, who["user"]))
    return {**out, "case": case_detail_out(out["case"])}


class JiraCommentIn(BaseModel):
    text: Optional[str] = Field(default=None, max_length=8000)


@router.post("/api/cases/{case_id}/jira-comment")
def case_jira_comment(case_id: str, body: Optional[JiraCommentIn] = None, db: Db = Depends(current_db)):
    """{key, posted, results}: the case summary (counts only, never sample rows) posted as the caller to each linked
    Jira issue. key is the first issue; posted is true when every comment was posted."""
    from app import jira_api as jira
    from services.cases.fixes import jira_text
    from services.cases.triage import _ai
    from services.jira import adf
    from services.jira.client import JiraError

    who = _who(db)
    case = _visible_case(db, who, case_id)
    store = SqlStore(db)
    keys = [link["ref"] for link in store.links(case_id) if link.get("kind") == "JIRA"]
    if not keys:
        raise HTTPException(409, "This case has no linked Jira issue.")
    text = _redacted((body or JiraCommentIn()).text, 8000) or jira_text({**case, "ai": _ai(case)}, store.artifacts(case_id))
    client, conn, _ = jira._client(db)
    results = []
    for key in keys[:10]:
        try:
            posted = client.add_comment(key, adf.from_markdown(text))
            jira._log(db, key, "COMMENT", "DONE", conn.get("cloud_id"), None,
                      {"comment_id": (posted or {}).get("id"), "case_id": case_id, "preview": adf.plain(text, 300)})
            results.append({"key": key, "ok": True})
        except JiraError as exc:
            jira._log(db, key, "COMMENT", "FAILED", conn.get("cloud_id"), None, error=exc.message)
            results.append({"key": key, "ok": False, "error": exc.message})
    store.event(case_id, "jira_commented", who["user"], {"keys": [r["key"] for r in results if r["ok"]],
                                                         "failed": [r["key"] for r in results if not r["ok"]]})
    return {"key": keys[0], "posted": all(r["ok"] for r in results), "results": results}
