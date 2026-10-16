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
import logging
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
from services.agentic.tools import JIRA_KEY

router = APIRouter()
log = logging.getLogger(__name__)
_audit_warned = False

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
    if isinstance(exc, HTTPException):
        return exc
    text = str(exc).lower()
    if "insufficient privileges" in text:
        return HTTPException(403, "Your Snowflake work role cannot use the copilot's tables. Switch to a role such as "
                                  "DATA_ENGINEER in the account menu, or ask an admin to grant the copilot to your role.")
    if _missing(exc):
        return HTTPException(503, "The copilot workspace is not deployed yet (migration V048). An admin can apply "
                                  "the latest migrations in Admin, Deploy.")
    return _snowflake_error(exc)


MAX_JSON = 400_000   # characters of one stored JSON value


def _dump(value: Any, limit: int = MAX_JSON) -> str:
    """JSON for PARSE_JSON that is always complete: an oversized value is stored as a clipped text, never cut mid-document."""
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return json.dumps({"truncated": True, "text": text[: limit - 200]})


# ---------------------------------------------------------------- storage

def _session(db: Db, session_id: str) -> Dict[str, Any]:
    found = db.query(f"""SELECT SESSION_ID, TITLE, STATUS, JIRA_KEY, DOMAIN_ID, MODEL, CREATED_BY,
                                TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS CREATED_AT, TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS UPDATED_AT
                           FROM {SESSIONS} WHERE SESSION_ID = %s AND STATUS <> 'DELETED' AND CREATED_BY = CURRENT_USER()""",
                       (session_id,))
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
                               TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS UPDATED_AT
                          FROM {CHANGES} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER() ORDER BY SEQ""",
                    (session_id,))
    for r in rows:
        for k in ("operations", "preview", "result"):
            r[k] = _json(r.get(k))
        r["seq"] = int(r["seq"])
    return rows


def _events(db: Db, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = f"""SELECT EVENT_ID, SEQ, KIND, CONTENT, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS CREATED_AT
                FROM {EVENTS} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER() ORDER BY SEQ DESC"""
    rows = db.query(sql + (f" LIMIT {int(limit)}" if limit else ""), (session_id,))
    for r in rows:
        r["content"] = _json(r.get("content"))
        r["seq"] = int(r["seq"])
    return list(reversed(rows))


def _add_event(db: Db, session_id: str, seq: int, kind: str, content: Any) -> None:
    db.execute(f"INSERT INTO {EVENTS} (EVENT_ID, SESSION_ID, SEQ, KIND, CONTENT) SELECT %s, %s, %s, %s, PARSE_JSON(%s)",
               (str(uuid.uuid4()), session_id, seq, kind, _dump(content)))


def _save_plan(db: Db, session_id: str, plan: List[Dict[str, Any]]) -> None:
    for s in plan:
        db.execute(f"UPDATE {STEPS} SET STATUS = %s, NOTE = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE SESSION_ID = %s AND STEP_NO = %s AND CREATED_BY = CURRENT_USER()",
                   (s["status"], (s.get("note") or "")[:2000], session_id, s["no"]))


def _save_change(db: Db, session_id: str, change: Dict[str, Any]) -> None:
    """Store a drafted change. A revision updates the open draft in place; when that draft was published or discarded
    meanwhile (another tab, or during this turn), the revision becomes a new draft instead of reviving it."""
    if change.get("status") == "DISCARDED":   # replaced by a newer draft in this turn
        if change.get("change_id"):
            db.execute(f"UPDATE {CHANGES} SET STATUS = 'DISCARDED', UPDATED_AT = CURRENT_TIMESTAMP() WHERE CHANGE_ID = %s "
                       "AND SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()",
                       (change["change_id"], session_id))
        return
    ops, prev = _dump(change["operations"], 2_000_000), _dump(change.get("preview") or {})
    if change.get("change_id"):
        updated = db.execute_count(
            f"""UPDATE {CHANGES} SET TITLE = %s, KIND = %s, OPERATIONS = PARSE_JSON(%s), PREVIEW = PARSE_JSON(%s),
                       UPDATED_AT = CURRENT_TIMESTAMP()
                 WHERE CHANGE_ID = %s AND SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()""",
            (change["title"], change["kind"], ops, prev, change["change_id"], session_id))
        if updated:
            return
        found = db.query(f"SELECT COALESCE(MAX(SEQ), 0) + 1 AS N FROM {CHANGES} WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()",
                         (session_id,))
        change["seq"] = int(found[0]["n"]) if found else int(change.get("seq") or 1)
        change["status"] = "PROPOSED"
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
                               TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS CREATED_AT
                          FROM {ATTACHMENTS} WHERE SESSION_ID = %s AND NOT IS_DELETED AND CREATED_BY = CURRENT_USER()
                         ORDER BY CREATED_AT""", (session_id,))
    for r in rows:
        r["chars"], r["size_bytes"] = int(r.get("chars") or 0), int(r.get("size_bytes") or 0)
    return rows


def _attachment_text(db: Db, session_id: str, attachment_id: str) -> str:
    found = db.query(f"SELECT TEXT_CONTENT FROM {ATTACHMENTS} WHERE ATTACHMENT_ID = %s AND SESSION_ID = %s "
                     "AND NOT IS_DELETED AND CREATED_BY = CURRENT_USER()", (attachment_id, session_id))
    return (found[0].get("text_content") or "") if found else ""


def _audit_tool(db: Db, session_id: str, event: Dict[str, Any]) -> None:
    """CORE.AGENT_TOOL_CALL row for one copilot tool call, like the platform's other agents record theirs (redacted
    arguments, outcome, duration, the person). Never fails the turn: a role that cannot write the audit table still
    has the call in the conversation's own events."""
    summary = event.get("summary")
    text = json.dumps(summary, default=str)[:3900] if summary is not None else ""
    try:
        db.execute("""INSERT INTO CORE.AGENT_TOOL_CALL (TOOL_CALL_ID, AGENT_RUN_ID, TOOL_NAME, INPUT_JSON, OUTPUT_SUMMARY,
                          STATUS, DURATION_MS, ERROR, CALLED_BY)
                      SELECT %s, %s, %s, PARSE_JSON(%s), NULLIF(%s, ''), %s, %s, NULLIF(%s, ''), CURRENT_USER()""",
                   (str(uuid.uuid4()), session_id, f"copilot.{str(event.get('tool') or '')[:100]}",
                    _dump({"session_id": session_id, "args": event.get("args") or {}}, 50_000), text,
                    "SUCCEEDED" if event.get("ok") else "FAILED", int(event.get("ms") or 0),
                    str(event.get("detail") or event.get("error") or "")[:3900]))
    except Exception as exc:
        global _audit_warned
        if not _audit_warned:   # once per process: the role cannot write the audit table (or it is missing)
            _audit_warned = True
            log.warning("copilot tool calls are not written to CORE.AGENT_TOOL_CALL: %s", str(exc)[:300])


def _code_context(db: Db, domain_id: Optional[str], question: str) -> str:
    """The connected repositories' code most relevant to the request (services/code/context.py, stage COPILOT):
    exact name and lineage matches, Cortex Search and the code graph. Empty when no repository is indexed."""
    if not question.strip():
        return ""
    try:
        from app.code_api import _rows, _search
        from services.code import context as code_ctx

        out = code_ctx.code_context(_rows(db), _search(db), stage="COPILOT", domain_id=domain_id, question=question[:2000])
        code_ctx.record_usage(lambda sql, params: db.execute(sql.replace("?", "%s"), tuple(params)), None, "COPILOT",
                              out.get("citations") or [])
        return out.get("text") or ""
    except Exception:
        return ""


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
        rows = db.query(f"""SELECT S.SESSION_ID, ANY_VALUE(S.TITLE) AS TITLE, ANY_VALUE(S.STATUS) AS STATUS,
                                   ANY_VALUE(S.JIRA_KEY) AS JIRA_KEY,
                                   TO_VARCHAR(MAX(S.UPDATED_AT), 'YYYY-MM-DD"T"HH24:MI:SS.FF3TZH:TZM') AS UPDATED_AT,
                                   COUNT_IF(C.STATUS = 'PROPOSED') AS PROPOSED, COUNT(C.CHANGE_ID) AS CHANGES
                              FROM {SESSIONS} S LEFT JOIN {CHANGES} C ON C.SESSION_ID = S.SESSION_ID
                             WHERE S.CREATED_BY = CURRENT_USER() AND S.STATUS NOT IN ('ARCHIVED', 'DELETED')
                             GROUP BY S.SESSION_ID ORDER BY MAX(S.UPDATED_AT) DESC LIMIT 100""")
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


@router.post("/api/copilot/sessions/{session_id}/delete")
def delete_session(session_id: str, db: Db = Depends(current_db)):
    """Remove a conversation from the workspace. Its unpublished drafts are discarded (they were never applied);
    anything already published stays published. The rows are kept, marked deleted, for audit."""
    try:
        _session(db, session_id)
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
    if db.query(f"SELECT 1 AS X FROM {CHANGES} WHERE SESSION_ID = %s AND STATUS = 'PUBLISHING' AND CREATED_BY = CURRENT_USER() LIMIT 1",
                (session_id,)):
        raise HTTPException(409, "This conversation is publishing right now. Delete it when publishing has finished.")
    db.execute(f"UPDATE {CHANGES} SET STATUS = 'DISCARDED', UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()", (session_id,))
    db.execute(f"UPDATE {ATTACHMENTS} SET IS_DELETED = TRUE WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()", (session_id,))
    db.execute(f"UPDATE {SESSIONS} SET STATUS = 'DELETED', UPDATED_AT = CURRENT_TIMESTAMP() "
               "WHERE SESSION_ID = %s AND CREATED_BY = CURRENT_USER()", (session_id,))
    return {"session_id": session_id, "deleted": True}


@router.post("/api/copilot/sessions/{session_id}/changes/{change_id}/discard")
def discard_change(session_id: str, change_id: str, db: Db = Depends(current_db)):
    try:
        _session(db, session_id)
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
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
    try:
        await run_in_threadpool(_session, db, session_id)
        existing = await run_in_threadpool(_attachments, db, session_id)
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
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
    try:
        _session(db, session_id)
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
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
    bad = [k for k in keys if not JIRA_KEY.match(k)]
    if bad:
        raise HTTPException(400, f"Not Jira issue keys: {', '.join(bad[:5])}")
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
    asked = body.message or next((str((e.get("content") or {}).get("text") or "") for e in reversed(state["events"])
                                  if e.get("kind") == "USER" and isinstance(e.get("content"), dict)), "")
    state["code_context"] = await run_in_threadpool(_code_context, db, state["session"].get("domain_id"), asked)
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
                if kind == "tool":
                    await run_in_threadpool(_audit_tool, db, session_id, event)
                    event = {k: v for k, v in event.items() if k != "detail"}   # internal error text stays in the audit row
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


def _check_change(db: Db, change: Dict[str, Any]) -> Dict[str, Any]:
    """What publishing this change would meet, call by call, for the signed-in person: refused (with why), sent to an
    approver, or applied. References to earlier results ({op0.version}) are stood in for, since only the route matters."""
    from app.governance import preview_call

    out: Dict[str, Any] = {"status": "ok", "reason": "", "approver_role": None, "privileges": []}
    for op in change.get("operations") or []:
        path = publisher.REF.sub("0", str(op.get("path") or ""))
        seen = preview_call(db, str(op.get("method") or "POST"), path, op.get("body"))
        if seen.get("privilege") and seen["privilege"] not in out["privileges"]:
            out["privileges"].append(seen["privilege"])
        if seen["verdict"] == "FORBID":
            return {**out, "status": "forbidden", "reason": seen.get("reason") or "not allowed"}
        if seen["verdict"] == "REQUEST" and out["status"] == "ok":
            out.update(status="approval", approver_role=seen.get("approver_role"), reason=seen.get("reason") or "")
    return out


@router.get("/api/copilot/sessions/{session_id}/publish-check")
def publish_check(session_id: str, db: Db = Depends(current_db)):
    """For the review drawer: each open draft's outcome if published now (ok, approval or forbidden, with the reason),
    from the same governance rules the publish will meet. Nothing is called."""
    try:
        _session(db, session_id)
        changes = [c for c in _changes(db, session_id) if c.get("status") == "PROPOSED"]
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
    out = {}
    for c in changes:
        try:
            out[c["change_id"]] = _check_change(db, c)
        except Exception:   # an unknown answer never blocks publishing: the API still decides each call
            out[c["change_id"]] = {"status": "unknown", "reason": "", "approver_role": None, "privileges": []}
    return {"checks": out}


class PublishIn(BaseModel):
    change_ids: List[str] = Field(min_length=1, max_length=100)   # the drafts the person chose in the review drawer


def _save_result(db: Db, session_id: str, change: Dict[str, Any]) -> None:
    db.execute(f"""UPDATE {CHANGES} SET STATUS = %s, REQUEST_ID = %s, RESULT = PARSE_JSON(%s), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE CHANGE_ID = %s AND SESSION_ID = %s AND STATUS = 'PUBLISHING' AND CREATED_BY = CURRENT_USER()""",
               (change["status"], change.get("request_id"), _dump(change.get("result") or {}),
                change["change_id"], session_id))


def _claim(db: Db, session_id: str, change_ids: List[str]) -> List[str]:
    """Mark the chosen drafts PUBLISHING; only the ones this call moved are published (two tabs never replay twice)."""
    claimed = []
    for change_id in change_ids:
        if db.execute_count(f"UPDATE {CHANGES} SET STATUS = 'PUBLISHING', UPDATED_AT = CURRENT_TIMESTAMP() "
                            "WHERE CHANGE_ID = %s AND SESSION_ID = %s AND STATUS = 'PROPOSED' AND CREATED_BY = CURRENT_USER()",
                            (change_id, session_id)):
            claimed.append(change_id)
    return claimed


def _unclaim(db: Db, session_id: str, detail: str) -> None:
    """After an interruption: whatever is still PUBLISHING may be partly applied, so it is marked failed with the reason."""
    db.execute(f"""UPDATE {CHANGES} SET STATUS = 'FAILED', RESULT = PARSE_JSON(%s), UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE SESSION_ID = %s AND STATUS = 'PUBLISHING' AND CREATED_BY = CURRENT_USER()""",
               (json.dumps({"detail": detail}), session_id))


@router.post("/api/copilot/sessions/{session_id}/publish")
async def publish(session_id: str, body: PublishIn, request: Request, db: Db = Depends(current_db)):
    """Approve and Publish, as a server-sent event stream: each drafted change replayed through the API as the person
    (start, result, stopped, done). The API decides what applies, what goes to an approver and what is refused."""
    def load():
        _session(db, session_id)
        claimed = _claim(db, session_id, list(dict.fromkeys(body.change_ids)))
        found = _changes(db, session_id)
        for c in found:   # publish.run works on open drafts; the claimed ones are open for this call
            if c["change_id"] in claimed:
                c["status"] = "PROPOSED"
        return found, claimed, _plan(db, session_id), _events(db, session_id, 1)

    try:
        changes, claimed, plan, last = await run_in_threadpool(load)
    except HTTPException:
        raise
    except Exception as exc:
        raise _deployed_or_503(exc) from exc
    if not claimed:
        raise HTTPException(409, "There is nothing drafted to publish: these changes were published, discarded or are "
                                 "being published from another window.")
    changes = [c for c in changes if c["change_id"] in claimed]
    seq = {"next": (last[-1]["seq"] + 1) if last else 1}
    call = caller(request.headers)

    async def stream() -> AsyncIterator[str]:
        outcome: List[Dict[str, Any]] = []
        try:
            rail.merge(plan, "approval", "DONE", "approved by you", force=True)
            rail.merge(plan, "publish", "RUNNING", "", force=True)
            await run_in_threadpool(_save_plan, db, session_id, plan)
            yield _sse({"type": "plan", "plan": plan})
            async for event in publisher.run(call, changes, claimed):
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
        finally:
            # anything not recorded (an error, or the browser went away mid-publish) is marked, never left PUBLISHING
            await run_in_threadpool(_unclaim, db, session_id,
                                    "Publishing was interrupted; this change may be partly applied. Check the store before trying again.")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
