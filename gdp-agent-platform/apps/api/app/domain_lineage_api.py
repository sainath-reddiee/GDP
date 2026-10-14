"""How a domain is built, and how complete it is (the domain page's Lineage tab and Overview card).

GET /api/domains/{id}/data-model: the domain's data model (approved, or the open draft and its changes) for the diagram.
GET /api/domains/{id}/provenance: the sources its knowledge came from (services/knowledge/provenance.py), the build
timeline, and the coverage and trust score with the Copilot requests that close each gap (coverage.py). Read only.
Every source query fails soft: a schema that is not deployed only drops its part.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends

from app.db import Db
from app.main import current_db, domain_detail
from services.knowledge import coverage, provenance

router = APIRouter()


def _soft(db: Db, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


def _arr(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(v) for v in value or []]


def _approved_entities(db: Db, domain_model: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """The approved model's tables (retired ones left out), or None without an approved version."""
    if not domain_model or not domain_model.get("approved_version"):
        return None
    from services.common.sql import variant
    from services.modeling import spec_v2

    found = _soft(db, "SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = %s AND VERSION = %s",
                  (domain_model["model_id"], int(domain_model["approved_version"])))
    if not found:
        return None
    spec = spec_v2.normalize_v2(variant(found[0]["spec"]) or {}, domain_model["model_id"])
    return spec_v2.active(spec)["design"]["entities"]


@router.get("/api/domains/{domain_id}/provenance")
def domain_provenance(domain_id: str, db: Db = Depends(current_db)):
    detail = domain_detail(domain_id, db)   # 404 for an unknown domain, as the domain page
    name = str(detail["domain"]["domain_name"])
    rows = _soft(db, """SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, SOURCE_REFERENCE, ORIGIN, STATUS, VERIFIED_BY,
                               CREATED_BY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT
                          FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                         WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS IN ('ACTIVE', 'DRAFT')""", (domain_id,))
    usage = {r["knowledge_id"]: int(r["n"]) for r in _soft(db, """
        SELECT U.KNOWLEDGE_ID, COUNT(*) AS N FROM KNOWLEDGE.KNOWLEDGE_USAGE U
          JOIN KNOWLEDGE.DOMAIN_KNOWLEDGE K ON K.KNOWLEDGE_ID = U.KNOWLEDGE_ID
         WHERE K.DOMAIN_ID = %s AND U.USED_AT >= DATEADD(DAY, -90, CURRENT_TIMESTAMP()) GROUP BY 1""", (domain_id,))}
    tables = sorted({str(t.get("target_table") or "").upper() for t in detail.get("targets") or [] if t.get("target_table")})
    repos = []
    if tables:   # code that names the domain's target tables, per connected repository serving the domain
        for r in _soft(db, f"""SELECT R.NAME, R.DOMAIN_IDS, COUNT(DISTINCT C.CHUNK_ID) AS MATCHED
                                 FROM CODE.REPO R JOIN CODE.CODE_CHUNK C ON C.REPO_ID = R.REPO_ID
                                WHERE R.ENABLED AND UPPER(C.NAME) IN ({', '.join(['%s'] * len(tables))})
                                GROUP BY 1, 2""", tuple(tables)):
            served = _arr(r.get("domain_ids"))
            if not served or domain_id in served:
                repos.append({"name": r["name"], "matched": int(r["matched"] or 0)})
    versions = _soft(db, """SELECT VERSION, CHANGE_KIND, CHANGE_NOTE, CREATED_BY,
                                   TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT
                              FROM KNOWLEDGE.DOMAIN_VERSION WHERE DOMAIN_ID = %s ORDER BY VERSION DESC LIMIT 20""", (domain_id,))
    fqns = [".".join(str(t.get(k) or "") for k in ("target_database", "target_schema", "target_table")).upper()
            for t in detail.get("targets") or []]
    sttm: Set[str] = set()
    dq: Set[str] = set()
    if fqns:
        marks = ", ".join(["%s"] * len(fqns))
        sttm = {str(r["t"]).upper() for r in _soft(db, f"SELECT DISTINCT UPPER(TARGET_ENTITY) AS T FROM GOVERNANCE.STTM_CATALOG "
                                                         f"WHERE UPPER(TARGET_ENTITY) IN ({marks})", tuple(fqns))}
        dq = {str(r["t"]).upper() for r in _soft(db, f"SELECT DISTINCT UPPER(TABLE_NAME) AS T FROM GOVERNANCE.DQ_CHECKS_CATALOG "
                                                       f"WHERE STATUS = 'ACTIVE' AND UPPER(TABLE_NAME) IN ({marks})", tuple(fqns))}
    qa_sql = [r["sql_expression"] for r in _soft(db, """SELECT Q.SQL_EXPRESSION FROM GOVERNANCE.DOMAIN_QA_QUERIES Q
                                                         JOIN GOVERNANCE.DOMAIN_QA_PACKS P ON P.PACK_ID = Q.PACK_ID
                                                        WHERE P.DOMAIN_ID = %s AND P.STATUS NOT IN ('RETIRED', 'SUPERSEDED')
                                                        LIMIT 2000""", (domain_id,))]
    reg = next(iter(_soft(db, "SELECT CONFIG:standard::STRING AS STANDARD, CONFIG:mdm:role::STRING AS MDM_ROLE "
                              "FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))), {})
    standard = reg.get("standard")
    bindings = _soft(db, "SELECT STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD FROM KNOWLEDGE.SKILL_STAGE_BINDING")
    own_skills = _soft(db, "SELECT STAGE, SKILL_NAME, MODE, REPLACES, POSITION, ENABLED FROM KNOWLEDGE.DOMAIN_SKILL "
                           "WHERE DOMAIN_ID = %s", (domain_id,))
    from services.knowledge.skills import mdm_rows

    own_skills = list(own_skills) + mdm_rows(reg.get("mdm_role"), str(standard or "GDP").upper())   # from the MDM contract
    loaded = _soft(db, """SELECT C.INPUT_JSON:skill::STRING AS SKILL, C.INPUT_JSON:version::STRING AS VERSION, COUNT(*) AS N,
                                 COUNT(DISTINCT C.RUN_ID) AS RUNS, TO_VARCHAR(MAX(C.CREATED_AT), 'YYYY-MM-DD HH24:MI') AS LAST
                            FROM AUDIT.AGENT_TOOL_CALL C JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = C.RUN_ID
                           WHERE C.TOOL_NAME = 'load_skill' AND R.DOMAIN_ID = %s GROUP BY 1, 2""", (domain_id,))
    repo_rows = [r for r in _soft(db, """SELECT REPO_ID, NAME, GIT_URL, BRANCH, LAST_COMMIT, DOMAIN_IDS,
                                                TO_VARCHAR(LAST_INDEXED_AT, 'YYYY-MM-DD HH24:MI') AS LAST_INDEXED_AT
                                           FROM CODE.REPO WHERE ENABLED""")
                 if not _arr(r.get("domain_ids")) or domain_id in _arr(r.get("domain_ids"))]
    files = _soft(db, f"""SELECT DISTINCT REPO_ID, PATH, NAME, KIND FROM CODE.CODE_CHUNK
                           WHERE UPPER(NAME) IN ({', '.join(['%s'] * len(tables))}) ORDER BY PATH LIMIT 200""",
                  tuple(tables)) if tables else []
    runs = _soft(db, """SELECT K.SOURCE_RUN_ID AS RUN_ID, ANY_VALUE(R.RUN_NAME) AS RUN_NAME, ANY_VALUE(R.CURRENT_STATE) AS CURRENT_STATE,
                               COUNT(*) AS ITEMS, ARRAY_AGG(DISTINCT K.KNOWLEDGE_TYPE) AS KINDS
                          FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = K.SOURCE_RUN_ID
                         WHERE K.DOMAIN_ID = %s AND K.IS_CURRENT AND K.SOURCE_RUN_ID IS NOT NULL
                         GROUP BY 1 ORDER BY ITEMS DESC LIMIT 20""", (domain_id,))
    for r in runs:
        r["kinds"] = _arr(r.get("kinds"))
    manifest = provenance.manifest(detail, standard=standard, bindings=bindings, loaded=loaded, repos=repo_rows,
                                   files=files, runs=runs, rows=rows, domain_skills=own_skills)
    documents = [{"title": d.get("title") or d.get("file_name"), "url": d.get("url"), "current": True,
                  "kind": "confluence" if d.get("kind") != "UPLOAD" else ("excel" if str(d.get("file_name") or "").lower().endswith((".xlsx", ".xls", ".csv")) else "document"),
                  "kind_label": "Confluence" if d.get("kind") != "UPLOAD" else "Uploaded"}
                 for d in _soft(db, """SELECT KIND, TITLE, URL, FILE_NAME FROM CONFLUENCE.SOURCE
                                        WHERE DOMAIN_ID = %s AND STATUS = 'ACTIVE' ORDER BY CREATED_AT""", (domain_id,))]
    domain_model = next(iter(_soft(db, """SELECT MODEL_ID, APPROVED_VERSION, OPEN_VERSION FROM GOVERNANCE.DOMAIN_MODEL
                                           WHERE DOMAIN_ID = %s""", (domain_id,))), None)
    table_models = _soft(db, """SELECT TABLE_MODEL_ID, TABLE_NAME, CURRENT_VERSION FROM GOVERNANCE.TABLE_MODEL
                                 WHERE DOMAIN_ID = %s AND COALESCE(MODEL_LAYER, 'SILVER') = 'SILVER' ORDER BY TABLE_NAME LIMIT 100""",
                         (domain_id,))
    sttm_rows = _soft(db, f"""SELECT CATALOG_ID, VERSION, TARGET_ENTITY, STATUS FROM GOVERNANCE.STTM_CATALOG
                               WHERE UPPER(TARGET_ENTITY) IN ({', '.join(['%s'] * len(fqns))})
                               QUALIFY ROW_NUMBER() OVER (PARTITION BY CATALOG_ID ORDER BY VERSION DESC) = 1""",
                      tuple(fqns)) if fqns else []
    ingestion = _soft(db, """SELECT LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE, IS_ACTIVE
                               FROM PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG WHERE DOMAIN_ID = %s LIMIT 200""", (domain_id,))
    jira = _soft(db, """SELECT DISTINCT L.ISSUE_KEY FROM JIRA.ISSUE_LINK L
                         WHERE NOT COALESCE(L.IS_DELETED, FALSE)
                           AND (L.DOMAIN_ID = %s OR L.RUN_ID IN (SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s))
                         LIMIT 100""", (domain_id, domain_id))
    proposed = next(iter(_soft(db, "SELECT COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND STATUS = 'PROPOSED'",
                               (domain_id,))), {}).get("n") or 0
    stale = next(iter(_soft(db, """SELECT COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                    WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'ACTIVE'
                                      AND (REVIEW_DUE < CURRENT_DATE() OR VERIFIED_BY IS NULL)""", (domain_id,))), {}).get("n") or 0
    members = _soft(db, "SELECT USER_NAME, ROLE FROM KNOWLEDGE.DOMAIN_MEMBER WHERE DOMAIN_ID = %s", (domain_id,))
    example_tables = [r["t"] for r in _soft(db, """SELECT DISTINCT UPPER(CONTENT_JSON:target_table::STRING) AS T
                                                    FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                                   WHERE DOMAIN_ID = %s AND KNOWLEDGE_TYPE = 'EXAMPLE' AND IS_CURRENT
                                                     AND STATUS = 'ACTIVE'""", (domain_id,)) if r.get("t")]
    kinds: Dict[str, int] = {}
    for r in rows:
        if r.get("status") == "ACTIVE":
            kinds[r["knowledge_type"]] = kinds.get(r["knowledge_type"], 0) + 1
    ready = provenance.readiness(detail, members=members, entities=_approved_entities(db, domain_model), knowledge_types=kinds,
                                 verified=sum(1 for r in rows if r.get("verified_by")), total=len(rows),
                                 example_tables=example_tables, dq_tables=len(dq), documents=len(documents))
    return {"domain": detail["domain"], "readiness": ready,
            "manifest": manifest,
            "flow": provenance.flow(detail, manifest, rows, domain_model=domain_model, table_models=table_models, sttm=sttm_rows,
                                    ingestion=ingestion, jira=jira, documents=documents, proposed=int(proposed), stale=int(stale)),
            "needs_verification": provenance.needs_verification(rows, usage), "proposed": int(proposed),
            "provenance": provenance.build(detail, rows, usage, repos, versions),
            "coverage": coverage.score(name, detail.get("targets") or [], rows, sttm, dq, qa_sql, usage)}


@router.get("/api/domains/{domain_id}/contract")
def domain_contract(domain_id: str, db: Db = Depends(current_db)):
    """The domain's contract as the platform reads it: the Markdown file bundled in the dbt skill (the version in use),
    and what is extracted from it (hub, tables, columns, business keys, required mapping keys)."""
    from services.knowledge import contracts, usage as skill_usage

    detail = domain_detail(domain_id, db)
    path = str(detail.get("contract") or "")
    if not path:
        return {"path": None, "text": "", "detail": "This domain has no contract; it was created in the app."}
    parts = path.split("/")
    rel = "/".join(parts[4:]) if len(parts) > 4 else parts[-1]          # path inside the skill folder
    skill = next(iter(_soft(db, """SELECT SKILL_NAME, VERSION, REVISION, CONTENT FROM KNOWLEDGE.SKILL_REGISTRY
                                    WHERE UPPER(SKILL_NAME) = %s AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1""",
                            (skill_usage.DBT_SKILL,))), None)
    text = skill_usage.skill_file(skill.get("content") or "", rel) if skill else ""
    where = "skill registry"
    if not text:   # before the skill is seeded: the file shipped with the platform
        from pathlib import Path as _P
        f = _P(__file__).resolve().parents[3] / path
        if f.is_file():
            text, where = f.read_text(encoding="utf-8"), "repository file"
    parsed = contracts.parse_contract(text) if text else {}
    targets = [{"table": t.get("table"), "columns": len(t.get("columns") or []), "hkey_columns": t.get("hkey_columns") or [],
                "reference_ctes": sorted((t.get("reference_ctes") or {}).keys())} for t in parsed.get("targets") or []]
    return {"path": path, "file": parts[-1], "format": "Markdown (.md)", "where": where,
            "skill": {"name": skill.get("skill_name"), "version": skill.get("version"), "revision": skill.get("revision")} if skill else None,
            "text": text, "chars": len(text),
            "extracted": {"database": parsed.get("database"), "schema": parsed.get("schema"), "hub": parsed.get("hub"),
                          "targets": targets, "required": parsed.get("required_mapping") or []}}


@router.get("/api/domains/{domain_id}/data-model")
def domain_data_model(domain_id: str, view: str = "approved", db: Db = Depends(current_db)):
    """The domain's data model for the Lineage tab: the approved version, or the open draft with what it changes.
    Each table carries what it already has downstream (active knowledge items, STTM, a table model)."""
    from services.common.sql import variant
    from services.modeling import diagram, spec_v2

    domain_detail(domain_id, db)
    model = next(iter(_soft(db, """SELECT MODEL_ID, APPROVED_VERSION, OPEN_VERSION, DIAGRAM_LAYOUT FROM GOVERNANCE.DOMAIN_MODEL
                                    WHERE DOMAIN_ID = %s""", (domain_id,))), None)
    if not model:
        return {"model": None}
    model_id, approved_v, open_v = model["model_id"], model.get("approved_version"), model.get("open_version")
    versions = {int(r["version"]): r for r in _soft(db, """SELECT VERSION, STATUS, SPEC, APPROVED_BY,
                                                              TO_VARCHAR(APPROVED_AT, 'YYYY-MM-DD HH24:MI') AS APPROVED_AT,
                                                              CREATED_BY, TO_VARCHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI') AS UPDATED_AT
                                                         FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = %s
                                                          AND VERSION IN (%s, %s)""",
                                                    (model_id, int(approved_v or 0), int(open_v or 0)))}
    spec_of = lambda v: spec_v2.normalize_v2(variant(versions[v]["spec"]) or {}, model_id) if v in versions else None  # noqa: E731
    approved = spec_of(int(approved_v)) if approved_v else None
    draft = spec_of(int(open_v)) if open_v else None
    shown_v = int(open_v) if view == "draft" and draft else int(approved_v) if approved else int(open_v) if draft else None
    if shown_v is None:
        return {"model": {"model_id": model_id, "approved_version": approved_v, "open_version": open_v}, "diagram": None}
    shown = draft if shown_v == open_v else approved
    knowledge = {r["t"]: r["n"] for r in _soft(db, """SELECT UPPER(CONTENT_JSON:target_table::STRING) AS T, COUNT(*) AS N
                                                       FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                                      WHERE DOMAIN_ID = %s AND IS_CURRENT AND STATUS = 'ACTIVE'
                                                        AND CONTENT_JSON:target_table IS NOT NULL GROUP BY 1""", (domain_id,))
                 if r.get("t")}
    sttm = [r["target_entity"] for r in _soft(db, "SELECT DISTINCT TARGET_ENTITY FROM GOVERNANCE.STTM_CATALOG WHERE DOMAIN_ID = %s",
                                              (domain_id,))]
    table_models = [r["table_name"] for r in _soft(db, "SELECT TABLE_NAME FROM GOVERNANCE.TABLE_MODEL WHERE DOMAIN_ID = %s",
                                                   (domain_id,))]
    meta = versions[shown_v]
    out = diagram.build(shown, model_id=model_id, layout=variant(model.get("diagram_layout")) or {},
                        approved=approved if shown is draft and approved else None,
                        knowledge=knowledge, sttm=sttm, table_models=table_models)
    out.update(version=shown_v, status=meta.get("status"), approved_by=meta.get("approved_by"),
               approved_at=meta.get("approved_at"), updated_at=meta.get("updated_at"),
               platform=(shown.get("physical_defaults") or {}).get("platform") or "SNOWFLAKE",
               location={k: (shown.get("physical_defaults") or {}).get(k) for k in ("database", "schema")})
    return {"model": {"model_id": model_id, "approved_version": approved_v, "open_version": open_v}, "diagram": out}
