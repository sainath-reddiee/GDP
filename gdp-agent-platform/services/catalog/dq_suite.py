"""Data Quality suites: the checks of each table, versioned, reviewed and approved in the app (V040).

  CORE.DQ_SUITE          one row per checked table: approved version, open version, domain and domain model
  CORE.DQ_SUITE_VERSION  the versions; CHECKS is the table's list of checks in the neutral form, each with a
                               stable check_id
  CORE.DQ_CHECKS_CATALOG the runtime copy the scanner and the schedules read; written only by an approved
                               version (apply_suite_version, owner's rights)

Lifecycle: DRAFT -> IN_REVIEW -> APPROVED (-> PUBLISHED once its Git pull request is merged), or REJECTED; the version
it replaces becomes SUPERSEDED. A table has at most one open version. Every edit from the Data Quality Store (add,
edit, pause, resume, retire, seed from a profile, promote from a run) lands in the open draft, opening one when there
is none; the draft is saved in place with an etag, so two people never overwrite each other. Approval is four eyes
(the approver neither submitted nor edited the version), blocked by validation errors, and a version that loosens
the checks (removes or pauses one, lowers its severity, relaxes a threshold) needs `acknowledge_loosened`. Checks are
validated against the approved domain model: a column the model does not have is a warning, a retired column or
table an error, and the approved model version is recorded on the approved suite version.

Every function takes the caller's user name explicitly, so the rules are testable whichever session runs the SQL.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.catalog import dq
from services.common.sql import clip, insert_rows, rows, scalar, variant

SUITES = "CORE.DQ_SUITE"
VERSIONS = "CORE.DQ_SUITE_VERSION"
CATALOG = dq.CATALOG
OPEN = ("DRAFT", "IN_REVIEW")
LIVE = ("APPROVED", "PUBLISHED")
CHECK_STATUSES = ("ACTIVE", "PAUSED")
RANK = {"LOOSENED": 3, "CHANGED": 2, "TIGHTENED": 1, "DOCUMENTATION": 0}


class Conflict(AssertionError):
    """A stale etag or a concurrent open draft (HTTP 409)."""


def suite_id_for(table: str) -> str:
    return str(uuid.UUID(hashlib.md5(f"dq:{table.upper()}".encode("utf-8")).hexdigest()))


def _low(r: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in r.items()}


def _ts(value: Any) -> Optional[str]:
    return str(value)[:19] if value else None


# ---------------------------------------------------------------- pure: checks, diff, validation, export


def check_record(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """A check as a suite keeps it: the validated neutral form, its SodaCL and headline, plus id, status and lineage."""
    cleaned, problems = dq.normalize(raw)
    if problems:
        return {}, problems
    status = str(raw.get("status") or "ACTIVE").upper()
    origin = str(raw.get("origin") or "USER").upper()
    return {"check_id": str(raw.get("check_id") or uuid.uuid4()), "target_column": cleaned["target_column"] or None,
            "check_type": cleaned["check_type"], "severity": cleaned["severity"], "requirement": cleaned.get("requirement"),
            "definition": cleaned["definition"], "soda_dsl": cleaned["soda_dsl"], "headline": cleaned["headline"],
            "status": status if status in CHECK_STATUSES else "ACTIVE",
            "origin": origin if origin in dq.ORIGINS else "USER", "source_run_id": raw.get("source_run_id")}, []


def normalize_checks(checks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    out, problems, seen = [], [], set()
    for raw in checks or []:
        record, bad = check_record(raw)
        if bad:
            problems.append(f"{raw.get('target_column') or 'table'}: {'; '.join(bad)}")
            continue
        if record["check_id"] in seen:
            record["check_id"] = str(uuid.uuid4())
        seen.add(record["check_id"])
        out.append(record)
    return out, problems


BOUND_KEYS = ("max_percent", "max_invalid_percent", "max_failed", "max", "min", "gt", "threshold")


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _bounds(definition: Dict[str, Any]) -> Dict[str, float]:
    """Every numeric bound of a check, to tell a relaxed check from a stricter one: "max" bounds loosen upward, "min"
    bounds loosen downward. A range or row count can have both; so can a `between` threshold."""
    out: Dict[str, float] = {}
    for key in ("max_percent", "max_invalid_percent", "max_failed", "max"):
        if _num(definition.get(key)) is not None:
            out.setdefault("max", _num(definition[key]))
    for key in ("min", "gt"):
        if _num(definition.get(key)) is not None:
            out.setdefault("min", _num(definition[key]))
    t = definition.get("threshold")
    if isinstance(t, dict):
        between = t.get("between")
        if isinstance(between, list) and len(between) == 2 and all(_num(x) is not None for x in between):
            out["min"], out["max"] = float(between[0]), float(between[1])
        elif _num(t.get("value")) is not None:
            op = str(t.get("op") or "")
            if op in ("<", "<="):
                out["max"] = float(t["value"])
            elif op in (">", ">="):
                out["min"] = float(t["value"])
    if isinstance(t, str):
        m = re.match(r"^(\d+(?:\.\d+)?)\s*([mhd])$", t.strip().lower())
        if m:
            out["max"] = float(m[1]) * {"m": 1, "h": 60, "d": 1440}[m[2]]
    return out


def _bound_change(a: Dict[str, float], b: Dict[str, float]) -> Optional[str]:
    """LOOSENED when any bound relaxed or was dropped, TIGHTENED when bounds only got stricter or were added."""
    looser = tighter = False
    for side in ("min", "max"):
        if side in a and side not in b:
            looser = True
        elif side in b and side not in a:
            tighter = True
        elif side in a and a[side] != b[side]:
            relaxed = b[side] > a[side] if side == "max" else b[side] < a[side]
            looser, tighter = looser or relaxed, tighter or not relaxed
    return "LOOSENED" if looser else "TIGHTENED" if tighter else None


def _change_class(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[str, List[str]]:
    fields = [f for f in ("target_column", "severity", "status", "definition", "requirement")
              if json.dumps(a.get(f), sort_keys=True, default=str) != json.dumps(b.get(f), sort_keys=True, default=str)]
    classes = []
    if "status" in fields:
        classes.append("LOOSENED" if b.get("status") == "PAUSED" else "TIGHTENED")
    if "severity" in fields:
        classes.append("LOOSENED" if b.get("severity") == "WARN" else "TIGHTENED")
    if "target_column" in fields:
        classes.append("CHANGED")
    if "definition" in fields:
        da, db_ = a.get("definition") or {}, b.get("definition") or {}
        same_shape = {k: v for k, v in da.items() if k not in BOUND_KEYS} == \
            {k: v for k, v in db_.items() if k not in BOUND_KEYS}
        if da.get("kind") != db_.get("kind"):
            classes.append("LOOSENED")   # a different check under the same id: the old guarantee is gone
        elif same_shape:
            classes.append(_bound_change(_bounds(da), _bounds(db_)) or "CHANGED")
        else:
            classes.append("CHANGED")
    if not classes and fields:
        classes.append("DOCUMENTATION")
    return (max(classes, key=lambda c: RANK[c]) if classes else "DOCUMENTATION"), fields


def diff(base: Optional[List[Dict[str, Any]]], checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Changes from `base` (the approved checks; None for a new suite) to `checks`, matched by check_id.
    Classes: TIGHTENED (added, resumed, stricter), LOOSENED (removed, paused, softer), CHANGED (a different rule or
    column), DOCUMENTATION (the reason only)."""
    before = {c["check_id"]: c for c in base or []}
    after = {c["check_id"]: c for c in checks}
    changes: List[Dict[str, Any]] = []
    for cid, c in after.items():
        label = f"{c.get('target_column') or 'table'}: {c.get('headline') or c.get('check_type')}"
        if cid not in before:
            changes.append({"check_id": cid, "kind": "ADDED", "class": "TIGHTENED", "column": c.get("target_column"),
                            "headline": c.get("headline"), "message": f"{label} added ({c['severity'].lower()})"})
            continue
        cls, fields = _change_class(before[cid], c)
        if fields:
            old = before[cid]
            parts = []
            if "severity" in fields:
                parts.append(f"severity {old['severity'].lower()} to {c['severity'].lower()}")
            if "status" in fields:
                parts.append("paused" if c.get("status") == "PAUSED" else "resumed")
            if "definition" in fields or "target_column" in fields:
                parts.append(f"was {old.get('headline') or old.get('check_type')}")
            if fields == ["requirement"]:
                parts.append("reason changed")
            changes.append({"check_id": cid, "kind": "CHANGED", "class": cls, "column": c.get("target_column"),
                            "headline": c.get("headline"), "from_headline": old.get("headline"), "fields": fields,
                            "message": f"{label} ({'; '.join(parts)})"})
    for cid, c in before.items():
        if cid not in after:
            changes.append({"check_id": cid, "kind": "REMOVED", "class": "LOOSENED", "column": c.get("target_column"),
                            "headline": c.get("headline"),
                            "message": f"{c.get('target_column') or 'table'}: {c.get('headline') or c.get('check_type')} removed"})
    counts = {k: sum(1 for x in changes if x["class"] == k) for k in RANK}
    return {"changes": changes, "counts": counts, "loosened": counts["LOOSENED"] > 0}


def validate(checks: List[Dict[str, Any]], model: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Problems with a suite: duplicate checks (ERROR) and, against the approved domain model, columns it does not
    have (WARN) or has retired (ERROR), or a retired table (ERROR)."""
    issues: List[Dict[str, Any]] = []
    for i, c in enumerate(checks):
        if any(dq.same_check(c, other) for other in checks[:i]):
            issues.append({"check_id": c["check_id"], "severity": "ERROR",
                           "message": f"{c.get('headline') or c['check_type']} is the same as another check"})
    if not model:
        return issues
    where = f"the approved model ({model.get('entity')} v{model.get('model_version')})"
    if model.get("entity_status") == "RETIRED":
        issues.append({"check_id": None, "severity": "ERROR", "message": f"{model.get('entity')} is retired in {where}"})
    columns = {str(k).upper(): v for k, v in (model.get("columns") or {}).items()}
    for c in checks:
        col = str(c.get("target_column") or "").upper()
        if not col:
            continue
        if col not in columns:
            issues.append({"check_id": c["check_id"], "severity": "WARN", "message": f"{col} is not a column of {where}"})
        elif columns[col] == "RETIRED":
            issues.append({"check_id": c["check_id"], "severity": "ERROR", "message": f"{col} is retired in {where}"})
    return issues


def blocking(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [i for i in issues if i["severity"] == "ERROR"]


def model_for_table(spec: Dict[str, Any], table: str) -> Optional[Dict[str, Any]]:
    """The entity of an approved model specification that is `table` (DATABASE.SCHEMA.TABLE, matched on placement,
    then on the table name alone), with its columns and their status."""
    from services.modeling import spec_v2

    parts = [p.strip('"').upper() for p in table.split(".")]
    name = parts[-1]
    best = None
    for e in spec.get("design", {}).get("entities", []):
        if str(e.get("entity_name") or "").upper() != name:
            continue
        db, schema = spec_v2.placement(spec, e)
        if [str(db or "").upper(), str(schema or "").upper(), name] == parts:
            best = e
            break
        best = best or e
    if not best:
        return None
    return {"entity": best["entity_name"], "entity_status": best.get("status") or "ACTIVE",
            "columns": {a["name"]: a.get("status") or "ACTIVE" for a in best.get("attributes") or []}}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_") or "table"


def export(table: str, checks: List[Dict[str, Any]], fmt: str) -> Tuple[str, str, str]:
    """(file name, media type, content) of a suite's ACTIVE checks as SodaCL (soda) or a Great Expectations suite (gx)."""
    from services.quality.gx import render_suite
    from services.soda.expectations import render_yaml

    active = [{**c, "target_table": table} for c in checks if c.get("status", "ACTIVE") == "ACTIVE"]
    base = _slug(table)
    if fmt == "gx":
        return f"{base}.gx.json", "application/json", json.dumps(render_suite(table, active), indent=2) + "\n"
    return f"{base}.soda.yml", "application/x-yaml", render_yaml(table, active)


PORTABLE_FORMAT = "gdp-dq-suite"
PORTABLE_KEYS = ("target_column", "severity", "requirement", "status", "definition")


def portable(table: str, domain: Optional[str], checks: List[Dict[str, Any]], databases: Dict[str, str],
             environment: Optional[str] = None, version: Optional[int] = None) -> Tuple[str, str, str]:
    """(file name, media type, content) of a suite as a portable file with real table names, plus the environment it
    came from and that environment's database per role (`databases`, Admin, Environments). Imported elsewhere
    (import_suite), or committed as domain/<name>/dq/<table>.dq.json to seed an environment at deploy, the names of
    a mapped database move to the importing environment's database for the same role."""
    out = []
    for c in checks:
        if c.get("status", "ACTIVE") not in CHECK_STATUSES:
            continue
        item = {k: c.get(k) for k in PORTABLE_KEYS if c.get(k) not in (None, "")}
        item["definition"] = dict(c.get("definition") or {})
        out.append(item)
    from services.common.environments import DOMAIN

    doc = {"format": PORTABLE_FORMAT, "format_version": 1, "table": table, "domain": domain or None,
           "source": {"environment": environment, "suite_version": version,
                      "databases": {k: v for k, v in sorted((databases or {}).items()) if k != DOMAIN}},
           "checks": out}
    return f"{_slug(table)}.dq.json", "application/json", json.dumps(doc, indent=2, default=str) + "\n"


def parse_portable(text: str) -> Dict[str, Any]:
    """The portable file as {table, domain, source, checks}; raises ValueError on a malformed file."""
    try:
        doc = json.loads(text or "")
    except ValueError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != PORTABLE_FORMAT:
        raise ValueError(f"not a {PORTABLE_FORMAT} file (export one from the Data Quality Store)")
    if int(doc.get("format_version") or 1) > 1:
        raise ValueError(f"format version {doc.get('format_version')} is newer than this platform reads")
    if not str(doc.get("table") or "").strip():
        raise ValueError("the file names no table")
    checks = doc.get("checks")
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise ValueError("checks must be a list of objects")
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    return {"table": str(doc["table"]).strip(), "domain": doc.get("domain"), "source": source, "checks": checks}


def localize(doc: Dict[str, Any], here: Dict[str, str], config: Optional[Dict[str, Any]] = None,
             environment: str = "") -> Tuple[str, List[Dict[str, Any]], List[str]]:
    """(table, checks, problems) of a parsed portable file in this environment (`here`: its database per role).
    Names in a database the file's source environment maps (its own `databases`, else that environment in
    `config`), or any configured environment maps, become this environment's database for the same role."""
    from services.common import environments as envs

    source = dict((doc.get("source") or {}).get("databases") or {})
    if not source and (doc.get("source") or {}).get("environment"):
        source = envs.bindings_of(config, doc["source"]["environment"])
    problems: List[str] = []

    def physical(text: str) -> str:
        stored = envs.store_form(envs.to_logical(text, source), config, {k: v for k, v in here.items() if k != envs.DOMAIN})
        value, unbound = envs.resolve(stored, here, environment)
        problems.extend(p for p in unbound if p not in problems)
        return value

    table = physical(doc["table"])
    out = []
    for c in doc["checks"]:
        d = dict(c.get("definition") or {})
        for key in ("reference_table", "query"):
            if d.get(key):
                d[key] = physical(str(d[key]))
        out.append({**{k: c.get(k) for k in PORTABLE_KEYS if k != "definition"}, "definition": d})
    if not problems:
        try:
            table = dq._fqn(table)
        except AssertionError:
            problems.append(f"{table}: the table must be DATABASE.SCHEMA.TABLE")
    return table, out, problems


def artifacts(table: str, domain: Optional[str], checks: List[Dict[str, Any]]) -> Dict[str, str]:
    """The files of a suite pull request, under dq/<domain>/: SodaCL and the Great Expectations suite."""
    folder = f"dq/{_slug(domain or 'unassigned')}"
    out = {}
    for fmt in ("soda", "gx"):
        name, _, content = export(table, checks, fmt)
        out[f"{folder}/{name}"] = content
    return out


# ---------------------------------------------------------------- Snowpark: reads


def _catalog_checks(session, table: str) -> List[Dict[str, Any]]:
    try:
        found = rows(session, f"""SELECT CHECK_ID, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION, SODA_DSL, SEVERITY, STATUS,
                                         ORIGIN, REQUIREMENT, SOURCE_RUN_ID
                                    FROM {CATALOG} WHERE TABLE_NAME = ? AND STATUS <> 'RETIRED'
                                   ORDER BY COLUMN_NAME NULLS FIRST, CHECK_TYPE""", [table])
    except Exception:  # before V038
        found = rows(session, f"""SELECT CHECK_ID, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION, SODA_DSL, SEVERITY, STATUS,
                                         ORIGIN FROM {CATALOG} WHERE TABLE_NAME = ? AND STATUS <> 'RETIRED'
                                   ORDER BY COLUMN_NAME NULLS FIRST, CHECK_TYPE""", [table])
    out = []
    for r in found:
        record, bad = check_record({"check_id": r["CHECK_ID"], "target_column": r.get("COLUMN_NAME"),
                                    "severity": r.get("SEVERITY"), "requirement": r.get("REQUIREMENT"),
                                    "definition": variant(r.get("CHECK_DEFINITION")) or {}, "status": r.get("STATUS"),
                                    "origin": r.get("ORIGIN"), "source_run_id": r.get("SOURCE_RUN_ID")})
        if not bad:
            out.append(record)
    return out


def _header(session, table: str) -> Optional[Dict[str, Any]]:
    found = rows(session, f"""SELECT SUITE_ID, TABLE_NAME, DOMAIN_ID, MODEL_ID, APPROVED_VERSION, OPEN_VERSION,
                                     DEFAULT_REPO_ID FROM {SUITES} WHERE TABLE_NAME = ?""", [table])
    if not found:
        return None
    h = _low(found[0])
    for k in ("approved_version", "open_version"):
        h[k] = int(h[k]) if h.get(k) is not None else None
    return h


VERSION_FIELDS = """VERSION, STATUS, BASE_VERSION, ETAG, AUTHORS, ORIGIN, CHANGE_NOTE, MODEL_ID, MODEL_VERSION,
                    SUBMITTED_BY, SUBMITTED_AT, APPROVED_BY, APPROVED_AT, REJECTED_BY, REJECTED_AT, REJECT_REASON,
                    REVIEW_SUMMARY, CATALOG_SYNCED_AT, REPO_ID, BRANCH, PR_URL, PR_NUMBER, PR_STATE, MERGED_SHA,
                    PR_SYNCED_AT, CREATED_BY, CREATED_AT, UPDATED_AT"""


def _meta(r: Dict[str, Any]) -> Dict[str, Any]:
    v = _low(r)
    v["version"] = int(v["version"])
    for k in ("base_version", "model_version", "pr_number"):
        v[k] = int(v[k]) if v.get(k) is not None else None
    v["authors"] = variant(v.get("authors")) or []
    v["review_summary"] = variant(v.get("review_summary"))
    for k in ("submitted_at", "approved_at", "rejected_at", "catalog_synced_at", "pr_synced_at", "created_at", "updated_at"):
        v[k] = _ts(v.get(k))
    return v


def _versions(session, suite_id: str) -> List[Dict[str, Any]]:
    return [_meta(r) for r in rows(session, f"SELECT {VERSION_FIELDS} FROM {VERSIONS} WHERE SUITE_ID = ? "
                                            "ORDER BY VERSION DESC", [suite_id])]


def _open(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((v for v in versions if v["status"] in OPEN), None)


def _checks(session, suite_id: str, version: int) -> List[Dict[str, Any]]:
    found = rows(session, f"SELECT CHECKS FROM {VERSIONS} WHERE SUITE_ID = ? AND VERSION = ?", [suite_id, int(version)])
    assert found, f"version {version} not found"
    return list(variant(found[0]["CHECKS"]) or [])


def _domain_name(session, domain_id: Optional[str]) -> Optional[str]:
    if not domain_id:
        return None
    try:
        return scalar(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
    except Exception:
        return None


def model_context(session, table: str, domain_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The approved domain model entity for the table: {model_id, model_version, entity, entity_status, columns}."""
    from services.modeling import spec_v2

    if not domain_id:
        return None
    try:
        found = rows(session, """SELECT M.MODEL_ID, M.APPROVED_VERSION, D.SPEC FROM CORE.DOMAIN_MODEL M
                                   JOIN CORE.MODEL_DESIGNS D ON D.MODEL_ID = M.MODEL_ID AND D.VERSION = M.APPROVED_VERSION
                                  WHERE M.DOMAIN_ID = ? AND M.APPROVED_VERSION IS NOT NULL AND COALESCE(M.LAYER, 'SILVER') = 'SILVER'""", [domain_id])
    except Exception:  # before V039
        return None
    if not found:
        return None
    spec = spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, found[0]["MODEL_ID"])
    entity = model_for_table(spec, table)
    if not entity:
        return {"model_id": found[0]["MODEL_ID"], "model_version": int(found[0]["APPROVED_VERSION"]), "entity": None,
                "columns": None, "note": "the table is not an entity of the approved model"}
    return {"model_id": found[0]["MODEL_ID"], "model_version": int(found[0]["APPROVED_VERSION"]), **entity}


def _validate(session, header: Dict[str, Any], checks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    model = model_context(session, header["table_name"], header.get("domain_id"))
    return validate(checks, model if model and model.get("entity") else None), model


def list_suites(session) -> List[Dict[str, Any]]:
    out = []
    for r in rows(session, f"""SELECT S.SUITE_ID, S.TABLE_NAME, S.DOMAIN_ID, S.APPROVED_VERSION, S.OPEN_VERSION,
                                      O.STATUS AS OPEN_STATUS, O.AUTHORS AS OPEN_AUTHORS, O.SUBMITTED_BY,
                                      O.REVIEW_SUMMARY AS OPEN_SUMMARY, A.PR_STATE, A.PR_URL, A.APPROVED_AT, A.APPROVED_BY,
                                      A.MODEL_VERSION
                                 FROM {SUITES} S
                                 LEFT JOIN {VERSIONS} O ON O.SUITE_ID = S.SUITE_ID AND O.VERSION = S.OPEN_VERSION
                                 LEFT JOIN {VERSIONS} A ON A.SUITE_ID = S.SUITE_ID AND A.VERSION = S.APPROVED_VERSION
                                ORDER BY S.TABLE_NAME"""):
        s = _low(r)
        s["open_authors"] = variant(s.get("open_authors")) or []
        s["open_summary"] = variant(s.get("open_summary"))
        s["approved_at"] = _ts(s.get("approved_at"))
        for k in ("approved_version", "open_version", "model_version"):
            s[k] = int(s[k]) if s.get(k) is not None else None
        out.append(s)
    return out


def get_suite(session, table: str, version: Optional[int] = None) -> Dict[str, Any]:
    """The suite of a table: header, versions, and the chosen version (default: the open one, else the approved one)
    with its checks, its changes from the approved version, its validation and its model context."""
    table = dq._fqn(table)
    header = _header(session, table)
    if not header:
        if not _catalog_checks(session, table):  # nothing to version yet; the first edit opens version 1
            return {"suite": None, "table": table, "versions": [], "open": None, "current": None}
        header = _ensure(session, table, None, "")
    versions = _versions(session, header["suite_id"])
    current_open = _open(versions)
    pick = int(version or (current_open or {}).get("version") or header.get("approved_version") or 0)
    current = next((v for v in versions if v["version"] == pick), None)
    out: Dict[str, Any] = {"suite": {**header, "domain_name": _domain_name(session, header.get("domain_id"))},
                           "versions": versions, "open": current_open, "current": None}
    if current:
        checks = _checks(session, header["suite_id"], pick)
        approved = header.get("approved_version")
        base = _checks(session, header["suite_id"], approved) if approved and approved != pick else None
        issues, model = _validate(session, header, checks)
        out["current"] = {**current, "checks": checks, "issues": issues, "model": model,
                          "changes": diff(base, checks) if base is not None or not approved else
                          {"changes": [], "counts": {k: 0 for k in RANK}, "loosened": False}}
    return out


def diff_versions(session, table: str, from_version: Optional[int], to_version: int) -> Dict[str, Any]:
    header = _header(session, dq._fqn(table))
    assert header, "this table has no data quality suite"
    base = from_version or header.get("approved_version")
    return {"from": base, "to": int(to_version),
            **diff(_checks(session, header["suite_id"], int(base)) if base else None,
                   _checks(session, header["suite_id"], int(to_version)))}


def export_version(session, table: str, version: Optional[int], fmt: str) -> Tuple[str, str, str]:
    header = _header(session, dq._fqn(table))
    assert header, "this table has no data quality suite"
    pick = int(version or header.get("approved_version") or header.get("open_version") or 0)
    assert pick, "the suite has no version yet"
    if fmt == "portable":
        from services.common import environments as envs

        config = envs.load(session)
        return portable(header["table_name"], _domain_name(session, header.get("domain_id")),
                        _checks(session, header["suite_id"], pick),
                        envs.current_mapping(session, header.get("domain_id"), config), config.get("current"), pick)
    return export(header["table_name"], _checks(session, header["suite_id"], pick), fmt)


def import_suite(session, text: str, user: str, domain_id: Optional[str] = None) -> Dict[str, Any]:
    """A portable suite file into this environment: names of a mapped database moved to this environment's database
    for the same role (localize), its checks added to the table's draft for review and approval like any edit. Checks the table already has (approved or in the draft) are skipped; when none is new no draft opens."""
    from services.common import environments as envs

    doc = parse_portable(text)
    config = envs.load(session)
    if not domain_id and doc.get("domain"):
        domain_id = scalar(session, "SELECT DOMAIN_ID FROM CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(?) "
                                    "LIMIT 1", [str(doc["domain"])])
    table, raws, problems = localize(doc, envs.current_mapping(session, domain_id, config), config,
                                     config.get("current") or "")
    assert not problems, "; ".join(problems[:5])
    header = _header(session, table)
    have: List[Dict[str, Any]] = []
    if header:
        current = _open(_versions(session, header["suite_id"]))
        pick = (current or {}).get("version") or header.get("approved_version")
        have = _checks(session, header["suite_id"], int(pick)) if pick else []
    fresh = []
    for raw in raws:
        record, bad = check_record(raw)
        if bad or not any(dq.same_check(c, record) for c in have + fresh):
            fresh.append(raw if bad else record)
    base = {"table": table, "environment": config.get("current"), "checks": len(raws)}
    if not fresh:
        return {**base, "added": 0, "skipped": len(raws), "problems": [], "staged": False}
    result = stage_many(session, table, [{k: r.get(k) for k in PORTABLE_KEYS} for r in fresh], "USER", user, domain_id)
    return {**base, **result, "skipped": int(result.get("skipped") or 0) + len(raws) - len(fresh)}


# ---------------------------------------------------------------- Snowpark: lifecycle


def _insert_version(session, suite_id: str, version: int, status: str, checks: List[Dict[str, Any]], origin: str,
                    base: Optional[int], user: str, note: str) -> str:
    etag = uuid.uuid4().hex
    insert_rows(session, VERSIONS,
                ["SUITE_ID", "VERSION", "STATUS", "BASE_VERSION", "CHECKS", "ETAG", "AUTHORS", "ORIGIN", "CHANGE_NOTE",
                 "CREATED_BY"],
                ["?", "?::NUMBER", "?", "NULLIF(?, '')::NUMBER", "PARSE_JSON(?)", "?", "PARSE_JSON(?)::ARRAY", "?",
                 "NULLIF(?, '')", "?"],
                [[suite_id, int(version), status, base or "", json.dumps(checks, default=str), etag,
                  json.dumps([user.upper()] if user else []), origin, clip(note, 2000), user.upper() or "SYSTEM"]])
    return etag


def _ensure(session, table: str, domain_id: Optional[str], user: str) -> Dict[str, Any]:
    """The table's suite; a table with catalog checks but no suite gets one whose APPROVED version 1 is the catalog
    as it is (the checks already running were never reviewed as a set, so this is the starting point)."""
    header = _header(session, table)
    if header:
        return header
    current = _catalog_checks(session, table)
    domain = domain_id or dq._domain_for(session, table, None)
    model = model_context(session, table, domain)
    suite_id = suite_id_for(table)
    insert_rows(session, SUITES, ["SUITE_ID", "TABLE_NAME", "DOMAIN_ID", "MODEL_ID", "CREATED_BY"],
                ["?", "?", "NULLIF(?, '')", "NULLIF(?, '')", "?"],
                [[suite_id, table, domain or "", (model or {}).get("model_id") or "", user.upper() or "SYSTEM"]])
    if current:
        _insert_version(session, suite_id, 1, "APPROVED", current, "BACKFILL", None, "SYSTEM_BACKFILL",
                        "The checks in the catalog when versioning started")
        session.sql(f"""UPDATE {VERSIONS} SET APPROVED_BY = 'SYSTEM_BACKFILL', APPROVED_AT = CURRENT_TIMESTAMP(),
                               CATALOG_SYNCED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ? AND VERSION = 1""",
                    params=[suite_id]).collect()
        session.sql(f"UPDATE {SUITES} SET APPROVED_VERSION = 1 WHERE SUITE_ID = ?", params=[suite_id]).collect()
    header = _header(session, table)
    assert header, "the suite could not be created"
    return header


def _claim_open(session, suite_id: str, version: int) -> None:
    opened = [int(r["VERSION"]) for r in rows(session, f"SELECT VERSION FROM {VERSIONS} WHERE SUITE_ID = ? "
                                                       "AND STATUS IN ('DRAFT', 'IN_REVIEW') ORDER BY VERSION", [suite_id])]
    if opened and opened[0] != int(version):
        session.sql(f"DELETE FROM {VERSIONS} WHERE SUITE_ID = ? AND VERSION = ?", params=[suite_id, int(version)]).collect()
        raise Conflict(f"CONFLICT: version {opened[0]} was opened as a draft just now; open that one instead")
    session.sql(f"UPDATE {SUITES} SET OPEN_VERSION = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ?",
                params=[int(version), suite_id]).collect()


def open_draft(session, table: str, user: str, from_version: Optional[int] = None, note: str = "",
               domain_id: Optional[str] = None) -> Dict[str, Any]:
    """Start the next version from the approved one, or from an older version (a rollback, reviewed like any
    change). A table without checks gets an empty version 1. Refused while another version is open."""
    table = dq._fqn(table)
    header = _ensure(session, table, domain_id, user)
    versions = _versions(session, header["suite_id"])
    current = _open(versions)
    if current:
        raise Conflict(f"CONFLICT: version {current['version']} is already open "
                       f"({current['status'].lower().replace('_', ' ')}); continue that one")
    base = int(from_version or header.get("approved_version") or 0)
    if base:
        assert any(v["version"] == base for v in versions), f"version {base} not found"
    checks = _checks(session, header["suite_id"], base) if base else []
    version = (max(v["version"] for v in versions) + 1) if versions else 1
    origin = "ROLLBACK" if from_version and from_version != header.get("approved_version") else "STORE"
    etag = _insert_version(session, header["suite_id"], version, "DRAFT", checks, origin, base or None, user,
                           note or (f"Rollback to version {base}" if origin == "ROLLBACK" else ""))
    _claim_open(session, header["suite_id"], version)
    return {"table": table, "suite_id": header["suite_id"], "version": version, "status": "DRAFT", "etag": etag}


def _write_draft(session, header: Dict[str, Any], current: Dict[str, Any], checks: List[Dict[str, Any]], user: str,
                 note: str = "") -> str:
    etag = uuid.uuid4().hex
    session.sql(f"""UPDATE {VERSIONS} SET CHECKS = PARSE_JSON(?), ETAG = ?,
                           AUTHORS = ARRAY_DISTINCT(ARRAY_APPEND(COALESCE(AUTHORS, ARRAY_CONSTRUCT()), ?)),
                           CHANGE_NOTE = COALESCE(NULLIF(?, ''), CHANGE_NOTE), UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE SUITE_ID = ? AND VERSION = ? AND ETAG = ? AND STATUS = 'DRAFT'""",
                params=[json.dumps(checks, default=str), etag, user.upper(), clip(note, 2000), header["suite_id"],
                        current["version"], current["etag"]]).collect()
    now = scalar(session, f"SELECT ETAG FROM {VERSIONS} WHERE SUITE_ID = ? AND VERSION = ?",
                 [header["suite_id"], current["version"]])
    if now != etag:
        raise Conflict("CONFLICT: the draft was saved by someone else at the same moment; reload to see their changes")
    return etag


def _summary(session, header: Dict[str, Any], checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    approved = header.get("approved_version")
    changes = diff(_checks(session, header["suite_id"], approved) if approved else None, checks)
    return {"counts": changes["counts"], "loosened": changes["loosened"], "changed": len(changes["changes"])}


def save_draft(session, table: str, payload_json: str, user: str) -> Dict[str, Any]:
    """Replace the open draft's checks. `etag` must be the one the caller read; a stale etag is a Conflict."""
    payload = json.loads(payload_json or "{}")
    table = dq._fqn(table)
    header = _header(session, table)
    assert header, "this table has no data quality suite"
    current = _open(_versions(session, header["suite_id"]))
    assert current, "there is no open draft; open one first"
    assert current["status"] == "DRAFT", "the version is in review; withdraw it to keep editing"
    if str(payload.get("etag") or "") != str(current.get("etag") or ""):
        raise Conflict(f"CONFLICT: the draft was saved by {(current.get('authors') or ['someone'])[-1]} since you opened it; "
                       "reload to see their changes")
    checks, problems = normalize_checks(payload.get("checks") or [])
    assert not problems, "; ".join(problems[:5])
    etag = _write_draft(session, header, current, checks, user, payload.get("change_note") or "")
    issues, _ = _validate(session, header, checks)
    return {"table": table, "version": current["version"], "etag": etag, "issues": issues,
            **_summary(session, header, checks)}


def stage(session, table: str, user: str, edit: Callable[[List[Dict[str, Any]]], Tuple[List[Dict[str, Any]], Dict[str, Any]]],
          domain_id: Optional[str] = None) -> Dict[str, Any]:
    """Apply one Data Quality Store edit to the table's open draft, opening a draft first when there is none.
    `edit(checks) -> (checks, info)`. Refused while a version is in review."""
    table = dq._fqn(table)
    header = _ensure(session, table, domain_id, user)
    current = _open(_versions(session, header["suite_id"]))
    opened = False
    if not current:
        # the edit runs on the approved checks first: when it is refused, or changes nothing, no draft is opened
        approved = header.get("approved_version")
        base = _checks(session, header["suite_id"], approved) if approved else []
        checks, info = edit(base)
        if json.dumps(checks, sort_keys=True, default=str) == json.dumps(base, sort_keys=True, default=str):
            return {"table": table, "version": approved, "status": "UNCHANGED", "draft_opened": False,
                    "staged": False, **info, **_summary(session, header, checks)}
        open_draft(session, table, user, domain_id=domain_id)
        current = _open(_versions(session, header["suite_id"]))
        opened = True
    else:
        assert current["status"] == "DRAFT", (f"version {current['version']} of {table} is in review; withdraw it or "
                                              "have it decided before changing the checks")
        checks, info = edit(_checks(session, header["suite_id"], current["version"]))
    _write_draft(session, header, current, checks, user)
    return {"table": table, "version": current["version"], "status": "DRAFT", "draft_opened": opened, "staged": True,
            **info, **_summary(session, header, checks)}


def stage_check(session, payload_json: str, user: str) -> Dict[str, Any]:
    """Add a check to the table's draft, or change one (`check_id`). The payload is the Data Quality Store check form:
    a `definition` (neutral form) or a one-line SodaCL `dsl`."""
    payload = json.loads(payload_json or "{}")
    raw = {"check_id": payload.get("check_id"), "target_column": payload.get("column") or payload.get("target_column"),
           "severity": payload.get("severity"), "requirement": payload.get("requirement"),
           "definition": payload.get("definition"), "origin": payload.get("origin") or "USER",
           "source_run_id": payload.get("source_run_id")}
    if payload.get("dsl") and not payload.get("definition"):
        parsed, problems = dq.parse_dsl(payload["dsl"])
        assert not problems, "; ".join(problems)
        raw["definition"] = parsed["definition"]
        if parsed.get("target_column"):
            raw["target_column"] = parsed["target_column"]

    def edit(checks: List[Dict[str, Any]]):
        existing = next((c for c in checks if c["check_id"] == raw["check_id"]), None) if raw["check_id"] else None
        assert existing or not raw["check_id"], "the check is not in the draft"
        record, problems = check_record({**(existing or {}), **{k: v for k, v in raw.items() if v is not None},
                                         "status": (existing or {}).get("status") or "ACTIVE"})
        assert not problems, "; ".join(problems)
        others = [c for c in checks if c["check_id"] != record["check_id"]]
        assert not any(dq.same_check(c, record) for c in others), "this table already has the same check"
        out = [record if c["check_id"] == record["check_id"] else c for c in checks] if existing else checks + [record]
        return out, {"check_id": record["check_id"], "headline": record["headline"], "soda_dsl": record["soda_dsl"],
                     "action": "changed" if existing else "added"}

    return stage(session, payload.get("table") or "", user, edit, payload.get("domain_id"))


def stage_status(session, table: str, check_id: str, status: str, user: str) -> Dict[str, Any]:
    """Pause, resume or retire (remove from the suite) a check in the table's draft."""
    status = str(status or "").upper()
    assert status in dq.STATUSES, f"status must be one of {list(dq.STATUSES)}"

    def edit(checks: List[Dict[str, Any]]):
        assert any(c["check_id"] == check_id for c in checks), "the check is not in the draft"
        if status == "RETIRED":
            return [c for c in checks if c["check_id"] != check_id], {"check_id": check_id, "action": "removed"}
        return ([{**c, "status": status} if c["check_id"] == check_id else c for c in checks],
                {"check_id": check_id, "action": "paused" if status == "PAUSED" else "resumed"})

    return stage(session, table, user, edit)


def stage_many(session, table: str, raws: List[Dict[str, Any]], origin: str, user: str,
               domain_id: Optional[str] = None, source_run_id: Optional[str] = None) -> Dict[str, Any]:
    """Checks from a profile or a run, added to the draft; checks the table already has are skipped."""
    def edit(checks: List[Dict[str, Any]]):
        added, skipped, problems = 0, 0, []
        out = list(checks)
        for raw in raws:
            record, bad = check_record({**raw, "origin": origin, "source_run_id": source_run_id})
            if bad:
                problems.append(f"{raw.get('target_column') or 'table'}: {'; '.join(bad)}")
                continue
            if any(dq.same_check(c, record) for c in out):
                skipped += 1
                continue
            out.append(record)
            added += 1
        return out, {"added": added, "skipped": skipped, "problems": problems[:20]}

    return stage(session, table, user, edit, domain_id)


def seed_profile(session, table: str, user: str, domain_id: Optional[str] = None) -> Dict[str, Any]:
    fqn, raws, domain = dq.profile_suggestions(session, table, domain_id)
    return stage_many(session, fqn, raws, "PROFILE", user, domain)


def seed_run(session, run_id: str, user: str) -> Dict[str, Any]:
    fqn, raws, domain = dq.run_suggestions(session, run_id)
    return stage_many(session, fqn, raws, "RUN", user, domain, run_id)


def submit(session, table: str, user: str, note: str = "") -> Dict[str, Any]:
    table = dq._fqn(table)
    header = _header(session, table)
    assert header, "this table has no data quality suite"
    current = _open(_versions(session, header["suite_id"]))
    assert current and current["status"] == "DRAFT", "there is no draft to submit"
    checks = _checks(session, header["suite_id"], current["version"])
    issues, _ = _validate(session, header, checks)
    problems = blocking(issues)
    assert not problems, f"fix {len(problems)} problem(s) before submitting: {problems[0]['message']}"
    summary = {**_summary(session, header, checks), "base_version": header.get("approved_version")}
    assert summary["changed"] or not header.get("approved_version"), \
        "the draft is the same as the approved version; there is nothing to review"
    session.sql(f"""UPDATE {VERSIONS} SET STATUS = 'IN_REVIEW', SUBMITTED_BY = ?, SUBMITTED_AT = CURRENT_TIMESTAMP(),
                           REVIEW_SUMMARY = PARSE_JSON(?), BASE_VERSION = NULLIF(?, '')::NUMBER,
                           CHANGE_NOTE = COALESCE(NULLIF(?, ''), CHANGE_NOTE), UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE SUITE_ID = ? AND VERSION = ? AND STATUS = 'DRAFT'""",
                params=[user.upper(), json.dumps(summary), str(header.get("approved_version") or ""), clip(note, 2000),
                        header["suite_id"], current["version"]]).collect()
    return {"table": table, "version": current["version"], "status": "IN_REVIEW", **summary}


def withdraw(session, table: str, user: str) -> Dict[str, Any]:
    table = dq._fqn(table)
    header = _header(session, table)
    assert header, "this table has no data quality suite"
    current = _open(_versions(session, header["suite_id"]))
    assert current and current["status"] == "IN_REVIEW", "there is no version in review"
    assert _own(current, user), "only the submitter or an author can withdraw the version"
    session.sql(f"""UPDATE {VERSIONS} SET STATUS = 'DRAFT', SUBMITTED_BY = NULL, SUBMITTED_AT = NULL,
                           UPDATED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ? AND VERSION = ? AND STATUS = 'IN_REVIEW'""",
                params=[header["suite_id"], current["version"]]).collect()
    return {"table": table, "version": current["version"], "status": "DRAFT"}


def _own(found: Dict[str, Any], user: str) -> bool:
    people = {str(a).upper() for a in found.get("authors") or []} | {str(found.get("submitted_by") or "").upper()}
    return user.upper() in people


def _in_review(session, header: Dict[str, Any], version: int, etag: Optional[str] = None) -> Dict[str, Any]:
    """The version in review; with `etag` (what the reviewer read), a version changed since is a Conflict."""
    found = next((v for v in _versions(session, header["suite_id"]) if v["version"] == int(version)), None)
    assert found, f"version {version} not found"
    assert found["status"] == "IN_REVIEW", f"version {version} is {found['status'].lower().replace('_', ' ')}, not in review"
    if etag and str(found.get("etag") or "") != str(etag):
        raise Conflict(f"CONFLICT: version {version} changed since you opened it; reload and review it again")
    return found


def reject(session, table: str, version: int, user: str, reason: str, etag: Optional[str] = None) -> Dict[str, Any]:
    table = dq._fqn(table)
    header = _header(session, table)
    assert header, "this table has no data quality suite"
    found = _in_review(session, header, version, etag)
    assert not _own(found, user), "you worked on this version; withdraw it instead of rejecting it"
    assert len((reason or "").strip()) >= 3, "say why the version is rejected"
    session.sql(f"""UPDATE {VERSIONS} SET STATUS = 'REJECTED', REJECTED_BY = ?, REJECTED_AT = CURRENT_TIMESTAMP(),
                           REJECT_REASON = ?, UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE SUITE_ID = ? AND VERSION = ? AND STATUS = 'IN_REVIEW'""",
                params=[user.upper(), clip(reason, 2000), header["suite_id"], int(version)]).collect()
    session.sql(f"UPDATE {SUITES} SET OPEN_VERSION = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ?",
                params=[header["suite_id"]]).collect()
    from services.knowledge.signals import keep, suite_decision

    keep(session, header.get("domain_id"), suite_decision(table, int(version), "REJECTED", [], reason), "QUALITY")
    return {"table": table, "version": int(version), "status": "REJECTED"}


def approve(session, table: str, version: int, user: str, acknowledge_loosened: bool = False,
            etag: Optional[str] = None) -> Dict[str, Any]:
    """Four eyes: the version becomes the table's approved checks (the previous version is superseded), reviewed
    against the approved domain model, whose version is recorded. The caller then applies it to the catalog
    (apply_suite_version, owner's rights)."""
    from services.common.sql import atomic

    table = dq._fqn(table)
    header = _header(session, table)
    assert header, "this table has no data quality suite"
    found = _in_review(session, header, version, etag)
    assert not _own(found, user), "you cannot approve a version you submitted or edited; ask another data quality approver"
    checks = _checks(session, header["suite_id"], int(version))
    issues, model = _validate(session, header, checks)
    problems = blocking(issues)
    assert not problems, f"the version has {len(problems)} error(s): {problems[0]['message']}"
    approved = header.get("approved_version")
    changes = diff(_checks(session, header["suite_id"], approved) if approved else None, checks)
    assert not changes["loosened"] or acknowledge_loosened, (
        f"LOOSENED: {changes['counts']['LOOSENED']} change(s) relax the checks; confirm and approve with "
        "acknowledge_loosened")
    summary = {"counts": changes["counts"], "loosened": changes["loosened"], "base_version": approved,
               "acknowledged_loosened": bool(changes["loosened"]), "approved_by": user.upper(),
               "warnings": len(issues)}
    model_id = (model or {}).get("model_id") or ""
    model_version = (model or {}).get("model_version") or ""

    def write() -> None:
        # only the content that was reviewed: still in review, and unchanged since it was read
        session.sql(f"""UPDATE {VERSIONS} SET STATUS = 'APPROVED', APPROVED_BY = ?, APPROVED_AT = CURRENT_TIMESTAMP(),
                               REVIEW_SUMMARY = PARSE_JSON(?), MODEL_ID = NULLIF(?, ''),
                               MODEL_VERSION = NULLIF(?, '')::NUMBER, UPDATED_AT = CURRENT_TIMESTAMP()
                         WHERE SUITE_ID = ? AND VERSION = ? AND STATUS = 'IN_REVIEW' AND ETAG = ?""",
                    params=[user.upper(), json.dumps(summary), model_id, str(model_version), header["suite_id"],
                            int(version), str(found.get("etag") or "")]).collect()
        now = scalar(session, f"SELECT STATUS FROM {VERSIONS} WHERE SUITE_ID = ? AND VERSION = ?",
                     [header["suite_id"], int(version)])
        if now != "APPROVED":
            raise Conflict(f"CONFLICT: version {version} changed while it was being approved; reload and review it again")
        session.sql(f"""UPDATE {VERSIONS} SET STATUS = 'SUPERSEDED', UPDATED_AT = CURRENT_TIMESTAMP()
                         WHERE SUITE_ID = ? AND VERSION <> ? AND STATUS IN ('APPROVED', 'PUBLISHED')""",
                    params=[header["suite_id"], int(version)]).collect()
        session.sql(f"""UPDATE {SUITES} SET APPROVED_VERSION = ?, OPEN_VERSION = NULL, MODEL_ID = COALESCE(NULLIF(?, ''), MODEL_ID),
                               UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ?""",
                    params=[int(version), model_id, user.upper(), header["suite_id"]]).collect()

    atomic(session, write)
    from services.knowledge.signals import keep, suite_decision

    keep(session, header.get("domain_id"), suite_decision(table, int(version), "APPROVED", checks), "QUALITY")
    return {"table": table, "suite_id": header["suite_id"], "version": int(version), "status": "APPROVED",
            "model_version": model_version or None, **summary}


def apply_suite_version(session, suite_id: str, version: int) -> Dict[str, Any]:
    """CORE.APPLY_DQ_SUITE (owner's rights): make the catalog hold exactly the approved version's checks. Checks are
    merged by CHECK_ID (keeping their run history and schedule), checks the version dropped are RETIRED."""
    found = rows(session, f"""SELECT S.TABLE_NAME, S.DOMAIN_ID, V.STATUS, V.CHECKS FROM {SUITES} S
                               JOIN {VERSIONS} V ON V.SUITE_ID = S.SUITE_ID WHERE S.SUITE_ID = ? AND V.VERSION = ?""",
                 [suite_id, int(version)])
    assert found, "suite version not found"
    assert found[0]["STATUS"] in LIVE, "only an approved suite version is applied to the catalog"
    table, domain = found[0]["TABLE_NAME"], found[0].get("DOMAIN_ID") or ""
    checks = list(variant(found[0]["CHECKS"]) or [])
    for c in checks:
        session.sql(f"""MERGE INTO {CATALOG} t
                         USING (SELECT ? AS CHECK_ID, ? AS TABLE_NAME, NULLIF(?, '') AS COLUMN_NAME, ? AS CHECK_TYPE,
                                       PARSE_JSON(?) AS CHECK_DEFINITION, ? AS SODA_DSL, ? AS SEVERITY, ? AS STATUS,
                                       ? AS ORIGIN, NULLIF(?, '') AS DOMAIN_ID) s
                            ON t.CHECK_ID = s.CHECK_ID
                          WHEN MATCHED THEN UPDATE SET TABLE_NAME = s.TABLE_NAME, COLUMN_NAME = s.COLUMN_NAME,
                               CHECK_TYPE = s.CHECK_TYPE, CHECK_DEFINITION = s.CHECK_DEFINITION, SODA_DSL = s.SODA_DSL,
                               SEVERITY = s.SEVERITY, STATUS = s.STATUS, DOMAIN_ID = COALESCE(s.DOMAIN_ID, t.DOMAIN_ID),
                               LAST_RUN_STATUS = IFF(t.CHECK_DEFINITION = s.CHECK_DEFINITION AND t.SEVERITY = s.SEVERITY,
                                                     t.LAST_RUN_STATUS, NULL),
                               UPDATED_AT = CURRENT_TIMESTAMP()
                          WHEN NOT MATCHED THEN INSERT (CHECK_ID, TABLE_NAME, COLUMN_NAME, CHECK_TYPE, CHECK_DEFINITION,
                               SODA_DSL, SEVERITY, STATUS, ORIGIN, DOMAIN_ID)
                               VALUES (s.CHECK_ID, s.TABLE_NAME, s.COLUMN_NAME, s.CHECK_TYPE, s.CHECK_DEFINITION,
                                       s.SODA_DSL, s.SEVERITY, s.STATUS, s.ORIGIN, s.DOMAIN_ID)""",
                    params=[c["check_id"], table, c.get("target_column") or "", c["check_type"],
                            json.dumps(c["definition"]), clip(c.get("soda_dsl"), 4000), c["severity"],
                            c.get("status") or "ACTIVE", c.get("origin") or "USER", domain]).collect()
        try:
            session.sql(f"""UPDATE {CATALOG} SET REQUIREMENT = NULLIF(?, ''),
                                   SOURCE_RUN_ID = COALESCE(NULLIF(?, ''), SOURCE_RUN_ID) WHERE CHECK_ID = ?""",
                        params=[clip(c.get("requirement") or "", 4000), c.get("source_run_id") or "",
                                c["check_id"]]).collect()
        except Exception:
            pass  # before V038
    keep = [c["check_id"] for c in checks]
    retire = f"""UPDATE {CATALOG} SET STATUS = 'RETIRED', UPDATED_AT = CURRENT_TIMESTAMP()
                  WHERE TABLE_NAME = ? AND STATUS <> 'RETIRED'"""
    if keep:
        retire += f" AND CHECK_ID NOT IN ({', '.join(['?'] * len(keep))})"
    session.sql(retire, params=[table, *keep]).collect()
    try:  # checks new to the catalog follow the table's schedule
        session.sql(f"""UPDATE {CATALOG} SET SCHEDULE_CRON = (SELECT MAX(CRON) FROM CORE.DQ_SCHEDULE WHERE TABLE_NAME = ?)
                         WHERE TABLE_NAME = ? AND SCHEDULE_CRON IS NULL""", params=[table, table]).collect()
    except Exception:
        pass
    session.sql(f"UPDATE {VERSIONS} SET CATALOG_SYNCED_AT = CURRENT_TIMESTAMP() WHERE SUITE_ID = ? AND VERSION = ?",
                params=[suite_id, int(version)]).collect()
    return {"table": table, "version": int(version), "checks": len(checks),
            "active": sum(1 for c in checks if c.get("status", "ACTIVE") == "ACTIVE")}


def backfill(session, user: str = "SYSTEM_BACKFILL") -> List[str]:
    """An APPROVED version 1 for every catalog table that has no suite yet (run by the deploy)."""
    try:
        tables = [r["TABLE_NAME"] for r in rows(session, f"""SELECT DISTINCT C.TABLE_NAME FROM {CATALOG} C
                                                               LEFT JOIN {SUITES} S ON S.TABLE_NAME = C.TABLE_NAME
                                                              WHERE S.SUITE_ID IS NULL AND C.STATUS <> 'RETIRED'""")]
    except Exception as exc:
        return [f"dq suites: skipped ({str(exc)[:120]})"]
    log = []
    for table in tables:
        try:
            _ensure(session, table, None, user)
            log.append(f"dq suite {table}: baseline v1")
        except Exception as exc:
            log.append(f"dq suite {table}: skipped ({str(exc)[:120]})")
    return log
