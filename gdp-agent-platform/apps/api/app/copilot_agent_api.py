"""Copilot workspace (/dashboard/copilot): sessions, conversational turns and drafted changes (V048).

A turn runs the agent (services/agentic/loop.py) with two functions: the model call (AI_COMPLETE through
services/common/llm.complete_json, stage COPILOT, cost recorded) and an in-process call to this API as the signed-in
person (app/internal_call.py), so the agent can never read or do more than the person can. Draft tools only read,
render and dry-run; the changes they draft are stored here with the exact calls that would apply them.

Every row is the caller's own: queries filter on CREATED_BY = CURRENT_USER(), and the row access policy
CORE.COPILOT_OWN_ROWS enforces the same in Snowflake.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.db import Db
from app.internal_call import caller
from app.main import _record_cost, _snowflake_error, current_db
from app.source_invoke import invoke_source
from services.agentic import attachments as files, changeset, loop, plan as rail, publish as publisher

router = APIRouter()

SESSIONS = "CORE.COPILOT_SESSION"
EVENTS = "CORE.COPILOT_EVENT"
STEPS = "CORE.COPILOT_PLAN_STEP"
CHANGES = "CORE.COPILOT_CHANGE"
ATTACHMENTS = "CORE.COPILOT_ATTACHMENT"
MAX_FILES = 20        # per conversation
HISTORY = 40          # events read back into the prompt
STORED_KINDS = {"thought": "THOUGHT", "tool": "TOOL", "ask": "ASK", "reply": "REPLY", "error": "ERROR", "change": "CHANGE"}
PUBLISH_STATUSES = ("APPLIED", "PENDING_APPROVAL", "FAILED")


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "does not exist" in text or "not authorized" in text


def _deployed_or_503(exc: Exception) -> HTTPException:
    if _missing(exc):
        return HTTPException(503, "The copilot workspace is not deployed yet (migration V048). An admin can apply "
                                  "the latest migrations in Admin, Deploy.")
    return _snowflake_error(exc)


# ---------------------------------------------------------------- storage

def _session(db: Db, session_id: str) -> Dict[str, Any]:
    found = db.query(f"""SELECT SESSION_ID, TITLE, STATUS, JIRA_KEY, DOMAIN_ID, MODEL, CREATED_BY,
                                CREATED_AT::VARCHAR AS CREATED_AT, UPDATED_AT::VARCHAR AS UPDATED_AT
                           FROM {SESSIONS} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()""", (session_id,))
    if not found:
        raise HTTPException(404, "conversation not found")
    return found[0]


def _plan(db: Db, session_id: str) -> List[Dict[str, Any]]:
    rows = {int(r["step_no"]): r for r in db.query(
        f"SELECT STEP_NO, STEP_KEY, STATUS, NOTE FROM {STEPS} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()",
        (session_id,))}
    out = rail.initial()
    for s in out:
        r = rows.get(s["no"])
        if r:
            s["status"], s["note"] = r["status"] or "PENDING", r.get("note") or ""
    return out


def _changes(db: Db, session_id: str) -> List[Dict[str, Any]]:
    rows = db.query(f"""SELECT CHANGE_ID, SEQ, CHANGE_KEY, KIND, TITLE, OPERATIONS, PREVIEW, STATUS, RESULT, REQUEST_ID,
                               UPDATED_AT::VARCHAR AS UPDATED_AT
                          FROM {CHANGES} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER() ORDER BY SEQ""",
                    (session_id,))
    for r in rows:
        for k in ("operations", "preview", "result"):
            r[k] = _json(r.get(k))
        r["seq"] = int(r["seq"])
    return rows


def _events(db: Db, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = f"""SELECT EVENT_ID, SEQ, KIND, CONTENT, CREATED_AT::VARCHAR AS CREATED_AT
                FROM {EVENTS} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER() ORDER BY SEQ DESC"""
    rows = db.query(sql + (f" LIMIT {int(limit)}" if limit else ""), (session_id,))
    for r in rows:
        r["content"] = _json(r.get("content"))
        r["seq"] = int(r["seq"])
    return list(reversed(rows))


def _add_event(db: Db, session_id: str, seq: int, kind: str, content: Any) -> None:
    db.execute(f"INSERT INTO {EVENTS} (EVENT_ID, SESSION_ID, SEQ, KIND, CONTENT) SELECT %s, %s, %s, %s, PARSE_JSON(%s)",
               (str(uuid.uuid4()), session_id, seq, kind, json.dumps(content, default=str)[:900000]))


def _save_plan(db: Db, session_id: str, plan: List[Dict[str, Any]]) -> None:
    for s in plan:
        db.execute(f"UPDATE {STEPS} SET STATUS = %s, NOTE = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE SESSION_ID = %s AND STEP_NO = %s AND CREATED_BY = CURRENT_USER()",
                   (s["status"], (s.get("note") or "")[:2000], session_id, s["no"]))


def _save_change(db: Db, session_id: str, change: Dict[str, Any]) -> None:
    ops, prev = json.dumps(change["operations"], default=str), json.dumps(change.get("preview") or {}, default=str)
    if change.get("change_id"):
        db.execute(f"""UPDATE {CHANGES} SET TITLE = %s, KIND = %s, OPERATIONS = PARSE_JSON(%s), PREVIEW = PARSE_JSON(%s),
                              STATUS = %s, UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE CHANGE_ID = %s AND SESSION_ID = %s AND CREATED_BY = CURRENT_USER()""",
                   (change["title"], change["kind"], ops, prev, change.get("status") or "PROPOSED",
                    change["change_id"], session_id))
        return
    change["change_id"] = str(uuid.uuid4())
    db.execute(f"""INSERT INTO {CHANGES} (CHANGE_ID, SESSION_ID, SEQ, CHANGE_KEY, KIND, TITLE, OPERATIONS, PREVIEW, STATUS)
                   SELECT %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), %s""",
               (change["change_id"], session_id, int(change.get("seq") or 1), change["change_key"], change["kind"],
                change["title"], ops, prev, change.get("status") or "PROPOSED"))


def _touch(db: Db, session_id: str, jira_key: Optional[str]) -> None:
    db.execute(f"UPDATE {SESSIONS} SET UPDATED_AT = CURRENT_TIMESTAMP(), JIRA_KEY = COALESCE(JIRA_KEY, %s) "
               "WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()", (jira_key, session_id))


def _attachments(db: Db, session_id: str) -> List[Dict[str, Any]]:
    rows = db.query(f"""SELECT ATTACHMENT_ID, FILE_NAME, MIME_TYPE, SIZE_BYTES, LENGTH(TEXT_CONTENT) AS CHARS, NOTE,
                               CREATED_AT::VARCHAR AS CREATED_AT
                          FROM {ATTACHMENTS} WHERE SESSION_ID = %s AND NOT IS_DELETED AND CREATED_BY = CURRENT_USER()
                         ORDER BY CREATED_AT""", (session_id,))
    for r in rows:
        r["chars"], r["size_bytes"] = int(r.get("chars") or 0), int(r.get("size_bytes") or 0)
    return rows


def _attachment_text(db: Db, session_id: str, attachment_id: str) -> str:
    found = db.query(f"SELECT TEXT_CONTENT FROM {ATTACHMENTS} WHERE ATTACHMENT_ID = %s AND SESSION_ID = %s "
                     "AND NOT IS_DELETED AND CREATED_BY = CURRENT_USER()", (attachment_id, session_id))
    return (found[0].get("text_content") or "") if found else ""


def _public_change(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("change_id", "seq", "change_key", "kind", "title", "status", "request_id", "result",
                                  "operations", "updated_at")}


# ---------------------------------------------------------------- routes

class SessionIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    jira_key: Optional[str] = Field(default=None, max_length=64)


@router.post("/api/copilot/sessions")
def create_session(body: SessionIn, db: Db = Depends(current_db)):
    session_id = str(uuid.uuid4())
    try:
        db.execute(f"INSERT INTO {SESSIONS} (SESSION_ID, TITLE, JIRA_KEY) SELECT %s, %s, %s",
                   (session_id, (body.title or "New conversation")[:256], (body.jira_key or "").upper() or None))
        for s in rail.initial():
            db.execute(f"INSERT INTO {STEPS} (SESSION_ID, STEP_NO, STEP_KEY, STATUS) SELECT %s, %s, %s, 'PENDING'",
                       (session_id, s["no"], s["key"]))
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
    return {"session_id": session_id}


@router.get("/api/copilot/sessions")
def list_sessions(db: Db = Depends(current_db)):
    try:
        rows = db.query(f"""SELECT S.SESSION_ID, S.TITLE, S.STATUS, S.JIRA_KEY, S.UPDATED_AT::VARCHAR AS UPDATED_AT,
                                   COUNT_IF(C.STATUS = 'PROPOSED') AS PROPOSED, COUNT(C.CHANGE_ID) AS CHANGES
                              FROM {SESSIONS} S LEFT JOIN {CHANGES} C ON C.SESSION_ID = S.SESSION_ID
                             WHERE S.CREATED_BY = CURRENT_USER() AND S.STATUS <> 'ARCHIVED'
                             GROUP BY 1, 2, 3, 4, 5 ORDER BY 5 DESC LIMIT 100""")
    except Exception as exc:
        if _missing(exc):
            return {"sessions": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    return {"sessions": rows, "deployed": True}


@router.get("/api/copilot/sessions/{session_id}")
def get_session(session_id: str, db: Db = Depends(current_db)):
    try:
        session = _session(db, session_id)
        changes = _changes(db, session_id)
        return {"session": session, "plan": _plan(db, session_id), "events": _events(db, session_id),
                "changes": [_public_change(c) for c in changes], "workbench": changeset.tabs(changes),
                "attachments": _attachments(db, session_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed_or_503(exc) from exc


@router.post("/api/copilot/sessions/{session_id}/changes/{change_id}/discard")
def discard_change(session_id: str, change_id: str, db: Db = Depends(current_db)):
    _session(db, session_id)
    changed = db.execute_count(f"UPDATE {CHANGES} SET STATUS = 'DISCARDED', UPDATED_AT = CURRENT_TIMESTAMP() "
                               "WHERE CHANGE_ID = %s AND SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()",
                               (change_id, session_id))
    if not changed:
        raise HTTPException(409, "Only a drafted change that is not published yet can be discarded.")
    return {"change_id": change_id, "status": "DISCARDED"}


@router.post("/api/copilot/sessions/{session_id}/attachments")
async def upload_attachments(session_id: str, files_in: List[UploadFile] = File(..., alias="files"),
                             db: Db = Depends(current_db)):
    """Attach files to the conversation. Their text is read now and kept; the files themselves are not stored."""
    await run_in_threadpool(_session, db, session_id)
    existing = await run_in_threadpool(_attachments, db, session_id)
    if len(existing) + len(files_in) > MAX_FILES:
        raise HTTPException(400, f"A conversation holds at most {MAX_FILES} files.")
    out = []
    for f in files_in:
        data = await f.read(files.MAX_BYTES + 1)
        text, note = files.extract(f.filename or "file", data)
        attachment_id = str(uuid.uuid4())
        await run_in_threadpool(db.execute, f"""INSERT INTO {ATTACHMENTS} (ATTACHMENT_ID, SESSION_ID, FILE_NAME, MIME_TYPE,
                                                    SIZE_BYTES, TEXT_CONTENT, NOTE) SELECT %s, %s, %s, %s, %s, %s, %s""",
                                (attachment_id, session_id, (f.filename or "file")[:512], (f.content_type or "")[:256],
                                 len(data), text, note or None))
        out.append({"attachment_id": attachment_id, "file_name": f.filename, "size_bytes": len(data),
                    "chars": len(text), "note": note})
    return {"attachments": out}


@router.post("/api/copilot/sessions/{session_id}/attachments/{attachment_id}/remove")
def remove_attachment(session_id: str, attachment_id: str, db: Db = Depends(current_db)):
    _session(db, session_id)
    if not db.execute_count(f"UPDATE {ATTACHMENTS} SET IS_DELETED = TRUE WHERE ATTACHMENT_ID = %s AND SESSION_ID = %s "
                            "AND CREATED_BY = CURRENT_USER()", (attachment_id, session_id)):
        raise HTTPException(404, "attachment not found")
    return {"attachment_id": attachment_id, "removed": True}


class TurnIn(BaseModel):
    message: Optional[str] = Field(default=None, max_length=8000)
    form_answers: Optional[Dict[str, Any]] = None
    change_id: Optional[str] = Field(default=None, max_length=36)   # "Ask revision" scoped to one drafted change
    jira_keys: List[str] = Field(default_factory=list, max_length=10)
    attachment_ids: List[str] = Field(default_factory=list, max_length=MAX_FILES)


def _sse(event: Dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@router.post("/api/copilot/sessions/{session_id}/turn")
async def turn(session_id: str, body: TurnIn, request: Request, db: Db = Depends(current_db)):
    """One turn as a server-sent event stream: thought, plan, tool, change, ask, reply, error, done."""
    if not (body.message or "").strip() and not body.form_answers:
        raise HTTPException(400, "Send a message or the answers to the question.")

    def load() -> Dict[str, Any]:
        session = _session(db, session_id)
        return {"session": session, "title": session.get("title"), "jira_key": session.get("jira_key"),
                "plan": _plan(db, session_id), "changes": _changes(db, session_id),
                "events": _events(db, session_id, HISTORY), "attachments": _attachments(db, session_id)}

    try:
        state = await run_in_threadpool(load)
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed_or_503(exc) from exc

    seq = {"next": (state["events"][-1]["seq"] + 1) if state["events"] else 1}
    user_kind, user_content = ("ANSWER", {"answers": body.form_answers, "text": body.message or ""}) if body.form_answers \
        else ("USER", {"text": body.message})
    if body.change_id:
        target = next((c for c in state["changes"] if c["change_id"] == body.change_id), None)
        if not target:
            raise HTTPException(404, "drafted change not found")
        user_content["revise"] = {"change_id": target["change_id"], "change_key": target["change_key"], "title": target["title"]}
    keys = [k.strip().upper() for k in body.jira_keys if k.strip()]
    if keys:
        user_content["jira_keys"] = keys
        state["jira_key"] = state.get("jira_key") or keys[0]
    named = {a["attachment_id"]: a["file_name"] for a in state["attachments"]}
    if body.attachment_ids:
        user_content["attachments"] = [{"attachment_id": a, "file_name": named.get(a)} for a in body.attachment_ids if a in named]

    async def read_attachment(attachment_id: str) -> str:
        return await run_in_threadpool(_attachment_text, db, session_id, attachment_id)

    state["ctx"] = {"attachments": [{k: a.get(k) for k in ("attachment_id", "file_name", "size_bytes", "chars", "note")}
                                    for a in state["attachments"]], "read_attachment": read_attachment}
    await run_in_threadpool(_add_event, db, session_id, seq["next"], user_kind, user_content)
    state["events"].append({"kind": user_kind, "content": user_content})
    seq["next"] += 1

    from services.common.llm import complete_json

    async def complete(prompt_text: str, schema: Dict[str, Any]):
        started = time.time()
        out = await run_in_threadpool(invoke_source, db, complete_json, prompt_text, schema, 4096, None, "COPILOT")
        await run_in_threadpool(_record_cost, db, None, "COPILOT", out[2], dict(out[1] or {}), started)
        return out

    call = caller(request.headers)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in loop.run_turn(complete, call, state):
                kind = event["type"]
                if kind == "usage":
                    continue
                if kind == "plan":
                    await run_in_threadpool(_save_plan, db, session_id, event["plan"])
                if kind == "change":
                    await run_in_threadpool(_save_change, db, session_id, event["change"])
                    event = {"type": "change", "change": _public_change(event["change"]),
                             "workbench": changeset.tabs(state["changes"])}
                if kind in STORED_KINDS:
                    stored = {k: v for k, v in event.items() if k not in ("type", "workbench")}
                    if kind == "change":
                        stored = {"change_id": event["change"]["change_id"], "title": event["change"]["title"],
                                  "kind": event["change"]["kind"]}
                    await run_in_threadpool(_add_event, db, session_id, seq["next"], STORED_KINDS[kind], stored)
                    seq["next"] += 1
                yield _sse(event)
            await run_in_threadpool(_touch, db, session_id, state.get("jira_key"))
        except Exception as exc:  # never leave the browser waiting on a broken stream
            yield _sse({"type": "error", "text": f"The turn stopped: {str(exc)[:500]}"})
            yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class PublishIn(BaseModel):
    change_ids: List[str] = Field(default_factory=list, max_length=100)   # empty: every drafted change


def _save_result(db: Db, session_id: str, change: Dict[str, Any]) -> None:
    db.execute(f"""UPDATE {CHANGES} SET STATUS = %s, REQUEST_ID = %s, RESULT = PARSE_JSON(%s), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE CHANGE_ID = %s AND SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()""",
               (change["status"], change.get("request_id"), json.dumps(change.get("result") or {}, default=str)[:900000],
                change["change_id"], session_id))


@router.post("/api/copilot/sessions/{session_id}/publish")
async def publish(session_id: str, body: PublishIn, request: Request, db: Db = Depends(current_db)):
    """Approve and Publish, as a server-sent event stream: each drafted change replayed through the API as the person
    (start, result, stopped, done). The API decides what applies, what goes to an approver and what is refused."""
    def load():
        _session(db, session_id)
        return _changes(db, session_id), _plan(db, session_id), _events(db, session_id, 1)

    try:
        changes, plan, last = await run_in_threadpool(load)
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
    if not [c for c in changes if c["status"] == "PROPOSED" and (not body.change_ids or c["change_id"] in body.change_ids)]:
        raise HTTPException(409, "There is nothing drafted to publish.")
    seq = {"next": (last[-1]["seq"] + 1) if last else 1}
    call = caller(request.headers)

    async def stream() -> AsyncIterator[str]:
        outcome: List[Dict[str, Any]] = []
        try:
            rail.merge(plan, "approval", "DONE", "approved by you", force=True)
            rail.merge(plan, "publish", "RUNNING", "", force=True)
            await run_in_threadpool(_save_plan, db, session_id, plan)
            yield _sse({"type": "plan", "plan": plan})
            async for event in publisher.run(call, changes, body.change_ids or None):
                if event["type"] == "result":
                    change = next(c for c in changes if c["change_id"] == event["change_id"])
                    await run_in_threadpool(_save_result, db, session_id, change)
                    outcome.append({k: event.get(k) for k in ("change_id", "title", "kind", "status", "request_id", "detail")})
                if event["type"] == "done":
                    failed = any(o["status"] == "FAILED" for o in outcome)
                    waiting = sum(1 for o in outcome if o["status"] == "PENDING_APPROVAL")
                    note = "stopped on a failure" if failed else f"{waiting} waiting for approval" if waiting else "published"
                    rail.merge(plan, "publish", "BLOCKED" if failed else "DONE", note, force=True)
                    await run_in_threadpool(_save_plan, db, session_id, plan)
                    await run_in_threadpool(_add_event, db, session_id, seq["next"], "PUBLISH", {"results": outcome})
                    await run_in_threadpool(_touch, db, session_id, None)
                    yield _sse({"type": "plan", "plan": plan})
                    yield _sse({"type": "published", "results": outcome})
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error", "text": f"Publishing stopped: {str(exc)[:500]}"})
            yield _sse({"type": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
