"""Onboarding requests (V075; services/onboarding/requests.py): a data owner asks for a source, a domain or a data
product, and the request travels to whoever acts next, from what the platform already holds.

Every read works the request's stage out again (`refresh`): sources a requester named are linked when they are
registered under that name, the run that onboards a source is linked when one exists, and the stage, the role it waits
on and what needs attention are written back with an automatic event when they change. A request whose every part is
done closes itself as DONE.

Governance: reads are open to signed-in people who may see the request's domain (domain membership,
services/governance/domains.can_see), as the domain pages are. Every write is checked here (policy.RULES lists these
routes as checked by the endpoint):
- file a request: the domain's owners and stewards, or DOMAIN.EDIT, RUN.CREATE or REQUEST.CHANGES;
- add a source: the requester, or SOURCE.CONNECT, or the domain's owners and stewards;
- link or unlink a registered source: SOURCE.CONNECT (the Data OS Administrator);
- link or unlink a run: RUN.CREATE or RUN.OPERATE;
- link the domain once it exists: DOMAIN.ONBOARD or DOMAIN.EDIT, or the requester;
- remove a source: the requester or SOURCE.CONNECT;
- cancel: the requester, or ROLE.MANAGE; reject (with a reason): SOURCE.CONNECT, DOMAIN.ONBOARD, DOMAIN.EDIT or ROLE.MANAGE;
- reopen: the requester or ROLE.MANAGE; comment: anyone who may see it.
Notifications stay in-app: a request waiting on a role is in that role's My Work queue.
"""

from __future__ import annotations

import functools
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db
from app.ops_api import _iso
from services.governance import domains as domain_access
from services.onboarding import requests as rq

router = APIRouter()

REQ_COLS = ("REQUEST_ID, REQUEST_TYPE, TITLE, BUSINESS_OUTCOME, DOMAIN_ID, PROPOSED_DOMAIN, PIPELINE, PRIORITY, NEEDED_BY, "
            "STATUS, STAGE, WAITING_ON, WAITING_LABEL, ATTENTION, REQUESTED_BY, CREATED_AT, UPDATED_AT, STAGE_CHANGED_AT, "
            "CLOSED_BY, CLOSED_AT, CLOSE_REASON")
ITEM_COLS = ("ITEM_ID, REQUEST_ID, SOURCE_SYSTEM_ID, SOURCE_NAME, SOURCE_NOTE, RUN_ID, STATUS, STATUS_DETAIL, ADDED_BY, "
             "ADDED_AT, UPDATED_AT, REMOVED_BY, REMOVED_AT")
# V078: new or enhance; read apart so the requests keep working before it is deployed (every request is then NEW)
INTENT_COLS = "INTENT, BASE_MODEL_VERSION"
RUN_COLS = ("RUN_ID, RUN_NAME, DOMAIN_ID, SOURCE_SYSTEM_ID, CURRENT_STATE, FAILED_FROM_STATE, FAILURE_REASON, IS_ARCHIVED, "
            "DELETED_AT, PARENT_RUN_ID, CREATED_AT, COALESCE(PIPELINE, 'SILVER') AS PIPELINE")


def _who(db: Db) -> Dict[str, Any]:
    from app.cases_api import _who as who

    return who(db)


def _holds(who: Dict[str, Any], *privileges: str) -> bool:
    return "*" in who["privileges"] or any(p in who["privileges"] for p in privileges)


def _owner(db: Db, who: Dict[str, Any], domain_id: Optional[str]) -> bool:
    """The caller is one of the domain's named owners or stewards."""
    return bool(domain_id) and who["user"].upper() in {u.upper() for u in domain_access.named_stewards(db, domain_id)}


def _need(ok: bool, what: str, how: str) -> None:
    if not ok:
        raise HTTPException(403, f"Not allowed: {what} needs {how}.")


def _missing_table(exc: Exception) -> bool:
    text = str(exc).lower()
    return "onboarding_request" in text and ("does not exist" in text or "not authorized" in text)


def _guard_deployed(fn):
    """Requests need V075. Before it is deployed, say so instead of failing with a Snowflake error."""
    @functools.wraps(fn)  # FastAPI reads the endpoint's parameters through the wrapper
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            if _missing_table(exc):
                raise HTTPException(503, "Onboarding requests are not deployed yet: apply migration V075 "
                                         "(python infrastructure/deploy_snowflake.py --database DEV_AI_PLATFORM).") from exc
            raise _snowflake_error(exc) from exc
    return run


def _out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (_iso(v) if not isinstance(v, date) or hasattr(v, "hour") else v.isoformat()) for k, v in row.items()}


def _event(db: Db, request_id: str, kind: str, note: Optional[str] = None, item_id: Optional[str] = None,
           frm: Optional[str] = None, to: Optional[str] = None, automatic: bool = False, actor: Optional[str] = None) -> None:
    db.execute(
        "INSERT INTO CORE.ONBOARDING_REQUEST_EVENT (EVENT_ID, REQUEST_ID, ITEM_ID, KIND, FROM_VALUE, TO_VALUE, NOTE, ACTOR, AUTOMATIC) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_USER()), %s)",
        (str(uuid.uuid4()), request_id, item_id, kind, frm, to, (note or None) and note[:4000], actor, automatic))


# ---------------------------------------------------------------- facts the assessment reads


def _sources(db: Db) -> Dict[str, Dict[str, Any]]:
    from app.main import sources_overview

    try:
        return {s["source_system_id"]: s for s in sources_overview(db=db)["sources"]}
    except Exception:
        # health could not be read: fall back to the registry alone (treated as reachable), never block the page
        rows = db.query("SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, ACTIVE_FLAG FROM CORE.SOURCE_REGISTRY")
        return {r["source_system_id"]: {**r, "health": "HEALTHY", "health_detail": ""} for r in rows}


def _domains(db: Db, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    marks = ", ".join(["%s"] * len(ids))
    rows = db.query(f"""SELECT D.DOMAIN_ID, D.DOMAIN_NAME, D.ACTIVE_FLAG, M.APPROVED_VERSION
                        FROM CORE.DOMAIN_REGISTRY D
                        LEFT JOIN CORE.DOMAIN_MODEL M ON M.DOMAIN_ID = D.DOMAIN_ID AND COALESCE(M.LAYER, 'SILVER') = 'SILVER'
                        WHERE D.DOMAIN_ID IN ({marks})""", tuple(ids))
    return {r["domain_id"]: r for r in rows}


def _domain_named(db: Db, name: str) -> Optional[Dict[str, Any]]:
    rows = db.query("SELECT DOMAIN_ID, DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s) "
                    "AND COALESCE(ACTIVE_FLAG, TRUE)", (name.strip(),))
    return rows[0] if rows else None


def _runs(db: Db, run_ids: List[str], source_ids: List[str]) -> tuple:
    """(runs by id, table runs by parent, candidate runs by source) for the given links and sources."""
    found: Dict[str, Dict[str, Any]] = {}
    children: Dict[str, List[Dict[str, Any]]] = {}
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    if run_ids:
        marks = ", ".join(["%s"] * len(run_ids))
        for r in db.query(f"SELECT {RUN_COLS} FROM CORE.WORKFLOW_RUN WHERE RUN_ID IN ({marks}) OR PARENT_RUN_ID IN ({marks})",
                          tuple(run_ids) * 2):
            if r["run_id"] in run_ids:
                found[r["run_id"]] = r
            if r.get("parent_run_id") in run_ids:
                children.setdefault(r["parent_run_id"], []).append(r)
    if source_ids:
        marks = ", ".join(["%s"] * len(source_ids))
        for r in db.query(f"SELECT {RUN_COLS} FROM CORE.WORKFLOW_RUN WHERE SOURCE_SYSTEM_ID IN ({marks}) AND DELETED_AT IS NULL",
                          tuple(source_ids)):
            by_source.setdefault(r["source_system_id"], []).append(r)
    return found, children, by_source


def _before_v077(exc: Exception) -> bool:
    text = str(exc).lower()
    return "invalid identifier" in text and ("intent" in text or "base_model_version" in text)


def _load(db: Db, where: str, params: tuple) -> List[Dict[str, Any]]:
    try:
        rows = db.query(f"SELECT {REQ_COLS}, {INTENT_COLS} FROM CORE.ONBOARDING_REQUEST WHERE {where} ORDER BY UPDATED_AT DESC", params)
    except Exception as exc:
        if not _before_v077(exc):
            raise
        rows = db.query(f"SELECT {REQ_COLS} FROM CORE.ONBOARDING_REQUEST WHERE {where} ORDER BY UPDATED_AT DESC", params)
    return [{**r, "intent": rq.intent(r)} for r in rows]


def _items(db: Db, request_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not request_ids:
        return out
    marks = ", ".join(["%s"] * len(request_ids))
    for r in db.query(f"SELECT {ITEM_COLS} FROM CORE.ONBOARDING_REQUEST_ITEM WHERE REQUEST_ID IN ({marks}) ORDER BY ADDED_AT",
                      tuple(request_ids)):
        out.setdefault(r["request_id"], []).append(r)
    return out


# ---------------------------------------------------------------- refresh: work the stage out, link what exists, write changes


def refresh(db: Db, requests: List[Dict[str, Any]], write: bool = True) -> List[Dict[str, Any]]:
    """The requests with their items, assessment and waits; links named sources and existing runs, and writes the
    stage (with an automatic event) when it changed. `write` False only reads (a caller who may not see a domain)."""
    if not requests:
        return []
    items = _items(db, [r["request_id"] for r in requests])
    sources = _sources(db)
    all_items = [i for rid in items for i in items[rid]]

    # a source named by its registered name gets linked to the registration (case and spacing ignored)
    for it in all_items:
        if write and not it.get("source_system_id") and not it.get("removed_at"):
            hit = rq.match_source(it.get("source_name") or "", sources.values())
            if hit:
                db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET SOURCE_SYSTEM_ID = %s, SOURCE_NAME = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
                           "WHERE ITEM_ID = %s AND SOURCE_SYSTEM_ID IS NULL", (hit["source_system_id"], hit["source_system_name"], it["item_id"]))
                _event(db, it["request_id"], "SOURCE_LINKED", f"Matched the registered source {hit['source_system_name']}",
                       item_id=it["item_id"], to=hit["source_system_id"], automatic=True)
                it["source_system_id"], it["source_name"] = hit["source_system_id"], hit["source_system_name"]

    # requests for a domain still to create: link it once a domain of that name exists
    for r in requests:
        if write and not r.get("domain_id") and r.get("proposed_domain") and r.get("status") == rq.OPEN:
            hit = _domain_named(db, r["proposed_domain"])
            if hit:
                db.execute("UPDATE CORE.ONBOARDING_REQUEST SET DOMAIN_ID = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s "
                           "AND DOMAIN_ID IS NULL", (hit["domain_id"], r["request_id"]))
                _event(db, r["request_id"], "DOMAIN_LINKED", f"The {hit['domain_name']} domain now exists", to=hit["domain_id"], automatic=True)
                r["domain_id"] = hit["domain_id"]

    run_ids = sorted({i["run_id"] for i in all_items if i.get("run_id")})
    source_ids = sorted({i["source_system_id"] for i in all_items if i.get("source_system_id")})
    runs, children, by_source = _runs(db, run_ids, source_ids)
    by_id = {r["request_id"]: r for r in requests}

    # a ready source with no run gets the run that already onboards it into the request's domain
    linked_now = False
    for it in all_items:
        req = by_id[it["request_id"]]
        if write and not it.get("run_id") and it.get("source_system_id") and not it.get("removed_at") and req.get("status") == rq.OPEN:
            run = rq.pick_run(it["source_system_id"], req.get("domain_id"), by_source.get(it["source_system_id"], []))
            if run:
                db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET RUN_ID = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s AND RUN_ID IS NULL",
                           (run["run_id"], it["item_id"]))
                _event(db, it["request_id"], "RUN_LINKED", f"Found the run {run['run_name']}", item_id=it["item_id"], to=run["run_id"], automatic=True)
                it["run_id"] = run["run_id"]
                linked_now = True
    # a run linked just now: load it with its table runs (a multi-table run stands where its table runs stand)
    if linked_now or any(i.get("run_id") and i["run_id"] not in runs for i in all_items):
        more, kids, _ = _runs(db, sorted({i["run_id"] for i in all_items if i.get("run_id")}), [])
        runs.update(more)
        children.update(kids)

    domains = _domains(db, sorted({r["domain_id"] for r in requests if r.get("domain_id")}))
    out = []
    for r in requests:
        its = items.get(r["request_id"], [])
        a = rq.assess(r, its, domains.get(r.get("domain_id")) if r.get("domain_id") else None, sources, runs, children)
        if write:
            _persist(db, r, its, a)
        domain = domains.get(r.get("domain_id") or "")
        out.append({
            **_out(r), "domain_name": domain.get("domain_name") if domain else None,
            "stage": a["stage"], "status": a["status"], "waiting_on": a["waiting_on"], "waiting_label": a["waiting_label"],
            "attention": a["attention"], "waits": a["waits"], "phase": a["phase"],
            "items": [{**_out(i), "status": a["items"][i["item_id"]]["status"], "status_detail": a["items"][i["item_id"]].get("detail"),
                       "phase": a["items"][i["item_id"]]["phase"],
                       "stage": a["items"][i["item_id"]]["stage"], "waits": a["items"][i["item_id"]]["waits"],
                       "attention": a["items"][i["item_id"]].get("attention"),
                       "run_name": runs.get(i.get("run_id") or "", {}).get("run_name"),
                       "run_state": runs.get(i.get("run_id") or "", {}).get("current_state")} for i in its],
        })
    return out


def _persist(db: Db, r: Dict[str, Any], items: List[Dict[str, Any]], a: Dict[str, Any]) -> None:
    for i in items:
        s = a["items"][i["item_id"]]
        if (i.get("status"), i.get("status_detail")) != (s["status"], (s.get("detail") or "")[:1000] or None):
            db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET STATUS = %s, STATUS_DETAIL = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s",
                       (s["status"], (s.get("detail") or "")[:1000] or None, i["item_id"]))
            for kind, note in rq.milestones(i, s, (r.get("request_type") or "").upper() == "PRODUCT"):
                _event(db, r["request_id"], kind, note, item_id=i["item_id"], frm=i.get("status"), to=s["status"], automatic=True)
    if r.get("status") == rq.OPEN and a["status"] == rq.DONE:
        db.execute("UPDATE CORE.ONBOARDING_REQUEST SET STATUS = 'DONE', STAGE = 'DONE', WAITING_ON = NULL, WAITING_LABEL = NULL, ATTENTION = NULL, "
                   "CLOSED_BY = 'PLATFORM', CLOSED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP(), STAGE_CHANGED_AT = CURRENT_TIMESTAMP() "
                   "WHERE REQUEST_ID = %s AND STATUS = 'OPEN'", (r["request_id"],))
        _event(db, r["request_id"], "DONE", "Every part of the request is done", frm=r.get("stage"), to="DONE", automatic=True)
        return
    if r.get("status") != rq.OPEN:
        return
    changed = (r.get("stage"), r.get("waiting_on"), r.get("attention")) != (a["stage"], a["waiting_on"], (a["attention"] or "")[:1000] or None)
    if changed:
        stage_moved = r.get("stage") != a["stage"]
        db.execute("UPDATE CORE.ONBOARDING_REQUEST SET STAGE = %s, WAITING_ON = %s, WAITING_LABEL = %s, ATTENTION = %s, UPDATED_AT = CURRENT_TIMESTAMP()"
                   + (", STAGE_CHANGED_AT = CURRENT_TIMESTAMP()" if stage_moved else "") + " WHERE REQUEST_ID = %s AND STATUS = 'OPEN'",
                   (a["stage"], a["waiting_on"], (a["waiting_label"] or "")[:256] or None, (a["attention"] or "")[:1000] or None, r["request_id"]))
        if stage_moved:
            # the first assessment of a new request is its routing, not a change
            _event(db, r["request_id"], "ROUTED" if r.get("stage") == "NEW" else "STAGE", a["waiting_label"],
                   frm=None if r.get("stage") == "NEW" else r.get("stage"), to=a["stage"], automatic=True)


def _visible(db: Db, who: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if domain_access.can_see(db, who["user"], r.get("domain_id"), who["privileges"], who["roles"])]


def _one(db: Db, who: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    rows = _visible(db, who, _load(db, "REQUEST_ID = %s", (request_id,)))
    if not rows:
        raise HTTPException(404, "No such request, or it belongs to a domain you are not a member of.")
    return rows[0]


def _item(db: Db, request_id: str, item_id: str) -> Dict[str, Any]:
    rows = db.query(f"SELECT {ITEM_COLS} FROM CORE.ONBOARDING_REQUEST_ITEM WHERE ITEM_ID = %s AND REQUEST_ID = %s", (item_id, request_id))
    if not rows:
        raise HTTPException(404, "No such source on this request.")
    return rows[0]


def _open(r: Dict[str, Any]) -> None:
    if r.get("status") != rq.OPEN:
        raise HTTPException(409, f"The request is {str(r.get('status')).lower()}; reopen it first.")


def _detail(db: Db, request_id: str) -> Dict[str, Any]:
    r = refresh(db, _load(db, "REQUEST_ID = %s", (request_id,)))[0]
    events = db.query("SELECT EVENT_ID, ITEM_ID, KIND, FROM_VALUE, TO_VALUE, NOTE, ACTOR, AUTOMATIC, CREATED_AT "
                      "FROM CORE.ONBOARDING_REQUEST_EVENT WHERE REQUEST_ID = %s ORDER BY CREATED_AT", (request_id,))
    return {**r, "events": [_out(e) for e in events]}


# ---------------------------------------------------------------- reads


@router.get("/api/onboarding-requests")
@_guard_deployed
def list_requests(scope: str = Query("all", pattern="^(all|mine|waiting)$"), status: str = Query("OPEN", pattern="^(OPEN|CLOSED|ALL)$"),
                  domain_id: Optional[str] = None, limit: int = Query(100, ge=1, le=500), db: Db = Depends(current_db)):
    """Requests the caller may see, newest activity first. scope=mine: filed by the caller; scope=waiting: waiting
    (now) on a privilege the caller holds. Every open request is worked out again on the way."""
    who = _who(db)
    where, params = ["1 = 1"], []
    if status == "OPEN":
        where.append("STATUS = 'OPEN'")
    elif status == "CLOSED":
        where.append("STATUS <> 'OPEN'")
    if domain_id:
        where.append("DOMAIN_ID = %s"); params.append(domain_id)
    if scope == "mine":
        where.append("UPPER(REQUESTED_BY) = %s"); params.append(who["user"].upper())
    rows = _visible(db, who, _load(db, " AND ".join(where), tuple(params))[: limit * 2])
    out = refresh(db, [r for r in rows if r.get("status") == rq.OPEN]) + [
        {**_out(r), "items": [], "waits": []} for r in rows if r.get("status") != rq.OPEN]
    if scope == "waiting":
        out = [r for r in out if r.get("status") == rq.OPEN and any(_holds(who, w["privilege"]) for w in r.get("waits", []))]
    out.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    counts = {s: sum(r.get("stage") == s for r in out if r.get("status") == rq.OPEN) for s in rq.STAGES}
    return {"requests": out[:limit], "counts": counts, "user": who["user"]}


@router.get("/api/onboarding-requests/{request_id}")
@_guard_deployed
def get_request(request_id: str, db: Db = Depends(current_db)):
    """One request with its sources, what each waits on, the stage, the history, and what the caller may do."""
    who = _who(db)
    r = _one(db, who, request_id)
    d = _detail(db, request_id)
    mine = str(r.get("requested_by") or "").upper() == who["user"].upper()
    owner = _owner(db, who, r.get("domain_id"))
    d["may"] = {
        "add_source": d["status"] == rq.OPEN and (mine or owner or _holds(who, "SOURCE.CONNECT")),
        "link_source": d["status"] == rq.OPEN and _holds(who, "SOURCE.CONNECT"),
        "link_run": d["status"] == rq.OPEN and _holds(who, "RUN.CREATE", "RUN.OPERATE"),
        "link_domain": d["status"] == rq.OPEN and not d.get("domain_id") and (mine or _holds(who, "DOMAIN.ONBOARD", "DOMAIN.EDIT")),
        "remove_source": d["status"] == rq.OPEN and (mine or _holds(who, "SOURCE.CONNECT")),
        "cancel": d["status"] == rq.OPEN and (mine or _holds(who, "ROLE.MANAGE")),
        "reject": d["status"] == rq.OPEN and _holds(who, "SOURCE.CONNECT", "DOMAIN.ONBOARD", "DOMAIN.EDIT", "ROLE.MANAGE"),
        # a finished request stays finished (a new need is a new request); a cancelled or rejected one can reopen
        "reopen": d["status"] in (rq.CANCELLED, rq.REJECTED) and (mine or _holds(who, "ROLE.MANAGE")),
        "comment": True,
    }
    d["duplicates"] = _duplicates(db, d.get("domain_id"), [i["source_system_id"] for i in d["items"] if i.get("source_system_id")],
                                  [i["source_name"] for i in d["items"]], exclude=request_id)
    return d


def _duplicates(db: Db, domain_id: Optional[str], source_ids: List[str], names: List[str], exclude: Optional[str] = None) -> List[Dict[str, Any]]:
    """Open requests for the same domain that already ask for one of these sources (by registration or by name)."""
    if not domain_id or not (source_ids or names):
        return []
    rows = db.query(
        "SELECT DISTINCT R.REQUEST_ID, R.TITLE, R.STAGE, R.REQUESTED_BY, I.SOURCE_NAME FROM CORE.ONBOARDING_REQUEST R "
        "JOIN CORE.ONBOARDING_REQUEST_ITEM I ON I.REQUEST_ID = R.REQUEST_ID AND I.REMOVED_AT IS NULL "
        "WHERE R.STATUS = 'OPEN' AND R.DOMAIN_ID = %s AND R.REQUEST_ID <> COALESCE(%s, '')", (domain_id, exclude))
    ids, want = set(source_ids), {"".join((n or "").upper().split()) for n in names}
    hits = db.query(f"SELECT ITEM_ID, REQUEST_ID, SOURCE_SYSTEM_ID FROM CORE.ONBOARDING_REQUEST_ITEM WHERE REQUEST_ID IN ({', '.join(['%s'] * len(rows))})",
                    tuple(r["request_id"] for r in rows)) if rows else []
    by_req = {}
    for h in hits:
        by_req.setdefault(h["request_id"], set()).add(h.get("source_system_id"))
    return [{"request_id": r["request_id"], "title": r["title"], "stage": r["stage"], "requested_by": r["requested_by"], "source_name": r["source_name"]}
            for r in rows if (by_req.get(r["request_id"], set()) & ids) or "".join((r["source_name"] or "").upper().split()) in want]


# ---------------------------------------------------------------- writes


class SourceIn(BaseModel):
    source_system_id: Optional[str] = Field(default=None, max_length=36)
    source_name: Optional[str] = Field(default=None, max_length=256)
    note: Optional[str] = Field(default=None, max_length=2000)


class NewRequest(BaseModel):
    request_type: str = Field(max_length=16)
    intent: str = Field(default="NEW", max_length=8)
    title: str = Field(max_length=256)
    business_outcome: Optional[str] = Field(default=None, max_length=4000)
    domain_id: Optional[str] = Field(default=None, max_length=36)
    proposed_domain: Optional[str] = Field(default=None, max_length=128)
    priority: str = Field(default="MEDIUM", max_length=8)
    needed_by: Optional[date] = None
    sources: List[SourceIn] = Field(default_factory=list, max_length=50)
    allow_duplicate: bool = False


def _source_row(db: Db, sources: Dict[str, Dict[str, Any]], s: SourceIn) -> Dict[str, Any]:
    if s.source_system_id:
        reg = sources.get(s.source_system_id)
        if not reg:
            raise HTTPException(422, f"Source {s.source_system_id} is not registered.")
        return {"source_system_id": s.source_system_id, "source_name": reg["source_system_name"], "note": s.note}
    name = (s.source_name or "").strip()
    hit = rq.match_source(name, sources.values())
    return {"source_system_id": hit["source_system_id"] if hit else None, "source_name": hit["source_system_name"] if hit else name, "note": s.note}


def _add_item(db: Db, request_id: str, row: Dict[str, Any]) -> str:
    item_id = str(uuid.uuid4())
    db.execute("INSERT INTO CORE.ONBOARDING_REQUEST_ITEM (ITEM_ID, REQUEST_ID, SOURCE_SYSTEM_ID, SOURCE_NAME, SOURCE_NOTE, STATUS) "
               "VALUES (%s, %s, %s, %s, %s, %s)",
               (item_id, request_id, row.get("source_system_id"), row["source_name"][:256], row.get("note"), "MISSING" if not row.get("source_system_id") else "NO_RUN"))
    _event(db, request_id, "SOURCE_ADDED", row.get("note"), item_id=item_id, to=row["source_name"][:256])
    return item_id


@router.post("/api/onboarding-requests")
@_guard_deployed
def create_request(body: NewRequest, db: Db = Depends(current_db)):
    """File a request. 409 with the open requests that already ask for one of the sources, unless allow_duplicate."""
    who = _who(db)
    kind = body.request_type.upper()
    intent = body.intent.upper()
    domain = _domains(db, [body.domain_id]).get(body.domain_id) if body.domain_id else None
    problems = rq.validate({**body.model_dump(), "request_type": kind, "intent": intent, "sources": [s.model_dump() for s in body.sources]},
                           bool(domain and domain.get("active_flag") is not False))
    if kind == "DOMAIN" and not body.domain_id and body.proposed_domain and _domain_named(db, body.proposed_domain):
        problems.append(f"A domain named {body.proposed_domain} already exists: enhance it instead.")
    if problems:
        raise HTTPException(422, " ".join(problems))
    if domain and not domain_access.can_see(db, who["user"], body.domain_id, who["privileges"], who["roles"]):
        raise HTTPException(404, "No such domain, or you are not a member of it.")
    _need(_owner(db, who, body.domain_id) or _holds(who, "DOMAIN.EDIT", "RUN.CREATE", "REQUEST.CHANGES"),
          "filing an onboarding request", "being an owner or steward of the domain, or DOMAIN.EDIT, RUN.CREATE or REQUEST.CHANGES")
    sources = _sources(db)
    rows = [_source_row(db, sources, s) for s in body.sources if s.source_system_id or (s.source_name or "").strip()]
    if kind == "PRODUCT" and not rows:
        # a data product is built by a Gold run, not from a source: one item waits for that run, so the request is
        # not done the moment it is filed
        rows = [{"source_system_id": None, "source_name": f"Gold run: {body.title.strip()[:200]}", "note": "Built on Gold from the domain's Silver tables"}]
    if not body.allow_duplicate:
        dupes = _duplicates(db, body.domain_id, [r["source_system_id"] for r in rows if r.get("source_system_id")], [r["source_name"] for r in rows])
        if dupes:
            raise HTTPException(409, {"message": "An open request already asks for this.", "duplicates": dupes})
    if intent == "ENHANCE" and kind == "SOURCE":
        unknown = [r["source_name"] for r in rows if not r.get("source_system_id")]
        if unknown:
            raise HTTPException(422, f"Enhancing needs a source already on the platform; {', '.join(unknown)} is not registered: "
                                     "request it as a new source.")
    request_id = str(uuid.uuid4())
    cols = ("REQUEST_ID, REQUEST_TYPE, TITLE, BUSINESS_OUTCOME, DOMAIN_ID, PROPOSED_DOMAIN, PIPELINE, PRIORITY, NEEDED_BY, STATUS, STAGE")
    values = (request_id, kind, body.title.strip()[:256], (body.business_outcome or "").strip() or None, body.domain_id,
              None if body.domain_id else (body.proposed_domain or "").strip() or None, "GOLD" if kind == "PRODUCT" else "SILVER",
              body.priority.upper(), body.needed_by, "NEW")
    # an Enhance domain request waits for a model newer than the one approved now
    base = domain.get("approved_version") if domain and kind == "DOMAIN" and intent == "ENHANCE" else None
    try:
        db.execute(f"INSERT INTO CORE.ONBOARDING_REQUEST ({cols}, {INTENT_COLS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)",
                   values + (intent, base))
    except Exception as exc:
        if not _before_v077(exc):
            raise
        if intent == "ENHANCE":
            raise HTTPException(503, "Enhance requests are not deployed yet: apply migration V078 "
                                     "(python infrastructure/deploy_snowflake.py --database DEV_AI_PLATFORM).") from exc
        db.execute(f"INSERT INTO CORE.ONBOARDING_REQUEST ({cols}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s)", values)
    _event(db, request_id, "SUBMITTED", body.business_outcome, to=kind if intent == "NEW" else f"{kind} (enhance)")
    for row in rows:
        _add_item(db, request_id, row)
    return _detail(db, request_id)


@router.post("/api/onboarding-requests/{request_id}/sources")
@_guard_deployed
def add_source(request_id: str, body: SourceIn, db: Db = Depends(current_db)):
    """Name one more source the request needs."""
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(str(r.get("requested_by") or "").upper() == who["user"].upper() or _owner(db, who, r.get("domain_id")) or _holds(who, "SOURCE.CONNECT"),
          "adding a source", "being the requester, an owner of the domain, or SOURCE.CONNECT")
    if not body.source_system_id and not (body.source_name or "").strip():
        raise HTTPException(422, "Name the source, or pick a registered one.")
    row = _source_row(db, _sources(db), body)
    taken = {(i.get("source_system_id") or "".join((i.get("source_name") or "").upper().split()))
             for i in _items(db, [request_id]).get(request_id, []) if not i.get("removed_at")}
    if (row.get("source_system_id") or "".join(row["source_name"].upper().split())) in taken:
        raise HTTPException(409, f"{row['source_name']} is already on this request.")
    _add_item(db, request_id, row)
    return _detail(db, request_id)


class LinkSource(BaseModel):
    source_system_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/onboarding-requests/{request_id}/sources/{item_id}/source")
@_guard_deployed
def link_source(request_id: str, item_id: str, body: LinkSource, db: Db = Depends(current_db)):
    """The Data OS Administrator links a named source to its registration (or clears a wrong link)."""
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(_holds(who, "SOURCE.CONNECT"), "linking a registered source", "the SOURCE.CONNECT privilege")
    it = _item(db, request_id, item_id)
    if body.source_system_id:
        reg = _sources(db).get(body.source_system_id)
        if not reg:
            raise HTTPException(422, "That source is not registered.")
        db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET SOURCE_SYSTEM_ID = %s, SOURCE_NAME = %s, RUN_ID = NULL, UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE ITEM_ID = %s", (body.source_system_id, reg["source_system_name"], item_id))
        _event(db, request_id, "SOURCE_LINKED", f"Linked to the registered source {reg['source_system_name']}", item_id=item_id,
               frm=it.get("source_system_id"), to=body.source_system_id)
    else:
        db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET SOURCE_SYSTEM_ID = NULL, RUN_ID = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s",
                   (item_id,))
        _event(db, request_id, "SOURCE_LINKED", "Unlinked from its registration", item_id=item_id, frm=it.get("source_system_id"))
    return _detail(db, request_id)


class LinkRun(BaseModel):
    run_id: Optional[str] = Field(default=None, max_length=36)


@router.post("/api/onboarding-requests/{request_id}/sources/{item_id}/run")
@_guard_deployed
def link_run(request_id: str, item_id: str, body: LinkRun, db: Db = Depends(current_db)):
    """Link the run that onboards this source (or unlink a wrong one). The run must read this source and belong to
    the request's domain, and must not be a table run of a multi-table run (link its parent)."""
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(_holds(who, "RUN.CREATE", "RUN.OPERATE"), "linking a run", "RUN.CREATE or RUN.OPERATE")
    it = _item(db, request_id, item_id)
    if body.run_id:
        found = db.query(f"SELECT {RUN_COLS} FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s AND DELETED_AT IS NULL", (body.run_id,))
        if not found:
            raise HTTPException(422, "No such run.")
        run = found[0]
        if run.get("parent_run_id"):
            raise HTTPException(422, "That is one table of a multi-table run: link the run it belongs to.")
        if it.get("source_system_id") and run.get("source_system_id") and run["source_system_id"] != it["source_system_id"]:
            raise HTTPException(422, f"That run reads another source, not {it['source_name']}.")
        if r.get("domain_id") and run.get("domain_id") and run["domain_id"] != r["domain_id"]:
            raise HTTPException(422, "That run belongs to another domain.")
        db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET RUN_ID = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s", (body.run_id, item_id))
        _event(db, request_id, "RUN_LINKED", f"Linked the run {run['run_name']}", item_id=item_id, frm=it.get("run_id"), to=body.run_id)
    else:
        db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET RUN_ID = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s", (item_id,))
        _event(db, request_id, "RUN_UNLINKED", None, item_id=item_id, frm=it.get("run_id"))
    return _detail(db, request_id)


@router.post("/api/onboarding-requests/{request_id}/sources/{item_id}/remove")
@_guard_deployed
def remove_source(request_id: str, item_id: str, db: Db = Depends(current_db)):
    """Take a source off the request (kept in the history)."""
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(str(r.get("requested_by") or "").upper() == who["user"].upper() or _holds(who, "SOURCE.CONNECT"),
          "removing a source", "being the requester, or SOURCE.CONNECT")
    it = _item(db, request_id, item_id)
    if it.get("removed_at"):
        return _detail(db, request_id)
    left = [i for i in _items(db, [request_id]).get(request_id, []) if not i.get("removed_at") and i["item_id"] != item_id]
    if r.get("request_type") == "SOURCE" and not left:
        raise HTTPException(409, "A source request needs at least one source: cancel the request instead.")
    db.execute("UPDATE CORE.ONBOARDING_REQUEST_ITEM SET REMOVED_BY = CURRENT_USER(), REMOVED_AT = CURRENT_TIMESTAMP(), STATUS = 'REMOVED', "
               "UPDATED_AT = CURRENT_TIMESTAMP() WHERE ITEM_ID = %s", (item_id,))
    _event(db, request_id, "SOURCE_REMOVED", None, item_id=item_id, frm=it.get("source_name"))
    return _detail(db, request_id)


class LinkDomain(BaseModel):
    domain_id: str = Field(max_length=36)


@router.post("/api/onboarding-requests/{request_id}/domain")
@_guard_deployed
def link_domain(request_id: str, body: LinkDomain, db: Db = Depends(current_db)):
    """Point a request for a new domain at the domain once it exists."""
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(str(r.get("requested_by") or "").upper() == who["user"].upper() or _holds(who, "DOMAIN.ONBOARD", "DOMAIN.EDIT"),
          "linking the domain", "being the requester, or DOMAIN.ONBOARD or DOMAIN.EDIT")
    if r.get("domain_id"):
        raise HTTPException(409, "The request already has its domain.")
    domain = _domains(db, [body.domain_id]).get(body.domain_id)
    if not domain or domain.get("active_flag") is False:
        raise HTTPException(422, "No such active domain.")
    db.execute("UPDATE CORE.ONBOARDING_REQUEST SET DOMAIN_ID = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s", (body.domain_id, request_id))
    _event(db, request_id, "DOMAIN_LINKED", f"Linked to the {domain['domain_name']} domain", to=body.domain_id)
    return _detail(db, request_id)


class Note(BaseModel):
    note: Optional[str] = Field(default=None, max_length=4000)


@router.post("/api/onboarding-requests/{request_id}/comment")
@_guard_deployed
def comment(request_id: str, body: Note, db: Db = Depends(current_db)):
    who = _who(db)
    _one(db, who, request_id)
    if not (body.note or "").strip():
        raise HTTPException(422, "Write the comment.")
    _event(db, request_id, "COMMENT", body.note.strip())
    db.execute("UPDATE CORE.ONBOARDING_REQUEST SET UPDATED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s", (request_id,))
    return _detail(db, request_id)


def _close(db: Db, request_id: str, status: str, reason: Optional[str], kind: str) -> Dict[str, Any]:
    db.execute("UPDATE CORE.ONBOARDING_REQUEST SET STATUS = %s, STAGE = 'CLOSED', WAITING_ON = NULL, WAITING_LABEL = NULL, CLOSED_BY = CURRENT_USER(), "
               "CLOSED_AT = CURRENT_TIMESTAMP(), CLOSE_REASON = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s AND STATUS = 'OPEN'",
               (status, (reason or "").strip() or None, request_id))
    _event(db, request_id, kind, reason, to=status)
    return _detail(db, request_id)


@router.post("/api/onboarding-requests/{request_id}/cancel")
@_guard_deployed
def cancel(request_id: str, body: Note, db: Db = Depends(current_db)):
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(str(r.get("requested_by") or "").upper() == who["user"].upper() or _holds(who, "ROLE.MANAGE"),
          "cancelling a request", "being the requester, or ROLE.MANAGE")
    return _close(db, request_id, rq.CANCELLED, body.note, "CANCELLED")


@router.post("/api/onboarding-requests/{request_id}/reject")
@_guard_deployed
def reject(request_id: str, body: Note, db: Db = Depends(current_db)):
    who = _who(db)
    r = _one(db, who, request_id)
    _open(r)
    _need(_holds(who, "SOURCE.CONNECT", "DOMAIN.ONBOARD", "DOMAIN.EDIT", "ROLE.MANAGE"), "rejecting a request",
          "SOURCE.CONNECT, DOMAIN.ONBOARD, DOMAIN.EDIT or ROLE.MANAGE")
    if not (body.note or "").strip():
        raise HTTPException(422, "Say why the request is rejected; the requester sees it.")
    if str(r.get("requested_by") or "").upper() == who["user"].upper() and "*" not in who["privileges"]:
        raise HTTPException(403, "You cannot reject your own request: cancel it instead.")
    return _close(db, request_id, rq.REJECTED, body.note, "REJECTED")


@router.post("/api/onboarding-requests/{request_id}/reopen")
@_guard_deployed
def reopen(request_id: str, body: Note, db: Db = Depends(current_db)):
    who = _who(db)
    r = _one(db, who, request_id)
    if r.get("status") == rq.OPEN:
        return _detail(db, request_id)
    if r.get("status") == rq.DONE:
        raise HTTPException(409, "The request is done; file a new request for anything more.")
    _need(str(r.get("requested_by") or "").upper() == who["user"].upper() or _holds(who, "ROLE.MANAGE"),
          "reopening a request", "being the requester, or ROLE.MANAGE")
    db.execute("UPDATE CORE.ONBOARDING_REQUEST SET STATUS = 'OPEN', CLOSED_BY = NULL, CLOSED_AT = NULL, CLOSE_REASON = NULL, "
               "UPDATED_AT = CURRENT_TIMESTAMP(), STAGE_CHANGED_AT = CURRENT_TIMESTAMP() WHERE REQUEST_ID = %s", (request_id,))
    _event(db, request_id, "REOPENED", body.note, frm=r.get("status"), to="OPEN")
    return _detail(db, request_id)
