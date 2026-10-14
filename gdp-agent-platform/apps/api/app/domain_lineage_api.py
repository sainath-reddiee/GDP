"""How a domain is built, and how complete it is (the domain page's Lineage tab and Overview card).

GET /api/domains/{id}/provenance: the sources its knowledge came from (services/knowledge/provenance.py), the build
timeline, and the coverage and trust score with the Copilot requests that close each gap (coverage.py). Read only.
Every source query fails soft: a schema that is not deployed only drops its part.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Set

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
    return {"domain": detail["domain"],
            "provenance": provenance.build(detail, rows, usage, repos, versions),
            "coverage": coverage.score(name, detail.get("targets") or [], rows, sttm, dq, qa_sql, usage)}
