"""Skills: catalogue grouped by category, versions, release labels, stage bindings, per-run trials and usage.

Every version is an immutable KNOWLEDGE.SKILL_REGISTRY row. The "production" label is what every run loads, the
"candidate" label marks the version under review, and CORE.RUN_SKILL_OVERRIDE lets one run try any version.
IS_CURRENT on the registry is kept equal to "holds the production label" for older readers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _json, current_db
from services.knowledge.skills import compact, diff_files, name_variants, next_version, slug, split_files, version_key
from services.knowledge.usage import STAGE_SKILLS

router = APIRouter()

STAGES = list(STAGE_SKILLS)
VERSION_COLS = """SKILL_ID, SKILL_NAME, SKILL_TYPE, VERSION, REVISION, STATUS, ORIGIN, CATEGORY_ID, PARENT_SKILL,
                  DESCRIPTION, CHANGE_NOTE, CHECKSUM, PARENT_SKILL_ID, CREATED_BY, CREATED_AT::VARCHAR AS CREATED_AT,
                  EVAL_JSON"""


def _ts(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _name(db: Db, name: str) -> str:
    """The registered spelling of a skill name (SODA-SKILL finds SODA_SKILL)."""
    names = name_variants(name)
    found = db.query("SELECT DISTINCT SKILL_NAME FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_NAME IN ("
                     + ", ".join(["%s"] * len(names)) + ")", tuple(names))
    if not found:
        raise HTTPException(404, f"Skill {name} not found")
    return found[0]["skill_name"]


def _labels(db: Db, name: Optional[str] = None) -> dict[str, dict[str, dict]]:
    sql = "SELECT SKILL_NAME, LABEL, SKILL_ID, MOVED_BY, MOVED_AT::VARCHAR AS MOVED_AT, NOTE FROM KNOWLEDGE.SKILL_LABEL"
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in db.query(sql + (" WHERE SKILL_NAME = %s" if name else ""), (name,) if name else ()):
        out[r["skill_name"]][r["label"]] = r
    return out


def _bindings(db: Db) -> list[dict]:
    return db.query("""SELECT STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, UPDATED_BY, UPDATED_AT::VARCHAR AS UPDATED_AT
                         FROM KNOWLEDGE.SKILL_STAGE_BINDING ORDER BY STAGE, POSITION, SKILL_NAME""")


def _categories(db: Db) -> list[dict]:
    return db.query("""SELECT CATEGORY_ID, NAME, DESCRIPTION, ICON, COLOR, POSITION, IS_SYSTEM
                         FROM KNOWLEDGE.SKILL_CATEGORY ORDER BY POSITION, NAME""")


def _brief(v: Optional[dict]) -> Optional[dict]:
    if not v:
        return None
    return {k: v.get(k) for k in ("skill_id", "version", "revision", "status", "origin", "created_by", "created_at")}


def _usage(db: Db) -> tuple[dict, dict]:
    """(per skill: loads and runs in 30 days, daily series, last used), (per version id: loads in 30 days)."""
    per: dict[str, dict] = {}
    by_version: dict[str, int] = defaultdict(int)
    try:
        rows = db.query("""SELECT UPPER(INPUT_JSON:skill::STRING) AS NAME, INPUT_JSON:skill_id::STRING AS SKILL_ID,
                                  TO_DATE(CREATED_AT)::VARCHAR AS DAY, COUNT(*) AS LOADS, COUNT(DISTINCT RUN_ID) AS RUNS
                             FROM AUDIT.AGENT_TOOL_CALL
                            WHERE TOOL_NAME = 'load_skill' AND CREATED_AT >= DATEADD(DAY, -30, CURRENT_TIMESTAMP())
                            GROUP BY 1, 2, 3""")
        last = db.query("""SELECT UPPER(INPUT_JSON:skill::STRING) AS NAME, MAX(CREATED_AT)::VARCHAR AS LAST_USED
                             FROM AUDIT.AGENT_TOOL_CALL WHERE TOOL_NAME = 'load_skill' GROUP BY 1""")
    except Exception:
        return {}, {}
    for r in rows:
        u = per.setdefault(r["name"], {"loads_30d": 0, "runs_30d": 0, "daily": defaultdict(int)})
        u["loads_30d"] += int(r["loads"] or 0)
        u["runs_30d"] += int(r["runs"] or 0)
        u["daily"][r["day"]] += int(r["loads"] or 0)
        if r.get("skill_id"):
            by_version[r["skill_id"]] += int(r["loads"] or 0)
    for r in last:
        per.setdefault(r["name"], {"loads_30d": 0, "runs_30d": 0, "daily": {}})["last_used"] = r["last_used"]
    return per, by_version


@router.get("/api/skills")
def list_skills(db: Db = Depends(current_db)):
    """Every skill once, grouped by category, with its production and candidate versions and 30-day usage."""
    try:
        versions = db.query(f"SELECT {VERSION_COLS} FROM KNOWLEDGE.SKILL_REGISTRY")
        labels, bindings, categories = _labels(db), _bindings(db), _categories(db)
        overrides = {r["skill_name"]: r["category_id"] for r in db.query("SELECT SKILL_NAME, CATEGORY_ID FROM KNOWLEDGE.SKILL_SETTING")}
    except Exception:  # before V021
        rows = db.query("""SELECT SKILL_ID, SKILL_NAME, SKILL_TYPE, VERSION, STATUS, DESCRIPTION, CREATED_BY,
                                  CREATED_AT::VARCHAR AS CREATED_AT FROM KNOWLEDGE.SKILL_REGISTRY WHERE IS_CURRENT""")
        return {"categories": [], "skills": [{**r, "category_id": "general", "production": _brief(r), "versions": 1,
                                               "stages": []} for r in rows], "stats": {}, "upgraded": False}
    usage, _ = _usage(db)
    stages: dict[str, list[dict]] = defaultdict(list)
    for b in bindings:
        stages[b["skill_name"]].append({"stage": b["stage"], "enabled": b["enabled"]})
    grouped: dict[str, list[dict]] = defaultdict(list)
    for v in versions:
        grouped[v["skill_name"]].append(v)
    skills = []
    for name, vs in grouped.items():
        vs.sort(key=lambda v: (int(v.get("revision") or 0), version_key(v.get("version"))), reverse=True)
        by_id = {v["skill_id"]: v for v in vs}
        lab = labels.get(name, {})
        prod = by_id.get((lab.get("production") or {}).get("skill_id")) or next((v for v in vs if v["status"] == "ACTIVE"), vs[0])
        cand = by_id.get((lab.get("candidate") or {}).get("skill_id"))
        u = next((usage[k] for k in [name.upper()] + name_variants(name) if k in usage), {})
        daily = u.get("daily") or {}
        skills.append({
            "skill_name": name, "description": prod.get("description"), "skill_type": prod.get("skill_type"),
            "category_id": overrides.get(name) or prod.get("category_id") or "general",
            "parent_skill": prod.get("parent_skill"), "origin": prod.get("origin") or "REPOSITORY",
            "production": {**_brief(prod), **{k: (lab.get("production") or {}).get(k) for k in ("moved_by", "moved_at")}},
            "candidate": _brief(cand) if cand and cand["skill_id"] != prod["skill_id"] else None,
            "versions": len(vs), "drafts": sum(1 for v in vs if v["status"] == "DRAFT"),
            "stages": stages.get(name, []),
            "loads_30d": u.get("loads_30d", 0), "runs_30d": u.get("runs_30d", 0), "last_used": u.get("last_used"),
            "daily": [daily.get(d, 0) for d in sorted(daily)][-14:],
            "status": prod.get("status"),
        })
    skills.sort(key=lambda s: (s["parent_skill"] or s["skill_name"], s["parent_skill"] is not None, s["skill_name"]))
    stats = {"skills": len(skills), "in_production": sum(1 for s in skills if s["production"]),
             "candidates": sum(1 for s in skills if s["candidate"]),
             "loads_30d": sum(s["loads_30d"] for s in skills),
             "unused_30d": sum(1 for s in skills if not s["loads_30d"])}
    return {"categories": categories, "skills": skills, "stats": stats, "stages": STAGES, "upgraded": True}


@router.get("/api/skills/bindings")
def get_bindings(db: Db = Depends(current_db)):
    names = [r["skill_name"] for r in db.query(
        "SELECT DISTINCT SKILL_NAME FROM KNOWLEDGE.SKILL_REGISTRY WHERE STATUS <> 'RETIRED' ORDER BY 1")]
    return {"stages": STAGES, "bindings": _bindings(db), "skills": names}


class Binding(BaseModel):
    stage: str = Field(min_length=1, max_length=32)
    skill_name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    position: int = Field(default=100, ge=0, le=100000)
    standard: Literal["ANY", "GDP"] = "ANY"


class Bindings(BaseModel):
    bindings: list[Binding]
    stages: Optional[list[str]] = None  # stages being replaced (default: every stage in the body)


@router.put("/api/skills/bindings")
def put_bindings(body: Bindings, db: Db = Depends(current_db)):
    stages = sorted({s.upper() for s in (body.stages or [b.stage for b in body.bindings])})
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        raise HTTPException(400, f"Unknown stage {', '.join(unknown)}")
    for stage in stages:
        keep = [b for b in body.bindings if b.stage.upper() == stage]
        db.execute("DELETE FROM KNOWLEDGE.SKILL_STAGE_BINDING WHERE STAGE = %s AND NOT ARRAY_CONTAINS(SKILL_NAME::VARIANT, PARSE_JSON(%s))",
                   (stage, json.dumps([b.skill_name for b in keep])))
        for b in keep:
            db.execute("""MERGE INTO KNOWLEDGE.SKILL_STAGE_BINDING T
                          USING (SELECT %s AS STAGE, %s AS SKILL_NAME, %s AS ENABLED, %s AS POSITION, %s AS STANDARD) S
                             ON T.STAGE = S.STAGE AND T.SKILL_NAME = S.SKILL_NAME
                          WHEN MATCHED THEN UPDATE SET ENABLED = S.ENABLED, POSITION = S.POSITION, STANDARD = S.STANDARD,
                               UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
                          WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD)
                               VALUES (S.STAGE, S.SKILL_NAME, S.ENABLED, S.POSITION, S.STANDARD)""",
                       (stage, b.skill_name, b.enabled, b.position, b.standard))
    return get_bindings(db)


class Category(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: Optional[str] = Field(default=None, max_length=1000)
    icon: Optional[str] = Field(default=None, max_length=40)
    color: Optional[str] = Field(default=None, max_length=16)
    position: Optional[int] = Field(default=None, ge=0, le=100000)


@router.post("/api/skills/categories")
def create_category(body: Category, db: Db = Depends(current_db)):
    cid = slug(body.name)
    if not cid:
        raise HTTPException(400, "Give the category a name")
    if db.query("SELECT 1 FROM KNOWLEDGE.SKILL_CATEGORY WHERE CATEGORY_ID = %s", (cid,)):
        raise HTTPException(409, f"Category {body.name} already exists")
    db.execute("""INSERT INTO KNOWLEDGE.SKILL_CATEGORY (CATEGORY_ID, NAME, DESCRIPTION, ICON, COLOR, POSITION, IS_SYSTEM)
                  SELECT %s, %s, %s, %s, %s, COALESCE(%s, (SELECT COALESCE(MAX(POSITION), 0) + 10 FROM KNOWLEDGE.SKILL_CATEGORY)), FALSE""",
               (cid, body.name, body.description, body.icon or "folder", body.color or "#64748b", body.position))
    return {"categories": _categories(db), "category_id": cid}


@router.put("/api/skills/categories/{category_id}")
def update_category(category_id: str, body: Category, db: Db = Depends(current_db)):
    db.execute("""UPDATE KNOWLEDGE.SKILL_CATEGORY SET NAME = %s, DESCRIPTION = %s, ICON = COALESCE(%s, ICON),
                         COLOR = COALESCE(%s, COLOR), POSITION = COALESCE(%s, POSITION) WHERE CATEGORY_ID = %s""",
               (body.name, body.description, body.icon, body.color, body.position, category_id))
    return {"categories": _categories(db)}


@router.delete("/api/skills/categories/{category_id}")
def delete_category(category_id: str, db: Db = Depends(current_db)):
    found = db.query("SELECT IS_SYSTEM FROM KNOWLEDGE.SKILL_CATEGORY WHERE CATEGORY_ID = %s", (category_id,))
    if not found:
        raise HTTPException(404, "Category not found")
    if found[0]["is_system"]:
        raise HTTPException(400, "Built-in categories cannot be deleted; rename or reorder them instead")
    db.execute("DELETE FROM KNOWLEDGE.SKILL_SETTING WHERE CATEGORY_ID = %s", (category_id,))
    db.execute("UPDATE KNOWLEDGE.SKILL_REGISTRY SET CATEGORY_ID = 'general' WHERE CATEGORY_ID = %s", (category_id,))
    db.execute("DELETE FROM KNOWLEDGE.SKILL_CATEGORY WHERE CATEGORY_ID = %s", (category_id,))
    return {"categories": _categories(db)}


class MoveCategory(BaseModel):
    category_id: str = Field(min_length=1, max_length=64)


@router.put("/api/skills/{name}/category")
def move_category(name: str, body: MoveCategory, db: Db = Depends(current_db)):
    skill = _name(db, name)
    if not db.query("SELECT 1 FROM KNOWLEDGE.SKILL_CATEGORY WHERE CATEGORY_ID = %s", (body.category_id,)):
        raise HTTPException(404, "Category not found")
    db.execute("""MERGE INTO KNOWLEDGE.SKILL_SETTING T USING (SELECT %s AS SKILL_NAME, %s AS CATEGORY_ID) S
                     ON T.SKILL_NAME = S.SKILL_NAME
                  WHEN MATCHED THEN UPDATE SET CATEGORY_ID = S.CATEGORY_ID, UPDATED_BY = CURRENT_USER(), UPDATED_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (SKILL_NAME, CATEGORY_ID) VALUES (S.SKILL_NAME, S.CATEGORY_ID)""",
               (skill, body.category_id))
    return {"skill_name": skill, "category_id": body.category_id}


@router.get("/api/skills/{name}")
def skill_detail(name: str, version: Optional[str] = None, db: Db = Depends(current_db)):
    """One skill: versions (newest first), labels and their history, the chosen version's files, stage bindings,
    usage per version and the runs that loaded it."""
    skill = _name(db, name)
    versions = db.query(f"SELECT {VERSION_COLS} FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_NAME = %s", (skill,))
    versions.sort(key=lambda v: (int(v.get("revision") or 0), version_key(v.get("version"))), reverse=True)
    labels = _labels(db, skill).get(skill, {})
    prod_id = (labels.get("production") or {}).get("skill_id")
    chosen = version or prod_id or versions[0]["skill_id"]
    row = db.query("SELECT SKILL_ID, CONTENT, CONFIG FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_ID = %s", (chosen,))
    if not row:
        raise HTTPException(404, "Version not found")
    history = db.query("""SELECT H.LABEL, H.FROM_SKILL_ID, H.TO_SKILL_ID, H.MOVED_BY, H.MOVED_AT::VARCHAR AS MOVED_AT, H.NOTE
                            FROM KNOWLEDGE.SKILL_LABEL_HISTORY H WHERE H.SKILL_NAME = %s ORDER BY H.MOVED_AT DESC LIMIT 100""", (skill,))
    bindings = [b for b in _bindings(db) if b["skill_name"] == skill]
    _, by_version = _usage(db)
    try:
        runs = db.query("""SELECT C.RUN_ID, R.RUN_NAME, R.CURRENT_STATE, C.INPUT_JSON:skill_id::STRING AS SKILL_ID,
                                  C.INPUT_JSON:version::STRING AS VERSION, C.INPUT_JSON:picked_by::STRING AS PICKED_BY,
                                  MAX(C.CREATED_AT)::VARCHAR AS LOADED_AT, COUNT(*) AS LOADS
                             FROM AUDIT.AGENT_TOOL_CALL C LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = C.RUN_ID
                            WHERE C.TOOL_NAME = 'load_skill' AND C.RUN_ID IS NOT NULL
                              AND ARRAY_CONTAINS(UPPER(C.INPUT_JSON:skill::STRING)::VARIANT, PARSE_JSON(%s))
                            GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY LOADED_AT DESC LIMIT 50""", (json.dumps(name_variants(skill)),))
    except Exception:
        runs = []
    overrides = db.query("""SELECT O.RUN_ID, R.RUN_NAME, O.SKILL_ID, O.SET_BY, O.SET_AT::VARCHAR AS SET_AT
                              FROM CORE.RUN_SKILL_OVERRIDE O LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = O.RUN_ID
                             WHERE O.SKILL_NAME = %s ORDER BY O.SET_AT DESC""", (skill,))
    category = db.query("SELECT CATEGORY_ID FROM KNOWLEDGE.SKILL_SETTING WHERE SKILL_NAME = %s", (skill,))
    for v in versions:
        v["loads_30d"] = by_version.get(v["skill_id"], 0)
        v["eval_json"] = _json(v.get("eval_json"))
        v["labels"] = [k for k, lab in labels.items() if lab.get("skill_id") == v["skill_id"]]
    children = [r["skill_name"] for r in db.query(
        "SELECT DISTINCT SKILL_NAME FROM KNOWLEDGE.SKILL_REGISTRY WHERE PARENT_SKILL = %s ORDER BY 1", (skill,))]
    return {
        "skill_name": skill, "versions": versions, "labels": labels, "label_history": history,
        "selected": {"skill_id": chosen, "files": split_files(row[0]["content"] or ""), "config": _json(row[0]["config"])},
        "bindings": bindings, "stages": STAGES, "runs": runs, "overrides": overrides, "children": children,
        "category_id": (category[0]["category_id"] if category else None) or (versions[0].get("category_id") if versions else None),
    }


@router.get("/api/skills/{name}/diff")
def skill_diff(name: str, base: str, head: str, db: Db = Depends(current_db)):
    skill = _name(db, name)
    found = {r["skill_id"]: r for r in db.query(
        "SELECT SKILL_ID, VERSION, REVISION, CONTENT FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_NAME = %s AND SKILL_ID IN (%s, %s)",
        (skill, base, head))}
    if base not in found or head not in found:
        raise HTTPException(404, "Version not found")
    files = diff_files(found[base]["content"] or "", found[head]["content"] or "")
    for f in files:
        f["ops"] = compact(f["ops"])
    meta = {k: {"skill_id": k, "version": found[k]["version"], "revision": found[k]["revision"]} for k in (base, head)}
    return {"base": meta[base], "head": meta[head], "files": files,
            "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files)}


class NewVersion(BaseModel):
    content: str = Field(min_length=20, max_length=2_000_000)
    base_skill_id: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=1000)
    change_note: str = Field(min_length=3, max_length=2000)
    status: Literal["DRAFT", "ACTIVE"] = "ACTIVE"
    set_candidate: bool = True
    origin: Literal["USER", "AI"] = "USER"
    eval: Optional[dict] = None  # test results from the skill builder, kept with the version


def _move(db: Db, skill: str, label: str, skill_id: Optional[str], note: Optional[str]) -> None:
    prev = (_labels(db, skill).get(skill, {}).get(label) or {}).get("skill_id")
    if skill_id:
        db.execute("""MERGE INTO KNOWLEDGE.SKILL_LABEL T USING (SELECT %s AS SKILL_NAME, %s AS LABEL, %s AS SKILL_ID, %s AS NOTE) S
                         ON T.SKILL_NAME = S.SKILL_NAME AND T.LABEL = S.LABEL
                      WHEN MATCHED THEN UPDATE SET SKILL_ID = S.SKILL_ID, NOTE = S.NOTE, MOVED_BY = CURRENT_USER(), MOVED_AT = CURRENT_TIMESTAMP()
                      WHEN NOT MATCHED THEN INSERT (SKILL_NAME, LABEL, SKILL_ID, NOTE) VALUES (S.SKILL_NAME, S.LABEL, S.SKILL_ID, S.NOTE)""",
                   (skill, label, skill_id, note))
    else:
        db.execute("DELETE FROM KNOWLEDGE.SKILL_LABEL WHERE SKILL_NAME = %s AND LABEL = %s", (skill, label))
    db.execute("""INSERT INTO KNOWLEDGE.SKILL_LABEL_HISTORY (EVENT_ID, SKILL_NAME, LABEL, FROM_SKILL_ID, TO_SKILL_ID, NOTE)
                  VALUES (%s, %s, %s, %s, %s, %s)""", (str(uuid.uuid4()), skill, label, prev, skill_id, note))
    if label == "production":
        db.execute("UPDATE KNOWLEDGE.SKILL_REGISTRY SET IS_CURRENT = (SKILL_ID = %s) WHERE SKILL_NAME = %s", (skill_id, skill))


@router.post("/api/skills/{name}/versions")
def create_version(name: str, body: NewVersion, db: Db = Depends(current_db)):
    """Save edited (or AI-drafted) content as a new immutable version."""
    skill = _name(db, name)
    versions = db.query(f"SELECT {VERSION_COLS} FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_NAME = %s", (skill,))
    by_id = {v["skill_id"]: v for v in versions}
    prod_id = (_labels(db, skill).get(skill, {}).get("production") or {}).get("skill_id")
    base = by_id.get(body.base_skill_id or "") or by_id.get(prod_id or "") or max(
        versions, key=lambda v: (int(v.get("revision") or 0), version_key(v.get("version"))))
    checksum = hashlib.sha256(body.content.encode("utf-8")).hexdigest()
    same = next((v for v in versions if v["checksum"] == checksum), None)
    if same:
        raise HTTPException(409, f"Version r{same['revision']} ({same['version']}) already has exactly this content")
    skill_id = str(uuid.uuid4())
    version = next_version(base["version"], [v["version"] for v in versions])
    db.execute("""INSERT INTO KNOWLEDGE.SKILL_REGISTRY (SKILL_ID, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, VERSION, STAGE_PATH, CHECKSUM,
                         STATUS, DESCRIPTION, CONTENT, CONFIG, IS_CURRENT, CREATED_BY, REVISION, ORIGIN, PARENT_SKILL_ID,
                         PARENT_SKILL, CATEGORY_ID, CHANGE_NOTE, EVAL_JSON)
                  SELECT %s, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, %s, STAGE_PATH, %s, %s, COALESCE(%s, DESCRIPTION), %s, CONFIG, FALSE,
                         CURRENT_USER(), (SELECT MAX(REVISION) FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_NAME = %s) + 1, %s,
                         SKILL_ID, PARENT_SKILL, CATEGORY_ID, %s, PARSE_JSON(NULLIF(%s, ''))
                    FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_ID = %s""",
               (skill_id, version, checksum, body.status, body.description, body.content, skill, body.origin,
                body.change_note, json.dumps(body.eval) if body.eval else "", base["skill_id"]))
    if body.set_candidate:
        _move(db, skill, "candidate", skill_id, body.change_note)
    return {"skill_id": skill_id, "version": version, "skill_name": skill}


@router.post("/api/skills/{name}/versions/{skill_id}/{action}")
def version_status(name: str, skill_id: str, action: Literal["retire", "restore"], db: Db = Depends(current_db)):
    skill = _name(db, name)
    labels = _labels(db, skill).get(skill, {})
    if action == "retire" and (labels.get("production") or {}).get("skill_id") == skill_id:
        raise HTTPException(400, "This version is in production. Promote another version first.")
    db.execute("UPDATE KNOWLEDGE.SKILL_REGISTRY SET STATUS = %s WHERE SKILL_ID = %s AND SKILL_NAME = %s",
               ("RETIRED" if action == "retire" else "ACTIVE", skill_id, skill))
    if action == "retire" and (labels.get("candidate") or {}).get("skill_id") == skill_id:
        _move(db, skill, "candidate", None, "Candidate retired")
    return {"ok": True}


class MoveLabel(BaseModel):
    skill_id: str = Field(min_length=1, max_length=36)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/skills/{name}/labels/{label}")
def move_label(name: str, label: Literal["production", "candidate"], body: MoveLabel, db: Db = Depends(current_db)):
    skill = _name(db, name)
    found = db.query("SELECT STATUS FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_ID = %s AND SKILL_NAME = %s", (body.skill_id, skill))
    if not found:
        raise HTTPException(404, "Version not found")
    if found[0]["status"] == "RETIRED":
        raise HTTPException(400, "Restore this version before labelling it")
    if label == "production":
        if found[0]["status"] == "DRAFT":
            db.execute("UPDATE KNOWLEDGE.SKILL_REGISTRY SET STATUS = 'ACTIVE' WHERE SKILL_ID = %s", (body.skill_id,))
        _move(db, skill, "production", body.skill_id, body.note or "Promoted to production")
        cand = (_labels(db, skill).get(skill, {}).get("candidate") or {}).get("skill_id")
        if cand == body.skill_id:
            _move(db, skill, "candidate", None, "Promoted to production")
    else:
        _move(db, skill, "candidate", body.skill_id, body.note)
    return {"skill_name": skill, "labels": _labels(db, skill).get(skill, {})}


@router.delete("/api/skills/{name}/labels/candidate")
def clear_candidate(name: str, db: Db = Depends(current_db)):
    skill = _name(db, name)
    _move(db, skill, "candidate", None, "Candidate cleared")
    return {"ok": True}


# ---------------------------------------------------------------- per run

@router.get("/api/runs/{run_id}/skills")
def run_skills(run_id: str, db: Db = Depends(current_db)):
    """Skills (and the exact versions) this run loaded, plus the versions it is set to try."""
    try:
        loaded = db.query("""SELECT UPPER(INPUT_JSON:skill::STRING) AS SKILL_NAME, INPUT_JSON:version::STRING AS VERSION,
                                    INPUT_JSON:skill_id::STRING AS SKILL_ID, INPUT_JSON:picked_by::STRING AS PICKED_BY,
                                    COUNT(*) AS LOADS, MAX(CREATED_AT)::VARCHAR AS LOADED_AT
                               FROM AUDIT.AGENT_TOOL_CALL WHERE TOOL_NAME = 'load_skill' AND RUN_ID = %s
                              GROUP BY 1, 2, 3, 4 ORDER BY LOADED_AT DESC""", (run_id,))
        overrides = db.query("""SELECT O.SKILL_NAME, O.SKILL_ID, R.VERSION, R.REVISION, O.SET_BY, O.SET_AT::VARCHAR AS SET_AT
                                  FROM CORE.RUN_SKILL_OVERRIDE O JOIN KNOWLEDGE.SKILL_REGISTRY R ON R.SKILL_ID = O.SKILL_ID
                                 WHERE O.RUN_ID = %s""", (run_id,))
        candidates = db.query("""SELECT L.SKILL_NAME, L.SKILL_ID, R.VERSION, R.REVISION
                                   FROM KNOWLEDGE.SKILL_LABEL L JOIN KNOWLEDGE.SKILL_REGISTRY R ON R.SKILL_ID = L.SKILL_ID
                                  WHERE L.LABEL = 'candidate' ORDER BY L.SKILL_NAME""")
    except Exception:
        return {"loaded": [], "overrides": [], "candidates": []}
    return {"loaded": loaded, "overrides": overrides, "candidates": candidates}


class RunSkill(BaseModel):
    skill_id: str = Field(min_length=1, max_length=36)


@router.put("/api/runs/{run_id}/skills/{name}")
def set_run_skill(run_id: str, name: str, body: RunSkill, db: Db = Depends(current_db)):
    skill = _name(db, name)
    if not db.query("SELECT 1 FROM KNOWLEDGE.SKILL_REGISTRY WHERE SKILL_ID = %s AND SKILL_NAME = %s AND STATUS <> 'RETIRED'",
                    (body.skill_id, skill)):
        raise HTTPException(404, "Version not found")
    db.execute("""MERGE INTO CORE.RUN_SKILL_OVERRIDE T USING (SELECT %s AS RUN_ID, %s AS SKILL_NAME, %s AS SKILL_ID) S
                     ON T.RUN_ID = S.RUN_ID AND T.SKILL_NAME = S.SKILL_NAME
                  WHEN MATCHED THEN UPDATE SET SKILL_ID = S.SKILL_ID, SET_BY = CURRENT_USER(), SET_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (RUN_ID, SKILL_NAME, SKILL_ID) VALUES (S.RUN_ID, S.SKILL_NAME, S.SKILL_ID)""",
               (run_id, skill, body.skill_id))
    return run_skills(run_id, db)


@router.delete("/api/runs/{run_id}/skills/{name}")
def clear_run_skill(run_id: str, name: str, db: Db = Depends(current_db)):
    skill = _name(db, name)
    db.execute("DELETE FROM CORE.RUN_SKILL_OVERRIDE WHERE RUN_ID = %s AND SKILL_NAME = %s", (run_id, skill))
    return run_skills(run_id, db)
