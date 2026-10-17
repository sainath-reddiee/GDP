"""What the value ledger counts, read from the platform's own tables (Db: %s binds, lower case column names).

Every reader returns None when its table cannot be read (a module not deployed, a role without access), so the ledger
shows that activity as not available instead of failing or counting zero.

Test and lab activity is left out (baselines `exclude`): runs whose ENVIRONMENT is in `run_environments` (TEST by
default; add DEV where development runs are only tries), and Airflow failures, incidents and repairs of the
environments in `airflow_envs` (the platform's own lab, local-lab, by default: its failures are injected on purpose).
Case triage has no environment and is always counted.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# a run that counts: not in an excluded environment, not deleted (no run at all counts: work outside a run)
RUN_OK = "NOT ARRAY_CONTAINS(UPPER(COALESCE(R.ENVIRONMENT, ''))::VARIANT, PARSE_JSON(%s)) AND R.DELETED_AT IS NULL"


def ENV_OK(column: str) -> str:   # noqa: N802 - reads like the constant above
    return f"NOT ARRAY_CONTAINS(COALESCE({column}, '')::VARIANT, PARSE_JSON(%s))"


# activity -> (SQL returning W week, KIND credit key, N count, the binds in order: 'days', 'runs' or 'envs')
ACTIVITY_SQL: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "mapping_column": (f"""
        SELECT DATE_TRUNC('week', D.REVIEWED_AT) AS W, D.DECISION AS KIND, COUNT(*) AS N
          FROM CORE.MAPPING_DECISION D LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = D.RUN_ID
         WHERE D.IS_CURRENT AND D.REVIEWED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {RUN_OK}
         GROUP BY 1, 2""", ("days", "runs")),
    "sttm_line": (f"""
        SELECT DATE_TRUNC('week', L.APPROVAL_TIMESTAMP) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.STTM_LINE L LEFT JOIN CORE.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
          LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = S.RUN_ID
         WHERE L.HUMAN_APPROVED AND L.APPROVAL_TIMESTAMP >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {RUN_OK}
         GROUP BY 1, 2""", ("days", "runs")),
    "dbt_file": (f"""
        SELECT DATE_TRUNC('week', L.CREATED_AT) AS W, 'APPROVED' AS KIND, SUM(COALESCE(L.FILES_GENERATED, 0)) AS N
          FROM (SELECT G.RUN_ID, G.CREATED_AT, G.FILES_GENERATED
                  FROM CORE.DBT_GENERATION_REGISTRY G LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = G.RUN_ID
                 WHERE G.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND UPPER(G.GENERATION_STATUS) <> 'FAILED'
                   AND {RUN_OK}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY G.RUN_ID ORDER BY G.GENERATION_VERSION DESC, G.CREATED_AT DESC) = 1) L
         GROUP BY 1, 2""", ("days", "runs")),
    "qa_test": (f"""
        SELECT DATE_TRUNC('week', T.CREATED_AT) AS W,
               IFF(EXISTS (SELECT 1 FROM CORE.QA_TEST_VERSION V WHERE V.TEST_ID = T.TEST_ID AND V.CHANGE = 'EDITED'),
                   'EDITED', 'APPROVED') AS KIND, COUNT(*) AS N
          FROM CORE.QA_TEST_CASE T LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = T.RUN_ID
         WHERE T.ORIGIN = 'AI' AND NOT COALESCE(T.IS_DELETED, FALSE)
           AND T.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {RUN_OK}
         GROUP BY 1, 2""", ("days", "runs")),
    "dq_check": (f"""
        SELECT DATE_TRUNC('week', S.REVIEWED_AT) AS W,
               IFF(EQUAL_NULL(S.GENERATED_DEFINITION, S.CHECK_DEFINITION) OR S.GENERATED_DEFINITION IS NULL,
                   'APPROVED', 'MODIFIED') AS KIND, COUNT(*) AS N
          FROM CORE.SODA_EXPECTATION_REGISTRY S LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = S.RUN_ID
         WHERE S.IS_CURRENT AND S.ORIGIN = 'AI' AND S.STATUS = 'APPROVED'
           AND S.REVIEWED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {RUN_OK}
         GROUP BY 1, 2""", ("days", "runs")),
    "auto_fix": (f"""
        SELECT DATE_TRUNC('week', COALESCE(R.FINISHED_AT, R.STARTED_AT)) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.RESOLUTION_RUN R LEFT JOIN CORE.FAILURE_EVENT F ON F.FAILURE_ID = R.FAILURE_ID
         WHERE R.OUTCOME = 'SUCCESS' AND COALESCE(R.FINISHED_AT, R.STARTED_AT) >= DATEADD(day, %s, CURRENT_TIMESTAMP())
           AND {ENV_OK('F.ENV_ID')}
         GROUP BY 1, 2""", ("days", "envs")),
    "rca_ticket": (f"""
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.INCIDENT_EVENT E LEFT JOIN CORE.INCIDENT I ON I.INCIDENT_ID = E.INCIDENT_ID
         WHERE E.KIND = 'rca_ticketed' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {ENV_OK('I.ENV_ID')}
         GROUP BY 1, 2""", ("days", "envs")),
    "incident_diagnosis": (f"""
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N
          FROM CORE.INCIDENT_EVENT E LEFT JOIN CORE.INCIDENT I ON I.INCIDENT_ID = E.INCIDENT_ID
         WHERE E.KIND = 'diagnosed' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {ENV_OK('I.ENV_ID')}
         GROUP BY 1, 2""", ("days", "envs")),
    "case_triage": ("""
        SELECT DATE_TRUNC('week', E.CREATED_AT) AS W, 'APPROVED' AS KIND, COUNT(*) AS N FROM CORE.CASE_EVENT E
         WHERE E.KIND = 'triaged' AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1, 2""", ("days",)),
}

COST_SQL = """
    SELECT DATE_TRUNC('week', CREATED_AT) AS W, SUM(COALESCE(ACTUAL_CREDITS, ESTIMATED_COST, 0)) AS CREDITS
      FROM CORE.COST_USAGE WHERE CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) GROUP BY 1"""

EPISODE_SQL = f"""
    SELECT E.INCIDENT_ID, I.FINGERPRINT, E.KIND, E.CREATED_AT, E.DETAIL
      FROM CORE.INCIDENT_EVENT E JOIN CORE.INCIDENT I ON I.INCIDENT_ID = E.INCIDENT_ID
     WHERE E.KIND IN ('opened', 'reopened', 'resolved', 'auto_fixed') AND I.PARENT_INCIDENT_ID IS NULL
       AND E.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND {ENV_OK('I.ENV_ID')}
     ORDER BY E.INCIDENT_ID, E.CREATED_AT LIMIT 50000"""

CUTOVER_SQL = f"""SELECT MIN(E.CREATED_AT) AS AT FROM CORE.INCIDENT_EVENT E JOIN CORE.INCIDENT I ON I.INCIDENT_ID = E.INCIDENT_ID
                   WHERE E.KIND = 'auto_fixed' AND {ENV_OK('I.ENV_ID')}"""

# the knowledge score over [now - from_days, now - to_days); binds: f(rom), t(o), e(xcluded environments)
_FAILURES = f"CORE.FAILURE_EVENT F WHERE {ENV_OK('F.ENV_ID')} AND F.CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) " \
            "AND F.CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())"
KNOWLEDGE_SQL = f"""
    SELECT
      (SELECT COUNT(*) FROM {_FAILURES} AND F.STATUS <> 'OUT_OF_SCOPE') AS FAILURES,
      (SELECT COUNT(*) FROM {_FAILURES} AND F.STATUS = 'RESOLVED' AND F.ISSUE_ID IS NOT NULL) AS AUTO_FIXED,
      (SELECT COUNT(DISTINCT F.ERROR_SIGNATURE) FROM {_FAILURES} AND F.STATUS <> 'OUT_OF_SCOPE') AS SIGNATURES,
      (SELECT COUNT(DISTINCT F.ERROR_SIGNATURE) FROM {_FAILURES} AND F.STATUS <> 'OUT_OF_SCOPE'
          AND (F.ISSUE_ID IS NOT NULL OR EXISTS (SELECT 1 FROM CORE.KNOWN_ISSUE K WHERE K.STATUS = 'PUBLISHED'
                                                   AND K.ERROR_SIGNATURE = F.ERROR_SIGNATURE)))
        AS COVERED_SIGNATURES,
      (SELECT COUNT(*) FROM CORE.DAG D WHERE COALESCE(D.IS_ACTIVE, TRUE) AND NOT COALESCE(D.IS_PAUSED, FALSE)
          AND {ENV_OK('D.ENV_ID')}) AS ACTIVE_DAGS,
      (SELECT COUNT(DISTINCT K.SOURCE_REFERENCE) FROM CORE.DOMAIN_KNOWLEDGE K
         JOIN CORE.DAG D ON K.SOURCE_REFERENCE = 'ops.dag.' || D.ENV_ID || '.' || D.DAG_ID   -- services.ops.dag_profile.reference
        WHERE K.KNOWLEDGE_TYPE = 'AIRFLOW_DAG' AND K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND {ENV_OK('D.ENV_ID')}
          AND COALESCE(D.IS_ACTIVE, TRUE) AND NOT COALESCE(D.IS_PAUSED, FALSE)
          AND K.CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS PROFILED_DAGS,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE IS_CURRENT AND STATUS = 'ACTIVE'
          AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS KNOWLEDGE_ITEMS,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE STATUS IN ('ACTIVE', 'SUPERSEDED')
          AND CREATED_AT >= DATEADD(day, %s, CURRENT_TIMESTAMP()) AND CREATED_AT < DATEADD(day, %s, CURRENT_TIMESTAMP()))
        AS KNOWLEDGE_NEW,
      (SELECT COUNT(*) FROM CORE.KNOWN_ISSUE WHERE STATUS = 'PUBLISHED'
          AND COALESCE(PUBLISHED_AT, CREATED_AT) < DATEADD(day, %s, CURRENT_TIMESTAMP())) AS KNOWN_FIXES,
      (SELECT COUNT(*) FROM CORE.DOMAIN_KNOWLEDGE WHERE KNOWLEDGE_TYPE = 'AIRFLOW_RUNBOOK' AND IS_CURRENT
          AND STATUS = 'ACTIVE') AS RUNBOOKS"""
KNOWLEDGE_BINDS = ("e", "f", "t") * 4 + ("e", "e", "t", "t", "f", "t", "t")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _lists(exclude: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """The JSON lists bound into the filters (run environments upper case, Airflow environment ids as written)."""
    exclude = exclude or {}
    return {"runs": json.dumps(sorted({str(e).strip().upper() for e in exclude.get("run_environments") or [] if str(e).strip()})),
            "envs": json.dumps(sorted({str(e).strip() for e in exclude.get("airflow_envs") or [] if str(e).strip()}))}


def activity_counts(db: Any, days: int, exclude: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[List[Dict[str, Any]]]]:
    lists = _lists(exclude)
    out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    for key, (sql, binds) in ACTIVITY_SQL.items():
        params = tuple(-abs(int(days)) if b == "days" else lists[b] for b in binds)
        rows = _safe(lambda sql=sql, params=params: db.query(sql, params))
        out[key] = None if rows is None else [{"week": str(r.get("w"))[:10], "kind": r.get("kind"),
                                               "n": int(r.get("n") or 0)} for r in rows]
    return out


def cost_rows(db: Any, days: int) -> Optional[List[Dict[str, Any]]]:
    rows = _safe(lambda: db.query(COST_SQL, (-abs(int(days)),)))
    return None if rows is None else [{"week": str(r.get("w"))[:10], "credits": float(r.get("credits") or 0)}
                                      for r in rows]


def incident_events(db: Any, days: int, exclude: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
    return _safe(lambda: db.query(EPISODE_SQL, (-abs(int(days)), _lists(exclude)["envs"])))


def cutover(db: Any, exclude: Optional[Dict[str, Any]] = None) -> Any:
    found = _safe(lambda: db.query(CUTOVER_SQL, (_lists(exclude)["envs"],)))
    return found[0].get("at") if found else None


def knowledge_counts(db: Any, from_days: int, to_days: int, exclude: Optional[Dict[str, Any]] = None
                     ) -> Optional[Dict[str, Any]]:
    """The knowledge score's inputs for the period from `from_days` ago to `to_days` ago (to_days 0: until now)."""
    values = {"f": -abs(int(from_days)), "t": -abs(int(to_days)), "e": _lists(exclude)["envs"]}
    found = _safe(lambda: db.query(KNOWLEDGE_SQL, tuple(values[b] for b in KNOWLEDGE_BINDS)))
    return dict(found[0]) if found else None
