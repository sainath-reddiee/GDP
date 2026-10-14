"""Gold Code Generation inside the run engine: a Gold run's approved STTM (one gold table) as gold dbt models.

The STTM is the reviewed contract (which silver column feeds which gold column, with any transformation); the
approved GOLD domain model gives the table's shape and relationships; the mastered-domain contracts give the key
resolutions (services/gold/rules.py). The run builds its target table plus the proposed dimensions the table
references (one row per descriptor value, from the same silver rows); conformed dimensions (DIM_COMPANY,
DIM_PROPERTY) and other facts are referenced, never rebuilt. The output goes through the same storage, validation,
review and publishing as Silver code (services/dbt/procedures.py).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from services.gold import dbt as gold_dbt
from services.gold import rules as gold_rules
from services.gold.propose import AUDIT

ENGINE = gold_dbt.ENGINE
SKILL_VERSION = "GDP-GOLD:1.0.0"
AUDIT_COLUMNS = {n for n, _ in AUDIT}


def _fqn(line: Dict[str, Any]) -> Optional[str]:
    parts = [line.get("source_database"), line.get("source_schema"), line.get("source_table")]
    return ".".join(str(p) for p in parts).upper() if all(parts) else (str(line.get("source_table") or "").upper() or None)


def gold_lines(entity: Dict[str, Any], sttm_lines: List[Dict[str, Any]], spec: Dict[str, Any],
               by_rule: Dict[str, Dict[str, Any]], driving: Optional[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(gold mapping lines, assumptions) for the target table and its proposed dimensions (pure)."""
    table = entity["entity_name"]
    by_name = {e["entity_name"]: e for e in spec["design"]["entities"]}
    rels = {r["from_column"]: r["to_entity"] for r in spec.get("relationships") or [] if r["from_entity"] == table}
    lines: List[Dict[str, Any]] = []
    notes: List[str] = []
    sources = {_fqn(l) for l in sttm_lines if l.get("source_column") and _fqn(l)}
    driving = (driving or "").upper() or (sorted(sources)[0] if sources else None)
    if driving and "." not in driving:
        driving = next((s for s in sources if s.endswith("." + driving)), driving)

    def line(column: str, kind: str, src: Optional[str] = None, src_col: Optional[str] = None, **extra: Any) -> None:
        lines.append({"gold_table": table, "gold_column": column, "kind": kind, "source_table": src,
                      "source_column": src_col, **extra})

    for l in sttm_lines:
        col = str(l.get("target_column") or "").upper()
        src_col = str(l.get("source_column") or "").upper() or None
        src = _fqn(l)
        if col == f"{table}_SKEY":
            line(col, "KEY", expression=f"SEQ_{table}_SKEY.NEXTVAL (kept for an existing row)")
            continue
        if col == f"{table}_HKEY":
            line(col, "DERIVED")
            continue
        if col in AUDIT_COLUMNS:
            line(col, "AUDIT")
            continue
        if not src_col or str(l.get("mapping_type") or "").upper() == "UNMAPPED":
            continue   # generated as NULL with an assumption by the model
        if driving and src != driving:
            notes.append(f"{table}.{col}: comes from {src}, not the driving table {driving}; a gold table reads one silver "
                         "table, so it is generated as NULL: join it in review or map it from the driving table")
            continue
        rule = by_rule.get(col)
        if rule and rule["source_column"].upper() == src_col:
            if rule["kind"] == "RESOLVE_MDM":
                line(col, "RESOLVE", src, src_col, source_layer="MDM", expression=rule["expression"])
            else:
                line(col, "RESOLVE", src, src_col, expression=rule["expression"])
            continue
        if col in rels and not str(rels[col]).startswith("DIM_"):
            line(col, "RESOLVE", src, src_col, expression=f"{rels[col]} on the parent's source record")
            continue
        target = next((a for a in entity["attributes"] if a["name"].upper() == col), {})
        transform = str(l.get("transformation") or "").strip()
        if str(l.get("mapping_type") or "").upper() == "TRANSFORM" and transform:
            line(col, "COLUMN", src, src_col, sql=transform.replace("{col}", src_col))
        else:
            cast = target.get("datatype") not in (None, "STRING") and str(target.get("datatype")) != str(l.get("source_datatype") or "")
            line(col, "COLUMN", src, src_col, expression=f"cast to {target.get('datatype')}" if cast else None)
    # the proposed dimensions the table references, from the same silver rows
    for l in [x for x in lines if x["kind"] == "RESOLVE"]:
        dim = by_name.get(rels.get(l["gold_column"], ""))
        if not dim or dim.get("conformed_from") or dim.get("table_type") != "DIMENSION" or not dim.get("business_keys"):
            continue
        d = dim["entity_name"]
        lines += [{"gold_table": d, "gold_column": f"{d}_SKEY", "kind": "KEY"},
                  {"gold_table": d, "gold_column": dim["business_keys"][0], "kind": "COLUMN",
                   "source_table": l["source_table"], "source_column": l["source_column"], "expression": f"distinct {l['source_column']}"},
                  {"gold_table": d, "gold_column": f"{d}_HKEY", "kind": "DERIVED"}]
        lines += [{"gold_table": d, "gold_column": a, "kind": "AUDIT"} for a in AUDIT_COLUMNS]
    return lines, notes


def project_yml(spec: Dict[str, Any]) -> str:
    domain = spec["domain"].lower()
    phys = spec.get("physical_defaults") or {}
    return (f"# generated by {ENGINE}: used when the repository skeleton is not fetched; the gdp-dbt project's own\n"
            f"# dbt_project.yml maps models/gold/{domain} the same way\n"
            f"name: 'gdp_gold_{domain}'\nversion: '1.0.0'\nconfig-version: 2\nprofile: 'gdp'\nmodel-paths: ['models']\n"
            f"models:\n  gdp_gold_{domain}:\n    gold:\n      {domain}:\n"
            f"        +database: {phys.get('database')}\n        +schema: {phys.get('schema')}\n        +tags: ['gold']\n")


def build(spec: Dict[str, Any], entity: Dict[str, Any], sttm_lines: List[Dict[str, Any]], design: Dict[str, Any], *,
          mastered: Dict[str, Dict[str, Any]], consumes: List[Dict[str, Any]], conventions: Dict[str, Any],
          env: str, soda_yaml: str, skeleton: Optional[Dict[str, str]] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
    """{generated, files (skeleton + generated), report, lineage} for a Gold run (pure)."""
    source_columns = [str(l["source_column"]) for l in sttm_lines if l.get("source_column")]
    by_rule = gold_rules.rules(entity, source_columns, consumes, mastered, spec.get("relationships"))
    lines, notes = gold_lines(entity, sttm_lines, spec, by_rule, design.get("driving_table"))
    tables = [entity["entity_name"]] + sorted({l["gold_table"] for l in lines} - {entity["entity_name"]})
    out = gold_dbt.generate(spec, lines, mastered=mastered, consumes=consumes, conventions=conventions, env=env,
                            build={"run_id": run_id} if run_id else None, only=tables)
    generated = dict(out["files"])
    report = dict(out["report"])
    report["assumptions"] = sorted(set(report.get("assumptions") or []) | set(notes))
    generated["release/gold-generation-report.json"] = json.dumps(report, indent=2)
    generated["soda/checks.yml"] = soda_yaml
    generated["mappings/sttm.json"] = json.dumps({"design": design, "lines": sttm_lines, "gold_lines": lines}, indent=2, default=str)
    skeleton = skeleton or {}
    if "dbt_project.yml" not in skeleton:
        generated["dbt_project.yml"] = project_yml(spec)
    report["counts"] = {"files": len(generated), "tables": len(tables)}
    return {"generated": generated, "files": {**skeleton, **generated}, "lineage": out["lineage"],
            "report": {**report, "todos": report["assumptions"], "casts": [], "hub": entity["entity_name"]}}


def assemble_for_run(session, run: Dict[str, Any], sttm: Dict[str, Any], design: Dict[str, Any], soda_yaml: str,
                     skeleton: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Load what a Gold run's generation needs (Snowpark session) and build it."""
    from services.common.sql import rows, variant
    from services.gold import context
    from services.gold.conventions import learn

    ctx = context.for_run(session, run)
    assert ctx, ("NO_GOLD_TARGET: this Gold run is not pinned to a table of an approved gold model; choose the gold "
                 "table on the Modeling step and approve the mapping again")
    sttm_lines = [{str(k).lower(): v for k, v in r.items()}
                  for r in rows(session, "SELECT * FROM CONTRACT.STTM_LINE WHERE STTM_ID = ? ORDER BY TARGET_COLUMN",
                                [sttm["STTM_ID"]])]
    try:
        chunks = [{str(k).lower(): v for k, v in r.items()} for r in rows(session, """
            SELECT C.PATH, C.KIND, C.NAME, C.TEXT, C.COLUMNS FROM CODE.CODE_CHUNK C JOIN CODE.REPO R ON R.REPO_ID = C.REPO_ID
             WHERE R.ENABLED AND (C.TEXT ILIKE '%m_insert_history_gold(%' OR LOWER(C.NAME) = 'dim_data_source') LIMIT 200""")]
        for c in chunks:
            c["columns"] = variant(c.get("columns")) or []
        conventions, _ = learn(chunks)
    except Exception:
        conventions = {}
    from services.reference import store

    project = store.latest(lambda sql, params: rows(session, sql, params), run["DOMAIN_ID"], "GOLD", "DBT_CODE")
    learned = ((project or {}).get("parsed") or {}).get("gold_conventions") or {}
    conventions = {**conventions, **learned}   # the project a person gave for this domain wins over the indexed repos
    built = build(ctx["spec"], ctx["entity"], sttm_lines, design, mastered=ctx["mastered"], consumes=ctx["consumes"],
                 conventions=conventions, env=ctx["env"], soda_yaml=soda_yaml, skeleton=skeleton, run_id=run.get("RUN_ID"))
    if project and (project.get("parsed") or {}).get("files"):
        from services.reference import score

        store.record(lambda sql, params: session.sql(sql, params=params).collect(), domain_id=run["DOMAIN_ID"], layer="GOLD",
                     kind="DBT_CODE", reference_id=project["reference_id"], run_id=run.get("RUN_ID"), stage="CODE",
                     score=score.code(built["generated"], project["parsed"]["files"], of="generated"))
    return built
