"""Knowledge hub: what the platform knows, how it learned it, what is waiting for review, and every version.

Items keep one LINEAGE_ID across versions. Automatic writers (services/knowledge/writer.py) either make a new version
current or, under the review policy, leave it PROPOSED for a steward. People can verify an item (with a review date),
roll it back to any earlier version, and see which runs and stages used it.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import (_DOMAIN_VOCAB, _KNOWLEDGE_COLUMNS, _config, _knowledge_item, _new_knowledge_version, _put_config,
                      _shape_knowledge, current_db)
from services.knowledge.skills import compact
from services.knowledge.writer import DEFAULT_POLICY

router = APIRouter()

FROM = "FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID"
FEED_COLS = """K.KNOWLEDGE_ID, K.LINEAGE_ID, K.TITLE, K.KNOWLEDGE_TYPE, K.ORIGIN, K.SOURCE_RUN_ID, R.RUN_NAME, K.VERSION,
               K.STATUS, K.IS_CURRENT, K.CREATED_BY, K.CREATED_AT::VARCHAR AS CREATED_AT, D.DOMAIN_NAME, K.CHANGE_NOTE,
               K.SOURCE_REFERENCE"""
STALE_DAYS = 180


def _changed() -> None:
    _DOMAIN_VOCAB.update(at=0.0, domains=[])


@router.get("/api/knowledge/overview")
def overview(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Headline numbers, the mix by type, origin and domain, and the newest things learned."""
    scope, params = ("AND K.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    counts = db.query(f"""SELECT K.KNOWLEDGE_TYPE, K.ORIGIN, D.DOMAIN_NAME, K.STATUS, COUNT(*) AS N
                            {FROM} WHERE K.IS_CURRENT {scope} GROUP BY 1, 2, 3, 4""", params)
    totals = db.query(f"""SELECT
            COUNT_IF(K.IS_CURRENT AND K.STATUS = 'ACTIVE') AS ACTIVE,
            COUNT_IF(K.STATUS = 'PROPOSED') AS INBOX,
            COUNT_IF(K.CREATED_AT >= DATEADD(DAY, -7, CURRENT_TIMESTAMP()) AND COALESCE(K.ORIGIN, 'USER') NOT IN ('USER', 'SEED', 'PACK_IMPORT')) AS LEARNED_7D,
            COUNT_IF(K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND (K.REVIEW_DUE < CURRENT_DATE()
                     OR (K.VERIFIED_AT IS NULL AND COALESCE(K.UPDATED_AT, K.CREATED_AT) < DATEADD(DAY, -{STALE_DAYS}, CURRENT_TIMESTAMP())))) AS STALE,
            COUNT_IF(K.IS_CURRENT AND K.VERIFIED_AT IS NOT NULL) AS VERIFIED
          {FROM} WHERE TRUE {scope}""", params)[0]
    try:
        used = db.query(f"""SELECT COUNT(DISTINCT U.LINEAGE_ID) AS USED, COUNT(*) AS USES FROM KNOWLEDGE.KNOWLEDGE_USAGE U
                              JOIN KNOWLEDGE.DOMAIN_KNOWLEDGE K ON K.KNOWLEDGE_ID = U.KNOWLEDGE_ID
                             WHERE U.USED_AT >= DATEADD(DAY, -30, CURRENT_TIMESTAMP()) {scope}""", params)[0]
        top = db.query(f"""SELECT K.KNOWLEDGE_ID, K.TITLE, K.KNOWLEDGE_TYPE, D.DOMAIN_NAME, COUNT(*) AS USES,
                                  COUNT(DISTINCT U.RUN_ID) AS RUNS
                             FROM KNOWLEDGE.KNOWLEDGE_USAGE U JOIN KNOWLEDGE.DOMAIN_KNOWLEDGE K ON K.KNOWLEDGE_ID = U.KNOWLEDGE_ID
                             JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                            WHERE U.USED_AT >= DATEADD(DAY, -30, CURRENT_TIMESTAMP()) {scope}
                            GROUP BY 1, 2, 3, 4 ORDER BY USES DESC LIMIT 8""", params)
    except Exception:
        used, top = {"used": 0, "uses": 0}, []
    by: dict[str, dict[str, int]] = {"type": {}, "origin": {}, "domain": {}, "status": {}}
    for r in counts:
        n = int(r["n"])
        for k, col in (("type", "knowledge_type"), ("origin", "origin"), ("domain", "domain_name"), ("status", "status")):
            key = str(r.get(col) or "UNKNOWN")
            by[k][key] = by[k].get(key, 0) + n
    return {"totals": {k: int(v or 0) for k, v in totals.items()} | {"used_30d": int(used.get("used") or 0),
                                                                     "uses_30d": int(used.get("uses") or 0)},
            "by": by, "top_used": top, "feed": feed(domain_id=domain_id, limit=12, db=db)["items"]}


@router.get("/api/knowledge/feed")
def feed(domain_id: Optional[str] = None, origin: Optional[str] = None, limit: int = 50, offset: int = 0,
         db: Db = Depends(current_db)):
    """Every version written, newest first: what was learned, from which run, and whether it is in use."""
    where, params = ["TRUE"], []
    if domain_id:
        where.append("K.DOMAIN_ID = %s"); params.append(domain_id)
    if origin:
        where.append("K.ORIGIN = %s"); params.append(origin.upper())
    rows = db.query(f"""SELECT {FEED_COLS} {FROM} LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = K.SOURCE_RUN_ID
                         WHERE {' AND '.join(where)} ORDER BY K.CREATED_AT DESC LIMIT %s OFFSET %s""",
                    tuple(params + [max(1, min(limit, 200)), max(0, offset)]))
    return {"items": rows}


@router.get("/api/knowledge/inbox")
def inbox(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Proposed versions waiting for review, each with the version currently in use (if any) to compare."""
    scope, params = ("AND K.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    proposed = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS}, R.RUN_NAME {FROM} LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = K.SOURCE_RUN_ID
                             WHERE K.STATUS = 'PROPOSED' {scope} ORDER BY K.CREATED_AT DESC LIMIT 200""", params)
    lineages = [p["lineage_id"] for p in proposed if p.get("lineage_id")]
    current = {}
    if lineages:
        for c in db.query(f"""SELECT {_KNOWLEDGE_COLUMNS} {FROM} WHERE K.IS_CURRENT
                                AND ARRAY_CONTAINS(K.LINEAGE_ID::VARIANT, PARSE_JSON(%s))""", (json.dumps(lineages),)):
            current[c["lineage_id"]] = _shape_knowledge(c)
    return {"items": [{**_shape_knowledge(p), "run_name": p.get("run_name"), "current": current.get(p.get("lineage_id"))}
                      for p in proposed]}


class Decide(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/knowledge/inbox/decide")
def decide(body: Decide, db: Db = Depends(current_db)):
    """Approve: the proposal becomes the version in use. Reject: it is kept as REJECTED and nothing changes."""
    done = 0
    for kid in body.ids:
        found = db.query("SELECT KNOWLEDGE_ID, LINEAGE_ID, STATUS FROM KNOWLEDGE.DOMAIN_KNOWLEDGE WHERE KNOWLEDGE_ID = %s", (kid,))
        if not found or found[0]["status"] != "PROPOSED":
            continue
        if body.decision == "approve":
            db.execute("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, UPDATED_AT = CURRENT_TIMESTAMP(),
                                 STATUS = IFF(STATUS IN ('ACTIVE', 'RETIRED'), 'SUPERSEDED', STATUS)
                           WHERE LINEAGE_ID = %s AND IS_CURRENT""", (found[0]["lineage_id"],))
            db.execute("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'ACTIVE', IS_CURRENT = TRUE, REVIEWED_BY = CURRENT_USER(),
                                 REVIEW_NOTE = %s, VERIFIED_BY = CURRENT_USER(), VERIFIED_AT = CURRENT_TIMESTAMP(),
                                 UPDATED_AT = CURRENT_TIMESTAMP() WHERE KNOWLEDGE_ID = %s""", (body.note, kid))
        else:
            db.execute("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET STATUS = 'REJECTED', REVIEWED_BY = CURRENT_USER(),
                                 REVIEW_NOTE = %s, UPDATED_AT = CURRENT_TIMESTAMP() WHERE KNOWLEDGE_ID = %s""", (body.note, kid))
        done += 1
    _changed()
    return {"decided": done, "decision": body.decision}


@router.get("/api/knowledge/{knowledge_id}/versions")
def versions(knowledge_id: str, db: Db = Depends(current_db)):
    item = _knowledge_item(db, knowledge_id)
    rows = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS}, R.RUN_NAME {FROM} LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = K.SOURCE_RUN_ID
                         WHERE K.LINEAGE_ID = %s ORDER BY K.VERSION DESC, K.CREATED_AT DESC""",
                    (item.get("lineage_id") or knowledge_id,))
    return {"lineage_id": item.get("lineage_id"), "versions": [{**_shape_knowledge(r), "run_name": r.get("run_name")} for r in rows] or [item]}


def _text_ops(old: str, new: str) -> dict:
    import difflib

    a, b = (old or "").splitlines(), (new or "").splitlines()
    ops, plus, minus = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops += [[" ", i1 + k + 1, j1 + k + 1, a[i1 + k]] for k in range(i2 - i1)]
            continue
        ops += [["-", k + 1, None, a[k]] for k in range(i1, i2)]
        ops += [["+", None, k + 1, b[k]] for k in range(j1, j2)]
        minus += i2 - i1
        plus += j2 - j1
    return {"status": "same" if not plus and not minus else "changed", "added": plus, "removed": minus, "ops": compact(ops)}


@router.get("/api/knowledge/{knowledge_id}/diff")
def diff(knowledge_id: str, base: str, head: str, db: Db = Depends(current_db)):
    a, b = _knowledge_item(db, base), _knowledge_item(db, head)
    if (a.get("lineage_id") or base) != (b.get("lineage_id") or head):
        raise HTTPException(400, "Both versions must belong to the same item")
    pretty = lambda v: json.dumps(v, indent=2, sort_keys=True, default=str) if v is not None else ""  # noqa: E731
    files = []
    for path, old, new in (("title", a["title"], b["title"]), ("content", a["content"], b["content"]),
                           ("content_json", pretty(a.get("content_json")), pretty(b.get("content_json")))):
        files.append({"path": path, **_text_ops(old, new)})
    return {"base": {"knowledge_id": base, "version": a["version"]}, "head": {"knowledge_id": head, "version": b["version"]},
            "files": files, "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files)}


class Rollback(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/knowledge/{knowledge_id}/rollback")
def rollback(knowledge_id: str, body: Rollback, db: Db = Depends(current_db)):
    """Make an earlier version the one in use again, as a new version (history is never rewritten)."""
    old = _knowledge_item(db, knowledge_id)
    if old.get("is_current"):
        raise HTTPException(400, "This is already the version in use")
    current = db.query(f"SELECT {_KNOWLEDGE_COLUMNS} {FROM} WHERE K.LINEAGE_ID = %s AND K.IS_CURRENT", (old["lineage_id"],))
    if not current:
        raise HTTPException(409, "The item has no version in use (it was rejected or retired); restore it from its history instead")
    base = _shape_knowledge(current[0])
    if not base["editable"]:
        raise HTTPException(409, base["read_only_reason"])
    new_id = _new_knowledge_version(db, base, old["knowledge_type"], old["title"], old["content"], old.get("content_json"),
                                    body.note or f"Rolled back to v{old['version']}", status="ACTIVE")
    _changed()
    return _knowledge_item(db, new_id)


class Verify(BaseModel):
    review_in_days: int = Field(default=STALE_DAYS, ge=7, le=1095)


@router.post("/api/knowledge/{knowledge_id}/verify")
def verify(knowledge_id: str, body: Verify, db: Db = Depends(current_db)):
    """A person confirms the item is still right; it is due for review again after review_in_days."""
    item = _knowledge_item(db, knowledge_id)
    if not item.get("is_current"):
        raise HTTPException(400, "Verify the version in use")
    db.execute("""UPDATE KNOWLEDGE.DOMAIN_KNOWLEDGE SET VERIFIED_BY = CURRENT_USER(), VERIFIED_AT = CURRENT_TIMESTAMP(),
                         REVIEW_DUE = DATEADD(DAY, %s, CURRENT_DATE()) WHERE KNOWLEDGE_ID = %s""",
               (body.review_in_days, knowledge_id))
    return _knowledge_item(db, knowledge_id)


@router.get("/api/knowledge/{knowledge_id}/usage")
def usage(knowledge_id: str, db: Db = Depends(current_db)):
    item = _knowledge_item(db, knowledge_id)
    try:
        rows = db.query("""SELECT U.RUN_ID, R.RUN_NAME, R.CURRENT_STATE, U.STAGE, K.VERSION, COUNT(*) AS USES,
                                  MAX(U.USED_AT)::VARCHAR AS LAST_USED
                             FROM KNOWLEDGE.KNOWLEDGE_USAGE U JOIN KNOWLEDGE.DOMAIN_KNOWLEDGE K ON K.KNOWLEDGE_ID = U.KNOWLEDGE_ID
                             LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = U.RUN_ID
                            WHERE U.LINEAGE_ID = %s GROUP BY 1, 2, 3, 4, 5 ORDER BY LAST_USED DESC LIMIT 100""",
                        (item.get("lineage_id") or knowledge_id,))
    except Exception:
        rows = []
    by_stage: dict[str, int] = {}
    for r in rows:
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + int(r["uses"])
    return {"runs": rows, "by_stage": by_stage, "total": sum(by_stage.values())}


@router.get("/api/knowledge/duplicates")
def duplicates(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Likely duplicates: current items of the same domain, type and target table whose titles and content are nearly
    the same (string similarity, no AI cost)."""
    scope, params = ("AND A.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    rows = db.query(f"""SELECT A.KNOWLEDGE_ID AS A_ID, A.TITLE AS A_TITLE, A.ORIGIN AS A_ORIGIN, A.UPDATED_AT::VARCHAR AS A_UPDATED,
                               B.KNOWLEDGE_ID AS B_ID, B.TITLE AS B_TITLE, B.ORIGIN AS B_ORIGIN, B.UPDATED_AT::VARCHAR AS B_UPDATED,
                               A.KNOWLEDGE_TYPE, D.DOMAIN_NAME, JAROWINKLER_SIMILARITY(LOWER(A.TITLE), LOWER(B.TITLE)) AS SCORE
                          FROM KNOWLEDGE.DOMAIN_KNOWLEDGE A
                          JOIN KNOWLEDGE.DOMAIN_KNOWLEDGE B ON B.DOMAIN_ID = A.DOMAIN_ID AND B.KNOWLEDGE_TYPE = A.KNOWLEDGE_TYPE
                               AND A.KNOWLEDGE_ID < B.KNOWLEDGE_ID AND B.IS_CURRENT AND B.STATUS = 'ACTIVE'
                               AND COALESCE(A.LINEAGE_ID, A.KNOWLEDGE_ID) <> COALESCE(B.LINEAGE_ID, B.KNOWLEDGE_ID)
                          JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = A.DOMAIN_ID
                         WHERE A.IS_CURRENT AND A.STATUS = 'ACTIVE' {scope}
                           AND JAROWINKLER_SIMILARITY(LOWER(A.TITLE), LOWER(B.TITLE)) >= 95
                           -- the same title for another table or column is not a duplicate: the content must match too
                           AND JAROWINKLER_SIMILARITY(LOWER(LEFT(A.CONTENT, 400)), LOWER(LEFT(B.CONTENT, 400))) >= 97
                           AND COALESCE(A.CONTENT_JSON:target_table::STRING, '') = COALESCE(B.CONTENT_JSON:target_table::STRING, '')
                           AND UPPER(COALESCE(A.CONTENT_JSON:target_column::STRING, '')) = UPPER(COALESCE(B.CONTENT_JSON:target_column::STRING, ''))
                           AND UPPER(COALESCE(A.CONTENT_JSON:source_column::STRING, '')) = UPPER(COALESCE(B.CONTENT_JSON:source_column::STRING, ''))
                         ORDER BY SCORE DESC LIMIT 100""", params)
    return {"pairs": rows}


@router.get("/api/config/knowledge-policy")
def get_policy(db: Db = Depends(current_db)):
    from services.knowledge.manage import KNOWLEDGE_TYPES

    stored = _config(db, "KNOWLEDGE_LEARNING_POLICY", {}) or {}
    merged = {**DEFAULT_POLICY, **{str(k).upper(): str(v).lower() for k, v in stored.items()}}
    return {"types": sorted(KNOWLEDGE_TYPES), "policy": {t: merged.get(t, "auto") for t in KNOWLEDGE_TYPES},
            "copilot": merged.get("ORIGIN:COPILOT", "review"), "defaults": DEFAULT_POLICY}


class Policy(BaseModel):
    policy: dict[str, Literal["auto", "review"]]
    copilot: Literal["auto", "review"] = "review"


@router.put("/api/config/knowledge-policy")
def put_policy(body: Policy, db: Db = Depends(current_db)):
    from services.knowledge.manage import KNOWLEDGE_TYPES

    unknown = [k for k in body.policy if k.upper() not in KNOWLEDGE_TYPES]
    if unknown:
        raise HTTPException(400, f"Unknown knowledge type {', '.join(unknown)}")
    value: dict[str, Any] = {k.upper(): v for k, v in body.policy.items()}
    value["ORIGIN:COPILOT"] = body.copilot
    _put_config(db, "KNOWLEDGE_LEARNING_POLICY", value, "Per knowledge type: auto (use at once) or review (wait in the inbox)")
    return get_policy(db)


@router.get("/api/knowledge/stale")
def stale(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Items in use that are past their review date, or never verified and unchanged for STALE_DAYS."""
    scope, params = ("AND K.DOMAIN_ID = %s", (domain_id,)) if domain_id else ("", ())
    rows = db.query(f"""SELECT {_KNOWLEDGE_COLUMNS} {FROM}
                         WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE' {scope}
                           AND (K.REVIEW_DUE < CURRENT_DATE() OR (K.VERIFIED_AT IS NULL AND
                                COALESCE(K.UPDATED_AT, K.CREATED_AT) < DATEADD(DAY, -{STALE_DAYS}, CURRENT_TIMESTAMP())))
                         ORDER BY COALESCE(K.REVIEW_DUE, TO_DATE(COALESCE(K.UPDATED_AT, K.CREATED_AT))) LIMIT 200""", params)
    return {"items": [_shape_knowledge(r) for r in rows], "stale_days": STALE_DAYS}
