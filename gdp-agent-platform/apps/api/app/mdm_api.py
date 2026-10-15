"""The MDM (Reltio) layer of a domain: where it sits in the medallion and how its records reach gold.

GET  /api/domains/{id}/mdm             role (MASTERED, CONSUMER or none), the contract, the entity chain pre-MDM silver,
                                       cross-reference, golden record, gold dimension; for a mastered domain the
                                       domains that consume it; the resolution and coverage SQL (services/mdm/contract.py)
GET  /api/domains/{id}/mdm/coverage    read-only: active pre-MDM records per source system and how many have an active
                                       cross-reference row, run on the domain's environment
PUT  /api/domains/{id}/mdm             mark a domain as a consumer of mastered domains (DOMAIN.EDIT); a mastered
                                       domain's contract comes from its repository mdm.json and is not edited here
Nothing here writes to the Reltio-owned schemas.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db, domain_detail
from services.mdm import contract as mdm

router = APIRouter()


def _config(db: Db, domain_id: str) -> Dict[str, Any]:
    found = db.query("SELECT DOMAIN_NAME, CONFIG FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    raw = found[0].get("config")
    return {"name": found[0]["domain_name"], "config": json.loads(raw) if isinstance(raw, str) else (raw or {})}


def _env(config: Dict[str, Any]) -> str:
    """The environment prefix of the domain's silver database (DEV_GDP_SILVER_DB gives DEV)."""
    m = re.match(r"^([A-Z]+)_", str(config.get("silver_database") or "").upper())
    return m.group(1) if m else "DEV"


def _all(db: Db) -> List[Dict[str, Any]]:
    rows = db.query("""SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG:mdm AS MDM FROM CORE.DOMAIN_REGISTRY
                        WHERE ACTIVE_FLAG AND CONFIG:mdm IS NOT NULL ORDER BY DOMAIN_NAME""")
    for r in rows:
        r["mdm"] = json.loads(r["mdm"]) if isinstance(r.get("mdm"), str) else r.get("mdm")
    return rows


@router.get("/api/domains/{domain_id}/mdm")
def domain_mdm(domain_id: str, db: Db = Depends(current_db)):
    detail = domain_detail(domain_id, db)
    reg = _config(db, domain_id)
    contract = reg["config"].get("mdm") or None
    role = str((contract or {}).get("role") or "").upper() or None
    env = _env(reg["config"])
    others = _all(db)
    mastered = {r["domain_name"]: r for r in others if str((r["mdm"] or {}).get("role")).upper() == "MASTERED"}
    out: Dict[str, Any] = {"domain": detail["domain"], "role": role, "env": env,
                           "mastered_domains": [{"domain_id": r["domain_id"], "domain_name": n,
                                                 "silver_column": ((r["mdm"] or {}).get("pre_mdm") or {}).get("skey"),
                                                 "gold_column": ((r["mdm"] or {}).get("gold") or {}).get("skey")}
                                                for n, r in mastered.items()]}
    if role == "MASTERED":
        out.update(contract={k: v for k, v in contract.items()}, chain=mdm.chain(reg["name"], contract),
                   sql={"resolution": mdm.resolution_sql(contract, env), "coverage": mdm.coverage_sql(contract, env)},
                   consumers=[{"domain_id": r["domain_id"], "domain_name": r["domain_name"],
                               "via": [c for c in (r["mdm"] or {}).get("consumes") or [] if str(c.get("domain")).upper() == reg["name"].upper()]}
                              for r in others if str((r["mdm"] or {}).get("role")).upper() == "CONSUMER"
                              and any(str(c.get("domain")).upper() == reg["name"].upper() for c in (r["mdm"] or {}).get("consumes") or [])])
    elif role == "CONSUMER":
        consumes = []
        for c in contract.get("consumes") or []:
            m = mastered.get(str(c.get("domain")).upper())
            item = {**c, "domain_id": m["domain_id"] if m else None, "known": bool(m)}
            if m:
                item["chain"] = mdm.chain(m["domain_name"], m["mdm"])
                item["gold"] = {k: m["mdm"]["gold"].get(k) for k in ("schema", "dimension", "skey", "mastered_id")}
            consumes.append(item)
        out.update(contract=contract, consumes=consumes)
    knowledge = db.query("""SELECT COUNT(*) AS N FROM CORE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = %s AND IS_CURRENT
                              AND SOURCE_REFERENCE ILIKE %s""", (domain_id, "%.mdm.rule.%"))
    out["rules"] = int((knowledge[0] if knowledge else {}).get("n") or 0)
    return out


@router.get("/api/domains/{domain_id}/mdm/coverage")
def domain_mdm_coverage(domain_id: str, db: Db = Depends(current_db)):
    """Read-only match rate per source system: how many active pre-MDM records have an active cross-reference row."""
    reg = _config(db, domain_id)
    contract = reg["config"].get("mdm") or {}
    if str(contract.get("role") or "").upper() != "MASTERED":
        raise HTTPException(400, "Coverage is measured on a mastered domain (Company or Property).")
    sql = mdm.coverage_sql(contract, _env(reg["config"]))
    try:
        rows = db.query(sql)
    except Exception as exc:
        text = str(exc)
        if "does not exist" in text.lower() or "not authorized" in text.lower():
            raise HTTPException(404, "The MDM tables are not readable with your role in this environment: " + text.splitlines()[0][:300]) from None
        raise _snowflake_error(exc) from exc
    total = sum(int(r.get("records") or 0) for r in rows)
    matched = sum(int(r.get("matched") or 0) for r in rows)
    return {"sources": rows, "records": total, "matched": matched,
            "matched_pct": round(100.0 * matched / total, 2) if total else None, "sql": sql}


class ConsumeIn(BaseModel):
    domain: str = Field(min_length=1, max_length=128)
    silver_column: str = Field(min_length=1, max_length=255)
    gold_column: str = Field(min_length=1, max_length=255)


class MdmIn(BaseModel):
    consumes: List[ConsumeIn] = Field(default_factory=list, max_length=10)


@router.put("/api/domains/{domain_id}/mdm")
def set_domain_mdm(domain_id: str, body: MdmIn, db: Db = Depends(current_db)):
    """Mark the domain as a consumer of mastered domains (empty list: not linked to MDM)."""
    reg = _config(db, domain_id)
    current = reg["config"].get("mdm") or {}
    if str(current.get("role") or "").upper() == "MASTERED":
        raise HTTPException(409, f"{reg['name']} is mastered by Reltio; its MDM contract comes from the repository (domain/<name>/mdm.json).")
    mastered = [r["domain_name"] for r in _all(db) if str((r["mdm"] or {}).get("role")).upper() == "MASTERED"]
    contract: Optional[Dict[str, Any]] = None
    if body.consumes:
        contract = {"role": "CONSUMER", "consumes": [{"domain": c.domain.upper(), "silver_column": c.silver_column.upper(),
                                                       "gold_column": c.gold_column.upper()} for c in body.consumes]}
        problems = mdm.validate(reg["name"], contract, mastered)
        if problems:
            raise HTTPException(400, "; ".join(problems))
    from app.domains_api import _baseline, _domain_caches_changed, _domain_snapshot

    try:
        _baseline(db, domain_id)
        if contract:
            db.execute("UPDATE CORE.DOMAIN_REGISTRY SET CONFIG = OBJECT_INSERT(COALESCE(CONFIG, OBJECT_CONSTRUCT()), 'mdm', PARSE_JSON(%s), TRUE), "
                       "UPDATED_AT = CURRENT_TIMESTAMP() WHERE DOMAIN_ID = %s", (json.dumps(contract), domain_id))
        else:
            db.execute("UPDATE CORE.DOMAIN_REGISTRY SET CONFIG = OBJECT_DELETE(COALESCE(CONFIG, OBJECT_CONSTRUCT()), 'mdm'), "
                       "UPDATED_AT = CURRENT_TIMESTAMP() WHERE DOMAIN_ID = %s", (domain_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    _domain_caches_changed()
    _domain_snapshot(db, domain_id, "EDIT", ("Consumes " + ", ".join(c["domain"] for c in contract["consumes"])) if contract
                     else "Not linked to MDM")
    return domain_mdm(domain_id, db)
