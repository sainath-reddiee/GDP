"""A domain's playbook per layer: how its Silver or Gold runs model and code, in one place.

GET /api/domains/{id}/playbook?pipeline=SILVER|GOLD           the skills each stage loads (platform, domain, MDM), the
                                                               conventions, the rules this domain changes, the knowledge
                                                               each stage reads with its gaps, and the layer's model
GET /api/domains/{id}/playbook/preview?pipeline=&stage=&target= what a run of this layer is given at a stage: the skills
                                                               it loads and the domain context in its prompt, built by
                                                               the same functions the run uses
Read only; changing skills, conventions, rules and knowledge goes through their own endpoints (and approvals).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.db import Db
from app.domain_sources_api import domain_skills
from app.domains_api import domain_conventions_get, domain_rules
from app.main import current_db
from app.source_invoke import invoke_source
from services.knowledge import playbook

router = APIRouter()


def _layer(pipeline: str) -> str:
    return "GOLD" if str(pipeline).upper() == "GOLD" else "SILVER"


@router.get("/api/domains/{domain_id}/playbook")
def domain_playbook(domain_id: str, pipeline: str = "SILVER", db: Db = Depends(current_db)):
    layer = _layer(pipeline)
    skills = domain_skills(domain_id, layer, db)
    rules = domain_rules(domain_id, layer, db)
    changed = sorted(k for k, v in (rules.get("overrides") or {}).items() if v is not None)
    try:
        rows = db.query("""SELECT KNOWLEDGE_TYPE, COALESCE(LAYER, 'ANY') AS LAYER, COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                            WHERE DOMAIN_ID = %s AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE' GROUP BY 1, 2""",
                        (domain_id,))
    except Exception:   # before V055: every item is shared
        rows = [{**r, "layer": "ANY"} for r in db.query("""SELECT KNOWLEDGE_TYPE, COUNT(*) AS N FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                                            WHERE DOMAIN_ID = %s AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                                                            GROUP BY 1""", (domain_id,))]
    counts: dict = {}
    for r in rows:
        counts.setdefault(r["knowledge_type"], {})[str(r["layer"]).upper()] = int(r["n"])
    try:
        model = db.query("""SELECT MODEL_ID, APPROVED_VERSION, OPEN_VERSION FROM GOVERNANCE.DOMAIN_MODEL
                             WHERE DOMAIN_ID = %s AND COALESCE(LAYER, 'SILVER') = %s""", (domain_id, layer))
    except Exception:
        model = []
    return {"domain_id": domain_id, "pipeline": layer, "mdm_role": skills.get("mdm_role"),
            "skills": {k: skills[k] for k in ("standard", "stages", "available", "deployed", "other_layer")},
            "conventions": domain_conventions_get(domain_id, layer, db),
            "rules": {"changed": [{"key": k, "value": rules["overrides"][k], "base": (rules.get("base") or {}).get(k)} for k in changed]},
            "knowledge": playbook.coverage(counts, layer),
            "model": model[0] if model else None,
            "readiness": layer_readiness(domain_id, layer, db, counts) if layer == "GOLD" else []}


def _facts(db: Db, domain_id: str, counts: dict) -> dict:
    """What the layer readiness reads: the MDM contract, the approved Silver and Gold models and the gold tables."""
    import json

    from services.mdm import contract as mdm
    from services.modeling import spec_v2

    found = db.query("SELECT DOMAIN_NAME, CONFIG:mdm AS MDM FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    contract = found[0].get("mdm")
    contract = json.loads(contract) if isinstance(contract, str) else (contract or {})
    models = {r["layer"]: r for r in db.query("""SELECT MODEL_ID, APPROVED_VERSION, COALESCE(LAYER, 'SILVER') AS LAYER
                                                  FROM GOVERNANCE.DOMAIN_MODEL WHERE DOMAIN_ID = %s""", (domain_id,))}
    gold = models.get("GOLD") or {}
    tables = []
    if gold.get("approved_version"):
        spec = db.query("SELECT SPEC FROM GOVERNANCE.MODEL_DESIGNS WHERE MODEL_ID = %s AND VERSION = %s",
                        (gold["model_id"], int(gold["approved_version"])))
        if spec:
            raw = spec[0]["spec"]
            norm = spec_v2.active(spec_v2.normalize_v2(json.loads(raw) if isinstance(raw, str) else raw or {}, gold["model_id"]))
            tables = [{"entity": e["entity_name"], "columns": [a["name"].upper() for a in e.get("attributes") or []]}
                      for e in norm["design"]["entities"] if not e.get("conformed_from")]
    consumers = [r["domain_name"] for r in db.query("""SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG
                                                        AND ARRAY_CONTAINS(%s::VARIANT, TRANSFORM(COALESCE(CONFIG:mdm:consumes, ARRAY_CONSTRUCT()), c -> c:domain))""",
                                                     (found[0]["domain_name"],))] if str(contract.get("role") or "").upper() == "MASTERED" else []
    return {"role": contract.get("role"), "consumes": contract.get("consumes") or [],
            "silver_model": (models.get("SILVER") or {}).get("approved_version"), "gold_model": gold.get("approved_version"),
            "gold_tables": tables,
            "gold_definitions": sum(v for k, v in (counts.get("MODEL_DEFINITION") or {}).items() if k in ("GOLD", "ANY")),
            "exceptions": sum((counts.get("EXCEPTION") or {}).values()),
            "contract_problems": mdm.validate(found[0]["domain_name"], contract) if contract else ["no Reltio contract"],
            "consumers": consumers}


def layer_readiness(domain_id: str, layer: str, db: Db, counts: dict) -> list:
    return playbook.layer_readiness(layer, _facts(db, domain_id, counts))


@router.get("/api/domains/{domain_id}/mdm-layer")
def mdm_layer(domain_id: str, db: Db = Depends(current_db)):
    """The MDM (Reltio) layer of a mastered domain: its readiness and the MDM rules its runs read."""
    rows = db.query("""SELECT KNOWLEDGE_ID, TITLE, CONTENT, COALESCE(LAYER, 'ANY') AS LAYER FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                        WHERE DOMAIN_ID = %s AND IS_CURRENT AND KNOWLEDGE_TYPE = 'EXCEPTION'
                          AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE' ORDER BY TITLE LIMIT 100""", (domain_id,))
    counts = {"EXCEPTION": {"ANY": len(rows)}}
    return {"readiness": playbook.layer_readiness("MDM", _facts(db, domain_id, counts)), "rules": rows}


@router.get("/api/domains/{domain_id}/playbook/preview")
def playbook_preview(domain_id: str, pipeline: str = "SILVER", stage: str = "MAPPING", target: Optional[str] = None,
                     db: Db = Depends(current_db)):
    from services.common.standard import normalize_standard
    from services.knowledge.skills import stage_skills
    from services.knowledge.usage import domain_context

    layer = _layer(pipeline)
    found = db.query("SELECT CONFIG:standard::STRING AS STANDARD FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))
    if not found:
        raise HTTPException(404, "domain not found")
    standard = normalize_standard(found[0].get("standard")) or "GDP"
    stage = stage.upper()
    skills = invoke_source(db, stage_skills, stage, standard, domain_id, layer)
    context = invoke_source(db, domain_context, domain_id, (target or "").upper() or None, 6000, standard, layer) \
        if stage in ("MAPPING", "STTM", "DBT") else ""
    return {"pipeline": layer, "stage": stage, "standard": standard, "skills": skills, "context": context}
