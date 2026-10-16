"""What a Gold run works against (Snowpark session): the approved GOLD domain model it is pinned to, its target table
in that model, the mastered-domain contracts, and what the run's domain consumes. None for a Silver run."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from services.common.sql import rows, variant
from services.common.standard import GOLD, run_pipeline


def contracts(session) -> Dict[str, Dict[str, Any]]:
    """Every active domain's MDM section, by domain name."""
    out = {}
    for r in rows(session, """SELECT DOMAIN_ID, DOMAIN_NAME, CONFIG:mdm AS MDM, CONFIG:silver_database::STRING AS SILVER_DATABASE
                                FROM CORE.DOMAIN_REGISTRY WHERE ACTIVE_FLAG"""):
        out[r["DOMAIN_NAME"]] = {"domain_id": r["DOMAIN_ID"], "mdm": variant(r.get("MDM")) or {},
                                 "silver_database": r.get("SILVER_DATABASE")}
    return out


def mastered(all_contracts: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {n: c["mdm"] for n, c in all_contracts.items() if str(c["mdm"].get("role") or "").upper() == "MASTERED"}


def env_of(database: Optional[str]) -> str:
    m = re.match(r"^([A-Z]+)_", str(database or "").upper())
    return m.group(1) if m else "DEV"


def approved_spec(session, model_id: str, version: int) -> Dict[str, Any]:
    from services.modeling import spec_v2

    found = rows(session, """SELECT D.SPEC, D.STATUS FROM CORE.MODEL_DESIGNS D WHERE D.MODEL_ID = ? AND D.VERSION = ?""",
                 [model_id, int(version)])
    assert found and found[0]["STATUS"] in ("APPROVED", "PUBLISHED"), "the gold model version the run uses is not approved"
    return spec_v2.active(spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, model_id))


def for_run(session, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """{spec, entity, consumes, mastered, env, domain} for a Gold run pinned to an approved gold model, else None."""
    if run_pipeline(run) != GOLD or not run.get("MODEL_ID") or not run.get("MODEL_VERSION"):
        return None
    spec = approved_spec(session, run["MODEL_ID"], int(run["MODEL_VERSION"]))
    name = str(run.get("MODEL_ENTITY") or (run.get("TARGET_MODEL") or "").split(".")[-1]).upper()
    entity = next((e for e in spec["design"]["entities"] if e["entity_name"] == name), None)
    assert entity, f"{name} is not a table of the approved gold model"
    every = contracts(session)
    domain = next((n for n, c in every.items() if c["domain_id"] == run.get("DOMAIN_ID")), spec.get("domain"))
    mine = (every.get(domain) or {}).get("mdm") or {}
    return {"spec": spec, "entity": entity, "consumes": mine.get("consumes") or [], "mastered": mastered(every),
            "role": str(mine.get("role") or "").upper() or None, "domain": domain,
            "env": env_of((spec.get("physical_defaults") or {}).get("database"))}
