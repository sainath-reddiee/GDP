"""A domain's own skills and documents (V051).

Skills: GET/PUT /api/domains/{id}/skills. Runs of the domain load its skills at their stage (services/knowledge/
skills.with_domain): added to the stage's bound skills, or in place of one of them.

Documents: Confluence pages or spaces linked to the domain (read as the signed-in person through their Atlassian
sign-in) and uploaded files. Each page's text is kept with its version; the AI proposes knowledge from it
(services/knowledge/documents.py), and every proposal waits for a steward in the review inbox. A refresh re-reads only
pages whose version changed.

Uploads (V052): any file is accepted and kept as it is in @CONFLUENCE.DOCUMENT_FILES; it is read in the background
(services/knowledge/document_files.py: text, Excel, Cortex document parsing for PDF, Word, PowerPoint and HTML, OCR
and an image model for images), and the outcome is recorded on the document. GET .../file returns the original;
GET .../review returns the text read with every item proposed from it, for review side by side.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.db import Db, transaction
from app.main import _snowflake_error, current_db, domain_detail
from app.source_invoke import invoke_source
from services.knowledge import document_files, documents

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

FILE_COLUMNS = """S.STAGE_PATH, S.FILE_SIZE, S.CONTENT_TYPE, S.FILE_SHA256, S.FILE_KIND,
       IFF(S.READ_STATUS IN ('QUEUED', 'READING') AND S.READ_STARTED_AT < DATEADD(MINUTE, -%d, CURRENT_TIMESTAMP()),
           'INTERRUPTED', S.READ_STATUS) AS READ_STATUS,
       S.READ_METHOD, S.READ_NOTE, S.PAGES AS PAGES_COUNT, S.CHARS_TOTAL, S.CHARS_READ,
       TO_VARCHAR(S.READ_FINISHED_AT, 'YYYY-MM-DD HH24:MI') AS READ_FINISHED_AT""" % document_files.STALE_MINUTES


def _files_deployed(exc: Exception) -> bool:
    """False when V052 is not applied yet (the file columns are unknown)."""
    return "invalid identifier" not in str(exc).lower()


def _sources(db: Db, domain_id: str) -> List[Dict[str, Any]]:
    base = """SELECT S.SOURCE_ID, S.KIND, S.SPACE_KEY, S.PAGE_ID, S.TITLE, S.URL, S.FILE_NAME, S.STATUS, S.CREATED_BY,
                     TO_VARCHAR(S.CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT,
                     TO_VARCHAR(S.LAST_REFRESHED_AT, 'YYYY-MM-DD HH24:MI') AS LAST_REFRESHED_AT, S.LAST_RESULT{files}
                FROM CONFLUENCE.SOURCE S WHERE S.DOMAIN_ID = %s AND S.STATUS = 'ACTIVE' ORDER BY S.CREATED_AT DESC"""
    try:
        rows = db.query(base.format(files=", " + FILE_COLUMNS), (domain_id,))
    except Exception as exc:
        if _files_deployed(exc):
            raise
        rows = db.query(base.format(files=""), (domain_id,))
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
            return {"sources": [], "deployed": False, "files": False, "confluence": _confluence_state(db)}
        raise _snowflake_error(exc) from exc
    return {"sources": sources, "deployed": True, "files": _files_ready(db), "confluence": _confluence_state(db),
            "limits": {"max_mb": document_files.MAX_FILE_BYTES // (1024 * 1024), "max_files": document_files.MAX_FILES}}


def _files_ready(db: Db) -> bool:
    """True once V052 is applied (originals are kept and any file type is read)."""
    try:
        db.query("SELECT STAGE_PATH FROM CONFLUENCE.SOURCE LIMIT 0")
        return True
    except Exception:
        return False


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


def _propose(db: Db, domain_id: str, source_id: str, page: Dict[str, Any], origin: str, progress=None) -> Dict[str, Any]:
    detail = domain_detail(domain_id, db)
    tables = [t.get("target_table") for t in detail.get("targets") or []]

    def run(session):
        return documents.propose(session, domain_id=domain_id, domain_name=detail["domain"]["domain_name"], tables=tables,
                                 source_id=source_id, page_id=page["id"], title=page.get("title") or "document",
                                 text=page.get("text") or "", version=page.get("version"), url=page.get("url"), origin=origin,
                                 progress=progress)
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
            row = _upload_row(db, domain_id, source_id)
            if not row.get("stage_path"):
                raise HTTPException(400, "This document was uploaded before originals were kept; upload it again to read it again.")
            if row.get("read_status") in ("QUEUED", "READING"):
                raise HTTPException(409, "The document is being read now.")
            _start_reading(db, domain_id, source_id)
            return {"source_id": source_id, "read_status": "QUEUED"}
        return _refresh(db, domain_id, found[0])

    try:
        return await run_in_threadpool(go)
    except HTTPException:
        raise
    except Exception as exc:
        if hasattr(exc, "status") and hasattr(exc, "message"):
            raise jira_api._jira_error(exc) from None
        raise _deployed(exc) from exc


def _upload_row(db: Db, domain_id: str, source_id: str) -> Dict[str, Any]:
    found = db.query("""SELECT SOURCE_ID, FILE_NAME, TITLE, STAGE_PATH, CONTENT_TYPE, FILE_SIZE, FILE_KIND, READ_STATUS
                          FROM CONFLUENCE.SOURCE WHERE SOURCE_ID = %s AND DOMAIN_ID = %s AND KIND = 'UPLOAD'""",
                     (source_id, domain_id))
    if not found:
        raise HTTPException(404, "document not found")
    return found[0]


def _put(db: Db, path: str, data: bytes) -> None:
    """Keep the original file at @CONFLUENCE.DOCUMENT_FILES/<path>."""
    folder, name = path.rsplit("/", 1)
    tmp = Path(tempfile.mkdtemp())
    try:
        local = tmp / name
        local.write_bytes(data)
        db.execute(f"PUT 'file://{local.as_posix()}' '@{document_files.STAGE}/{folder}/' AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _get(db: Db, path: str) -> bytes:
    """The original file kept at @CONFLUENCE.DOCUMENT_FILES/<path>."""
    tmp = Path(tempfile.mkdtemp())
    try:
        db.execute(f"GET '@{document_files.STAGE}/{path}' 'file://{tmp.as_posix()}/'")
        found = [f for f in tmp.rglob("*") if f.is_file()]
        if not found:
            raise HTTPException(404, "The original file is no longer in the document stage.")
        return found[0].read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _set(db: Db, source_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k.upper()} = %s" for k in fields)
    db.execute(f"UPDATE CONFLUENCE.SOURCE SET {cols} WHERE SOURCE_ID = %s", (*fields.values(), source_id))


_reading: set = set()
_reading_lock = threading.Lock()


def _start_reading(db: Db, domain_id: str, source_id: str, data: Optional[bytes] = None) -> None:
    """Read the document in the background (a long PDF read by Cortex takes minutes); its status is kept on the row,
    so the page shows progress and an interrupted read can be started again from the kept file."""
    with _reading_lock:
        if source_id in _reading:
            return
        _reading.add(source_id)
    _set(db, source_id, read_status="QUEUED", read_note="Waiting to be read")
    db.execute("UPDATE CONFLUENCE.SOURCE SET READ_STARTED_AT = CURRENT_TIMESTAMP(), READ_FINISHED_AT = NULL WHERE SOURCE_ID = %s",
               (source_id,))

    def run() -> None:
        try:
            _read_upload(db, domain_id, source_id, data)
        except Exception as exc:
            try:
                _set(db, source_id, read_status="FAILED", read_note=str(getattr(exc, "detail", exc))[:3900])
                db.execute("UPDATE CONFLUENCE.SOURCE SET READ_FINISHED_AT = CURRENT_TIMESTAMP() WHERE SOURCE_ID = %s", (source_id,))
            except Exception:
                pass
        finally:
            with _reading_lock:
                _reading.discard(source_id)

    threading.Thread(target=run, name=f"document-{source_id[:8]}", daemon=True).start()


def _read_upload(db: Db, domain_id: str, source_id: str, data: Optional[bytes] = None) -> Dict[str, Any]:
    """Read one kept file, store its text, propose its knowledge, and record the outcome on the document."""
    row = _upload_row(db, domain_id, source_id)
    name = row.get("file_name") or row.get("title") or "document"
    path = row["stage_path"]
    _set(db, source_id, read_status="READING", read_note="Reading the file")
    if data is None:
        data = _get(db, path)
    detail = domain_detail(domain_id, db)
    out = document_files.read(lambda sql, params: db.query(sql, params), name, data, path, detail["domain"]["domain_name"])
    text = (out["text"] or "").replace("\r\n", "\n").strip()
    notes = [n for n in [out["note"]] if n]
    if out["attempts"] and text:
        notes.append("Also tried: " + "; ".join(out["attempts"][:4]))
    elif out["attempts"]:
        notes.append("; ".join(out["attempts"][:6]))
    if not text:
        _set(db, source_id, read_status="NOT_READABLE", file_kind=out["kind"], read_method=out["method"] or None,
             read_note=(" ".join(notes) or "No text could be read from it; the file is kept.")[:3900], chars_total=0, chars_read=0)
        db.execute("""UPDATE CONFLUENCE.SOURCE SET READ_FINISHED_AT = CURRENT_TIMESTAMP(), LAST_REFRESHED_AT = CURRENT_TIMESTAMP(),
                             LAST_RESULT = PARSE_JSON('{"read": 0, "proposed": 0}') WHERE SOURCE_ID = %s""", (source_id,))
        return {"read_status": "NOT_READABLE"}
    _set(db, source_id, file_kind=out["kind"], read_method=out["method"], pages=out.get("pages"), chars_total=len(text),
         read_note="Proposing knowledge")
    page = {"id": hashlib.sha256(data).hexdigest()[:16],
            "title": name, "version": 1, "url": None, "text": text}

    def progress(part: int, parts: int) -> None:
        try:
            _set(db, source_id, read_note=f"Proposing knowledge: part {part} of {parts}")
        except Exception:
            pass

    result = _propose(db, domain_id, source_id, page, "DOCUMENT", progress)
    db.execute("DELETE FROM CONFLUENCE.PAGE WHERE SOURCE_ID = %s", (source_id,))   # one text per upload, the latest read
    _store_page(db, source_id, page, result.get("proposed", 0))
    unread = int(result.get("chars_unread") or 0)
    if unread:
        notes.append(f"Only the first {result.get('chars_read', 0):,} of {len(text):,} characters were read for knowledge "
                     f"({result.get('parts')} parts); split the document to have the rest read.")
    _set(db, source_id, read_status="PARTIAL" if unread else "READY", chars_read=int(result.get("chars_read") or len(text)),
         read_note=" ".join(notes)[:3900] or None)
    db.execute("""UPDATE CONFLUENCE.SOURCE SET READ_FINISHED_AT = CURRENT_TIMESTAMP(), LAST_REFRESHED_AT = CURRENT_TIMESTAMP(),
                         LAST_RESULT = PARSE_JSON(%s) WHERE SOURCE_ID = %s""",
               (json.dumps({"read": 1, "proposed": result.get("proposed", 0), "unchanged": result.get("unchanged", 0)}), source_id))
    return {"read_status": "PARTIAL" if unread else "READY", **result}


@router.post("/api/domains/{domain_id}/documents/upload")
async def upload_documents(domain_id: str, files_in: List[UploadFile] = File(..., alias="files"), db: Db = Depends(current_db)):
    """Upload documents of any type (up to 10 files of 50 MB). Each original is kept in @CONFLUENCE.DOCUMENT_FILES and
    read in the background; the knowledge it states is proposed for review. The same file uploaded again to the same
    domain is not stored twice."""
    files = []
    for f in files_in[: document_files.MAX_FILES]:
        data = await f.read(document_files.MAX_FILE_BYTES + 1)
        files.append(((f.filename or "document")[:512], data))
    skipped = len(files_in) - len(files)

    def store() -> List[Dict[str, Any]]:
        domain_detail(domain_id, db)
        if not _files_ready(db):
            raise HTTPException(503, "Keeping uploaded files needs migration V052. An admin applies it in Admin, Deploy.")
        out = []
        for name, data in files:
            if len(data) > document_files.MAX_FILE_BYTES:
                out.append({"file_name": name, "error": f"larger than {document_files.MAX_FILE_BYTES // (1024 * 1024)} MB"})
                continue
            if not data:
                out.append({"file_name": name, "error": "the file is empty"})
                continue
            digest = document_files.sha256(data)
            dup = db.query("""SELECT SOURCE_ID, FILE_NAME FROM CONFLUENCE.SOURCE WHERE DOMAIN_ID = %s AND KIND = 'UPLOAD'
                               AND STATUS = 'ACTIVE' AND FILE_SHA256 = %s""", (domain_id, digest))
            if dup:
                out.append({"file_name": name, "source_id": dup[0]["source_id"], "duplicate": True,
                            "note": f"Already uploaded as {dup[0]['file_name']}; not stored twice."})
                continue
            source_id = str(uuid.uuid4())
            path = document_files.stage_path(domain_id, source_id, name)
            kind, note = document_files.classify(name, data)
            _put(db, path, data)
            db.execute("""INSERT INTO CONFLUENCE.SOURCE (SOURCE_ID, DOMAIN_ID, KIND, TITLE, FILE_NAME, STAGE_PATH, FILE_SIZE,
                                                         CONTENT_TYPE, FILE_SHA256, FILE_KIND, READ_STATUS)
                          SELECT %s, %s, 'UPLOAD', %s, %s, %s, %s, %s, %s, %s, 'QUEUED'""",
                       (source_id, domain_id, name[:1024], name, path, len(data), document_files.content_type(name), digest, kind))
            _start_reading(db, domain_id, source_id, data)
            out.append({"file_name": name, "source_id": source_id, "kind": kind, "size": len(data), "read_status": "QUEUED",
                        "note": note or None})
        return out

    try:
        out = await run_in_threadpool(store)
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed(exc) from exc
    return {"documents": out, "skipped": skipped}


@router.get("/api/domains/{domain_id}/documents/{source_id}/file")
def document_file(domain_id: str, source_id: str, inline: bool = False, db: Db = Depends(current_db)):
    """The original uploaded file. Shown in the browser only for PDF and common images; anything else downloads."""
    try:
        row = _upload_row(db, domain_id, source_id)
        if not row.get("stage_path"):
            raise HTTPException(404, "This document was uploaded before originals were kept.")
        data = _get(db, row["stage_path"])
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed(exc) from exc
    ctype = row.get("content_type") or document_files.content_type(row.get("file_name") or "")
    show = inline and ctype in document_files.INLINE
    name = document_files.safe_name(row.get("file_name") or "document")
    return Response(content=data, media_type=ctype if show else "application/octet-stream",
                    headers={"Content-Disposition": f'{"inline" if show else "attachment"}; filename="{name}"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                             "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"})


@router.get("/api/domains/{domain_id}/documents/{source_id}/review")
def document_review(domain_id: str, source_id: str, db: Db = Depends(current_db)):
    """The document as read (its text per page, with [Page N] markers) and every item proposed from it, with its
    quote, page and decision, so each item can be checked against the passage it comes from."""
    from services.common.sql import variant

    domain_detail(domain_id, db)
    found = db.query("""SELECT SOURCE_ID, KIND, TITLE, FILE_NAME, URL, STATUS FROM CONFLUENCE.SOURCE
                         WHERE SOURCE_ID = %s AND DOMAIN_ID = %s""", (source_id, domain_id))
    if not found:
        raise HTTPException(404, "document not found")
    source = found[0]
    try:
        extra = db.query(f"SELECT {FILE_COLUMNS} FROM CONFLUENCE.SOURCE S WHERE S.SOURCE_ID = %s", (source_id,))
        source.update(extra[0] if extra else {})
    except Exception:
        pass
    pages = db.query("""SELECT PAGE_ID, TITLE, VERSION, URL, TEXT, TO_VARCHAR(FETCHED_AT, 'YYYY-MM-DD HH24:MI') AS FETCHED_AT
                          FROM CONFLUENCE.PAGE WHERE SOURCE_ID = %s ORDER BY TITLE""", (source_id,))
    items = db.query("""SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON, STATUS, IS_CURRENT, LINEAGE_ID,
                               CONFIDENCE, CREATED_BY, REVIEWED_BY, REVIEW_NOTE,
                               TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                          FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                         WHERE DOMAIN_ID = %s AND CONTENT_JSON:source:source_id::STRING = %s
                           AND STATUS IN ('PROPOSED', 'ACTIVE', 'REJECTED')
                         ORDER BY CREATED_AT DESC LIMIT 1000""", (domain_id, source_id))
    text_of = {p["page_id"]: p.get("text") or "" for p in pages}
    seen, out = set(), []
    for it in items:   # the newest decision per item (lineage)
        key = it.get("lineage_id") or it["knowledge_id"]
        if key in seen:
            continue
        seen.add(key)
        cj = variant(it.get("content_json")) or {}
        page_id = str((cj.get("source") or {}).get("page_id") or "")
        quote = cj.get("quote") or ""
        text = text_of.get(page_id, "")
        out.append({"knowledge_id": it["knowledge_id"], "knowledge_type": it["knowledge_type"], "title": it["title"],
                    "content": it["content"], "status": it["status"], "confidence": it.get("confidence"),
                    "quote": quote, "page_id": page_id, "found": document_files.find(text, quote) is not None,
                    "page": document_files.page_of(text, quote), "target_table": cj.get("target_table"),
                    "target_column": cj.get("target_column"), "created_by": it.get("created_by"),
                    "created_at": it.get("created_at"), "reviewed_by": it.get("reviewed_by"), "review_note": it.get("review_note")})
    order = {"PROPOSED": 0, "ACTIVE": 1, "REJECTED": 2}
    out.sort(key=lambda i: (order.get(i["status"], 3), i["page"] or 0, i["title"] or ""))
    limit = 400_000
    return {"source": source,
            "pages": [{**{k: p.get(k) for k in ("page_id", "title", "version", "url", "fetched_at")},
                       "text": (p.get("text") or "")[:limit], "truncated": len(p.get("text") or "") > limit} for p in pages],
            "items": out, "counts": {s_: sum(1 for i in out if i["status"] == s_) for s_ in ("PROPOSED", "ACTIVE", "REJECTED")}}


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


# ---------------------------------------------------------------- correct-output examples

def _approved_entities(db: Db, domain_id: str) -> Dict[str, Dict[str, Any]]:
    """The tables of the domain's approved data model, by name (empty before a model is approved)."""
    from services.common.sql import variant
    from services.modeling import spec_v2

    found = db.query("""SELECT D.SPEC, M.MODEL_ID FROM GOVERNANCE.DOMAIN_MODEL M
                          JOIN GOVERNANCE.MODEL_DESIGNS D ON D.MODEL_ID = M.MODEL_ID AND D.VERSION = M.APPROVED_VERSION
                         WHERE M.DOMAIN_ID = %s""", (domain_id,))
    if not found:
        return {}
    spec = spec_v2.active(spec_v2.normalize_v2(variant(found[0]["spec"]) or {}, found[0]["model_id"]))
    return {e["entity_name"]: e for e in spec["design"]["entities"]}


@router.get("/api/domains/{domain_id}/examples")
def domain_examples(domain_id: str, db: Db = Depends(current_db)):
    """The correct-output examples per table: the accepted ones and the ones waiting for review."""
    from services.common.sql import variant

    domain_detail(domain_id, db)
    rows = db.query("""SELECT KNOWLEDGE_ID, STATUS, IS_CURRENT, CONTENT_JSON, CREATED_BY, VERIFIED_BY,
                              TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                         FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                        WHERE DOMAIN_ID = %s AND KNOWLEDGE_TYPE = 'EXAMPLE'
                          AND (IS_CURRENT AND STATUS = 'ACTIVE' OR STATUS = 'PROPOSED')
                        ORDER BY CREATED_AT DESC""", (domain_id,))
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cj = variant(r.get("content_json")) or {}
        table = str(cj.get("target_table") or "").upper()
        if not table:
            continue
        slot = out.setdefault(table, {"table": table, "active": None, "proposed": None})
        key = "active" if r["status"] == "ACTIVE" else "proposed"
        if slot[key] is None:
            slot[key] = {"knowledge_id": r["knowledge_id"], "columns": cj.get("columns") or [], "rows": (cj.get("rows") or [])[:10],
                         "row_count": cj.get("row_count") or 0, "file": cj.get("file"), "warnings": cj.get("warnings") or [],
                         "created_by": r.get("created_by"), "created_at": r.get("created_at"), "verified_by": r.get("verified_by")}
    return {"tables": out}


@router.post("/api/domains/{domain_id}/examples/{table}")
async def upload_examples(domain_id: str, table: str, file: UploadFile = File(...), db: Db = Depends(current_db)):
    """Correct output rows of one table (CSV or Excel), checked against the approved data model and proposed for
    review as the table's examples (a new upload replaces the waiting proposal)."""
    from services.knowledge import examples

    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(400, "The file is larger than 10 MB; examples need only a few rows.")
    name = (file.filename or "examples.csv")[:300]
    table = table.strip().upper()

    def work():
        domain_detail(domain_id, db)
        try:
            header, rows = examples.read_rows(name, data)
            checked = examples.check(table, header, rows, _approved_entities(db, domain_id).get(table))
        except examples.ExampleError as exc:
            raise HTTPException(400, str(exc)) from None
        written = invoke_source(db, lambda session: examples.write(session, domain_id=domain_id, checked=checked, file_name=name))
        return {**{k: checked[k] for k in ("table", "columns", "row_count", "unknown_columns", "absent_columns", "missing_keys",
                                         "warnings")}, "knowledge_id": written, "unchanged": written is None}

    try:
        return await run_in_threadpool(work)
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc
