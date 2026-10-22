"""What depends on the parts of a domain model a version changes in a breaking way (reads only).

For each breaking change (services/modeling/model_diff.breaking_paths) the reviewer sees what already uses that
entity or column: STTM lines, data quality checks of runs and of the Data Quality Store, saved QA tests, generated dbt
files and open runs targeting the entity. Tables are matched by their last name part, so a reference stored as a
bare name or as DATABASE.SCHEMA.TABLE is found either way. Every source fails soft: one that is not readable is listed
under `unavailable` instead of failing the review.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

Query = Callable[[str, list], List[Dict[str, Any]]]

LIMIT = 50


def _low(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{str(k).lower(): v for k, v in r.items()} for r in rows]


SOURCES: List[Tuple[str, str, str]] = [
    # (key, label, SQL with {table} and {column} predicates over the entity and column binds)
    ("sttm", "Data Mapping lines", """
        SELECT L.TARGET_TABLE AS ENTITY, L.TARGET_COLUMN AS COLUMN_NAME, S.RUN_ID, S.STTM_ID, S.STATUS,
               L.SOURCE_TABLE || '.' || COALESCE(L.SOURCE_COLUMN, '') AS DETAIL
          FROM CORE.STTM_LINE L JOIN CORE.STTM_REGISTRY S ON S.STTM_ID = L.STTM_ID
         WHERE S.STATUS IN ('DRAFT', 'REVIEW', 'APPROVED') AND {table} AND {column}"""),
    ("run_checks", "Run data quality checks", """
        SELECT TARGET_TABLE AS ENTITY, TARGET_COLUMN AS COLUMN_NAME, RUN_ID, EXPECTATION_ID AS ID, STATUS,
               CHECK_TYPE AS DETAIL
          FROM CORE.SODA_EXPECTATION_REGISTRY WHERE IS_CURRENT AND STATUS <> 'REJECTED' AND {table} AND {column}"""),
    ("store_checks", "Data Quality Store checks", """
        SELECT TABLE_NAME AS ENTITY, COLUMN_NAME, NULL AS RUN_ID, CHECK_ID AS ID, STATUS, SODA_DSL AS DETAIL
          FROM CORE.DQ_CHECKS_CATALOG WHERE STATUS <> 'RETIRED' AND {table} AND {column}"""),
    ("qa_tests", "Saved QA tests", """
        SELECT TARGET_FQN AS ENTITY, TARGET_COLUMN AS COLUMN_NAME, RUN_ID, TEST_ID AS ID, NULL AS STATUS, TITLE AS DETAIL
          FROM CORE.QA_TEST_CASE WHERE NOT COALESCE(IS_DELETED, FALSE) AND {table} AND {qa_column}"""),
    ("dbt", "Generated dbt files", """
        SELECT G.TARGET_MODEL AS ENTITY, NULL AS COLUMN_NAME, G.RUN_ID, A.FILE_PATH AS ID, G.GENERATION_STATUS AS STATUS,
               'v' || G.GENERATION_VERSION AS DETAIL
          FROM CORE.DBT_GENERATION_REGISTRY G JOIN CORE.GENERATED_ARTIFACT A ON A.GENERATION_ID = G.GENERATION_ID
         WHERE G.GENERATION_STATUS NOT IN ('SUPERSEDED', 'FAILED') AND A.FILE_PATH ILIKE 'models/%%'
           AND {dbt_table} AND {dbt_column}"""),
    ("runs", "Open runs targeting the entity", """
        SELECT TARGET_MODEL AS ENTITY, NULL AS COLUMN_NAME, RUN_ID, RUN_NAME AS ID, CURRENT_STATE AS STATUS,
               NULL AS DETAIL
          FROM CORE.WORKFLOW_RUN WHERE NOT COALESCE(IS_ARCHIVED, FALSE) AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
           AND {run_table}"""),
]


def _predicates(key: str, column: str) -> Dict[str, str]:
    """SQL predicates for one source; `?` binds are the entity name, then the column name when one is given."""
    table_expr = {"sttm": "L.TARGET_TABLE", "run_checks": "TARGET_TABLE", "store_checks": "TABLE_NAME",
                  "qa_tests": "TARGET_FQN"}.get(key, "TARGET_TABLE")
    preds = {
        "table": f"UPPER(SPLIT_PART({table_expr}, '.', -1)) = ?",
        "column": ("UPPER(" + ("L.TARGET_COLUMN" if key == "sttm" else ("COLUMN_NAME" if key == "store_checks" else "TARGET_COLUMN"))
                   + ") = ?") if column else "TRUE",
        "qa_column": "(UPPER(TARGET_COLUMN) = ? OR SQL_TEXT ILIKE '%' || ? || '%')" if column else "TRUE",
        "dbt_table": "UPPER(SPLIT_PART(G.TARGET_MODEL, '.', -1)) = ?",
        "dbt_column": "A.CONTENT ILIKE '%' || ? || '%'" if column else "TRUE",
        "run_table": "UPPER(SPLIT_PART(TARGET_MODEL, '.', -1)) = ?",
    }
    return preds


def _binds(key: str, entity: str, column: str) -> list:
    if key == "runs":
        return [entity]
    if key == "qa_tests":
        return [entity] + ([column, column] if column else [])
    return [entity] + ([column] if column else [])


def analyze(query: Query, paths: List[Dict[str, str]]) -> Dict[str, Any]:
    """{items: [{entity, column, kind, refs: {source: [rows]}, total}], totals: {source: n}, unavailable: [source]}.
    `query(sql, params)` uses ? binds (a Snowpark session wrapper)."""
    items, totals, unavailable = [], {k: 0 for k, _, _ in SOURCES}, set()
    for p in paths[:100]:
        entity, column = str(p.get("entity") or "").upper(), str(p.get("column") or "").upper()
        if not entity:
            continue
        refs: Dict[str, List[Dict[str, Any]]] = {}
        for key, _label, sql in SOURCES:
            if key in unavailable or (key == "runs" and column):
                continue
            text = sql.format(**_predicates(key, column)).replace("%%", "%")
            try:
                found = _low(query(text + f" LIMIT {LIMIT}", _binds(key, entity, column)))
            except Exception:
                unavailable.add(key)
                continue
            if found:
                refs[key] = found
                totals[key] += len(found)
        items.append({"entity": entity, "column": column or None, "kind": p.get("kind"), "refs": refs,
                      "total": sum(len(v) for v in refs.values())})
    return {"items": items, "totals": totals, "unavailable": sorted(unavailable),
            "labels": {k: label for k, label, _ in SOURCES}}


def for_versions(session, model_id: str, from_version: Optional[int], to_version: int) -> Dict[str, Any]:
    """Impact of going from one version to another (default: from the approved version)."""
    from services.common.sql import rows
    from services.modeling import domain_model, model_diff

    changes = domain_model.diff_versions(session, model_id, from_version, to_version)
    paths = model_diff.breaking_paths(changes)
    result = analyze(lambda sql, params: rows(session, sql, params), paths)
    return {"from": from_version, "to": to_version, "breaking": changes["breaking"], "counts": changes["counts"], **result}
