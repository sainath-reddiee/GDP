"""Case storage in Snowflake (CASES schema, migration V034), through the API's Db (%s placeholders, lower-case keys).

The service (services/cases/service.py) only talks to this interface, so the unit tests run it against an in-memory
store. Visibility is not checked here: callers check the domain before reading or writing a case.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.cases.rules import OPEN_STATUSES

SYSTEM = "system"
_OPEN_SQL = ", ".join(f"'{s}'" for s in OPEN_STATUSES)   # constants, never user input

CASE_SELECT = f"""
SELECT C.CASE_ID, C.CASE_NUMBER, C.DOMAIN_ID, D.DOMAIN_NAME, C.TITLE, C.DESCRIPTION, C.KIND, C.SOURCE, C.SOURCE_REF,
       C.PAGE_CONTEXT, C.STATUS, C.SEVERITY, C.ASSIGNEE, C.TEAM_ID, C.SLA_DUE_AT, C.TARGET_TABLE_ID,
       IFF(T.TARGET_TABLE_ID IS NULL, NULL, T.TARGET_DATABASE || '.' || T.TARGET_SCHEMA || '.' || T.TARGET_TABLE) AS TARGET_FQN,
       C.RUN_ID, C.REPO_ID, C.MODELS, C.FINGERPRINT, C.AI, C.AI_SUMMARY, C.DUPLICATE_OF, C.RESOLUTION, C.RESOLVED_BY,
       C.RESOLVED_AT, C.CLOSED_AT, C.REOPENED, C.OPENED_BY, C.OPENED_AT, C.UPDATED_AT,
       (C.STATUS IN ({_OPEN_SQL}) AND C.SLA_DUE_AT IS NOT NULL AND C.SLA_DUE_AT < CURRENT_TIMESTAMP()) AS SLA_BREACHED,
       (SELECT COUNT(*) FROM CASES.CASE_LINK L WHERE L.CASE_ID = C.CASE_ID) AS LINKS_COUNT
  FROM CASES.CASE_RECORD C
  LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = C.DOMAIN_ID
  LEFT JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID"""

# columns the service may set with update(); timestamps go through TRY_TO_TIMESTAMP_LTZ
UPDATABLE = {"TITLE", "DESCRIPTION", "KIND", "SEVERITY", "DOMAIN_ID", "TARGET_TABLE_ID", "TEAM_ID", "ASSIGNEE", "STATUS",
             "RESOLUTION", "RESOLVED_BY", "DUPLICATE_OF", "RUN_ID"}
TIMESTAMPS = {"RESOLVED_AT", "CLOSED_AT"}
NOW = "now"   # value for a timestamp column: the current time


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class SqlStore:
    def __init__(self, db):
        self.db = db

    # ---- cases
    def get(self, case_id: str) -> Optional[Dict[str, Any]]:
        found = self.db.query(CASE_SELECT + " WHERE C.CASE_ID = %s", (case_id,))
        return found[0] if found else None

    def find_open_by_source(self, source: str, ref: str) -> Optional[Dict[str, Any]]:
        found = self.db.query(CASE_SELECT + f" WHERE C.SOURCE = %s AND C.SOURCE_REF = %s AND C.STATUS IN ({_OPEN_SQL})"
                              " ORDER BY C.OPENED_AT LIMIT 1", (source, ref))
        return found[0] if found else None

    def count_by_source(self, source: str, ref: str) -> int:
        found = self.db.query("SELECT COUNT(*) AS N FROM CASES.CASE_RECORD WHERE SOURCE = %s AND SOURCE_REF = %s", (source, ref))
        return int(found[0]["n"] or 0) if found else 0

    def find_open_by_fingerprint(self, domain_id: Optional[str], fp: str) -> Optional[Dict[str, Any]]:
        found = self.db.query(CASE_SELECT + f" WHERE C.FINGERPRINT = %s AND EQUAL_NULL(C.DOMAIN_ID, %s)"
                              f" AND C.STATUS IN ({_OPEN_SQL}) ORDER BY C.OPENED_AT LIMIT 1", (fp, domain_id))
        return found[0] if found else None

    def insert(self, row: Dict[str, Any], sla_hours: int) -> None:
        self.db.execute(
            """INSERT INTO CASES.CASE_RECORD (CASE_ID, DOMAIN_ID, TITLE, DESCRIPTION, KIND, SOURCE, SOURCE_REF, PAGE_CONTEXT,
                                              STATUS, SEVERITY, ASSIGNEE, TEAM_ID, SLA_DUE_AT, TARGET_TABLE_ID, RUN_ID,
                                              FINGERPRINT, OPENED_BY)
               SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), 'NEW', %s, %s, %s,
                      DATEADD(hour, %s, CURRENT_TIMESTAMP()), %s, %s, %s, %s""",
            (row["case_id"], row.get("domain_id"), row["title"], row.get("description"), row["kind"], row["source"],
             row.get("source_ref"), json.dumps(row.get("page_context")), row["severity"], row.get("assignee"),
             row.get("team_id"), int(sla_hours), row.get("target_table_id"), row.get("run_id"), row.get("fingerprint"),
             row["opened_by"]))

    def update(self, case_id: str, fields: Dict[str, Any], expect_status: Optional[str] = None,
               sla_hours: Optional[int] = None, reopen: bool = False) -> int:
        sets, params = ["UPDATED_AT = CURRENT_TIMESTAMP()"], []
        for col, value in fields.items():
            col = col.upper()
            if col in TIMESTAMPS:
                if value == NOW:
                    sets.append(f"{col} = CURRENT_TIMESTAMP()")
                else:
                    sets.append(f"{col} = TRY_TO_TIMESTAMP_LTZ(%s::VARCHAR)")
                    params.append(value)
            elif col in UPDATABLE:
                sets.append(f"{col} = %s")
                params.append(value)
            else:
                raise ValueError(f"not an updatable case column: {col}")
        if sla_hours is not None:
            sets.append("SLA_DUE_AT = DATEADD(hour, %s, OPENED_AT)")
            params.append(int(sla_hours))
        if reopen:
            sets.append("REOPENED = COALESCE(REOPENED, 0) + 1")
        where = "CASE_ID = %s"
        params.append(case_id)
        if expect_status:
            where += " AND STATUS = %s"
            params.append(expect_status)
        return self.db.execute_count(f"UPDATE CASES.CASE_RECORD SET {', '.join(sets)} WHERE {where}", tuple(params))

    # ---- claims and events
    def claim(self, key: str) -> bool:
        return self.db.execute_count("""
            MERGE INTO CASES.CASE_EVENT T USING (SELECT %s AS K) S ON T.IDEMPOTENCY_KEY = S.K
            WHEN NOT MATCHED THEN INSERT (EVENT_ID, KIND, ACTOR, IDEMPOTENCY_KEY) VALUES (%s, 'claim', 'system', S.K)""",
                                     (key, str(uuid.uuid4()))) >= 1

    def claimed_case(self, key: str) -> Optional[str]:
        found = self.db.query("SELECT CASE_ID FROM CASES.CASE_EVENT WHERE IDEMPOTENCY_KEY = %s LIMIT 1", (key,))
        return found[0].get("case_id") if found else None

    def bind(self, key: str, case_id: str, kind: str, actor: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self.db.execute("""UPDATE CASES.CASE_EVENT SET CASE_ID = %s, KIND = %s, ACTOR = %s, DETAIL = PARSE_JSON(%s)
                            WHERE IDEMPOTENCY_KEY = %s""", (case_id, kind, actor, json.dumps(detail or {}, default=str), key))

    def release_claim(self, key: str) -> None:
        """Free a claim whose case could not be written, so the next attempt can take it."""
        self.db.execute("UPDATE CASES.CASE_EVENT SET IDEMPOTENCY_KEY = NULL, KIND = 'claim_failed' "
                        "WHERE IDEMPOTENCY_KEY = %s AND CASE_ID IS NULL", (key,))

    def event(self, case_id: str, kind: str, actor: str = SYSTEM, detail: Optional[Dict[str, Any]] = None) -> None:
        self.db.execute("""INSERT INTO CASES.CASE_EVENT (EVENT_ID, CASE_ID, KIND, ACTOR, DETAIL)
                           SELECT %s, %s, %s, %s, PARSE_JSON(%s)""",
                        (str(uuid.uuid4()), case_id, kind, actor or SYSTEM, json.dumps(detail or {}, default=str)))

    def events(self, case_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query("""SELECT EVENT_ID, KIND, ACTOR, DETAIL, CREATED_AT FROM CASES.CASE_EVENT
                                 WHERE CASE_ID = %s AND KIND <> 'claim' ORDER BY CREATED_AT, EVENT_ID LIMIT 1000""", (case_id,))
        for r in rows:
            r["detail"] = _json(r.get("detail"))
        return rows

    # ---- links
    def links(self, case_id: str) -> List[Dict[str, Any]]:
        return self.db.query("""SELECT LINK_ID, KIND, REF, LABEL, URL, STATE, CREATED_BY, CREATED_AT FROM CASES.CASE_LINK
                                 WHERE CASE_ID = %s ORDER BY CREATED_AT, LINK_ID""", (case_id,))

    def add_link(self, case_id: str, kind: str, ref: str, label: Optional[str], url: Optional[str], actor: str) -> bool:
        """One MERGE per link: the same (case, kind, ref) is stored once. True when it was added."""
        return self.db.execute_count("""
            MERGE INTO CASES.CASE_LINK T USING (SELECT %s AS CASE_ID, %s AS KIND, %s AS REF) S
               ON T.CASE_ID = S.CASE_ID AND T.KIND = S.KIND AND T.REF = S.REF
            WHEN NOT MATCHED THEN INSERT (LINK_ID, CASE_ID, KIND, REF, LABEL, URL, STATE, CREATED_BY)
                 VALUES (%s, S.CASE_ID, S.KIND, S.REF, %s, %s, 'OK', %s)""",
                                     (case_id, kind, ref, str(uuid.uuid4()), label, url, actor)) >= 1

    def remove_link(self, case_id: str, link_id: str) -> int:
        return self.db.execute_count("DELETE FROM CASES.CASE_LINK WHERE CASE_ID = %s AND LINK_ID = %s", (case_id, link_id))

    # ---- artifacts
    def artifacts(self, case_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query("""SELECT ARTIFACT_ID, TYPE, TITLE, CONTENT, DIFF, STATUS, PROPOSED_BY, DECIDED_BY, DECIDED_AT,
                                       CREATED_AT FROM CASES.CASE_ARTIFACT WHERE CASE_ID = %s ORDER BY CREATED_AT""", (case_id,))
        for r in rows:
            r["content"] = _json(r.get("content"))
        return rows

    def artifact(self, case_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        found = self.db.query("""SELECT ARTIFACT_ID, CASE_ID, TYPE, TITLE, CONTENT, DIFF, STATUS, PROPOSED_BY, DECIDED_BY,
                                        DECIDED_AT, CREATED_AT FROM CASES.CASE_ARTIFACT
                                  WHERE CASE_ID = %s AND ARTIFACT_ID = %s""", (case_id, artifact_id))
        if not found:
            return None
        found[0]["content"] = _json(found[0].get("content"))
        return found[0]

    def add_artifact(self, case_id: str, kind: str, title: str, content: Dict[str, Any], diff: Optional[str],
                     proposed_by: str) -> str:
        artifact_id = str(uuid.uuid4())
        self.db.execute("""INSERT INTO CASES.CASE_ARTIFACT (ARTIFACT_ID, CASE_ID, TYPE, TITLE, CONTENT, DIFF, STATUS, PROPOSED_BY)
                           SELECT %s, %s, %s, %s, PARSE_JSON(%s), %s, 'PROPOSED', %s""",
                        (artifact_id, case_id, kind, (title or kind)[:500], json.dumps(content or {}, default=str),
                         diff[:100000] if diff else None, proposed_by))
        return artifact_id

    def set_artifact(self, case_id: str, artifact_id: str, status: Optional[str] = None, actor: Optional[str] = None,
                     content: Optional[Dict[str, Any]] = None, expect_status: Optional[str] = None) -> int:
        """Change an artifact's status (with who decided it and when) and/or replace its content."""
        sets, params = ["UPDATED_AT = CURRENT_TIMESTAMP()"], []
        if status:
            sets += ["STATUS = %s", "DECIDED_BY = %s", "DECIDED_AT = CURRENT_TIMESTAMP()"]
            params += [status, actor or SYSTEM]
        if content is not None:
            sets.append("CONTENT = PARSE_JSON(%s)")
            params.append(json.dumps(content, default=str))
        where, wparams = "CASE_ID = %s AND ARTIFACT_ID = %s", [case_id, artifact_id]
        if expect_status:
            where += " AND STATUS = %s"
            wparams.append(expect_status)
        return self.db.execute_count(f"UPDATE CASES.CASE_ARTIFACT SET {', '.join(sets)} WHERE {where}",
                                     tuple(params + wparams))

    def supersede_ai_artifacts(self, case_id: str, note: str) -> int:
        """Proposals of an earlier triage that nobody decided: rejected by the system (accepted and applied ones stay)."""
        return self.db.execute_count(
            """UPDATE CASES.CASE_ARTIFACT
                  SET STATUS = 'REJECTED', DECIDED_BY = 'system', DECIDED_AT = CURRENT_TIMESTAMP(),
                      UPDATED_AT = CURRENT_TIMESTAMP(),
                      CONTENT = OBJECT_INSERT(COALESCE(CONTENT, OBJECT_CONSTRUCT()), 'decision_note', %s::VARIANT, TRUE)
                WHERE CASE_ID = %s AND STATUS = 'PROPOSED' AND PROPOSED_BY = 'ai'""", (note, case_id))

    # ---- AI
    def set_ai(self, case_id: str, ai: Dict[str, Any], summary: str, models: Optional[List[str]] = None) -> None:
        sets = ["AI = PARSE_JSON(%s)", "AI_SUMMARY = %s", "UPDATED_AT = CURRENT_TIMESTAMP()"]
        params: List[Any] = [json.dumps(ai, default=str), (summary or "")[:2000]]
        if models:
            sets.append("MODELS = PARSE_JSON(%s)::ARRAY")
            params.append(json.dumps(list(models)[:100]))
        params.append(case_id)
        self.db.execute(f"UPDATE CASES.CASE_RECORD SET {', '.join(sets)} WHERE CASE_ID = %s", tuple(params))

    def ai_calls(self, actor: str, minutes: int = 60) -> int:
        """AI calls (triage and questions; cached answers are not counted) the user made in the last minutes."""
        found = self.db.query(f"""SELECT COUNT(*) AS N FROM CASES.CASE_EVENT
                                   WHERE UPPER(ACTOR) = %s AND KIND IN ('triaged', 'ai_question')
                                     AND CREATED_AT >= DATEADD(minute, -{int(minutes)}, CURRENT_TIMESTAMP())""",
                              (str(actor or "").upper(),))
        return int(found[0]["n"] or 0) if found else 0
