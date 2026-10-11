"""Run assets in the asset stores: every run's model design and dbt generation (Modeling Store), every run's data
quality checks (Data Quality Store) and every run's saved QA tests (QA Store), tenant-wide and domain-scoped.

Reads come straight from the run tables. Writes made here are store edits, so the governance middleware always
queues them for another person's approval (services/governance/policy.py STORE_ROUTES), except promoting tests into
a QA pack draft: a draft is not live, and the pack's own submit and approve gates publishing.

  GET  /api/hub/models/runs                latest MODELING.MODEL_DESIGN per run, with its publication
  GET  /api/hub/dbt                        latest CODEGEN.DBT_GENERATION_REGISTRY row per run
  GET  /api/hub/dbt/{generation_id}        one generation: files, versions, publication
  POST /api/hub/dbt/{generation_id}/files  edit one file (queued; a published or approved generation gets a new version)
  GET  /api/dq/run-checks                  CONTRACT.SODA_EXPECTATION_REGISTRY current checks with their latest result
  GET  /api/dq/run-checks/coverage         a run's STTM target columns without a check
  POST /api/dq/run-checks/decisions        approve, reject or modify a run's checks (queued)
  GET  /api/qa/run-tests                   CONTRACT.QA_TEST_CASE with the latest outcome and recent history
  POST /api/qa/run-tests/promote           copy tests into a new or existing QA pack draft
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, _snowflake_error, current_db

router = APIRouter()


def _domain(db: Db, value: Optional[str]) -> Optional[str]:
    """A domain id from an id or a name (the domain switcher sends names); None for all domains."""
    if not value:
        return None
    try:
        found = db.query("SELECT DOMAIN_ID FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s OR UPPER(DOMAIN_NAME) = UPPER(%s) "
                         "LIMIT 1", (value, value))
    except Exception:
        return None
    if not found:
        raise HTTPException(404, f"domain {value} not found")
    return found[0]["domain_id"]


def _run_scope(alias: str, domain_id: Optional[str]) -> tuple[str, tuple]:
    return (f"AND {alias}.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())


def _like(q: Optional[str]) -> Optional[str]:
    q = (q or "").strip()
    return f"%{q}%" if q else None


# ---------------------------------------------------------------- Modeling Store: run models

RUN_MODELS = """
WITH M AS (
    SELECT DESIGN_ID, RUN_ID, VERSION, STATUS, ORIGIN, DECISION, DESIGN_JSON, CREATED_BY, CREATED_AT, APPROVED_BY
      FROM MODELING.MODEL_DESIGN WHERE STATUS IN ('APPROVED', 'DRAFT')
   QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY VERSION DESC) = 1
), P AS (
    SELECT RUN_ID, STATUS, HEAD_BRANCH, PR_URL, PR_NUMBER FROM CODEGEN.GIT_PUBLICATION
   QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT M.DESIGN_ID, M.RUN_ID, R.RUN_NAME, R.CURRENT_STATE, R.DOMAIN_ID, D.DOMAIN_NAME, M.VERSION, M.STATUS, M.ORIGIN,
       M.DECISION, M.DESIGN_JSON, M.CREATED_BY, M.CREATED_AT::VARCHAR AS CREATED_AT, M.APPROVED_BY,
       P.STATUS AS PUBLICATION_STATUS, P.HEAD_BRANCH, P.PR_URL, P.PR_NUMBER
  FROM M
  JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = M.RUN_ID
  LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
  LEFT JOIN P ON P.RUN_ID = M.RUN_ID
 WHERE NOT COALESCE(R.IS_ARCHIVED, FALSE) {scope} {search}
 ORDER BY M.CREATED_AT DESC LIMIT 500"""


def _entities(design: Any) -> list[dict]:
    d = _json(design) if isinstance(design, str) else (design or {})
    found = (d.get("entities") or d.get("tables") or []) if isinstance(d, dict) else []
    out = []
    for e in found if isinstance(found, list) else []:
        if isinstance(e, dict):
            out.append({"name": e.get("name") or e.get("table") or e.get("entity"), "kind": e.get("kind") or e.get("type"),
                        "columns": len(e.get("columns") or e.get("attributes") or [])})
    return out


@router.get("/api/hub/models/runs")
def run_models(domain: Optional[str] = None, q: Optional[str] = None, db: Db = Depends(current_db)):
    did = _domain(db, domain)
    scope, params = _run_scope("R", did)
    like = _like(q)
    search = "AND (R.RUN_NAME ILIKE %s OR D.DOMAIN_NAME ILIKE %s)" if like else ""
    try:
        found = db.query(RUN_MODELS.format(scope=scope, search=search), params + ((like, like) if like else ()))
    except Exception:
        return {"models": [], "deployed": False}
    models = []
    for r in found:
        entities = _entities(r.pop("design_json", None))
        models.append({**r, "entities": entities, "entity_count": len(entities)})
    return {"models": models, "deployed": True}


# ---------------------------------------------------------------- Modeling Store: dbt generations

DBT_LIST = """
WITH G AS (
    SELECT * FROM CODEGEN.DBT_GENERATION_REGISTRY
   QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY GENERATION_VERSION DESC) = 1
), P AS (
    SELECT RUN_ID, GENERATION_ID, STATUS, HEAD_BRANCH, PR_URL, PR_NUMBER FROM CODEGEN.GIT_PUBLICATION
   QUALIFY ROW_NUMBER() OVER (PARTITION BY RUN_ID ORDER BY CREATED_AT DESC) = 1
), S AS (
    {sttm}
)
SELECT G.GENERATION_ID, G.RUN_ID, R.RUN_NAME, R.CURRENT_STATE, R.DOMAIN_ID, G.DOMAIN, G.TARGET_MODEL,
       G.GENERATION_VERSION, G.GENERATION_STATUS, G.FILES_GENERATED, G.PARENT_GENERATION_ID, G.CREATED_BY,
       G.CREATED_AT::VARCHAR AS CREATED_AT, P.STATUS AS PUBLICATION_STATUS, P.GENERATION_ID AS PUBLISHED_GENERATION_ID,
       P.HEAD_BRANCH, P.PR_URL, P.PR_NUMBER, COALESCE(S.CHANGED_AT > G.CREATED_AT, FALSE) AS STTM_CHANGED
  FROM G
  JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = G.RUN_ID
  LEFT JOIN P ON P.RUN_ID = G.RUN_ID
  LEFT JOIN S ON S.RUN_ID = G.RUN_ID
 WHERE NOT COALESCE(R.IS_ARCHIVED, FALSE) {scope} {search}
 ORDER BY G.CREATED_AT DESC LIMIT 500"""


STTM_EDITS = "SELECT RUN_ID, MAX(CREATED_AT) AS CHANGED_AT FROM GOVERNANCE.STTM_CATALOG WHERE ORIGIN = 'CATALOG_EDIT' GROUP BY RUN_ID"
NO_STTM_EDITS = "SELECT NULL::VARCHAR AS RUN_ID, NULL::TIMESTAMP_LTZ AS CHANGED_AT"


@router.get("/api/hub/dbt")
def dbt_generations(domain: Optional[str] = None, q: Optional[str] = None, db: Db = Depends(current_db)):
    did = _domain(db, domain)
    scope, params = _run_scope("R", did)
    like = _like(q)
    search = "AND (R.RUN_NAME ILIKE %s OR G.TARGET_MODEL ILIKE %s)" if like else ""
    args = params + ((like, like) if like else ())
    try:
        found = db.query(DBT_LIST.format(sttm=STTM_EDITS, scope=scope, search=search), args)
    except Exception:
        try:  # before V036 there is no STTM catalog, so no "STTM changed" flag
            found = db.query(DBT_LIST.format(sttm=NO_STTM_EDITS, scope=scope, search=search), args)
        except Exception:
            return {"generations": [], "deployed": False}
    return {"generations": found, "deployed": True}


def _generation(db: Db, generation_id: str) -> dict:
    found = db.query("""SELECT GENERATION_ID, RUN_ID, PARENT_GENERATION_ID, DOMAIN, TARGET_MODEL, STTM_ID, STTM_VERSION,
                               FILES_GENERATED, GENERATION_VERSION, GENERATION_STATUS, CREATED_BY,
                               CREATED_AT::VARCHAR AS CREATED_AT
                          FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE GENERATION_ID = %s""", (generation_id,))
    if not found:
        raise HTTPException(404, "dbt generation not found")
    return found[0]


def _published(db: Db, generation_id: str) -> bool:
    try:
        return bool(db.query("SELECT 1 AS X FROM CODEGEN.GIT_PUBLICATION WHERE GENERATION_ID = %s AND STATUS = 'PUBLISHED' "
                             "LIMIT 1", (generation_id,)))
    except Exception:
        return False


@router.get("/api/hub/dbt/{generation_id}")
def dbt_generation(generation_id: str, db: Db = Depends(current_db)):
    gen = _generation(db, generation_id)
    files = db.query("""SELECT ARTIFACT_ID, ARTIFACT_TYPE, FILE_PATH, CONTENT_SHA256, CONTENT
                          FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s ORDER BY FILE_PATH""", (generation_id,))
    versions = db.query("""SELECT GENERATION_ID, GENERATION_VERSION, GENERATION_STATUS, PARENT_GENERATION_ID, CREATED_BY,
                                  CREATED_AT::VARCHAR AS CREATED_AT
                             FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s
                            ORDER BY GENERATION_VERSION DESC LIMIT 50""", (gen["run_id"],))
    run = db.query("SELECT RUN_NAME, CURRENT_STATE, DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s", (gen["run_id"],))
    try:
        publication = db.query("""SELECT STATUS, HEAD_BRANCH, BASE_BRANCH, PR_URL, PR_NUMBER, COMMIT_SHA, GENERATION_ID,
                                         CREATED_AT::VARCHAR AS CREATED_AT
                                    FROM CODEGEN.GIT_PUBLICATION WHERE RUN_ID = %s ORDER BY CREATED_AT DESC LIMIT 1""",
                               (gen["run_id"],))
    except Exception:
        publication = []
    latest = bool(versions) and versions[0]["generation_id"] == generation_id
    published = _published(db, generation_id)
    return {"generation": gen, "run": run[0] if run else None, "files": files, "versions": versions,
            "publication": publication[0] if publication else None, "latest": latest, "published": published,
            # an approved or published generation is never changed in place: an edit makes the next version
            "edit_makes_version": published or str(gen.get("generation_status") or "").upper() == "APPROVED"}


class DbtFileEdit(BaseModel):
    file_path: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=2_000_000)
    base_sha: Optional[str] = Field(default=None, max_length=64)
    note: str = Field(min_length=3, max_length=2000)


def _new_version(db: Db, gen: dict) -> dict:
    """Copy a generation into the next version for its run (PARENT_GENERATION_ID links them); the old one is
    superseded. The run's publish step pushes the latest version."""
    new_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CODEGEN.DBT_GENERATION_REGISTRY (GENERATION_ID, RUN_ID, PARENT_GENERATION_ID, DOMAIN, TARGET_MODEL,
                         STTM_ID, STTM_VERSION, FILES_GENERATED, SKILL_VERSION, KNOWLEDGE_VERSION, MODEL_VERSION,
                         GENERATION_VERSION, GENERATION_STATUS, STAGE_PATH, CREATED_BY)
                  SELECT %s, RUN_ID, GENERATION_ID, DOMAIN, TARGET_MODEL, STTM_ID, STTM_VERSION, FILES_GENERATED,
                         SKILL_VERSION, KNOWLEDGE_VERSION, MODEL_VERSION, GENERATION_VERSION + 1, 'GENERATED', STAGE_PATH,
                         CURRENT_USER()
                    FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE GENERATION_ID = %s""", (new_id, gen["generation_id"]))
    db.execute("""INSERT INTO CODEGEN.GENERATED_ARTIFACT (ARTIFACT_ID, GENERATION_ID, RUN_ID, ARTIFACT_TYPE, FILE_PATH, CONTENT,
                         CONTENT_SHA256)
                  SELECT UUID_STRING(), %s, RUN_ID, ARTIFACT_TYPE, FILE_PATH, CONTENT, CONTENT_SHA256
                    FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s""", (new_id, gen["generation_id"]))
    db.execute("UPDATE CODEGEN.DBT_GENERATION_REGISTRY SET GENERATION_STATUS = 'SUPERSEDED' WHERE GENERATION_ID = %s",
               (gen["generation_id"],))
    return {"generation_id": new_id, "generation_version": int(gen["generation_version"]) + 1}


@router.post("/api/hub/dbt/{generation_id}/files")
def edit_dbt_file(generation_id: str, body: DbtFileEdit, db: Db = Depends(current_db)):
    """Applied after approval. Edits the run's latest generation; when it is approved or already published the edit
    becomes the next generation version instead, so what was reviewed or pushed stays as it was."""
    gen = _generation(db, generation_id)
    latest = db.query("SELECT GENERATION_ID FROM CODEGEN.DBT_GENERATION_REGISTRY WHERE RUN_ID = %s "
                      "ORDER BY GENERATION_VERSION DESC LIMIT 1", (gen["run_id"],))
    if latest and latest[0]["generation_id"] != generation_id:
        raise HTTPException(409, "A newer dbt generation exists for this run; open it and make the change there.")
    path = body.file_path.strip()
    current = db.query("SELECT CONTENT_SHA256 FROM CODEGEN.GENERATED_ARTIFACT WHERE GENERATION_ID = %s AND FILE_PATH = %s",
                       (generation_id, path))
    if not current:
        raise HTTPException(404, f"No generated file {path}")
    if body.base_sha and current[0]["content_sha256"] != body.base_sha:
        raise HTTPException(409, f"{path} changed since you opened it; reload and make the change again.")
    sha = hashlib.sha256(body.content.encode("utf-8")).hexdigest()
    if sha == current[0]["content_sha256"]:
        raise HTTPException(400, "The file is unchanged.")
    target = {"generation_id": generation_id, "generation_version": int(gen["generation_version"])}
    versioned = _published(db, generation_id) or str(gen.get("generation_status") or "").upper() == "APPROVED"
    try:
        if versioned:
            target = _new_version(db, gen)
        db.execute("UPDATE CODEGEN.GENERATED_ARTIFACT SET CONTENT = %s, CONTENT_SHA256 = %s WHERE GENERATION_ID = %s "
                   "AND FILE_PATH = %s", (body.content, sha, target["generation_id"], path))
        db.execute("""INSERT INTO CORE.WORKFLOW_EVENT (EVENT_ID, RUN_ID, FROM_STATE, TO_STATE, ACTOR_TYPE, ACTOR, REASON,
                                                       DETAILS, GRAPH_VERSION)
                      SELECT %s, RUN_ID, CURRENT_STATE, CURRENT_STATE, 'HUMAN', CURRENT_USER(), %s, PARSE_JSON(%s), GRAPH_VERSION
                        FROM CORE.WORKFLOW_RUN WHERE RUN_ID = %s""",
                   (str(uuid.uuid4()), f"dbt {path} edited in the Modeling Store (v{target['generation_version']}): "
                                       f"{body.note.strip()}"[:2000],
                    json.dumps({"generation_id": target["generation_id"], "file_path": path, "new_version": versioned}),
                    gen["run_id"]))
    except HTTPException:
        raise
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {**target, "file_path": path, "content_sha256": sha, "new_version": versioned, "run_id": gen["run_id"]}


# ---------------------------------------------------------------- Data Quality Store: run checks

RUN_CHECKS = """
WITH C AS (
    SELECT E.EXPECTATION_ID, E.RUN_ID, E.DOMAIN_ID, E.TARGET_TABLE, E.TARGET_COLUMN, E.CHECK_TYPE, E.CHECK_DEFINITION,
           E.SEVERITY, E.ORIGIN, E.CLIENT_REQUIREMENT, E.STATUS, E.VERSION, E.REVIEWED_BY
      FROM CONTRACT.SODA_EXPECTATION_REGISTRY E
     WHERE E.IS_CURRENT {status} {run} {table}
), R AS (
    SELECT EXPECTATION_ID, RESULT_ID, OUTCOME, MEASURED, FAILED_ROWS, DETAIL, SAMPLE_ROWS, SQL_TEXT,
           CREATED_AT::VARCHAR AS CHECKED_AT
      FROM QUALITY.CHECK_RESULT WHERE EXPECTATION_ID IN (SELECT EXPECTATION_ID FROM C)
   QUALIFY ROW_NUMBER() OVER (PARTITION BY EXPECTATION_ID ORDER BY CREATED_AT DESC) = 1
)
SELECT C.*, W.RUN_NAME, W.CURRENT_STATE, D.DOMAIN_NAME, R.RESULT_ID, R.OUTCOME, R.MEASURED, R.FAILED_ROWS, R.DETAIL, R.SAMPLE_ROWS,
       R.SQL_TEXT, R.CHECKED_AT
  FROM C
  JOIN CORE.WORKFLOW_RUN W ON W.RUN_ID = C.RUN_ID
  LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = COALESCE(C.DOMAIN_ID, W.DOMAIN_ID)
  LEFT JOIN R ON R.EXPECTATION_ID = C.EXPECTATION_ID
 WHERE NOT COALESCE(W.IS_ARCHIVED, FALSE) {scope}
 ORDER BY W.RUN_NAME, C.TARGET_TABLE, C.TARGET_COLUMN NULLS FIRST, C.CHECK_TYPE LIMIT 3000"""


@router.get("/api/dq/run-checks")
def run_checks(domain: Optional[str] = None, status: Optional[str] = None, run: Optional[str] = None,
               table: Optional[str] = None, db: Db = Depends(current_db)):
    from services.soda.expectations import render_check

    did = _domain(db, domain)
    status = (status or "").upper()
    if status and status not in ("PROPOSED", "APPROVED", "REJECTED"):
        raise HTTPException(400, "status must be PROPOSED, APPROVED or REJECTED")
    params: tuple = ()
    sql = RUN_CHECKS.format(status="AND E.STATUS = %s" if status else "", run="AND E.RUN_ID = %s" if run else "",
                            table="AND E.TARGET_TABLE ILIKE %s" if table else "",
                            scope="AND COALESCE(C.DOMAIN_ID, W.DOMAIN_ID) = %s" if did else "")
    params += (status,) if status else ()
    params += (run,) if run else ()
    params += (f"%{table}%",) if table else ()
    params += (did,) if did else ()
    try:
        found = db.query(sql, params)
    except Exception:
        return {"checks": [], "runs": [], "deployed": False}
    runs: dict[str, dict] = {}
    for c in found:
        definition = _json(c.get("check_definition")) if isinstance(c.get("check_definition"), str) else (c.get("check_definition") or {})
        c["definition"] = {k: v for k, v in (definition or {}).items() if k not in ("evidence", "backtest")}
        c["evidence"] = (definition or {}).get("evidence")
        c["backtest"] = (definition or {}).get("backtest")
        c.pop("check_definition", None)
        c["sample_rows"] = _json(c.get("sample_rows")) if isinstance(c.get("sample_rows"), str) else c.get("sample_rows")
        try:
            c["sodacl"] = render_check({"target_table": c["target_table"], "target_column": c.get("target_column"),
                                        "check_type": c.get("check_type"), "definition": c["definition"],
                                        "severity": c.get("severity"), "requirement": c.get("client_requirement")})
        except Exception:
            c["sodacl"] = None
        r = runs.setdefault(c["run_id"], {"run_id": c["run_id"], "run_name": c.get("run_name"), "domain_name": c.get("domain_name"),
                                          "current_state": c.get("current_state"), "checks": 0, "proposed": 0, "approved": 0,
                                          "rejected": 0, "failing": 0})
        r["checks"] += 1
        key = str(c.get("status") or "").lower()
        if key in ("proposed", "approved", "rejected"):
            r[key] += 1
        if c.get("outcome") in ("FAIL", "ERROR"):
            r["failing"] += 1
    return {"checks": found, "runs": sorted(runs.values(), key=lambda r: str(r.get("run_name") or "")), "deployed": True}


@router.get("/api/dq/run-checks/coverage")
def run_check_coverage(run: str, db: Db = Depends(current_db)):
    """A run's STTM target columns with no current, non-rejected check."""
    try:
        columns = db.query("""SELECT L.TARGET_COLUMN, L.MAPPING_TYPE FROM CONTRACT.STTM_LINE L
                               WHERE L.STTM_ID = (SELECT STTM_ID FROM CONTRACT.STTM_REGISTRY WHERE RUN_ID = %s
                                                   ORDER BY STTM_VERSION DESC LIMIT 1)
                               ORDER BY L.TARGET_COLUMN""", (run,))
        checked = {str(r["target_column"]).upper() for r in db.query(
            """SELECT DISTINCT TARGET_COLUMN FROM CONTRACT.SODA_EXPECTATION_REGISTRY
                WHERE RUN_ID = %s AND IS_CURRENT AND STATUS <> 'REJECTED' AND TARGET_COLUMN IS NOT NULL""", (run,))}
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    missing = [c for c in columns if str(c["target_column"]).upper() not in checked]
    total = len(columns)
    return {"run_id": run, "columns": total, "covered": total - len(missing), "missing": missing,
            "coverage": round(100 * (total - len(missing)) / total, 1) if total else None}


class RunCheckDecision(BaseModel):
    expectation_id: str = Field(min_length=1, max_length=64)
    decision: Literal["APPROVED", "REJECTED", "MODIFIED"]
    justification: Optional[str] = Field(default=None, max_length=4000)
    requirement: Optional[str] = Field(default=None, max_length=4000)
    severity: Optional[Literal["FAIL", "WARN"]] = None
    definition: Optional[dict] = None


class RunCheckDecisions(BaseModel):
    run_id: str = Field(min_length=1, max_length=36)
    decisions: list[RunCheckDecision] = Field(min_length=1, max_length=200)


@router.post("/api/dq/run-checks/decisions")
def decide_run_checks(body: RunCheckDecisions, db: Db = Depends(current_db)):
    """Applied after approval, through the run's own decision logic (validation, stage rules, audit)."""
    from app.main import SodaDecisions, save_soda

    return save_soda(body.run_id, SodaDecisions(decisions=[d.model_dump(exclude_none=True) for d in body.decisions]), db)


# ---------------------------------------------------------------- QA Store: run tests

RUN_TESTS = """
WITH T AS (
    SELECT T.TEST_ID, T.RUN_ID, T.DOMAIN_ID, T.TARGET_TABLE_ID, T.SCOPE, T.TARGET_FQN, T.CATEGORY, T.TITLE, T.OBJECTIVE,
           T.SQL_TEXT, T.EXPECTED, T.SEVERITY, T.TARGET_COLUMN, T.ORIGIN, T.CREATED_BY, T.CREATED_AT::VARCHAR AS CREATED_AT
      FROM CONTRACT.QA_TEST_CASE T
     WHERE NOT COALESCE(T.IS_DELETED, FALSE) {run} {search}
), H AS (
    SELECT TEST_ID, RESULT_ID, OUTCOME, ROWS_RETURNED, DETAIL, SAMPLE_ROWS, CREATED_AT::VARCHAR AS RAN_AT,
           ROW_NUMBER() OVER (PARTITION BY TEST_ID ORDER BY CREATED_AT DESC) AS RN
      FROM QUALITY.QA_RESULT WHERE TEST_ID IN (SELECT TEST_ID FROM T)
   QUALIFY RN <= 10
), HA AS (
    SELECT TEST_ID, ARRAY_AGG(OBJECT_CONSTRUCT('outcome', OUTCOME, 'ran_at', RAN_AT)) WITHIN GROUP (ORDER BY RAN_AT) AS HISTORY
      FROM H GROUP BY TEST_ID
)
SELECT T.*, W.RUN_NAME, D.DOMAIN_NAME, HA.HISTORY, L.RESULT_ID, L.OUTCOME, L.ROWS_RETURNED, L.DETAIL, L.SAMPLE_ROWS, L.RAN_AT
  FROM T
  LEFT JOIN HA ON HA.TEST_ID = T.TEST_ID
  LEFT JOIN CORE.WORKFLOW_RUN W ON W.RUN_ID = T.RUN_ID
  LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = COALESCE(T.DOMAIN_ID, W.DOMAIN_ID)
  LEFT JOIN H L ON L.TEST_ID = T.TEST_ID AND L.RN = 1
 WHERE NOT COALESCE(W.IS_ARCHIVED, FALSE) {scope}
 ORDER BY T.CREATED_AT DESC LIMIT 2000"""


@router.get("/api/qa/run-tests")
def run_tests(domain: Optional[str] = None, run: Optional[str] = None, q: Optional[str] = None, db: Db = Depends(current_db)):
    did = _domain(db, domain)
    like = _like(q)
    sql = RUN_TESTS.format(run="AND T.RUN_ID = %s" if run else "",
                           search="AND (T.TITLE ILIKE %s OR T.TARGET_FQN ILIKE %s OR T.SQL_TEXT ILIKE %s)" if like else "",
                           scope="AND COALESCE(T.DOMAIN_ID, W.DOMAIN_ID) = %s" if did else "")
    params = ((run,) if run else ()) + ((like, like, like) if like else ()) + ((did,) if did else ())
    try:
        found = db.query(sql, params)
    except Exception:
        return {"tests": [], "deployed": False}
    for t in found:
        for key in ("history", "sample_rows"):
            if isinstance(t.get(key), str):
                t[key] = _json(t[key])
        t["history"] = t.get("history") or []
    executed = [t for t in found if t.get("outcome")]
    passed = sum(1 for t in executed if t["outcome"] == "PASS")
    return {"tests": found, "deployed": True,
            "summary": {"tests": len(found), "executed": len(executed), "passed": passed,
                        "failed": sum(1 for t in executed if t["outcome"] in ("FAIL", "ERROR")),
                        "pass_rate": round(100 * passed / len(executed), 1) if executed else None}}


class PromoteTests(BaseModel):
    test_ids: list[str] = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=128)
    pack_id: Optional[str] = Field(default=None, max_length=36)
    name: Optional[str] = Field(default=None, max_length=128)
    note: Optional[str] = Field(default=None, max_length=2000)


def _query_name(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (title or "test").lower()).strip("_")[:100] or "test"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def pack_queries(tests: list[dict], taken: set[str]) -> list[dict]:
    """Saved run and table tests as pack queries. A test passes when its query returns no rows, as pack queries do."""
    severity = {"CRITICAL": "ERROR", "HIGH": "ERROR", "ERROR": "ERROR"}
    return [{"query_name": _query_name(t.get("title") or "", taken), "sql_expression": t["sql_text"],
             "description": t.get("objective") or t.get("title"),
             "severity": severity.get(str(t.get("severity") or "").upper(), "WARN"),
             "expectation": t.get("expected") or "0 rows",
             "tags": [str(t["category"]).lower()] if t.get("category") else []} for t in tests]


@router.post("/api/qa/run-tests/promote")
def promote_tests(body: PromoteTests, db: Db = Depends(current_db)):
    """Copy tests into a QA pack draft: a new pack, or the next draft of an existing one. Nothing goes live until the
    pack is submitted and approved."""
    from app.qa_api import PackIn, _call, _svc

    did = _domain(db, body.domain)
    if not did:
        raise HTTPException(400, "Choose the domain the pack belongs to.")
    ids = list(dict.fromkeys(body.test_ids))
    found = db.query(f"""SELECT TEST_ID, TITLE, OBJECTIVE, SQL_TEXT, EXPECTED, SEVERITY, CATEGORY
                           FROM CONTRACT.QA_TEST_CASE
                          WHERE NOT COALESCE(IS_DELETED, FALSE) AND TEST_ID IN ({", ".join(["%s"] * len(ids))})""", tuple(ids))
    if len(found) != len(ids):
        raise HTTPException(404, "Some tests were not found or are deleted; reload and choose again.")
    packs = _svc("packs")
    if body.pack_id:
        current = _call(db, packs.get_pack, body.pack_id, None)
        pack = current["pack"]
        if pack.get("domain_id") and pack["domain_id"] != did:
            raise HTTPException(400, "That pack belongs to another domain.")
        existing = [{k: q[k] for k in ("query_name", "sql_expression", "description", "severity", "expectation", "tags")
                     if q.get(k) is not None} for q in current.get("queries") or []]
        known_sql = {re.sub(r"\s+", " ", q["sql_expression"] or "").strip().upper() for q in existing}
        fresh = [t for t in found if re.sub(r"\s+", " ", t["sql_text"] or "").strip().upper() not in known_sql]
        if not fresh:
            raise HTTPException(400, "Those tests are already in the pack.")
        payload = PackIn(name=pack["name"], description=pack.get("description"), tags=pack.get("tags") or [],
                         queries=existing + pack_queries(fresh, {q["query_name"] for q in existing}),
                         change_note=body.note or f"Added {len(fresh)} test{'s' if len(fresh) != 1 else ''} from runs",
                         base_version=pack.get("version"))
        result = _call(db, packs.save_draft, body.pack_id, json.dumps(payload.model_dump()))
        return {"pack_id": body.pack_id, "added": len(fresh), "skipped": len(found) - len(fresh), "result": result}
    if not (body.name or "").strip():
        raise HTTPException(400, "Name the new pack, or choose an existing one.")
    payload = PackIn(name=body.name.strip(), description=None, tags=["from-runs"], queries=pack_queries(found, set()),
                     change_note=body.note or "Created from run tests")
    result = _call(db, packs.create_pack, did, json.dumps(payload.model_dump()))
    return {"pack_id": (result or {}).get("pack_id"), "added": len(found), "skipped": 0, "result": result}
