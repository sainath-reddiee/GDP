"""Profiling Store governance (V041): scheduled profiling, comparing any two passes, and reviewed column annotations.

Schedules. A schedule profiles a database.schema (every table, or a list) on a cron. It is a Snowflake task
PROFILE.PROF_<hash> that calls PROFILE.RUN_SCHEDULED_PROFILE(SCHEDULE_ID) (owner's rights: the owner role needs SELECT
on the scheduled tables). Each run uses the normal in-place profiling path (the cache skips tables whose fingerprint
did not change unless FORCE_REFRESH), and records the outcome on the schedule, including the tables whose newest
pass drifted from the one before.

Compare. Any two passes of a table, column by column: type, nulls, distinct values, semantic type, PII and range,
with the same drift flags the drawer shows against the previous pass.

Annotations. What people know about a column that the profiler cannot tell (description, business definition, PII
class, semantic type, accepted values) is proposed, then approved or rejected by another person. An approved
annotation supersedes the previous one for the column and overrides the profiler's guess in every profile view. When
the table is an entity of its domain's approved model and has the column, the annotation is also proposed to the
model as a GOVERNANCE.MODEL_CHANGE (SOURCE 'ANNOTATION') carrying the fields as a PATCH; modelers merge it into the
draft from the Model Workbench (services/modeling/run_link.merge_change applies the patch), so the model only changes
through its own review.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.common.sql import clip, insert_rows, rows, scalar, variant

SCHEDULES = "GOVERNANCE.PROFILE_SCHEDULE"
ANNOTATIONS = "GOVERNANCE.PROFILE_ANNOTATION"
CHANGES = "GOVERNANCE.MODEL_CHANGE"
CRON = re.compile(r"^\s*(\S+\s+){4}\S+(\s+[A-Za-z_]+(?:/[A-Za-z_]+)*)?\s*$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
CLASS = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
FIELDS = ("description", "business_definition", "pii_classification", "semantic_type", "accepted_values")


def _low(r: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in r.items()}


def _ts(value: Any) -> Optional[str]:
    return str(value)[:19] if value else None


def _parts(fqn: str) -> List[str]:
    parts = [p.strip('"') for p in re.findall(r'"(?:[^"]|"")+"|[^.]+', str(fqn or "").strip())]
    assert len(parts) == 3 and all(parts), "table must be DATABASE.SCHEMA.TABLE"
    return parts


def table_key(fqn: str) -> str:
    return ".".join(p.upper() for p in _parts(fqn))


# ---------------------------------------------------------------- pure: schedules


def task_name(schedule_id: str) -> str:
    return "PROF_" + hashlib.md5(schedule_id.encode("utf-8")).hexdigest()[:16].upper()


def schedule_sql(database: str, schedule_id: str, cron: str, warehouse: str) -> Tuple[str, List[str]]:
    """(task fqn, statements) for a profiling schedule; identifiers and the cron are validated, the id is a UUID."""
    assert NAME.match(database or ""), "unsafe database name"
    assert NAME.match(warehouse or ""), "unsafe warehouse name"
    assert CRON.match(cron or ""), "cron needs five fields and an optional time zone, e.g. 0 5 * * * UTC"
    uuid.UUID(schedule_id)
    task = f"{database}.PROFILE.{task_name(schedule_id)}"
    return task, [
        f"CREATE OR REPLACE TASK {task} WAREHOUSE = {warehouse} SCHEDULE = 'USING CRON {cron.strip()}' "
        f"COMMENT = 'Profiling Store: scheduled profiling {schedule_id}' "
        f"AS CALL {database}.PROFILE.RUN_SCHEDULED_PROFILE('{schedule_id}')",
        f"ALTER TASK {task} RESUME",
    ]


# ---------------------------------------------------------------- pure: compare two passes


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def compare(a: List[Dict[str, Any]], b: List[Dict[str, Any]], null_jump: float = 10.0,
            distinct_jump: float = 0.5) -> Dict[str, Any]:
    """Pass `a` (older) against pass `b` (newer), column by column (rows keyed by lower-case names, as the API
    returns them). Each column: status added, removed, changed or same; both sides' metrics; the deltas; the flags."""
    keys = ("data_type", "semantic_type", "pii_classification", "null_pct", "distinct_count", "cardinality",
            "min_value", "max_value", "row_count")
    before = {r["column_name"]: r for r in a}
    after = {r["column_name"]: r for r in b}
    order = [r["column_name"] for r in b] + [r["column_name"] for r in a if r["column_name"] not in after]
    out = []
    for name in order:
        x, y = before.get(name), after.get(name)
        flags: List[str] = []
        if x is None:
            flags.append("added")
        elif y is None:
            flags.append("removed")
        else:
            if (x.get("data_type") or "") != (y.get("data_type") or ""):
                flags.append(f"type {x.get('data_type')} -> {y.get('data_type')}")
            n1, n2 = _num(x.get("null_pct")), _num(y.get("null_pct"))
            if n1 is not None and n2 is not None and abs(n2 - n1) >= null_jump:
                flags.append(f"nulls {n1:.1f}% -> {n2:.1f}%")
            d1, d2 = _num(x.get("distinct_count")), _num(y.get("distinct_count"))
            if d1 and d2 is not None and abs(d2 - d1) / d1 > distinct_jump:
                flags.append(f"distinct {int(d1)} -> {int(d2)}")
            for k, label in (("semantic_type", "semantic type"), ("pii_classification", "PII")):
                if (x.get(k) or "") != (y.get(k) or ""):
                    flags.append(f"{label} {x.get(k) or '-'} -> {y.get(k) or '-'}")
        n1 = _num((x or {}).get("null_pct"))
        n2 = _num((y or {}).get("null_pct"))
        out.append({"column_name": name, "status": "added" if x is None else "removed" if y is None else
                    ("changed" if flags else "same"), "flags": flags,
                    "a": {k: (x or {}).get(k) for k in keys} if x else None,
                    "b": {k: (y or {}).get(k) for k in keys} if y else None,
                    "null_delta": round(n2 - n1, 2) if n1 is not None and n2 is not None else None})
    rows_a = next((_num(r.get("row_count")) for r in a if r.get("row_count") is not None), None)
    rows_b = next((_num(r.get("row_count")) for r in b if r.get("row_count") is not None), None)
    return {"columns": out, "row_count": {"a": rows_a, "b": rows_b,
                                          "delta": (rows_b - rows_a) if rows_a is not None and rows_b is not None else None},
            "counts": {s: sum(1 for c in out if c["status"] == s) for s in ("added", "removed", "changed", "same")}}


# ---------------------------------------------------------------- pure: annotations


def clean_annotation(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The fields an annotation sets (only the given ones), or problems."""
    problems, out = [], {}
    for k in ("description", "business_definition"):
        v = str(payload.get(k) or "").strip()
        if v:
            out[k] = v[:4000]
    for k in ("pii_classification", "semantic_type"):
        v = str(payload.get(k) or "").strip().upper().replace(" ", "_")
        if v:
            if CLASS.match(v):
                out[k] = v
            else:
                problems.append(f"{k.replace('_', ' ')} is letters, digits and _ (2 to 64)")
    values = payload.get("accepted_values")
    if values:
        if not isinstance(values, list) or len(values) > 200 or any(len(str(v)) > 256 for v in values):
            problems.append("accepted values are a list of up to 200 short values")
        else:
            out["accepted_values"] = [str(v) for v in values if str(v).strip()]
    if not out and not problems:
        problems.append("set at least one of description, business definition, PII class, semantic type or accepted values")
    return out, problems


def patch_for(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Annotation fields in the domain model's attribute shape."""
    out: Dict[str, Any] = {}
    for k in ("description", "business_definition", "semantic_type", "accepted_values"):
        if fields.get(k):
            out[k] = fields[k]
    if fields.get("pii_classification"):
        cls = fields["pii_classification"]
        out["pii"] = {"flag": cls != "NONE", "classification": None if cls == "NONE" else cls}
    return out


def apply_patch(spec: Dict[str, Any], entity: str, column: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of the specification with the column's fields set (entity and column matched case-insensitively)."""
    out = json.loads(json.dumps(spec, default=str))
    for e in out.get("design", {}).get("entities", []):
        if str(e.get("entity_name")).upper() != entity.upper():
            continue
        for a in e.get("attributes") or []:
            if str(a.get("name")).upper() == column.upper():
                a.update(json.loads(json.dumps(patch)))
                return out
    raise AssertionError(f"{entity}.{column} is not in the model draft")


# ---------------------------------------------------------------- Snowpark: scheduled profiling


def _drifted(session, database: str, schema: str, tables: Sequence[str]) -> List[Dict[str, Any]]:
    """Tables whose newest pass drifted from the one before, with the changed columns."""
    from services.catalog.profiles import drift_between

    out = []
    for t in tables[:200]:
        try:
            passes = [r["PROFILE_ID"] for r in rows(session, """SELECT PROFILE_ID FROM GOVERNANCE.DATA_PROFILES
                                                                 WHERE UPPER(DATABASE_NAME) = UPPER(?) AND UPPER(SCHEMA_NAME) = UPPER(?)
                                                                   AND UPPER(TABLE_NAME) = UPPER(?)
                                                                 GROUP BY PROFILE_ID ORDER BY MAX(PROFILED_AT) DESC LIMIT 2""",
                                                         [database, schema, t])]
            if len(passes) < 2:
                continue
            cols = {}
            for pid in passes:
                cols[pid] = [_low(r) for r in rows(session, """SELECT COLUMN_NAME, DATA_TYPE, NULL_PCT, DISTINCT_COUNT
                                                                 FROM GOVERNANCE.DATA_PROFILES WHERE PROFILE_ID = ?""", [pid])]
            drift = drift_between(cols[passes[1]], cols[passes[0]])
            if drift["changed"]:
                out.append({"table": t, "columns": {k: v for k, v in list(drift["columns"].items())[:20]}})
        except Exception:
            continue
    return out


def run_scheduled(session, schedule_id: str) -> Dict[str, Any]:
    """PROFILE.RUN_SCHEDULED_PROFILE (owner's rights, called by the schedule's task or Run now)."""
    from services.profiling.procedures import profile_source_tables, source_table_refs

    found = rows(session, f"""SELECT SOURCE_SYSTEM_ID, DATABASE_NAME, SCHEMA_NAME, TABLES, FORCE_REFRESH, ENABLED
                               FROM {SCHEDULES} WHERE SCHEDULE_ID = ?""", [schedule_id])
    assert found, "schedule not found"
    s = found[0]
    tables = list(variant(s.get("TABLES")) or [])
    status, result = "OK", {}
    try:
        if not tables:
            _, refs = source_table_refs(session, s["SOURCE_SYSTEM_ID"])
            tables = [r.table for r in refs][:500]
        assert tables, "the schema has no tables"
        out = profile_source_tables(session, s["SOURCE_SYSTEM_ID"],
                                    json.dumps({"tables": tables, "force_refresh": bool(s.get("FORCE_REFRESH"))}))
        result = {"profiled": out.get("profiled", []), "cached": out.get("cached", []), "failed": out.get("failed", [])[:50],
                  "drifted": _drifted(session, s["DATABASE_NAME"], s["SCHEMA_NAME"], out.get("profiled", []))}
        status = "PARTIAL" if result["failed"] else "OK"
    except Exception as exc:
        status, result = "FAILED", {"error": clip(exc, 1000)}
    session.sql(f"""UPDATE {SCHEDULES} SET LAST_RUN_AT = CURRENT_TIMESTAMP(), LAST_STATUS = ?, LAST_RESULT = PARSE_JSON(?),
                           UPDATED_AT = CURRENT_TIMESTAMP() WHERE SCHEDULE_ID = ?""",
                params=[status, json.dumps(result, default=str), schedule_id]).collect()
    return {"schedule_id": schedule_id, "status": status, **{k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}}


# ---------------------------------------------------------------- Snowpark: annotations


ANNOTATION_FIELDS = """ANNOTATION_ID, TABLE_FQN, COLUMN_NAME, DESCRIPTION, BUSINESS_DEFINITION, PII_CLASSIFICATION,
                       SEMANTIC_TYPE, ACCEPTED_VALUES, DETECTED, NOTE, STATUS, PROPOSED_BY, PROPOSED_AT, DECIDED_BY,
                       DECIDED_AT, DECISION_NOTE, MODEL_ID, MODEL_CHANGE_ID"""


def _annotation(r: Dict[str, Any]) -> Dict[str, Any]:
    a = _low(r)
    a["accepted_values"] = variant(a.get("accepted_values")) or []
    a["detected"] = variant(a.get("detected")) or {}
    a["proposed_at"], a["decided_at"] = _ts(a.get("proposed_at")), _ts(a.get("decided_at"))
    return a


def list_annotations(session, table: Optional[str] = None, status: Optional[str] = None,
                     limit: int = 200) -> List[Dict[str, Any]]:
    where, params = [], []
    if table:
        where.append("TABLE_FQN = ?")
        params.append(table_key(table))
    if status:
        where.append("STATUS = ?")
        params.append(status.upper())
    sql = (f"SELECT {ANNOTATION_FIELDS} FROM {ANNOTATIONS}" + (f" WHERE {' AND '.join(where)}" if where else "")
           + f" ORDER BY PROPOSED_AT DESC LIMIT {max(1, min(int(limit), 500))}")
    return [_annotation(r) for r in rows(session, sql, params)]


def _detected(session, table: str, column: str) -> Dict[str, Any]:
    db, schema, name = _parts(table)
    try:
        found = rows(session, """SELECT DESCRIPTION, PII_CLASSIFICATION, SEMANTIC_TYPE FROM GOVERNANCE.DATA_PROFILES
                                  WHERE UPPER(DATABASE_NAME) = UPPER(?) AND UPPER(SCHEMA_NAME) = UPPER(?)
                                    AND UPPER(TABLE_NAME) = UPPER(?) AND UPPER(COLUMN_NAME) = UPPER(?)
                                  ORDER BY PROFILED_AT DESC LIMIT 1""", [db, schema, name, column])
    except Exception:
        found = []
    return _low(found[0]) if found else {}


def propose(session, payload_json: str, user: str) -> Dict[str, Any]:
    """A proposed annotation of one column. A newer proposal by anyone replaces an older pending one for the column."""
    payload = json.loads(payload_json or "{}")
    table = table_key(payload.get("table") or "")
    column = str(payload.get("column") or "").strip().upper()
    assert column and len(column) <= 256, "name the column"
    fields, problems = clean_annotation(payload)
    assert not problems, "; ".join(problems)
    detected = _detected(session, table, column)
    session.sql(f"""UPDATE {ANNOTATIONS} SET STATUS = 'WITHDRAWN', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP(),
                           DECISION_NOTE = 'replaced by a newer proposal'
                     WHERE TABLE_FQN = ? AND COLUMN_NAME = ? AND STATUS = 'PROPOSED'""",
                params=[user.upper(), table, column]).collect()
    annotation_id = str(uuid.uuid4())
    insert_rows(session, ANNOTATIONS,
                ["ANNOTATION_ID", "TABLE_FQN", "COLUMN_NAME", "DESCRIPTION", "BUSINESS_DEFINITION", "PII_CLASSIFICATION",
                 "SEMANTIC_TYPE", "ACCEPTED_VALUES", "DETECTED", "NOTE", "STATUS", "PROPOSED_BY"],
                ["?", "?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')", "NULLIF(?, '')",
                 "PARSE_JSON(NULLIF(?, ''))::ARRAY", "PARSE_JSON(?)", "NULLIF(?, '')", "'PROPOSED'", "?"],
                [[annotation_id, table, column, fields.get("description") or "", fields.get("business_definition") or "",
                  fields.get("pii_classification") or "", fields.get("semantic_type") or "",
                  json.dumps(fields["accepted_values"]) if fields.get("accepted_values") else "",
                  json.dumps(detected, default=str), clip(payload.get("note"), 2000), user.upper()]])
    return {"annotation_id": annotation_id, "table": table, "column": column, "status": "PROPOSED", "fields": fields}


def _pending(session, annotation_id: str) -> Dict[str, Any]:
    found = rows(session, f"SELECT {ANNOTATION_FIELDS} FROM {ANNOTATIONS} WHERE ANNOTATION_ID = ?", [annotation_id])
    assert found, "annotation not found"
    a = _annotation(found[0])
    assert a["status"] == "PROPOSED", f"the annotation is {a['status'].lower()}"
    return a


def withdraw(session, annotation_id: str, user: str) -> Dict[str, Any]:
    a = _pending(session, annotation_id)
    assert str(a.get("proposed_by") or "").upper() == user.upper(), "only the person who proposed it can withdraw it"
    session.sql(f"""UPDATE {ANNOTATIONS} SET STATUS = 'WITHDRAWN', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP()
                     WHERE ANNOTATION_ID = ?""", params=[user.upper(), annotation_id]).collect()
    return {"annotation_id": annotation_id, "status": "WITHDRAWN"}


def reject(session, annotation_id: str, user: str, note: str) -> Dict[str, Any]:
    a = _pending(session, annotation_id)
    assert str(a.get("proposed_by") or "").upper() != user.upper(), "you proposed it; withdraw it instead"
    assert len((note or "").strip()) >= 3, "say why the annotation is rejected"
    session.sql(f"""UPDATE {ANNOTATIONS} SET STATUS = 'REJECTED', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP(),
                           DECISION_NOTE = ? WHERE ANNOTATION_ID = ?""",
                params=[user.upper(), clip(note, 2000), annotation_id]).collect()
    return {"annotation_id": annotation_id, "status": "REJECTED"}


def _model_entity(session, table: str) -> Optional[Dict[str, Any]]:
    """The approved model and entity that `table` is, if any: {model_id, version, spec, entity}."""
    from services.catalog import dq, dq_suite
    from services.modeling import spec_v2

    domain = dq._domain_for(session, table, None)
    if not domain:
        return None
    try:
        found = rows(session, """SELECT M.MODEL_ID, M.APPROVED_VERSION, D.SPEC FROM GOVERNANCE.DOMAIN_MODEL M
                                   JOIN GOVERNANCE.MODEL_DESIGNS D ON D.MODEL_ID = M.MODEL_ID AND D.VERSION = M.APPROVED_VERSION
                                  WHERE M.DOMAIN_ID = ? AND M.APPROVED_VERSION IS NOT NULL""", [domain])
    except Exception:
        return None
    if not found:
        return None
    spec = spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, found[0]["MODEL_ID"])
    entity = dq_suite.model_for_table(spec, table)
    if not entity:
        return None
    return {"model_id": found[0]["MODEL_ID"], "version": int(found[0]["APPROVED_VERSION"]), "spec": spec, **entity}


def _propose_to_model(session, a: Dict[str, Any], user: str) -> Optional[Dict[str, Any]]:
    """An approved annotation as a proposal to the table's domain model (when the table is one of its entities)."""
    from services.modeling import model_diff

    target = _model_entity(session, a["table_fqn"])
    if not target:
        return None
    columns = {str(k).upper(): v for k, v in (target.get("columns") or {}).items()}
    if columns.get(a["column_name"].upper()) not in ("ACTIVE",):
        return {"model_id": target["model_id"], "skipped": f"{a['column_name']} is not an active column of {target['entity']}"}
    patch = patch_for({k: a.get(k) for k in FIELDS})
    proposed = apply_patch(target["spec"], target["entity"], a["column_name"], patch)
    changes = model_diff.diff(target["spec"], proposed)
    if not changes["changes"]:
        return {"model_id": target["model_id"], "skipped": "the approved model already says this"}
    entity = next(e for e in proposed["design"]["entities"] if e["entity_name"] == target["entity"])
    change_id = str(uuid.uuid4())
    insert_rows(session, CHANGES,
                ["CHANGE_ID", "MODEL_ID", "BASE_VERSION", "TARGET_ENTITY", "PROPOSED_ENTITIES", "DIFF", "STATUS", "NOTE",
                 "CREATED_BY", "SOURCE", "ANNOTATION_ID", "PATCH"],
                ["?", "?", "?::NUMBER", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "'PROPOSED'", "NULLIF(?, '')", "?",
                 "'ANNOTATION'", "?", "PARSE_JSON(?)"],
                [[change_id, target["model_id"], target["version"], target["entity"], json.dumps([entity], default=str),
                  json.dumps({"counts": changes["counts"], "breaking": changes["breaking"], "changes": changes["changes"][:50]},
                             default=str),
                  clip(f"Annotation of {a['column_name']} approved by {user.upper()}" + (f": {a['note']}" if a.get("note") else ""),
                       2000), str(a.get("proposed_by") or user).upper(), a["annotation_id"],
                  json.dumps({"entity": target["entity"], "column": a["column_name"], "set": patch})]])
    return {"model_id": target["model_id"], "change_id": change_id, "entity": target["entity"], "counts": changes["counts"]}


def approve(session, annotation_id: str, user: str, note: str = "") -> Dict[str, Any]:
    """Four eyes: the annotation becomes the column's approved one (the previous is superseded) and, when the table is
    an entity of its domain's approved model, is proposed to the model."""
    a = _pending(session, annotation_id)
    assert str(a.get("proposed_by") or "").upper() != user.upper(), "you cannot approve your own annotation; ask another approver"
    session.sql(f"""UPDATE {ANNOTATIONS} SET STATUS = 'SUPERSEDED'
                     WHERE TABLE_FQN = ? AND COLUMN_NAME = ? AND STATUS = 'APPROVED'""",
                params=[a["table_fqn"], a["column_name"]]).collect()
    session.sql(f"""UPDATE {ANNOTATIONS} SET STATUS = 'APPROVED', DECIDED_BY = ?, DECIDED_AT = CURRENT_TIMESTAMP(),
                           DECISION_NOTE = NULLIF(?, '') WHERE ANNOTATION_ID = ?""",
                params=[user.upper(), clip(note, 2000), annotation_id]).collect()
    try:
        model = _propose_to_model(session, a, user)
    except Exception as exc:  # the annotation stands; the model proposal can be made by hand
        model = {"error": clip(exc, 300)}
    if model and model.get("change_id"):
        session.sql(f"UPDATE {ANNOTATIONS} SET MODEL_ID = ?, MODEL_CHANGE_ID = ? WHERE ANNOTATION_ID = ?",
                    params=[model["model_id"], model["change_id"], annotation_id]).collect()
    return {"annotation_id": annotation_id, "status": "APPROVED", "table": a["table_fqn"], "column": a["column_name"],
            "model": model}


def effective(session, table: str) -> Dict[str, Any]:
    """{approved: {column: annotation}, pending: {column: annotation}} for a table's profile views."""
    out: Dict[str, Dict[str, Any]] = {"approved": {}, "pending": {}}
    try:
        found = rows(session, f"""SELECT {ANNOTATION_FIELDS} FROM {ANNOTATIONS}
                                   WHERE TABLE_FQN = ? AND STATUS IN ('APPROVED', 'PROPOSED')""", [table_key(table)])
    except Exception:  # before V041
        return out
    for r in found:
        a = _annotation(r)
        out["approved" if a["status"] == "APPROVED" else "pending"][a["column_name"]] = a
    return out
