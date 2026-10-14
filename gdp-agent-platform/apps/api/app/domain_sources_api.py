"""A domain's own skills and documents (V051).

Skills: GET/PUT /api/domains/{id}/skills. Runs of the domain load its skills at their stage (services/knowledge/
skills.with_domain): added to the stage's bound skills, or in place of one of them.

Documents: Confluence pages or spaces linked to the domain (read as the signed-in person through their Atlassian
sign-in) and uploaded files. Each page's text is kept with its version; the AI proposes knowledge from it
(services/knowledge/documents.py), and every proposal waits for a steward in the review inbox. A refresh re-reads only
pages whose version changed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.db import Db, transaction
from app.main import _snowflake_error, current_db, domain_detail
from app.source_invoke import invoke_source
from services.knowledge import documents

router = APIRouter()
STAGES = ("LANDING", "PROFILING", "DOMAIN", "MODELING", "MAPPING", "STTM", "DBT", "SODA", "QA", "VALIDATION")
MAX_SPACE_PAGES = 20
MAX_FILE_BYTES = 10 * 1024 * 1024


def _missing(exc: Exception) -> bool:
    return "does not exist" in str(exc).lower()


def _deployed(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if _missing(exc):
        return HTTPException(503, "Domain skills and documents need migration V051. An admin applies it in Admin, Deploy.")
    return _snowflake_error(exc)


# ---------------------------------------------------------------- skills

@router.get("/api/domains/{domain_id}/skills")
def domain_skills(domain_id: str, db: Db = Depends(current_db)):
    """Per stage: the skills the standard binds, the domain's own, and what the domain's runs load."""
    from services.knowledge.skills import bound, with_domain
    from services.knowledge.usage import GDP_ONLY_SKILLS, STAGE_SKILLS

    detail = domain_detail(domain_id, db)
    standard = next((r.get("standard") for r in db.query("SELECT CONFIG:standard::STRING AS STANDARD FROM KNOWLEDGE.DOMAIN_REGISTRY "
                                                       "WHERE DOMAIN_ID = %s", (domain_id,))), None) or "GENERIC"
    bindings = db.query("SELECT STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD FROM KNOWLEDGE.SKILL_STAGE_BINDING")
    deployed = True
    try:
        own = db.query("""SELECT STAGE, SKILL_NAME, MODE, REPLACES, POSITION, ENABLED, NOTE, UPDATED_BY,
                                 TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI') AS UPDATED_AT
                            FROM KNOWLEDGE.DOMAIN_SKILL WHERE DOMAIN_ID = %s ORDER BY STAGE, POSITION""", (domain_id,))
    except Exception as exc:
        if not _missing(exc):
            raise _snowflake_error(exc) from exc
        own, deployed = [], False
    available = db.query("""SELECT SKILL_NAME, ANY_VALUE(DESCRIPTION) AS DESCRIPTION, MAX(VERSION) AS VERSION
                              FROM KNOWLEDGE.SKILL_REGISTRY WHERE STATUS = 'ACTIVE' GROUP BY 1 ORDER BY 1""")
    std = str(standard).upper()
    stages = []
    for stage in STAGES:
        fallback = [n for n in STAGE_SKILLS.get(stage, []) if std == "GDP" or n not in GDP_ONLY_SKILLS]
        base = bound(bindings, stage, std, fallback)
        mine = [r for r in own if str(r["stage"]).upper() == stage]
        stages.append({"stage": stage, "standard_skills": base, "domain_skills": mine, "effective": with_domain(base, mine, stage)})
    return {"domain": detail["domain"], "standard": std, "stages": stages, "deployed": deployed,
            "available": [{"name": a["skill_name"], "description": (a.get("description") or "")[:300], "version": a.get("version")}
                          for a in available]}


class DomainSkillIn(BaseModel):
    stage: str = Field(min_length=2, max_length=32)
    skill_name: str = Field(min_length=1, max_length=128)
    mode: str = Field(default="ADD", pattern="^(ADD|REPLACE)$")
    replaces: Optional[str] = Field(default=None, max_length=128)
    position: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True
    note: Optional[str] = Field(default=None, max_length=2000)


class DomainSkillsIn(BaseModel):
    skills: List[DomainSkillIn] = Field(max_length=200)


@router.put("/api/domains/{domain_id}/skills")
def set_domain_skills(domain_id: str, body: DomainSkillsIn, db: Db = Depends(current_db)):
    """Replace the domain's skills (what its runs load at each stage, on top of the stage's bound skills)."""
    domain_detail(domain_id, db)
    known = {str(r["skill_name"]).upper() for r in db.query("SELECT DISTINCT SKILL_NAME FROM KNOWLEDGE.SKILL_REGISTRY WHERE STATUS <> 'RETIRED'")}
    seen = set()
    for s in body.skills:
        stage = s.stage.upper()
        if stage not in STAGES:
            raise HTTPException(400, f"{s.stage} is not a stage; use one of {', '.join(STAGES)}")
        if s.skill_name.upper() not in known:
            raise HTTPException(400, f"{s.skill_name} is not a registered skill")
        if s.mode == "REPLACE" and not s.replaces:
            raise HTTPException(400, f"{s.skill_name}: say which stage skill it replaces")
        if (stage, s.skill_name.upper()) in seen:
            raise HTTPException(400, f"{s.skill_name} is listed twice for {stage}")
        seen.add((stage, s.skill_name.upper()))
    try:
        with transaction(db):   # replace the set together: never half of it
            db.execute("DELETE FROM KNOWLEDGE.DOMAIN_SKILL WHERE DOMAIN_ID = %s", (domain_id,))
            for s in body.skills:
                db.execute("""INSERT INTO KNOWLEDGE.DOMAIN_SKILL (DOMAIN_ID, STAGE, SKILL_NAME, MODE, REPLACES, POSITION, ENABLED, NOTE)
                              SELECT %s, %s, %s, %s, NULLIF(%s, ''), %s, %s, NULLIF(%s, '')""",
                           (domain_id, s.stage.upper(), s.skill_name, s.mode, s.replaces or "", s.position, s.enabled, s.note or ""))
    except Exception as exc:
        raise _deployed(exc) from exc
    return domain_skills(domain_id, db)


# ---------------------------------------------------------------- documents

def _sources(db: Db, domain_id: str) -> List[Dict[str, Any]]:
    rows = db.query("""SELECT S.SOURCE_ID, S.KIND, S.SPACE_KEY, S.PAGE_ID, S.TITLE, S.URL, S.FILE_NAME, S.STATUS, S.CREATED_BY,
                              TO_VARCHAR(S.CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT,
                              TO_VARCHAR(S.LAST_REFRESHED_AT, 'YYYY-MM-DD HH24:MI') AS LAST_REFRESHED_AT, S.LAST_RESULT
                         FROM CONFLUENCE.SOURCE S WHERE S.DOMAIN_ID = %s AND S.STATUS = 'ACTIVE' ORDER BY S.CREATED_AT DESC""",
                    (domain_id,))
    pages = db.query("""SELECT P.SOURCE_ID, P.PAGE_ID, P.TITLE, P.VERSION, P.URL, P.PROPOSED, LENGTH(P.TEXT) AS CHARS,
                               TO_VARCHAR(P.FETCHED_AT, 'YYYY-MM-DD HH24:MI') AS FETCHED_AT
                          FROM CONFLUENCE.PAGE P JOIN CONFLUENCE.SOURCE S ON S.SOURCE_ID = P.SOURCE_ID
                         WHERE S.DOMAIN_ID = %s AND S.STATUS = 'ACTIVE'""", (domain_id,))
    by: Dict[str, List[Dict[str, Any]]] = {}
    for p in pages:
        by.setdefault(p["source_id"], []).append({k: p.get(k) for k in ("page_id", "title", "version", "url", "proposed", "chars", "fetched_at")})
    for r in rows:
        r["last_result"] = json.loads(r["last_result"]) if isinstance(r.get("last_result"), str) else r.get("last_result")
        r["pages"] = by.get(r["source_id"], [])
    return rows


@router.get("/api/domains/{domain_id}/documents")
def domain_documents(domain_id: str, db: Db = Depends(current_db)):
    """The Confluence pages, spaces and uploaded documents linked to the domain, with what they proposed."""
    domain_detail(domain_id, db)
    try:
        sources = _sources(db, domain_id)
    except Exception as exc:
        if _missing(exc):
            return {"sources": [], "deployed": False, "confluence": _confluence_state(db)}
        raise _snowflake_error(exc) from exc
    return {"sources": sources, "deployed": True, "confluence": _confluence_state(db)}


def _confluence_state(db: Db) -> Dict[str, Any]:
    from app import jira_api

    try:
        st = jira_api.status(db)
    except Exception:
        return {"enabled": False, "granted": False, "connected": False}
    return {**(st.get("confluence") or {"enabled": False, "granted": False}), "connected": bool(st.get("connected"))}


def _store_page(db: Db, source_id: str, page: Dict[str, Any], proposed: int) -> None:
    db.execute("DELETE FROM CONFLUENCE.PAGE WHERE SOURCE_ID = %s AND PAGE_ID = %s", (source_id, page["id"]))
    db.execute("""INSERT INTO CONFLUENCE.PAGE (SOURCE_ID, PAGE_ID, TITLE, VERSION, TEXT_HASH, TEXT, URL, PROPOSED)
                  SELECT %s, %s, %s, %s, %s, %s, %s, %s""",
               (source_id, page["id"], (page.get("title") or "")[:1024], page.get("version"), documents.text_hash(page.get("text") or ""),
                page.get("text") or "", page.get("url"), proposed))


def _propose(db: Db, domain_id: str, source_id: str, page: Dict[str, Any], origin: str) -> Dict[str, Any]:
    detail = domain_detail(domain_id, db)
    tables = [t.get("target_table") for t in detail.get("targets") or []]

    def run(session):
        return documents.propose(session, domain_id=domain_id, domain_name=detail["domain"]["domain_name"], tables=tables,
                                 source_id=source_id, page_id=page["id"], title=page.get("title") or "document",
                                 text=page.get("text") or "", version=page.get("version"), url=page.get("url"), origin=origin)
    return invoke_source(db, run)


def _refresh(db: Db, domain_id: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """Re-read a Confluence source: pages whose version changed are read again and propose knowledge."""
    from app import jira_api

    client, _ = jira_api.confluence_client(db)
    known = {p["page_id"]: p for p in db.query("SELECT PAGE_ID, VERSION FROM CONFLUENCE.PAGE WHERE SOURCE_ID = %s", (source["source_id"],))}
    if source["kind"] == "PAGE":
        wanted = [{"id": source["page_id"]}]
    else:
        wanted = client.space_pages(source["space_key"], MAX_SPACE_PAGES)
    result = {"read": 0, "unchanged": 0, "proposed": 0, "errors": []}
    for w in wanted[:MAX_SPACE_PAGES]:
        if w.get("version") is not None and int((known.get(w["id"]) or {}).get("version") or -1) == int(w["version"]):
            result["unchanged"] += 1
            continue
        try:
            page = client.page(w["id"])
        except Exception as exc:
            result["errors"].append({"page_id": w["id"], "error": str(getattr(exc, "message", exc))[:300]})
            continue
        if int((known.get(page["id"]) or {}).get("version") or -1) == int(page.get("version") or -2):
            result["unchanged"] += 1
            continue
        out = _propose(db, domain_id, source["source_id"], page, "CONFLUENCE") if page.get("text") else {"proposed": 0}
        _store_page(db, source["source_id"], page, out.get("proposed", 0))
        result["read"] += 1
        result["proposed"] += out.get("proposed", 0)
    db.execute("""UPDATE CONFLUENCE.SOURCE SET LAST_REFRESHED_AT = CURRENT_TIMESTAMP(), LAST_RESULT = PARSE_JSON(%s)
                   WHERE SOURCE_ID = %s""", (json.dumps(result), source["source_id"]))
    return result


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/api/domains/{domain_id}/documents/confluence")
async def link_confluence(domain_id: str, body: LinkIn, db: Db = Depends(current_db)):
    """Link a Confluence page or space to the domain and read it now (its knowledge is proposed for review)."""
    from app import jira_api
    from services.confluence.client import parse_url

    space, page_id = parse_url(body.url)
    if not space and not page_id:
        raise HTTPException(400, "Paste a Confluence page or space link, such as https://team.atlassian.net/wiki/spaces/DATA/pages/123/Title")

    def link():
        domain_detail(domain_id, db)
        client, conn = jira_api.confluence_client(db)
        if page_id:
            first = client.page(page_id)
            kind, title, url, key = "PAGE", first.get("title"), first.get("url") or body.url, first.get("space") or space
        else:
            info = client.space(space)
            kind, title, url, key = "SPACE", info.get("name") or space, body.url, space
        dup = db.query("""SELECT SOURCE_ID FROM CONFLUENCE.SOURCE WHERE DOMAIN_ID = %s AND STATUS = 'ACTIVE' AND KIND = %s
                           AND COALESCE(PAGE_ID, '') = %s AND COALESCE(SPACE_KEY, '') = %s""",
                       (domain_id, kind, page_id or "", key if kind == "SPACE" else (key or "")))
        if dup:
            source_id = dup[0]["source_id"]
        else:
            source_id = str(uuid.uuid4())
            db.execute("""INSERT INTO CONFLUENCE.SOURCE (SOURCE_ID, DOMAIN_ID, KIND, CLOUD_ID, SPACE_KEY, PAGE_ID, TITLE, URL)
                          SELECT %s, %s, %s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, %s""",
                       (source_id, domain_id, kind, conn["cloud_id"], key or "", page_id or "", (title or "")[:1024], url[:2048]))
        source = {"source_id": source_id, "kind": kind, "page_id": page_id, "space_key": key}
        return {"source_id": source_id, "title": title, "kind": kind, "result": _refresh(db, domain_id, source)}

    try:
        return await run_in_threadpool(link)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        if hasattr(exc, "status") and hasattr(exc, "message"):   # a Jira or Confluence answer
            raise jira_api._jira_error(exc) from None
        raise _deployed(exc) from exc


@router.post("/api/domains/{domain_id}/documents/{source_id}/refresh")
async def refresh_document(domain_id: str, source_id: str, db: Db = Depends(current_db)):
    """Read a linked Confluence page or space again: changed pages propose their knowledge."""
    from app import jira_api

    def go():
        found = db.query("""SELECT SOURCE_ID, KIND, PAGE_ID, SPACE_KEY FROM CONFLUENCE.SOURCE
                             WHERE SOURCE_ID = %s AND DOMAIN_ID = %s AND STATUS = 'ACTIVE'""", (source_id, domain_id))
        if not found:
            raise HTTPException(404, "document not found")
        if found[0]["kind"] == "UPLOAD":
            raise HTTPException(400, "An uploaded document is not refreshed; upload the new version instead.")
        return _refresh(db, domain_id, found[0])

    try:
        return await run_in_threadpool(go)
    except HTTPException:
        raise
    except Exception as exc:
        if hasattr(exc, "status") and hasattr(exc, "message"):
            raise jira_api._jira_error(exc) from None
        raise _deployed(exc) from exc


@router.post("/api/domains/{domain_id}/documents/upload")
async def upload_documents(domain_id: str, files_in: List[UploadFile] = File(..., alias="files"), db: Db = Depends(current_db)):
    """Upload documents (an exported Confluence page, Word, Excel, Markdown, text): their text is read and their
    knowledge proposed for review. The files themselves are not kept."""
    from services.agentic import attachments

    out = []
    for f in files_in[:10]:
        data = await f.read(MAX_FILE_BYTES + 1)
        name = (f.filename or "document")[:512]
        if len(data) > MAX_FILE_BYTES:
            out.append({"file_name": name, "error": "larger than 10 MB"})
            continue
        text, note = attachments.extract(name, data)
        if not text.strip():
            out.append({"file_name": name, "error": note or "no text could be read from it"})
            continue
        digest = hashlib.sha256(data).hexdigest()

        def store(name=name, text=text, digest=digest):
            domain_detail(domain_id, db)
            source_id = str(uuid.uuid4())
            db.execute("""INSERT INTO CONFLUENCE.SOURCE (SOURCE_ID, DOMAIN_ID, KIND, TITLE, FILE_NAME, LAST_REFRESHED_AT)
                          SELECT %s, %s, 'UPLOAD', %s, %s, CURRENT_TIMESTAMP()""", (source_id, domain_id, name, name))
            page = {"id": digest[:16], "title": name, "version": 1, "url": None, "text": text}
            result = _propose(db, domain_id, source_id, page, "DOCUMENT")
            _store_page(db, source_id, page, result.get("proposed", 0))
            db.execute("UPDATE CONFLUENCE.SOURCE SET LAST_RESULT = PARSE_JSON(%s) WHERE SOURCE_ID = %s",
                       (json.dumps({"read": 1, "proposed": result.get("proposed", 0)}), source_id))
            return {"file_name": name, "source_id": source_id, "chars": len(text), "note": note, **result}

        try:
            out.append(await run_in_threadpool(store))
        except HTTPException:
            raise
        except Exception as exc:
            raise _deployed(exc) from exc
    return {"documents": out}


@router.post("/api/domains/{domain_id}/documents/{source_id}/remove")
def remove_document(domain_id: str, source_id: str, db: Db = Depends(current_db)):
    """Unlink a document. Knowledge already accepted from it stays (it was reviewed); proposals still waiting stay in
    the inbox to decide."""
    try:
        changed = db.execute_count("UPDATE CONFLUENCE.SOURCE SET STATUS = 'REMOVED' WHERE SOURCE_ID = %s AND DOMAIN_ID = %s "
                                   "AND STATUS = 'ACTIVE'", (source_id, domain_id))
    except Exception as exc:
        raise _deployed(exc) from exc
    if not changed:
        raise HTTPException(404, "document not found")
    return {"source_id": source_id, "removed": True}
