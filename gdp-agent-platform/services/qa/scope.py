"""QA on a domain target table (KNOWLEDGE.TARGET_TABLE_REGISTRY), with or without a run.

table_context() returns the same shape as procedures.context(), so the prompt builders, the guard, compile_check()
and run.execute() work unchanged. The target location, model spec and business keys come from the registry; the
latest STTM for the target (APPROVED first, else REVIEW, across runs) adds lines and sources when there is one.

PII masking for table tests, labelled with its basis:
  profile        the profile of the STTM's run flags PII source columns, mapped to targets through the STTM lines
  heuristic      no profile, but the target columns were curated (PII or semantic flags) and the model has no PII tag
  conservative   no profile and nothing curated, or the model or a source carries a PII tag: every result column
                 except the business keys is masked
Registry PII flags and column-name hints are added on every basis.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.common.sql import rows, variant

PII_TAG = re.compile(r"pii|sensitive|confidential|personal|gdpr|restricted", re.IGNORECASE)


def _registry(session, target_table_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?", [target_table_id])
    assert found, f"target table {target_table_id} does not exist"
    return found[0]


def _columns(session, target_table_id: str) -> List[Dict[str, Any]]:
    try:
        found = rows(session, """SELECT COLUMN_NAME, DATA_TYPE, IS_BUSINESS_KEY, IS_PII, SEMANTIC_TYPE,
                                        BUSINESS_DEFINITION, ACCEPTED_VALUES
                                   FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?
                                 QUALIFY ROW_NUMBER() OVER (PARTITION BY UPPER(COLUMN_NAME)
                                                            ORDER BY VERSION DESC, CREATED_AT DESC) = 1
                                  ORDER BY ORDINAL_POSITION""", [target_table_id])
    except Exception:
        return []
    return [{"name": str(r["COLUMN_NAME"]).upper(), "data_type": r.get("DATA_TYPE"),
             "is_key": bool(r.get("IS_BUSINESS_KEY")), "is_pii": bool(r.get("IS_PII")),
             "semantic_type": r.get("SEMANTIC_TYPE"), "definition": r.get("BUSINESS_DEFINITION"),
             "accepted_values": variant(r.get("ACCEPTED_VALUES")) or []} for r in found]


def table_sttm(session, target_table_id: str) -> Optional[Dict[str, Any]]:
    """The latest STTM for a target across runs: APPROVED first, else REVIEW; None when there is none."""
    found = rows(session, """SELECT STTM_ID, RUN_ID, TARGET_TABLE_ID, TABLE_DESIGN, DOMAIN_ID FROM CONTRACT.STTM_REGISTRY
                              WHERE TARGET_TABLE_ID = ? AND STATUS IN ('REVIEW', 'APPROVED')
                              ORDER BY IFF(STATUS = 'APPROVED', 0, 1), CREATED_AT DESC, STTM_VERSION DESC LIMIT 1""",
                 [target_table_id])
    return found[0] if found else None


def table_context(session, target_table_id: str) -> Dict[str, Any]:
    """procedures.context() for a target table, plus scope, retired and the PII columns and their basis."""
    from services.qa.procedures import _build

    registry = _registry(session, target_table_id)
    ctx = _build(session, table_sttm(session, target_table_id), registry=registry,
                 target_columns=_columns(session, target_table_id))
    active = registry.get("ACTIVE_FLAG")
    ctx.update(scope="TABLE", target_table_id=target_table_id, domain_id=registry.get("DOMAIN_ID") or ctx.get("domain_id"),
               retired=active is not None and not active)
    pii, basis = pii_columns(session, ctx)
    ctx["pii_columns"], ctx["pii_basis"] = sorted(pii), basis
    return ctx


def _tagged(session, ctx: Dict[str, Any]) -> bool:
    """The model or one of its source tables carries a PII-like user tag (CORE.TAG_ASSIGNMENT)."""
    import json

    keys = [".".join(p.strip('"').upper() for p in fqn.split("."))
            for fqn in [ctx["target"]["fqn"]] + list((ctx.get("sources") or {}).values()) if fqn and fqn.count(".") == 2]
    if not keys:
        return False
    try:
        found = rows(session, """SELECT TAG FROM CORE.TAG_ASSIGNMENT WHERE ENTITY_TYPE IN ('MODEL', 'PROFILE')
                                    AND ARRAY_CONTAINS(ENTITY_KEY::VARIANT, PARSE_JSON(?))""", [json.dumps(keys)])
    except Exception:
        return False
    return any(PII_TAG.search(str(r["TAG"])) for r in found)


def pii_columns(session, ctx: Dict[str, Any]) -> Tuple[Set[str], str]:
    """(upper-case PII column names, basis 'profile' | 'heuristic' | 'conservative'); see the module docstring."""
    from services.quality.scan import PII_HINT, PII_SEMANTICS

    found: Set[str] = set()
    basis = None
    run_id = ctx.get("run_id")
    if run_id:
        try:
            profile = rows(session, """SELECT TABLE_NAME, COLUMN_NAME, PII_CLASSIFICATION FROM PROFILE.PROFILE_REGISTRY
                                        WHERE RUN_ID = ? AND IS_CURRENT""", [run_id])
        except Exception:
            profile = []
        if profile:
            basis = "profile"
            flagged = {(str(r["TABLE_NAME"]).upper(), str(r["COLUMN_NAME"]).upper()) for r in profile
                       if str(r.get("PII_CLASSIFICATION") or "NONE").upper() != "NONE"}
            profiled = {t for t, _ in flagged} | {str(r["TABLE_NAME"]).upper() for r in profile}
            names = {c for _, c in flagged}
            found |= names
            for l in ctx.get("lines") or []:
                table, column = str(l.get("source_table") or "").upper(), str(l.get("source_column") or "").upper()
                if column and ((table, column) in flagged or (table not in profiled and column in names)):
                    found.add(str(l["target_column"]).upper())
    columns = ctx.get("columns") or []
    found |= {c["name"] for c in columns if c.get("is_pii") or str(c.get("semantic_type") or "").upper() in PII_SEMANTICS}
    candidates = [c["name"] for c in columns] + [str(l.get("target_column") or "") for l in ctx.get("lines") or []] \
        + [str(l.get("source_column") or "") for l in ctx.get("lines") or []]
    found |= {n.upper() for n in candidates if n and PII_HINT.search(n)}
    if basis is None:
        curated = any(c.get("is_pii") or c.get("semantic_type") for c in columns)
        basis = "heuristic" if curated and not _tagged(session, ctx) else "conservative"
    return found, basis


def list_tables(session, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Target tables (optionally of one domain) with STTM presence, saved table tests and the last table QA run."""
    found = rows(session, """
        SELECT T.TARGET_TABLE_ID, T.DOMAIN_ID, T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE,
               COALESCE(T.ACTIVE_FLAG, TRUE) AS ACTIVE, COALESCE(S.N, 0) AS STTMS, COALESCE(C.N, 0) AS TESTS,
               Q.STARTED_AT::VARCHAR AS LAST_RUN_AT, Q.TESTS AS LAST_TESTS, Q.PASSED, Q.FAILED, Q.REVIEW, Q.NOT_RUN,
               Q.ERRORS
          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
          LEFT JOIN (SELECT TARGET_TABLE_ID, COUNT(*) AS N FROM CONTRACT.STTM_REGISTRY
                      WHERE STATUS IN ('REVIEW', 'APPROVED') GROUP BY TARGET_TABLE_ID) S
            ON S.TARGET_TABLE_ID = T.TARGET_TABLE_ID
          LEFT JOIN (SELECT TARGET_TABLE_ID, COUNT(*) AS N FROM CONTRACT.QA_TEST_CASE
                      WHERE SCOPE = 'TABLE' AND NOT COALESCE(IS_DELETED, FALSE) GROUP BY TARGET_TABLE_ID) C
            ON C.TARGET_TABLE_ID = T.TARGET_TABLE_ID
          LEFT JOIN (SELECT * FROM QUALITY.QA_RUN WHERE SCOPE = 'TABLE'
                     QUALIFY ROW_NUMBER() OVER (PARTITION BY TARGET_TABLE_ID ORDER BY STARTED_AT DESC) = 1) Q
            ON Q.TARGET_TABLE_ID = T.TARGET_TABLE_ID
         WHERE (? = '' OR T.DOMAIN_ID = ?)
         ORDER BY T.TARGET_DATABASE, T.TARGET_SCHEMA, T.TARGET_TABLE""", [domain_id or "", domain_id or ""])
    return [{
        "target_table_id": r["TARGET_TABLE_ID"], "domain_id": r["DOMAIN_ID"],
        "fqn": ".".join(str(r[k]) for k in ("TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE")),
        "active": bool(r["ACTIVE"]), "has_sttm": int(r.get("STTMS") or 0) > 0, "tests": int(r.get("TESTS") or 0),
        "last_run_at": r.get("LAST_RUN_AT"),
        "last_outcome": None if not r.get("LAST_RUN_AT") else {
            "tests": int(r.get("LAST_TESTS") or 0), "passed": int(r.get("PASSED") or 0),
            "failed": int(r.get("FAILED") or 0), "review": int(r.get("REVIEW") or 0),
            "not_run": int(r.get("NOT_RUN") or 0), "errors": int(r.get("ERRORS") or 0)},
    } for r in found]
