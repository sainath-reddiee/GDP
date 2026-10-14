"""Gold mapping rules (pure): which silver column feeds which gold column of a Gold run's target, and how.

A Gold run maps silver (read in place) into one gold table of the domain's approved GOLD model. Most gold columns
carry a silver column of the same name, which the mapping engine scores on its own; the rules here cover what name
matching cannot see, so they are proposed with full confidence and explained:
- a mastered key: the silver hub's pre-MDM key (COMPANY_CORE_SKEY, or a role-prefixed one such as
  LANDLORD_COMPANY_CORE_SKEY) feeds the conformed gold key (DIM_COMPANY_SKEY, LANDLORD_DIM_COMPANY_SKEY), resolved
  through the full cross-reference of the mastered-domain contract;
- the data source: REF_GDP_SOURCE_SYSTEM_SKEY feeds DIM_DATA_SOURCE_SKEY;
- a proposed dimension's key: the descriptor column (OPPORTUNITY_STAGE) feeds DIM_<STEM>_<COLUMN>_SKEY.
The same rules shape the STTM line (a LOOKUP with the resolution spelled out) and the gold dbt model.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DATA_SOURCE_COLUMN, DATA_SOURCE_KEY = "REF_GDP_SOURCE_SYSTEM_SKEY", "DIM_DATA_SOURCE_SKEY"


def _consumed(consumes: List[Dict[str, Any]], mastered: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each consumed mastered domain with its silver and gold key names and its contract."""
    out = []
    for c in consumes or []:
        dom = str(c.get("domain") or "").upper()
        contract = mastered.get(dom) or {}
        gold = contract.get("gold") or {}
        silver_col = str(c.get("silver_column") or (contract.get("pre_mdm") or {}).get("skey") or "").upper()
        gold_col = str(c.get("gold_column") or gold.get("skey") or "").upper()
        if dom and silver_col and gold_col:
            out.append({"domain": dom, "silver_column": silver_col, "gold_column": gold_col, "contract": contract})
    return out


def resolution_text(entry: Dict[str, Any], source_column: str, target_column: str) -> str:
    """How a mastered key is resolved, in the words reviewers and the dbt generator read (names the gold dimension)."""
    c = entry["contract"]
    pre, xref, gold = c.get("pre_mdm") or {}, c.get("cross_reference") or {}, c.get("gold") or {}
    return (f"{source_column} -> {pre.get('table', entry['domain'] + '_CORE')} (SOURCE_UNIQUE_ID + source system) -> "
            f"{xref.get('table', entry['domain'] + '_CROSS_REFERENCE')} on {xref.get('source_key', 'SOURCE_UNIQUE_ID')} + "
            f"upper source-system name (full cross-reference, active, not deleted, latest) -> "
            f"{gold.get('dimension', 'DIM_' + entry['domain'])}.{gold.get('skey', target_column)}; -1 when unresolved")


def rule_for(target_column: str, target_entity: Dict[str, Any], source_columns: List[str],
             consumes: List[Dict[str, Any]], mastered: Dict[str, Dict[str, Any]],
             relationships: Optional[List[Dict[str, Any]]] = None,
             entities: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """{source_column, kind RESOLVE_MDM | DATA_SOURCE | DIMENSION_KEY, domain?, dimension?, expression} or None."""
    col = target_column.upper()
    sources = {s.upper() for s in source_columns}
    if col == DATA_SOURCE_KEY and DATA_SOURCE_COLUMN in sources:
        return {"source_column": DATA_SOURCE_COLUMN, "kind": "DATA_SOURCE", "dimension": "DIM_DATA_SOURCE",
                "expression": "DIM_DATA_SOURCE on the source system name of REF_GDP_SOURCE_SYSTEM (by "
                              "REF_GDP_SOURCE_SYSTEM_SKEY); -1 when unresolved"}
    for entry in _consumed(consumes, mastered):
        if not col.endswith(entry["gold_column"]):
            continue
        prefix = col[: -len(entry["gold_column"])]
        source = f"{prefix}{entry['silver_column']}"
        if source in sources:
            return {"source_column": source, "kind": "RESOLVE_MDM", "domain": entry["domain"],
                    "dimension": (entry["contract"].get("gold") or {}).get("dimension"),
                    "expression": resolution_text(entry, source, col)}
    rel = next((r for r in relationships or [] if r.get("from_entity") == target_entity.get("entity_name")
                and str(r.get("from_column") or "").upper() == col), None)
    dim = str((rel or {}).get("to_entity") or "")
    if dim.startswith("DIM_") and col == f"{dim}_SKEY":
        # DIM_<STEM>_<COLUMN>_SKEY: the longest source column the dimension's name ends with
        value = max((s for s in sources if dim.endswith(f"_{s}")), key=len, default=None)
        if value:
            return {"source_column": value, "kind": "DIMENSION_KEY", "dimension": dim,
                    "expression": f"{dim} on {value}; -1 when the value is not in the dimension"}
        # a mastered domain's child dimension: the parent dimension's business key (the mastered id) is in the row
        parent = next((e for e in entities or [] if str(e.get("entity_name") or "").upper() == dim.upper()), None)
        key = next((k.upper() for k in (parent or {}).get("business_keys") or [] if k.upper() in sources), None)
        if key:
            return {"source_column": key, "kind": "DIMENSION_KEY", "dimension": dim,
                    "expression": f"{dim} on {key}; -1 when unresolved"}
    return None


def rules(target_entity: Dict[str, Any], source_columns: List[str], consumes: List[Dict[str, Any]],
          mastered: Dict[str, Dict[str, Any]], relationships: Optional[List[Dict[str, Any]]] = None,
          entities: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """The rule of every gold column of the target that has one, keyed by gold column."""
    out = {}
    for a in target_entity.get("attributes") or []:
        if a.get("status") == "RETIRED":
            continue
        r = rule_for(a["name"], target_entity, source_columns, consumes, mastered, relationships, entities)
        if r:
            out[a["name"].upper()] = r
    return out


def shape_sttm(lines: List[Dict[str, Any]], entity: Dict[str, Any], by_target: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A Gold run's STTM lines in gold terms (pure). A key fed by a rule is a LOOKUP whose resolution is spelled out
    (LOOKUP_LOGIC and the business rule; the transformation stays empty, since QA and the dbt check read it as SQL).
    The gold model's own columns follow the gold rules, not the silver ones: the surrogate key comes from the table's
    sequence (stable for an existing row), the HKEY hashes the business columns, the audit columns are set by the load."""
    table = str(entity.get("entity_name") or "").upper()
    out = []
    for line in lines:
        l = dict(line)
        col = str(l.get("target_column") or "").upper()
        rule = by_target.get(col)
        if rule and str(l.get("source_column") or "").upper() == rule["source_column"].upper():
            l.update(mapping_type="LOOKUP", transformation=None, lookup_logic=rule["expression"],
                     business_rule=rule["expression"] if not l.get("business_rule") else l["business_rule"])
        elif col == f"{table}_SKEY":
            l.update(transformation=None, business_rule=f"SEQ_{table}_SKEY.NEXTVAL; an existing row keeps its key (merge on the business key)")
        elif col == f"{table}_HKEY":
            l.update(transformation=None, business_rule="SHA2 of the business columns, to detect changed rows")
        elif col in ("GDP_INSERTED_TS", "GDP_UPDATED_TS"):
            l.update(transformation="CURRENT_TIMESTAMP()::TIMESTAMP_NTZ", business_rule="set by the gold load")
        elif col in ("GDP_INSERTED_BY", "GDP_UPDATED_BY"):
            l.update(transformation="CURRENT_USER()", business_rule="set by the gold load")
        out.append(l)
    return out
