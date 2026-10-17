"""What the value ledger counts, read from the platform's own tables (Db: %s binds, lower case column names).

Every reader returns None when its table cannot be read (a module not deployed, a role without access), so the ledger
shows that activity as not available instead of failing or counting zero. Runs in the TEST environment are left out.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

NOT_TEST = "COALESCE(R.ENVIRONMENT, '') <> 'TEST' AND R.DELETED_AT IS NULL"

# activity -> SQL returning (W week, KIND credit key, N count) since DATEADD(day, -%s, now)
ACTIVITY_SQL: Dict[str, str] = {
    "mapping_column": f"""
        SELECT DATE_TRUNC('week', D.REVIEWED_AT) AS W, D.DECISION AS KIND, COUNT(*) AS N
          FROM CORE.MAPPING_DECISION D LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = D.RUN_ID
         WHERE D.IS_CURRENT AND D.REVIEWED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {NOT_TEST}
         GROUP BY 1, 2""",
    "sttm_line": """
        SELECT DATE_TRUNC('week', L.APPROVAL_TIMESTAMP) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.STTM_LINE L
         WHERE L.HUMAN_APPROVED AND L.APPROVAL_TIMESTAMP >= DATEADD(day, %s, CURRENT_TIMESTAMP())
         GROUP BY 1, 2""",
    "dbt_file": f"""
        SELECT DATE_TRUNC('week', L.CREATED_AT) AS W, 'APPROVED' AS KIND, SUM(COALESCE(L.FILES_GENERATED, 0)) AS N
          FROM (SELECT G.RUN_ID, G.CREATED_AT, G.FILES_GENERATED
                  FROM CORE.DBT_GENERATION_REGISTRY G LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = G.RUN_ID
                 WHERE G.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND UPPER(G.GENERATION_STATUS) <> 'FAILED'
                   AND {NOT_TEST}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY G.RUN_ID ORDER BY G.GENERATION_VERSION DESC, G.CREATED_AT DESC) = 1) L
         GROUP BY 1, 2""",
    "qa_test": """
        SELECT DATE_TRUNC('week', T.CREATED_AT) AS W,
               IFF(EXISTS (SELECT 1 FROM CORE.QA_TEST_VERSION V WHERE V.TEST_ID = T.TEST_ID AND V.CHANGE = 'EDITED'),
                   'EDITED', 'APPROVED') AS KIND, COUNT(*) AS N
          FROM CORE.QA_TEST_CASE T
         WHERE T.ORIGIN = 'AI' AND NOT COALESCE(T.IS_DELETED, FALSE)
           AND T.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP())
         GROUP BY 1, 2""",
    "dq_check": """
        SELECT DATE_TRUNC('week', S.REVIEWED_AT) AS W,
               IFF(EQUAL_NULL(S.GENERATED_DEFINITION, S.CHECK_DEFINITION) OR S.GENERATED_DEFINITION IS NULL,
                   'APPROVED', 'MODIFIED') AS KIND, COUNT(*) AS N
          FROM CORE.SODA_EXPECTATION_REGISTRY S
         WHERE S.IS_CURRENT AND S.ORIGIN = 'AI' AND S.STATUS = 'APPROVED'
           AND S.REVIEWED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP())
         GROUP BY 1, 2""",
    "auto_fix": """
        SELECT DATE_TRUNC('week', COALESCE(R.FINISHED_AT, R.STARTED_AT)) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.RESOLUTION_RUN R
         WHERE R.OUTCOME = 'SUCCESS' AND COALESCE(R.FINISHED_AT, R.STARTED_AT) >= DATEADD(day, %s, CURRENT_TIMESTAMP())
         GROUP BY 1, 2""",
    "rca_ticket": """
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N FROM CORE.INCIDENT_EVENT E
         WHERE E.KIND = 'rca_ticketed' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1, 2""",
    "incident_diagnosis": """
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N FROM CORE.INCIDENT_EVENT E
         WHERE E.KIND = 'diagnosed' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1, 2""",
    "case_triage": """
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N FROM CORE.CASE_EVENT E
         WHERE E.KIND = 'triaged' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1, 2""",
}

COST_SQL = """
    SELECT DATE_TRUNC('week', CREATED_AT) AS W, SUM(COALESCE(ACTUAL_CREDITS, ESTIMATED_COST, 0)) AS CREDITS
      FROM CORE.COST_USAGE WHERE CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1"""

EPISODE_SQL = """
    SELECT E.INCIDENT_ID, I.FINGERPRINT, E.KIND, E.CREATED_AT, E.DETAIL
      FROM CORE.INCIDENT_EVENT E JOIN CORE.INCIDENT I ON I.INCIDENT_ID = E.INCIDENT_ID
     WHERE E.KIND IN ('opened', 'reopened', 'resolved', 'auto_fixed') AND I.PARENT_INCIDENT_ID IS NULL
       AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP())
     ORDER BY E.INCIDENT_ID, E.CREATED_AT LIMIT 50000"""

CUTOVER_SQL = "SELECT MIN(CREATED_AT) AS AT FROM CORE.INCIDENT_EVENT WHERE KIND = 'auto_fixed'"

# the knowledge score over [now - from_days, now - to_days)
KNOWLEDGE_SQL = """
    SELECT
      (SELECT COUNT(*) FROM CORE.FAILURE_EVENT WHERE STATUS <> 'OUT_OF_SCOPE'
          AND CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP()))
        AS FAILURES,
      (SELECT COUNT(*) FROM CORE.FAILURE_EVENT WHERE STATUS = 'RESOLVED' AND ISSUE_ID IS NOT NULL
          AND CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP()))
        AS AUTO_FIXED,
      (SELECT COUNT(DISTINCT ERROR_SIGNATURE) FROM CORE.FAILURE_EVENT WHERE STATUS <> 'OUT_OF_SCOPE'
          AND CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP()))
        AS SIGNATURES,
      (SELECT COUNT(DISTINCT F.ERROR_SIGNATURE) FROM CORE.FAILURE_EVENT F
        WHERE F.STATUS <> 'OUT_OF_SCOPE' AND F.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP())
          AND F.CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())
          AND (F.ISSUE_ID IS NOT NULL OR EXISTS (SELECT 1 FROM CORE.KNOWN_ISSUE K WHERE K.STATUS = 'PUBLISHED'
                                                   AND K.ERROR_SIGNATURE = F.ERROR_SIGNATURE)))
        AS COVERED_SIGNATURES,
      (SELECT COUNT(*) FROM CORE.DAG WHERE COALESCE(IS_ACTIVE, TRUE) AND NOT COALESCE(IS_PAUSED, FALSE)) AS ACTIVE_DAGS,
      (SELECT COUNT(DISTINCT SOURCE_REFERENCE) FROM CORE.DOMAIN_KNOWLEDGE
        WHERE KNOWLEDGE_TYPE = 'AIRFLOW_DAG' AND IS_CURRENT AND STATUS = 'ACTIVE'
          AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS PROFILED_DAGS,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND STATUS = 'ACTIVE'
          AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS KNOWLEDGE_ITEMS,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE STATUS IN ('ACTIVE', 'SUPERSEDED')
          AND CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP()))
        AS KNOWLEDGE_NEW,
      (SELECT COUNT(*) FROM CORE.KNOWN_ISSUE WHERE STATUS = 'PUBLISHED'
          AND COALESCE(PUBLISHED_AT, CREATED_AT) < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS KNOWN_FIXES,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE KNOWLEDGE_TYPE = 'AIRFLOW_RUNBOOK' AND IS_CURRENT
          AND STATUS = 'ACTIVE') AS RUNBOOKS"""


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def activity_counts(db: Any, days: int) -> Dict[str, Optional[List[Dict[str, Any]]]]:
    out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    for key, sql in ACTIVITY_SQL.items():
        rows = _safe(lambda sql=sql: db.query(sql, (-abs(int(days)),)))
        out[key] = None if rows is None else [{"week": str(r.get("w"))[:10], "kind": r.get("kind"),
                                               "n": int(r.get("n") or 0)} for r in rows]
    return out


def cost_rows(db: Any, days: int) -> Optional[List[Dict[str, Any]]]:
    rows = _safe(lambda: db.query(COST_SQL, (-abs(int(days)),)))
    return None if rows is None else [{"week": str(r.get("w"))[:10], "credits": float(r.get("credits") or 0)}
                                      for r in rows]


def incident_events(db: Any, days: int) -> Optional[List[Dict[str, Any]]]:
    return _safe(lambda: db.query(EPISODE_SQL, (-abs(int(days)),)))


def cutover(db: Any) -> Any:
    found = _safe(lambda: db.query(CUTOVER_SQL))
    return found[0].get("at") if found else None


def knowledge_counts(db: Any, from_days: int, to_days: int) -> Optional[Dict[str, Any]]:
    """The knowledge score's inputs for the period from `from_days` ago to `to_days` ago (to_days 0: until now)."""
    a, b = -abs(int(from_days)), -abs(int(to_days))
    params = (a, b, a, b, a, b, a, b, b, b, a, b, b)
    found = _safe(lambda: db.query(KNOWLEDGE_SQL, params))
    return dict(found[0]) if found else None
