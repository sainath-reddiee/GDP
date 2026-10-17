"""The learning loop (slide 4): a solved ticket becomes a known fix.

1. From the ticket: a draft KNOWN_ISSUE is written from the solved incident (its failure's signature, DAG, task, class
   and match text are filled in), with a PROCEDURE fix or a SKILL when the repair needs several steps.
2. Review: another person (four eyes) checks the signature, the fix and whether approval is required next time.
3. Publish: SP_PUBLISH_KNOWN_ISSUE (this module) sets PUBLISHED.
4. Search refresh: the search service is refreshed right away, so the next claim can run the fix.
A miss never writes KNOWN_ISSUE: only a reviewer publishes.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.ops.redact import redact
from services.resolve import rules
from services.resolve.engine import ISSUE_COLUMNS, SEARCH_SERVICE, database_of, get_issue, get_skill

EDITABLE = ("title", "error_signature", "dag_id", "task_id", "exception_class", "match_text", "fix_type", "procedure_fqn",
            "skill_id", "approval_required", "owner", "notes")


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in EDITABLE:
        if key not in data:
            continue
        value = data[key]
        if key == "approval_required":
            out[key] = None if value is None else bool(value)
        elif key == "fix_type":
            kind = str(value or "").upper()
            if kind not in rules.FIX_TYPES:
                raise ValueError("fix type must be PROCEDURE or SKILL")
            out[key] = kind
        else:
            text = str(value).strip() if value is not None else ""
            out[key] = redact(text)[:8000 if key == "match_text" else 512] or None
    return out


def draft_from_incident(db: Any, incident_id: str) -> Dict[str, Any]:
    """The fields a fix written from a solved incident starts with (nothing is saved)."""
    found = db.query("""SELECT F.FAILURE_ID, F.DAG_ID, F.TASK_ID, F.EXCEPTION_CLASS, F.EXCEPTION_TEXT, F.ERROR_SIGNATURE
                          FROM CORE.FAILURE_EVENT F WHERE F.INCIDENT_ID = %s ORDER BY F.CREATED_AT DESC LIMIT 1""",
                     (incident_id,))
    inc = db.query("SELECT TITLE, DAG_ID, TASK_ID, ERROR_EXCERPT, RESOLUTION FROM CORE.INCIDENT WHERE INCIDENT_ID = %s",
                   (incident_id,))
    if not inc:
        raise LookupError(f"incident {incident_id} not found")
    i, f = inc[0], (found[0] if found else {})
    if f:
        dag, task, cls, sig = f["dag_id"], f.get("task_id"), f.get("exception_class"), f.get("error_signature")
        text = f.get("exception_text")
    else:
        parsed = rules.parse_failure(i.get("error_excerpt"))
        dag, task, cls, text = i["dag_id"], i.get("task_id"), parsed["exception_class"], parsed["exception_text"]
        sig = rules.error_signature(dag, task, cls, i.get("error_excerpt"))
    return {"title": str(i.get("title") or "")[:500], "error_signature": sig, "dag_id": dag, "task_id": task,
            "exception_class": cls, "match_text": rules.match_text(dag, task, cls, text), "fix_type": "PROCEDURE",
            "procedure_fqn": None, "skill_id": None, "approval_required": True, "notes": i.get("resolution"),
            "source_incident_id": incident_id, "source_failure_id": f.get("failure_id")}


def refresh_signatures(db: Any) -> int:
    """Bring live fixes in line with how failures are matched today: the error signature recomputed from the incident
    the fix was written from, and Snowflake's role-naming privilege hint dropped from the match text (services.ops.detect
    .signature). Without it a fix published before a matching change would stop matching its own failure. Changes only
    the matching fields, never what the fix runs. Returns the number of fixes updated."""
    from services.ops.detect import _ROLE_HINT

    changed = 0
    rows = db.query("""SELECT K.ISSUE_ID, K.DAG_ID, K.TASK_ID, K.EXCEPTION_CLASS, K.ERROR_SIGNATURE, K.MATCH_TEXT,
                              I.ERROR_EXCERPT
                         FROM CORE.KNOWN_ISSUE K LEFT JOIN CORE.INCIDENT I ON I.INCIDENT_ID = K.SOURCE_INCIDENT_ID
                        WHERE K.STATUS IN ('PUBLISHED', 'DRAFT')""")
    for r in rows:
        sig = r.get("error_signature")
        if sig and r.get("error_excerpt") and r.get("dag_id"):
            sig = rules.error_signature(r["dag_id"], r.get("task_id"), r.get("exception_class"), r["error_excerpt"])
        text = " ".join(_ROLE_HINT.sub("", str(r.get("match_text") or "")).split())
        if sig == r.get("error_signature") and text == " ".join(str(r.get("match_text") or "").split()):
            continue
        db.execute("UPDATE CORE.KNOWN_ISSUE SET ERROR_SIGNATURE = %s, MATCH_TEXT = %s, UPDATED_AT = CURRENT_TIMESTAMP() "
                   "WHERE ISSUE_ID = %s", (sig, text or r.get("match_text"), r["issue_id"]))
        changed += 1
    return changed


def create(db: Any, data: Dict[str, Any], actor: str) -> Dict[str, Any]:
    fields = _clean(data)
    if not fields.get("title") or not fields.get("match_text"):
        raise ValueError("a fix needs a title and match text")
    issue_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CORE.KNOWN_ISSUE (ISSUE_ID, TITLE, ERROR_SIGNATURE, DAG_ID, TASK_ID, EXCEPTION_CLASS,
                         MATCH_TEXT, FIX_TYPE, PROCEDURE_FQN, SKILL_ID, APPROVAL_REQUIRED, STATUS, VERSION_NO, OWNER,
                         SOURCE_INCIDENT_ID, SOURCE_FAILURE_ID, NOTES, CREATED_BY)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT', 1, %s, %s, %s, %s, %s)""",
               (issue_id, fields["title"], fields.get("error_signature"), fields.get("dag_id"), fields.get("task_id"),
                fields.get("exception_class"), fields["match_text"], fields.get("fix_type") or "PROCEDURE",
                fields.get("procedure_fqn"), fields.get("skill_id"),
                True if fields.get("approval_required") is None else fields["approval_required"],
                fields.get("owner") or actor, data.get("source_incident_id"), data.get("source_failure_id"),
                fields.get("notes"), actor[:256]))
    return get_issue(db, issue_id) or {}


def update(db: Any, issue_id: str, data: Dict[str, Any], actor: str) -> Dict[str, Any]:
    issue = get_issue(db, issue_id)
    if not issue:
        raise LookupError(f"fix {issue_id} not found")
    if str(issue.get("status")) != "DRAFT":
        raise ValueError("only a draft can be edited: retire a published fix and write a new one")
    if awaiting_approval(db, issue_id):
        raise ValueError("this fix waits for an approval: it cannot change while the approver reviews it")
    fields = _clean(data)
    if not fields:
        return issue
    sets = ", ".join(f"{k.upper()} = %s" for k in fields) + ", UPDATED_AT = CURRENT_TIMESTAMP()"
    db.execute(f"UPDATE CORE.KNOWN_ISSUE SET {sets} WHERE ISSUE_ID = %s AND STATUS = 'DRAFT'",
               tuple(fields.values()) + (issue_id,))
    return get_issue(db, issue_id) or {}


def awaiting_approval(db: Any, issue_id: str) -> bool:
    """True while an approval of this fix is OPEN: what the approver reviews must be what runs."""
    return bool(db.query("SELECT 1 AS X FROM CORE.APPROVAL_REQUEST WHERE ISSUE_ID = %s AND STATUS = 'OPEN' LIMIT 1",
                         (issue_id,)))


def skill_locked(db: Any, skill_id: str) -> Optional[str]:
    """Why the skill's steps may not change now (a published fix runs it, or a fix that runs it waits for approval),
    else None."""
    if db.query("SELECT 1 AS X FROM CORE.KNOWN_ISSUE WHERE SKILL_ID = %s AND STATUS = 'PUBLISHED' LIMIT 1", (skill_id,)):
        return "a published fix runs this skill: retire it before changing the steps"
    if db.query("""SELECT 1 AS X FROM CORE.KNOWN_ISSUE K JOIN CORE.APPROVAL_REQUEST A ON A.ISSUE_ID = K.ISSUE_ID
                    WHERE K.SKILL_ID = %s AND A.STATUS = 'OPEN' LIMIT 1""", (skill_id,)):
        return "a fix that runs this skill waits for an approval: it cannot change while the approver reviews it"
    return None


def refresh_search(db: Any, database: Optional[str] = None) -> Optional[str]:
    """Refresh the search service now; None when it worked, else why not (it refreshes itself within its lag)."""
    try:
        db.execute(f"ALTER CORTEX SEARCH SERVICE {database or database_of(db)}.CORE.{SEARCH_SERVICE} REFRESH")
        return None
    except Exception as exc:
        return redact(str(exc))[:300]


def publish(db: Any, issue_id: str, actor: str) -> Dict[str, Any]:
    """A reviewer publishes a draft (never its author), then the search service is refreshed."""
    issue = get_issue(db, issue_id)
    if not issue:
        raise LookupError(f"fix {issue_id} not found")
    database = database_of(db)
    rules.publish_check(issue, actor, get_skill(db, issue.get("skill_id")), database)
    if str(issue.get("fix_type")).upper() == "PROCEDURE":
        fqn = rules.fix_procedure(issue.get("procedure_fqn"), database)
        db.execute("UPDATE CORE.KNOWN_ISSUE SET PROCEDURE_FQN = %s WHERE ISSUE_ID = %s", (fqn, issue_id))
    changed = db.execute_count("""UPDATE CORE.KNOWN_ISSUE SET STATUS = 'PUBLISHED', PUBLISHED_BY = %s,
                                         PUBLISHED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP()
                                   WHERE ISSUE_ID = %s AND STATUS = 'DRAFT'""", (actor[:256], issue_id))
    if changed != 1:
        raise ValueError("the fix changed while it was being published")
    note = refresh_search(db, database)
    return {**(get_issue(db, issue_id) or {}), "search_refresh": "refreshed" if note is None else f"pending: {note}"}


def retire(db: Any, issue_id: str, actor: str) -> Dict[str, Any]:
    changed = db.execute_count("""UPDATE CORE.KNOWN_ISSUE SET STATUS = 'RETIRED', UPDATED_AT = CURRENT_TIMESTAMP(),
                                         NOTES = LEFT(COALESCE(NOTES, '') || %s, 4000)
                                   WHERE ISSUE_ID = %s AND STATUS IN ('DRAFT', 'PUBLISHED')""",
                               (f"\nRetired by {actor}.", issue_id))
    if changed != 1:
        raise ValueError("only a draft or published fix can be retired")
    refresh_search(db)
    return get_issue(db, issue_id) or {}


def list_issues(db: Any, status: Optional[str] = None, q: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    where, params = ["1 = 1"], []
    if status and status.upper() in rules.ISSUE_STATUSES:
        where.append("K.STATUS = %s")
        params.append(status.upper())
    if q:
        where.append("(K.TITLE ILIKE %s OR K.MATCH_TEXT ILIKE %s OR K.DAG_ID ILIKE %s)")
        params += [f"%{q}%"] * 3
    cols = ", ".join(f"K.{c.upper()}" for c in ISSUE_COLUMNS)
    return db.query(f"""SELECT {cols}, S.SKILL_NAME,
                               (SELECT COUNT(*) FROM CORE.RESOLUTION_RUN R WHERE R.ISSUE_ID = K.ISSUE_ID AND R.OUTCOME = 'SUCCESS') AS RUNS_OK,
                               (SELECT COUNT(*) FROM CORE.RESOLUTION_RUN R WHERE R.ISSUE_ID = K.ISSUE_ID AND R.OUTCOME = 'FAILED') AS RUNS_FAILED,
                               U.RUNS_30D, U.OK_30D, U.DAGS_30D, U.LAST_RUN_AT
                          FROM CORE.KNOWN_ISSUE K LEFT JOIN CORE.FIX_SKILL S ON S.SKILL_ID = K.SKILL_ID
                          LEFT JOIN ({_USAGE_SQL}) U ON U.ISSUE_ID = K.ISSUE_ID
                         WHERE {' AND '.join(where)} ORDER BY K.UPDATED_AT DESC LIMIT {int(limit)}""",
                    (RECENT_DAYS, *params))


RECENT_DAYS = 30
# how often each known fix ran lately: runs, successes, distinct DAGs and the last run (one %s: the days)
_USAGE_SQL = """SELECT R.ISSUE_ID, COUNT(*) AS RUNS_30D, COUNT_IF(R.OUTCOME = 'SUCCESS') AS OK_30D,
                       COUNT(DISTINCT F.DAG_ID) AS DAGS_30D, MAX(R.STARTED_AT) AS LAST_RUN_AT,
                       ARRAY_AGG(DISTINCT F.DAG_ID) WITHIN GROUP (ORDER BY F.DAG_ID) AS DAG_IDS
                  FROM CORE.RESOLUTION_RUN R JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = R.FAILURE_ID
                 WHERE R.ISSUE_ID IS NOT NULL AND R.OUTCOME IN ('SUCCESS', 'FAILED')
                   AND R.STARTED_AT >= DATEADD(day, -%s, CURRENT_TIMESTAMP())
                 GROUP BY R.ISSUE_ID"""


def recurring(db: Any, days: int = RECENT_DAYS, min_runs: int = 2, limit: int = 10) -> List[Dict[str, Any]]:
    """Known fixes that keep firing: [{issue_id, title, status, runs, ok, success_rate, dags, dag_ids, last_run_at}],
    the most frequent first. A fix that runs every week points at a cause worth repairing for good."""
    found = db.query(f"""SELECT K.ISSUE_ID, K.TITLE, K.STATUS, K.PROPOSED_BY_AGENT, U.RUNS_30D, U.OK_30D, U.DAGS_30D,
                                U.LAST_RUN_AT, U.DAG_IDS
                           FROM ({_USAGE_SQL}) U JOIN CORE.KNOWN_ISSUE K ON K.ISSUE_ID = U.ISSUE_ID
                          WHERE U.RUNS_30D >= %s ORDER BY U.RUNS_30D DESC, U.LAST_RUN_AT DESC LIMIT {int(limit)}""",
                     (max(1, min(int(days), 365)), int(min_runs)))
    out = []
    for r in found:
        runs, ok = int(r.get("runs_30d") or 0), int(r.get("ok_30d") or 0)
        dag_ids = r.get("dag_ids")
        if isinstance(dag_ids, str):
            try:
                dag_ids = json.loads(dag_ids)
            except ValueError:
                dag_ids = []
        out.append({"issue_id": r["issue_id"], "title": r.get("title"), "status": r.get("status"),
                    "proposed_by_agent": bool(r.get("proposed_by_agent")), "runs": runs, "ok": ok,
                    "success_rate": round(ok / runs, 3) if runs else None, "dags": int(r.get("dags_30d") or 0),
                    "dag_ids": list(dag_ids or [])[:10], "last_run_at": r.get("last_run_at"), "permanent_fix": None})
    if out:
        try:   # the newest permanent fix proposal of each (V066)
            from services.resolve.permanent import latest_for

            latest = latest_for(db, [o["issue_id"] for o in out])
            for o in out:
                p = latest.get(o["issue_id"])
                o["permanent_fix"] = ({"fix_id": p["fix_id"], "status": p.get("status"), "pr_url": p.get("pr_url")}
                                      if p else None)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- skills (multi-step fixes)

def save_skill(db: Any, data: Dict[str, Any], actor: str, skill_id: Optional[str] = None) -> Dict[str, Any]:
    name = str(data.get("skill_name") or "").strip()[:250]
    if not name:
        raise ValueError("a skill needs a name")
    steps = rules.skill_steps(data.get("steps"), database_of(db))
    clash = db.query("SELECT SKILL_ID FROM CORE.FIX_SKILL WHERE UPPER(SKILL_NAME) = UPPER(%s)", (name,))
    if clash and clash[0]["skill_id"] != skill_id:
        raise ValueError(f"a skill named {name} already exists")
    body = json.dumps(steps)
    if skill_id:
        locked = skill_locked(db, skill_id)
        if locked:
            raise ValueError(locked)
        db.execute("""UPDATE CORE.FIX_SKILL SET SKILL_NAME = %s, DESCRIPTION = %s, STEPS = PARSE_JSON(%s),
                             VERSION_NO = VERSION_NO + 1, OWNER = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SKILL_ID = %s""",
                   (name, str(data.get("description") or "")[:4000] or None, body, data.get("owner") or actor, skill_id))
    else:
        skill_id = str(uuid.uuid4())
        db.execute("""INSERT INTO CORE.FIX_SKILL (SKILL_ID, SKILL_NAME, DESCRIPTION, STEPS, OWNER, CREATED_BY)
                      SELECT %s, %s, %s, PARSE_JSON(%s), %s, %s""",
                   (skill_id, name, str(data.get("description") or "")[:4000] or None, body, data.get("owner") or actor,
                    actor[:256]))
    return get_skill(db, skill_id) or {}


def list_skills(db: Any) -> List[Dict[str, Any]]:
    out = []
    for r in db.query("SELECT SKILL_ID, SKILL_NAME, DESCRIPTION, STEPS, VERSION_NO, OWNER, UPDATED_AT FROM CORE.FIX_SKILL "
                      "ORDER BY SKILL_NAME"):
        row = dict(r)
        if isinstance(row.get("steps"), str):
            try:
                row["steps"] = json.loads(row["steps"])
            except ValueError:
                pass
        out.append(row)
    return out
