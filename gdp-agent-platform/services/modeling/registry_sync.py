"""Apply an approved domain model version to the domain registry (the runtime copy every stage reads).

CORE.TARGET_TABLE_REGISTRY / TARGET_COLUMN_REGISTRY stay what mapping, STTM, dbt, QA and DQ read; this module is
their only writer for domains that have a canonical model (CORE.APPLY_DOMAIN_MODEL, owner's rights). It keeps
registry ids stable (an entity's id is its TARGET_TABLE_ID, a column's id its TARGET_COLUMN_ID; rows registered
before the model existed are matched by name), so mapping decisions and STTM lines that point at a kept table or
column stay valid. Retired entities are deactivated, never deleted; columns a version drops or retires are removed
from the registry (approving such a version needs the breaking change acknowledged). Every application writes a
CORE.DOMAIN_VERSION snapshot, so the Domains page compare and rollback stay current.
A GOLD model registers its own facts, dimensions and bridges with LAYER = 'GOLD' (V054), so a Gold run maps into them
like a Silver run maps into silver tables; conformed dimensions (DIM_COMPANY, DIM_PROPERTY) belong to their mastered
domain and are not registered by a consumer. Readers that list a domain's silver targets filter on the layer.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.modeling import spec_v2

WORKBENCH_KEY = "workbench"


def _rows(session, sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    from services.common.sql import rows

    return rows(session, sql, params)


def model_spec_for(spec: Dict[str, Any], entity: Dict[str, Any], model_id: str, version: int) -> Dict[str, Any]:
    """MODEL_SPEC written on a registered table: the entity's own settings (hub/spoke, kept untouched) plus what the
    workbench adds under one key, so reading the registry back gives the same specification."""
    out = dict(entity.get("model_spec") or {})
    out.pop(WORKBENCH_KEY, None)
    live = {a["name"] for a in entity["attributes"] if a.get("status") != "RETIRED"}
    out[WORKBENCH_KEY] = {
        "model_id": model_id, "model_version": int(version), "entity_id": entity["id"],
        "primary_key": [a["name"] for a in entity["attributes"] if a.get("is_pk") and a["name"] in live],
        "unique_constraints": entity.get("unique_constraints") or [], "checks": entity.get("checks") or [],
        "physical": entity.get("physical") or {}, "subject_area": entity.get("subject_area"),
        "relationships": [{k: r[k] for k in ("id", "name", "from_entity", "from_column", "to_entity", "to_column",
                                             "cardinality", "from_optional", "to_optional", "enforce")}
                          for r in spec.get("relationships") or [] if r["from_entity"] == entity["entity_name"]],
        "columns": {a["name"]: {k: a.get(k) for k in ("id", "description", "default", "is_unique", "check", "tags")
                                if a.get(k) not in (None, "", [], False)} | (
                        {"pii_classification": a["pii"]["classification"]} if (a.get("pii") or {}).get("classification") else {}) | (
                        # BUSINESS_DEFINITION falls back to the description for older readers; remember that it did
                        {"definition_from_description": True} if a.get("description") and not a.get("business_definition") else {})
                    for a in entity["attributes"] if a["name"] in live},
    }
    # what older readers expect at the top level (dbt inputs and the run designer read these); keys added here are
    # listed so reading the registry back strips exactly them
    out[WORKBENCH_KEY]["added"] = [k for k in ("kind", "primary_key") if k not in out]
    out[WORKBENCH_KEY]["replaced"] = {k: out[k] for k in ("primary_key",) if k in out}
    out.setdefault("kind", entity.get("kind"))
    out["primary_key"] = out[WORKBENCH_KEY]["primary_key"]
    return out


def column_rows(entity: Dict[str, Any], table_id: str, existing: List[Dict[str, Any]], layer: str = "SILVER") -> List[List[Any]]:
    """Registry column values for the entity's active columns, each with the id it is stored under: the existing
    registry id when the column is matched (by id, then by name), else the column's own id."""
    by_id = {str(c["TARGET_COLUMN_ID"]): c for c in existing}
    by_name = {str(c["COLUMN_NAME"]).upper(): c for c in existing}
    keys = {k.upper() for k in entity.get("business_keys") or []}
    out, used = [], set()
    live = [a for a in entity["attributes"] if a.get("status") != "RETIRED"]
    if layer == "GOLD":
        from services.gold.propose import semantic_type

        live = [a if a.get("semantic_type") else {**a, "semantic_type": semantic_type(entity, a["name"])} for a in live]
    for n, a in enumerate(live, start=1):
        found = by_id.get(str(a["id"])) or by_name.get(a["name"].upper())
        cid = str(found["TARGET_COLUMN_ID"]) if found and str(found["TARGET_COLUMN_ID"]) not in used else str(a["id"])
        if cid in used:
            cid = str(uuid.uuid4())
        used.add(cid)
        pii = a.get("pii") or {}
        out.append([cid, table_id, a["name"], a["datatype"], n, bool(a.get("nullable", True)),
                    a.get("business_definition") or a.get("description") or "", a.get("semantic_type") or "",
                    a["name"].upper() in keys or bool(a.get("business_key")), bool(pii.get("flag")),
                    a.get("accepted_values") or []])
    return out


COLUMN_SOURCE = ("SELECT ? AS TARGET_COLUMN_ID, ? AS TARGET_TABLE_ID, ? AS COLUMN_NAME, ? AS DATA_TYPE, "
                 "?::NUMBER AS ORDINAL_POSITION, ?::BOOLEAN AS NULLABLE, NULLIF(?, '') AS BUSINESS_DEFINITION, "
                 "NULLIF(?, '') AS SEMANTIC_TYPE, ?::BOOLEAN AS IS_BUSINESS_KEY, ?::BOOLEAN AS IS_PII, "
                 "PARSE_JSON(?) AS ACCEPTED_VALUES")


def _write_columns(session, table_id: str, values: List[List[Any]]) -> int:
    from services.common.sql import INSERT_BATCH, _bind

    ids = [v[0] for v in values]
    if ids:
        session.sql(f"""DELETE FROM CORE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?
                          AND TARGET_COLUMN_ID NOT IN ({", ".join("?" for _ in ids)})""", params=[table_id, *ids]).collect()
    else:
        session.sql("DELETE FROM CORE.TARGET_COLUMN_REGISTRY WHERE TARGET_TABLE_ID = ?", params=[table_id]).collect()
    for start in range(0, len(values), INSERT_BATCH):
        batch = values[start:start + INSERT_BATCH]
        session.sql(f"""MERGE INTO CORE.TARGET_COLUMN_REGISTRY t
                        USING ({" UNION ALL ".join([COLUMN_SOURCE] * len(batch))}) s
                           ON t.TARGET_COLUMN_ID = s.TARGET_COLUMN_ID
                        WHEN MATCHED THEN UPDATE SET TARGET_TABLE_ID = s.TARGET_TABLE_ID, COLUMN_NAME = s.COLUMN_NAME,
                             DATA_TYPE = s.DATA_TYPE, ORDINAL_POSITION = s.ORDINAL_POSITION, NULLABLE = s.NULLABLE,
                             BUSINESS_DEFINITION = s.BUSINESS_DEFINITION, SEMANTIC_TYPE = s.SEMANTIC_TYPE,
                             IS_BUSINESS_KEY = s.IS_BUSINESS_KEY, IS_PII = s.IS_PII, ACCEPTED_VALUES = s.ACCEPTED_VALUES
                        WHEN NOT MATCHED THEN INSERT (TARGET_COLUMN_ID, TARGET_TABLE_ID, COLUMN_NAME, DATA_TYPE,
                             ORDINAL_POSITION, NULLABLE, BUSINESS_DEFINITION, SEMANTIC_TYPE, IS_BUSINESS_KEY, IS_PII,
                             ACCEPTED_VALUES, VERSION)
                             VALUES (s.TARGET_COLUMN_ID, s.TARGET_TABLE_ID, s.COLUMN_NAME, s.DATA_TYPE,
                                     s.ORDINAL_POSITION, s.NULLABLE, s.BUSINESS_DEFINITION, s.SEMANTIC_TYPE,
                                     s.IS_BUSINESS_KEY, s.IS_PII, s.ACCEPTED_VALUES, 1)""",
                    params=[_bind(v) for row in batch for v in row]).collect()
    return len(values)


def _find_table(session, entity: Dict[str, Any], domain_id: str, database: str, schema: str,
                layer: str = "SILVER") -> Optional[Dict[str, Any]]:
    found = _rows(session, "SELECT TARGET_TABLE_ID, TARGET_TABLE, RETIRED_AT FROM CORE.TARGET_TABLE_REGISTRY WHERE TARGET_TABLE_ID = ?",
                  [entity["id"]])
    if found:
        return found[0]
    found = _rows(session, """SELECT TARGET_TABLE_ID, TARGET_TABLE, RETIRED_AT FROM CORE.TARGET_TABLE_REGISTRY
                               WHERE DOMAIN_ID = ? AND UPPER(TARGET_DATABASE) = UPPER(?) AND UPPER(TARGET_SCHEMA) = UPPER(?)
                                 AND UPPER(TARGET_TABLE) = UPPER(?) AND COALESCE(LAYER, 'SILVER') = ?
                               ORDER BY ACTIVE_FLAG DESC LIMIT 1""", [domain_id, database, schema, entity["entity_name"], layer])
    return found[0] if found else None


def apply_spec(session, spec: Dict[str, Any], domain_id: str, model_id: str, version: int,
               layer: str = "SILVER") -> Dict[str, Any]:
    """Register every entity of a v2 specification for the domain (a gold model's own entities, with its layer).
    Returns what was registered and deactivated."""
    from services.common.sql import insert_rows

    registered, deactivated, kept_removed, columns = [], [], [], 0
    applied_ids = []
    for entity in spec["design"]["entities"]:
        if layer == "GOLD" and entity.get("conformed_from"):
            continue   # a conformed dimension is the mastered domain's table, referenced, never registered here
        database, schema = spec_v2.placement(spec, entity)
        assert database and schema, f"set the target database and schema for {entity['entity_name']}"
        existing = _find_table(session, entity, domain_id, database, schema, layer)
        if entity.get("status") == "RETIRED":
            if existing:
                session.sql("""UPDATE CORE.TARGET_TABLE_REGISTRY SET ACTIVE_FLAG = FALSE, MODEL_ID = ?, MODEL_VERSION = ?
                                WHERE TARGET_TABLE_ID = ?""",
                            params=[model_id, int(version), existing["TARGET_TABLE_ID"]]).collect()
                applied_ids.append(existing["TARGET_TABLE_ID"])
                deactivated.append(entity["entity_name"])
            continue
        model_spec = model_spec_for(spec, entity, model_id, version)
        values = [database, schema, entity["entity_name"], entity["table_type"], entity.get("grain") or "",
                  json.dumps(entity.get("business_keys") or []), entity.get("scd_type") or "1",
                  entity.get("description") or entity.get("purpose") or "", json.dumps(model_spec, default=str),
                  model_id, int(version), layer]
        if existing:
            table_id = existing["TARGET_TABLE_ID"]
            # a table removed from the domain through an approval (V068) stays removed when a model version is applied:
            # its definition is kept current, and only Restore (also approved) brings it back
            removed = bool(existing.get("RETIRED_AT"))
            if removed:
                kept_removed.append(entity["entity_name"])
            session.sql("""UPDATE CORE.TARGET_TABLE_REGISTRY SET TARGET_DATABASE = ?, TARGET_SCHEMA = ?, TARGET_TABLE = ?,
                                  TABLE_TYPE = ?, GRAIN = ?, BUSINESS_KEYS = PARSE_JSON(?), SCD_TYPE = ?, DESCRIPTION = ?,
                                  MODEL_SPEC = PARSE_JSON(?), MODEL_ID = ?, MODEL_VERSION = ?, LAYER = ?,
                                  ACTIVE_FLAG = ?, DOMAIN_ID = ?
                            WHERE TARGET_TABLE_ID = ?""", params=values + [not removed, domain_id, table_id]).collect()
        else:
            table_id = str(entity["id"])
            insert_rows(session, "CORE.TARGET_TABLE_REGISTRY",
                        ["TARGET_TABLE_ID", "DOMAIN_ID", "TARGET_DATABASE", "TARGET_SCHEMA", "TARGET_TABLE", "TABLE_TYPE",
                         "GRAIN", "BUSINESS_KEYS", "SCD_TYPE", "DESCRIPTION", "MODEL_SPEC", "MODEL_ID", "MODEL_VERSION",
                         "LAYER", "VERSION", "ACTIVE_FLAG"],
                        ["?", "?", "?", "?", "?", "?", "?", "PARSE_JSON(?)", "?", "?", "PARSE_JSON(?)", "?", "?::NUMBER",
                         "?", "1", "TRUE"],
                        [[table_id, domain_id] + values])
        applied_ids.append(table_id)
        current = _rows(session, "SELECT TARGET_COLUMN_ID, COLUMN_NAME FROM CORE.TARGET_COLUMN_REGISTRY "
                                 "WHERE TARGET_TABLE_ID = ?", [table_id])
        columns += _write_columns(session, table_id, column_rows(entity, table_id, current, layer))
        registered.append({"entity": entity["entity_name"], "entity_id": entity["id"], "target_table_id": table_id,
                           "fqn": f"{database}.{schema}.{entity['entity_name']}"})
    # tables the model registered before but no longer has stop being live (never deleted)
    if applied_ids:
        gone = _rows(session, f"""SELECT TARGET_TABLE_ID, TARGET_TABLE FROM CORE.TARGET_TABLE_REGISTRY
                                   WHERE DOMAIN_ID = ? AND MODEL_ID = ? AND ACTIVE_FLAG
                                     AND TARGET_TABLE_ID NOT IN ({", ".join("?" for _ in applied_ids)})""",
                     [domain_id, model_id, *applied_ids])
        for g in gone:
            session.sql("UPDATE CORE.TARGET_TABLE_REGISTRY SET ACTIVE_FLAG = FALSE, MODEL_VERSION = ? "
                        "WHERE TARGET_TABLE_ID = ?", params=[int(version), g["TARGET_TABLE_ID"]]).collect()
            deactivated.append(g["TARGET_TABLE"])
    return {"registered": registered, "deactivated": deactivated, "kept_removed": kept_removed, "columns": columns}


def _snapshot(session, domain_id: str, note: str) -> Optional[int]:
    from services.common.sql import rows
    from services.knowledge.domain_versions import snapshot

    def query(sql, params):
        return rows(session, sql.replace("%s", "?"), list(params))

    def execute(sql, params):
        session.sql(sql.replace("%s", "?"), params=list(params)).collect()

    try:
        return snapshot(query, execute, domain_id, "MODEL", note)
    except Exception:
        return None  # the registry is applied; the snapshot is a convenience for the Domains page


def apply_model_version(session, model_id: str, version: int) -> Dict[str, Any]:
    """CORE.APPLY_DOMAIN_MODEL: put an APPROVED (or PUBLISHED) version of a domain model into the registry."""
    from services.common.sql import variant

    found = _rows(session, """SELECT D.SPEC, D.STATUS, D.VERSION, M.DOMAIN_ID, M.DOMAIN_NAME, COALESCE(M.LAYER, 'SILVER') AS LAYER
                                FROM CORE.MODEL_DESIGNS D
                                JOIN CORE.DOMAIN_MODEL M ON M.MODEL_ID = D.MODEL_ID
                               WHERE D.MODEL_ID = ? AND D.VERSION = ?""", [model_id, int(version)])
    assert found, f"model version {version} not found"
    row = found[0]
    assert row["STATUS"] in ("APPROVED", "PUBLISHED"), f"only an approved version is applied (this one is {row['STATUS'].lower()})"
    spec = spec_v2.normalize_v2(variant(row["SPEC"]) or {}, model_id)
    layer = "GOLD" if row["LAYER"] == "GOLD" else "SILVER"
    result = apply_spec(session, spec, row["DOMAIN_ID"], model_id, int(version), layer)
    session.sql("UPDATE CORE.MODEL_DESIGNS SET REGISTRY_SYNCED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ? AND VERSION = ?",
                params=[model_id, int(version)]).collect()
    result["domain_version"] = _snapshot(session, row["DOMAIN_ID"], f"{row['DOMAIN_NAME']} model v{version} approved")
    _remember(session, row["DOMAIN_ID"], spec, result["registered"], model_id, int(version))
    return {"model_id": model_id, "version": int(version), "layer": layer, **result}


def _remember(session, domain_id: str, spec: Dict[str, Any], registered: List[Dict[str, Any]], model_id: str,
              version: int) -> None:
    """Each approved entity is MODEL_DEFINITION knowledge, so runs, the copilot and search cite the approved model."""
    try:
        from services.common.suggestion_stages import _knowledge
    except Exception:
        return
    by_name = {e["entity_name"]: e for e in spec["design"]["entities"]}
    for reg in registered:
        e = by_name.get(reg["entity"])
        if not e:
            continue
        content = {"target_table": e["entity_name"], "fqn": reg["fqn"], "kind": e["table_type"],
                   "purpose": e.get("description"), "grain": e.get("grain"), "business_keys": e.get("business_keys"),
                   "model_id": model_id, "model_version": version,
                   "columns": [{"name": a["name"], "datatype": a["datatype"], "nullable": a["nullable"], "is_pk": a["is_pk"],
                                "definition": a.get("business_definition") or a.get("description")}
                               for a in e["attributes"] if a.get("status") != "RETIRED"]}
        try:
            _knowledge(session, domain_id, "MODEL_DEFINITION",
                       f"Model {e['entity_name']}: {(e.get('description') or e['table_type'].lower())[:200]}",
                       content, f"model.{reg['fqn'].upper()}", ["DOMAIN_MODEL", f"v{version}"], "MODELING", None)
        except Exception:
            continue
