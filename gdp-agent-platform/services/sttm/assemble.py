"""Deterministic STTM assembly from approved mapping decisions and target metadata (pure)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.mapping.features import SYSTEM_DERIVED


def mapping_type(transformation: Optional[str], semantic_type: Optional[str], decided: bool) -> str:
    if semantic_type in SYSTEM_DERIVED:
        return "DERIVED"
    if not decided:
        return "UNMAPPED"
    if transformation:
        return "TRANSFORM"
    return "DIRECT"


def derived_expression(column: Dict[str, Any], source_system: str) -> str:
    semantic = column.get("semantic_type")
    if semantic == "SURROGATE_KEY":
        keys = column.get("business_keys") or ["customer_id"]
        return "MD5(CAST(" + " || '|' || ".join(k.lower() for k in keys) + " AS VARCHAR))"
    if semantic == "RECORD_SOURCE":
        return f"'{source_system}'"
    if semantic == "AUDIT_TIMESTAMP":
        return "CURRENT_TIMESTAMP()::TIMESTAMP_NTZ"
    return "NULL"


def assemble(target: Dict[str, Any], columns: List[Dict[str, Any]], decisions: Dict[str, Dict[str, Any]],
             source_system: str, source_database: str, source_schema: str) -> Dict[str, Any]:
    """columns are target columns; decisions keyed by TARGET_COLUMN_ID (approved ones only)."""
    keys = [c["column_name"] for c in columns if c.get("is_business_key")]
    lines = []
    for col in columns:
        decision = decisions.get(col["target_column_id"])
        semantic = col.get("semantic_type")
        decided = bool(decision)
        transformation = (decision or {}).get("transformation")
        if semantic in SYSTEM_DERIVED:
            transformation = derived_expression({**col, "business_keys": keys}, source_system)
            decided = True
        mtype = mapping_type(transformation, semantic, decided)
        src = decision or {}
        lines.append({
            "target_column": col["column_name"],
            "target_datatype": col["data_type"],
            "target_column_id": col["target_column_id"],
            "source_system": src.get("source_system") or (source_system if decided and semantic not in SYSTEM_DERIVED else None),
            "source_database": src.get("source_database") or (source_database if decided and semantic not in SYSTEM_DERIVED else None),
            "source_schema": src.get("source_schema") or (source_schema if decided and semantic not in SYSTEM_DERIVED else None),
            "source_table": src.get("source_table"),
            "source_column": src.get("source_column"),
            "source_datatype": src.get("source_datatype"),
            "mapping_type": mtype,
            "transformation": transformation,
            "business_rule": src.get("business_justification"),
            "business_definition": col.get("definition"),
            "nullable_rule": bool(col.get("nullable")),
            "uniqueness_rule": bool(col.get("is_business_key")),
            "accepted_values": col.get("accepted_values") or [],
            "scd_behavior": "TYPE_1" if target.get("scd_type") == "1" else target.get("scd_type"),
            "mapping_confidence": src.get("confidence"),
            "human_approved": decided and semantic not in SYSTEM_DERIVED,
            "decision_id": src.get("decision_id"),
            "reviewer": src.get("reviewer"),
            "required": not col.get("nullable") and semantic not in SYSTEM_DERIVED,
        })

    unmapped_required = [l["target_column"] for l in lines if l["mapping_type"] == "UNMAPPED" and l["required"]]
    source_tables = sorted({l["source_table"] for l in lines if l["source_table"]})
    design = {
        "grain": target.get("grain"),
        "business_keys": keys,
        "source_tables": source_tables,
        "join_paths": [],
        "dedup": f"keep the latest row per {', '.join(keys)}" if keys else "one row per source object",
        "scd_type": target.get("scd_type") or "1",
        "incremental_strategy": "merge",
        "target_database": target.get("target_database"),
        "target_schema": target.get("target_schema"),
        "target_table": target.get("table_name") or target.get("target_table"),
    }
    return {"table_design": design, "lines": lines, "unmapped_required": unmapped_required}
