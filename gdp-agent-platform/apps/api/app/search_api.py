"""Search the platform by meaning (services/search/resources.py): GET /api/search?q=&kinds=&limit=.

Workflows, domains, sources, runs, target tables and domain knowledge, ranked by a fusion of Cortex Search (keyword +
vector, CORE.RESOURCE_SEARCH and CORE.KNOWLEDGE_SEARCH) and the platform's own keyword scoring with synonyms. Before
the search service is deployed, or when Cortex is not available, the keyword half answers alone (mode "keyword").

"waiting on me" keeps what waits on a privilege the caller holds (a workflow's next step, a run's gate, a failed run
they operate); "failed" keeps failed runs and workflows that need attention. Results are limited to the domains the
caller may see (domain membership, services/governance/domains.can_see), as everywhere else.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.db import DATABASE, Db
from app.main import _json, current_db
from services.common.platform_schema import PLATFORM_SCHEMA
from services.governance import domains as domain_access
from services.governance.policy import privilege_for
from services.search import resources as rs

router = APIRouter()

GATE_TO = {"MAPPING_REVIEW": "MAPPING_APPROVED", "STTM_REVIEW": "STTM_APPROVED", "SODA_REVIEW": "SODA_APPROVED",
           "DBT_REVIEW": "DBT_APPROVED"}
FAILED_STATES = {"FAILED", "VALIDATION_FAILED"}


def gate_privilege(state: str) -> Optional[str]:
    """The privilege that decides a run's human gate, as the review endpoint checks it."""
    to = GATE_TO.get(state)
    return privilege_for("POST", "/api/runs/x/review", {"to_state": to, "decision": "APPROVE"})[0] if to else None


def _rows(db: Db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []   # a store not deployed yet answers nothing rather than failing the search


def documents(db: Db) -> Dict[str, Dict[str, Any]]:
    """Every searchable resource the keyword half scores, by key."""
    domains = {d["domain_id"]: d for d in _rows(db, "SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, ACTIVE_FLAG FROM CORE.DOMAIN_REGISTRY")}
    name = lambda did: (domains.get(did or "") or {}).get("domain_name")  # noqa: E731
    out: Dict[str, Dict[str, Any]] = {}

    def add(kind: str, rid: str, **fields: Any) -> None:
        k = rs.key(kind, rid)
        out[k] = {"key": k, "kind": kind, "id": rid, **fields}
        out[k]["href"] = rs.href(kind, rid, fields.get("domain_id"), fields.get("status"))

    for r in _rows(db, """SELECT REQUEST_ID, REQUEST_TYPE, TITLE, BUSINESS_OUTCOME, DOMAIN_ID, PROPOSED_DOMAIN, STATUS, STAGE,
                                 WAITING_ON, WAITING_LABEL, ATTENTION FROM CORE.ONBOARDING_REQUEST"""):
        add("WORKFLOW", r["request_id"], title=r["title"], subtitle=r.get("waiting_label") or r.get("stage"),
            text=f"{r.get('request_type')} request {r.get('business_outcome') or ''} {r.get('proposed_domain') or ''}",
            domain_id=r.get("domain_id"), domain_name=name(r.get("domain_id")) or r.get("proposed_domain"),
            status=r.get("status"), waiting_on=r.get("waiting_on"), attention=r.get("attention"))
    for d in domains.values():
        add("DOMAIN", d["domain_id"], title=d["domain_name"], subtitle=d.get("description"), text=d.get("owner"),
            domain_id=d["domain_id"], domain_name=d["domain_name"], status="ACTIVE" if d.get("active_flag") is not False else "RETIRED")
    for s in _rows(db, "SELECT SOURCE_SYSTEM_ID, SOURCE_SYSTEM_NAME, SOURCE_TYPE, DOMAIN_ID, OWNER, ACTIVE_FLAG FROM CORE.SOURCE_REGISTRY"):
        add("SOURCE", s["source_system_id"], title=s["source_system_name"],
            subtitle=" · ".join(x for x in (s.get("source_type"), name(s.get("domain_id"))) if x), text=s.get("owner"),
            domain_id=s.get("domain_id"), domain_name=name(s.get("domain_id")),
            status="ACTIVE" if s.get("active_flag") is not False else "RETIRED")
    for w in _rows(db, """SELECT RUN_ID, RUN_NAME, DOMAIN_ID, CURRENT_STATE, FAILURE_REASON, TARGET_MODEL, SOURCE_DATABASE, SOURCE_SCHEMA
                            FROM CORE.WORKFLOW_RUN WHERE DELETED_AT IS NULL AND NOT COALESCE(IS_ARCHIVED, FALSE)
                           ORDER BY UPDATED_AT DESC LIMIT 2000"""):
        add("RUN", w["run_id"], title=w["run_name"],
            subtitle=" · ".join(x for x in (w.get("current_state"), name(w.get("domain_id"))) if x),
            text=f"{w.get('failure_reason') or ''} {w.get('target_model') or ''} {w.get('source_database') or ''} {w.get('source_schema') or ''}",
            domain_id=w.get("domain_id"), domain_name=name(w.get("domain_id")), status=w.get("current_state"))
    for t in _rows(db, """SELECT TARGET_TABLE_ID, TARGET_TABLE, TARGET_SCHEMA, TABLE_TYPE, DESCRIPTION, GRAIN, DOMAIN_ID
                            FROM CORE.TARGET_TABLE_REGISTRY WHERE COALESCE(ACTIVE_FLAG, TRUE)"""):
        add("TABLE", t["target_table_id"], title=t["target_table"],
            subtitle=" · ".join(x for x in (t.get("target_schema"), name(t.get("domain_id"))) if x),
            text=f"{t.get('table_type') or ''} {t.get('description') or ''} {t.get('grain') or ''}",
            domain_id=t.get("domain_id"), domain_name=name(t.get("domain_id")), status="ACTIVE")
    return out


def _preview(db: Db, service: str, request: Dict[str, Any]) -> List[Dict[str, Any]]:
    found = db.query("SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s) AS R",
                     (f"{DATABASE}.{PLATFORM_SCHEMA}.{service}", json.dumps(request)))
    return (_json(found[0]["r"]) or {}).get("results", []) if found else []


def semantic(db: Db, text: str, kinds: List[str], limit: int) -> Optional[List[Dict[str, Any]]]:
    """Cortex Search hits as resources, best first; None when the search services cannot answer."""
    hits: List[Dict[str, Any]] = []
    ok = False
    if any(k != "KNOWLEDGE" for k in kinds):
        try:
            for h in _preview(db, "RESOURCE_SEARCH", rs.cortex_request(text, kinds, limit)):
                low = {k.lower(): v for k, v in h.items()}
                hits.append({"key": rs.key(low["kind"], low["resource_id"]), "kind": low["kind"], "id": low["resource_id"],
                             "title": low.get("title"), "subtitle": low.get("subtitle"), "domain_id": low.get("domain_id"),
                             "domain_name": low.get("domain_name"), "status": low.get("status")})
            ok = True
        except Exception:
            pass
    if "KNOWLEDGE" in kinds:
        try:
            from services.knowledge.search import search_request

            request = {**search_request(text, limit=min(limit, 20)), "columns": ["KNOWLEDGE_ID", "TITLE", "CONTENT", "DOMAIN_NAME", "KNOWLEDGE_TYPE"]}
            for h in _preview(db, "KNOWLEDGE_SEARCH", request):
                low = {k.lower(): v for k, v in h.items()}
                hits.append({"key": rs.key("KNOWLEDGE", low["knowledge_id"]), "kind": "KNOWLEDGE", "id": low["knowledge_id"],
                             "title": low.get("title"), "subtitle": f"{low.get('knowledge_type') or ''} · {(low.get('content') or '')[:120]}",
                             "domain_name": low.get("domain_name"), "status": "ACTIVE"})
            ok = True
        except Exception:
            pass
    return hits if ok else None


def _who(db: Db) -> Dict[str, Any]:
    from app.cases_api import _who as who

    return who(db)


def _holds(who: Dict[str, Any], privilege: Optional[str]) -> bool:
    return bool(privilege) and ("*" in who["privileges"] or privilege in who["privileges"])


def waits_on(doc: Dict[str, Any], who: Dict[str, Any]) -> bool:
    """The resource's next step is the caller's."""
    if doc["kind"] == "WORKFLOW":
        return doc.get("status") == "OPEN" and _holds(who, doc.get("waiting_on"))
    if doc["kind"] == "RUN":
        state = str(doc.get("status") or "")
        return _holds(who, gate_privilege(state)) or (state in FAILED_STATES and _holds(who, "RUN.OPERATE"))
    return False


def needs_attention(doc: Dict[str, Any]) -> bool:
    return (doc["kind"] == "RUN" and doc.get("status") in FAILED_STATES) or (doc["kind"] == "WORKFLOW" and bool(doc.get("attention")))


@router.get("/api/search")
def search(q: str = Query("", max_length=500), kinds: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=rs.MAX_LIMIT),
           db: Db = Depends(current_db)):
    intent = rs.parse(q)
    asked = [k.strip().upper() for k in kinds.split(",") if k.strip().upper() in rs.KINDS]
    wanted = asked or intent["kinds"] or list(rs.KINDS)
    if intent["mine"] or intent["failed"]:   # only workflows and runs wait on people or fail
        wanted = [k for k in wanted if k in ("WORKFLOW", "RUN")] or ["WORKFLOW", "RUN"]
    who = _who(db)
    docs = documents(db)
    seen: Dict[str, bool] = {}

    def visible(d: Dict[str, Any]) -> bool:
        did = d.get("domain_id")
        if did not in seen:
            seen[did] = domain_access.can_see(db, who["user"], did, who["privileges"], who["roles"])
        return seen[did]

    def keep(d: Dict[str, Any]) -> bool:
        return (d["kind"] in wanted and visible(d) and (not intent["mine"] or waits_on(d, who))
                and (not intent["failed"] or needs_attention(d)))

    pool = {k: d for k, d in docs.items() if keep(d)}
    if not intent["terms"]:
        # a filter alone ("waiting on me", "failed runs"): everything it keeps, workflows first
        if not (intent["mine"] or intent["failed"] or asked or intent["kinds"]):
            return {"query": q, "mode": "none", "intent": intent, "results": [], "counts": {}}
        ordered = sorted(pool.values(), key=lambda d: (rs.KINDS.index(d["kind"]), str(d.get("title") or "")))[:limit]
        results = rs.merge([], [(d["key"], 1.0, ["filter"]) for d in ordered], pool, limit)
        mode = "filter"
    else:
        keyword = rs.keyword_rank(intent["terms"], pool.values(), limit * 2)
        hits = semantic(db, intent["text"], wanted, limit * 2)
        if hits is None:
            mode, sem = "keyword", []
        else:
            mode = "hybrid"
            # semantic hits pass the same filters: kind, visibility, intent (knowledge has no domain id: its domain name)
            sem = [h for h in hits if (h["key"] in pool) or (h["kind"] == "KNOWLEDGE" and "KNOWLEDGE" in wanted
                                                              and not (intent["mine"] or intent["failed"]))]
        results = rs.merge(sem, keyword, pool, limit)
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"query": q, "mode": mode, "intent": intent, "results": results, "counts": counts}
