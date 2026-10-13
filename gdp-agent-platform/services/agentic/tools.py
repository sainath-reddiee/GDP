"""The copilot's tools: thin adapters over the platform's existing API routes.

Each tool gets `call(method, path, body=None, params=None) -> (status, data)`, an in-process request to the API made
with the signed-in person's own session, so the API's privilege checks apply to everything a tool reads or tries, and
`ctx`: the conversation's attachments ({"attachments": [...], "read_attachment": async (id) -> text}) and its drafted
changes ({"changes": [...]}), so a revision builds on the open draft for the same asset.
Read and draft tools run during a conversation; they only read, render or dry-run. A draft tool returns a *change*:
the exact calls that would apply it, kept for review and replayed only by Approve and Publish.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from services.agentic import changeset, preview

Call = Callable[..., Awaitable[Tuple[int, Any]]]

ID = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,127}$")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")
FQN = re.compile(r"^[A-Za-z0-9_$\".]{3,800}$")


class ToolError(Exception):
    """A tool could not do what was asked; the message goes back to the model and the chat."""


@dataclass
class Tool:
    name: str
    description: str
    step: str                                   # plan rail step it advances
    args: Dict[str, Dict[str, Any]]             # field -> {type, required, max, desc}
    run: Callable[..., Awaitable[Dict[str, Any]]] = field(repr=False)   # (call, args, ctx) -> result
    drafts: bool = False                        # produces a change for review


TYPES = {"str": str, "int": int, "bool": bool, "list": list, "dict": dict}


def validate(tool: Tool, args: Any) -> Dict[str, Any]:
    """Check arguments against the tool's declaration; unknown fields are refused."""
    if not isinstance(args, dict):
        raise ToolError(f"{tool.name}: arguments must be an object")
    unknown = set(args) - set(tool.args)
    if unknown:
        raise ToolError(f"{tool.name}: unknown argument(s) {sorted(unknown)}")
    out: Dict[str, Any] = {}
    for name, spec in tool.args.items():
        value = args.get(name)
        if value is None or value == "":
            if spec.get("required"):
                raise ToolError(f"{tool.name}: {name} is required")
            continue
        kind = TYPES[spec["type"]]
        if kind is int and isinstance(value, str) and value.isdigit():
            value = int(value)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise ToolError(f"{tool.name}: {name} must be a {spec['type']}")
        if spec.get("max") and isinstance(value, (str, list)) and len(value) > spec["max"]:
            raise ToolError(f"{tool.name}: {name} is too long (at most {spec['max']})")
        out[name] = value
    return out


def _ok(status: int, data: Any, what: str) -> Any:
    if status == 202 and isinstance(data, dict) and data.get("pending_approval"):
        raise ToolError(f"{what} needs approval ({data.get('detail') or data.get('approver_role')})")
    if status >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        raise ToolError(f"{what} failed ({status}): {str(detail)[:600]}")
    return data


def _id(value: str, what: str) -> str:
    if not ID.match(str(value or "")):
        raise ToolError(f"{what} is not a valid id: {value!r}")
    return quote(str(value), safe="")


def _pick(row: Dict[str, Any], keys: Tuple[str, ...]) -> Dict[str, Any]:
    low = {str(k).lower(): v for k, v in (row or {}).items()}
    return {k: low[k] for k in keys if low.get(k) not in (None, "", [])}


def _clip(value: Any, n: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= n else text[:n] + "…"


# ---------------------------------------------------------------- read tools

async def fetch_jira_issue(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    key = str(a["key"]).strip().upper()
    if not JIRA_KEY.match(key):
        raise ToolError(f"{key} is not a Jira issue key")
    data = _ok(*await call("GET", f"/api/jira/issues/{key}"), what=f"Reading {key}")
    issue = _pick(data, ("key", "summary", "status", "issue_type", "type", "priority", "assignee", "labels", "url"))
    issue["description"] = _clip(data.get("description") or "", 6000)
    issue["recent_comments"] = [_clip(c.get("text") or "", 600) for c in (data.get("comments") or [])[-5:]]
    return {"summary": {"issue": issue}, "steps": [("intake", "DONE", f"Read {key}")], "jira_key": key}


async def list_domains(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _ok(*await call("GET", "/api/domains"), what="Listing domains")
    rows = data.get("domains") if isinstance(data, dict) else data
    return {"summary": {"domains": [_pick(r, ("domain_id", "domain_name", "description", "active_flag")) for r in (rows or [])][:50]}}


async def fetch_domain_context(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = {"query": a["query"][:2000], "limit": 6, **({"domain": a["domain"]} if a.get("domain") else {})}
    data = _ok(*await call("POST", "/api/knowledge/search", body), what="Knowledge search")
    rows = (data or {}).get("results") if isinstance(data, dict) else data
    hits = [{**_pick(r, ("title", "knowledge_type", "domain_name", "source_reference")),
             "content": _clip({str(k).lower(): v for k, v in r.items()}.get("content") or "", 900)} for r in (rows or [])]
    return {"summary": {"knowledge": hits}, "steps": [("context", "DONE", f"{len(hits)} knowledge items")]}


async def inspect_source(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    db, schema = str(a["database"]), str(a["schema"])
    if not (NAME.match(db.strip('"')) and NAME.match(schema.strip('"'))):
        raise ToolError("database and schema must be identifiers")
    data = _ok(*await call("GET", "/api/catalog/inventory", params={"database": db, "schema": schema}),
               what=f"Reading {db}.{schema}")
    tables = [_pick(t, ("table_name", "name", "row_count", "status", "column_count", "pii_columns"))
              for t in (data.get("tables") or [])][:60]
    return {"summary": {"database": db, "schema": schema, "tables": tables,
                        "domain_candidates": (data.get("domain_candidates") or [])[:3]},
            "steps": [("context", "DONE", f"{db}.{schema}: {len(tables)} tables")]}


async def find_table_models(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {k: str(a[k])[:100] for k in ("search", "domain_id") if a.get(k)}
    data = _ok(*await call("GET", "/api/hub/table-models", params=params), what="Listing table models")
    models = [_pick(m, ("table_model_id", "table_name", "source_name", "domain_name", "model_layer", "table_type",
                        "current_version")) for m in (data.get("models") or [])][:25]
    return {"summary": {"table_models": models}}


def _entity(spec: Dict[str, Any]) -> Dict[str, Any]:
    entities = ((spec or {}).get("design") or {}).get("entities") or []
    if not entities:
        raise ToolError("the table model has no table yet")
    return entities[0]


async def _table_model(call: Call, model_id: str) -> Dict[str, Any]:
    data = _ok(*await call("GET", f"/api/hub/table-models/{_id(model_id, 'table_model_id')}"), what="Reading the table model")
    if not data.get("current"):
        raise ToolError("the table model has no saved version yet")
    return data


async def get_table_model(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    entity = _entity(data["current"]["spec"])
    return {"summary": {"model": _pick(data["model"], ("table_model_id", "table_name", "domain_name", "model_layer",
                                                       "table_type", "current_version")),
                        "entity": entity.get("entity_name"), "grain": entity.get("grain"),
                        "columns": [_pick(c, ("name", "datatype", "nullable", "is_pk", "description"))
                                    for c in entity.get("attributes") or []][:200]},
            "steps": [("context", "DONE", "")]}


def _open_change(ctx: Optional[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    """The open (unpublished) draft for an asset in this conversation, so a revision builds on it instead of
    starting again from the saved version and silently dropping what was drafted before."""
    for c in (ctx or {}).get("changes") or []:
        if c.get("change_key") == key and c.get("status") == "PROPOSED":
            return c
    return None


def _apply_columns(spec: Dict[str, Any], operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not operations:
        raise ToolError("operations is empty: say which columns to add, modify or drop")
    spec = copy.deepcopy(spec)
    entity = _entity(spec)
    attrs = entity.setdefault("attributes", [])
    for op in operations:
        if not isinstance(op, dict):
            raise ToolError("each operation must be an object")
        index = {str(c.get("name") or "").upper(): c for c in attrs}
        kind, name = str(op.get("op") or "").lower(), str(op.get("column") or "").strip()
        if not NAME.match(name):
            raise ToolError(f"{name!r} is not a column name")
        fields = {k: op[k] for k in ("datatype", "nullable", "is_pk", "description", "business_definition")
                  if op.get(k) is not None}
        for flag in ("nullable", "is_pk"):
            if flag in fields and not isinstance(fields[flag], bool):
                fields[flag] = str(fields[flag]).strip().lower() in ("true", "yes", "1", "y")
        if fields.get("is_pk") and "nullable" not in fields:
            fields["nullable"] = False   # a key column cannot be nullable (spec_v2.validate refuses it)
        existing = index.get(name.upper())
        if kind == "add":
            if existing:
                raise ToolError(f"{name} already exists in {entity.get('entity_name')}; use modify")
            if not fields.get("datatype"):
                raise ToolError(f"adding {name} needs a datatype")
            attrs.append({"name": name.upper(), "nullable": True, **fields})
        elif kind == "modify":
            if not existing:
                raise ToolError(f"{name} is not a column of {entity.get('entity_name')}")
            if not fields:
                raise ToolError(f"modify {name}: say what changes (datatype, nullable, is_pk, description)")
            existing.update(fields)
        elif kind == "drop":
            if not existing:
                raise ToolError(f"{name} is not a column of {entity.get('entity_name')}")
            attrs.remove(existing)
        else:
            raise ToolError(f"unknown operation {kind!r}; use add, modify or drop")
    return spec


async def update_table_model(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    header, current = data["model"], data["current"]
    model_id = _id(a["table_model_id"], "table_model_id")
    key = f"TABLE_MODEL:{a['table_model_id']}"
    prior = _open_change(ctx, key)
    base = ((prior or {}).get("operations") or [{}])[0].get("body", {}).get("spec") or current["spec"]
    draft = _apply_columns(base, a["operations"])
    rendered = _ok(*await call("POST", f"/api/hub/table-models/{model_id}/render",
                               {"spec": draft, "table_type": header.get("table_type") or "STANDARD",
                                "domain": header.get("domain_name") or None}), what="Rendering the model")
    errors = [i for i in rendered.get("issues") or [] if i.get("severity") == "ERROR"]
    if errors:
        raise ToolError("the changed model does not validate: " + "; ".join(f"{i.get('path')}: {i.get('message')}" for i in errors[:8]))
    note = a.get("change_note") or "Copilot draft"
    counts = preview.columns(current["spec"], rendered["spec"])["counts"]
    parts = [f"{n} column{'s' if n != 1 else ''} {k}" for k, n in counts.items() if n]
    change = changeset.make(
        key, "MODEL", f"Table model {header.get('table_name') or a['table_model_id']}: {', '.join(parts) or note}"[:512],
        [{"method": "PUT", "path": f"/api/hub/table-models/{model_id}",
          "body": {"spec": rendered["spec"], "etag": header.get("etag"), "change_note": note[:2000]}}],
        # the preview is always against the saved version, so it shows everything this draft would change
        {"model": preview.columns(current["spec"], rendered["spec"]),
         "files": preview.files_diff(preview.rendered_files(current), preview.rendered_files(rendered))})
    counts = change["preview"]["model"]["counts"]
    warnings = [f"{i.get('path')}: {i.get('message')}" for i in rendered.get("issues") or [] if i.get("severity") != "ERROR"][:8]
    return {"summary": {"drafted": change["title"], "columns_vs_saved_version": counts, "warnings": warnings,
                        "built_on_earlier_draft": bool(prior), "ddl_and_dbt_rendered": True},
            "change": change, "steps": [("model", "DONE", f"{counts['added']} added, {counts['changed']} changed, {counts['removed']} removed"),
                                        ("dryrun", "DONE", "DDL and dbt rendered")]}


async def generate_dbt_models(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    files = preview.rendered_files(data["current"])
    return {"summary": {"files": {p: _clip(t, 1500) for p, t in list(files.items())[:8]},
                        "note": "dbt and DDL are rendered from the table model; change the model to change them"}}


async def find_sttm_mappings(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {k: str(a[k])[:200] for k in ("q", "target", "domain") if a.get(k)}
    data = _ok(*await call("GET", "/api/hub/sttm", params=params), what="Listing mappings")
    rows = [_pick(m, ("catalog_id", "version", "target_entity", "source_system", "domain_name", "status", "line_count"))
            for m in (data.get("mappings") or [])][:20]
    return {"summary": {"mappings": rows}}


async def _mapping(call: Call, catalog_id: str) -> Dict[str, Any]:
    data = _ok(*await call("GET", f"/api/hub/sttm/{_id(catalog_id, 'catalog_id')}"), what="Reading the mapping")
    return data.get("mapping") or {}


LINE_KEYS = ("target_column", "target_datatype", "mapping_type", "source_table", "source_column", "transformation")


async def get_sttm_mapping(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = await _mapping(call, a["catalog_id"])
    return {"summary": {"catalog_id": entry.get("catalog_id"), "version": entry.get("version"),
                        "target_entity": entry.get("target_entity"), "status": entry.get("status"),
                        "lines": [_pick(l, LINE_KEYS) for l in entry.get("lines") or []][:150]}}


def _merge_by_column(earlier: List[Dict[str, Any]], later: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in list(earlier) + list(later):
        col = str(item.get("target_column") or "").upper()
        out[col] = {**out.get(col, {}), **item}
    return list(out.values())


async def generate_sttm_mapping(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from services.catalog import sttm as cat_sttm

    entry = await _mapping(call, a["catalog_id"])
    cid = _id(a["catalog_id"], "catalog_id")
    key = f"STTM:{a['catalog_id']}"
    current = {str(l.get("target_column") or "").upper(): l for l in entry.get("lines") or []}
    edits, notes = [], []
    for e in a.get("edits") or []:
        if not isinstance(e, dict) or not e.get("target_column"):
            raise ToolError("each edit needs a target_column")
        col = str(e["target_column"])
        if col.upper() not in current:
            raise ToolError(f"{col} is not a target column of this mapping; add it with add_lines")
        edit = {"target_column": col, **{k: v for k, v in e.items() if k in cat_sttm.EDITABLE and v is not None}}
        if e.get("instruction") and not e.get("transformation"):
            line = current.get(col.upper()) or {}
            status, proposal = await call("POST", f"/api/hub/sttm/{cid}/refine", {
                "target_column": col, "prompt": str(e["instruction"])[:2000],
                "current_transformation": line.get("transformation"), "source_table": line.get("source_table"),
                "source_column": line.get("source_column")})
            if status >= 400 or not isinstance(proposal, dict) or not proposal.get("transformation"):
                detail = proposal.get("detail") if isinstance(proposal, dict) else proposal
                raise ToolError(f"No AI proposal for {col}: {_clip(detail, 300)}. Give the transformation yourself instead.")
            edit["transformation"] = proposal["transformation"]
            notes.append({"target_column": col, "rationale": _clip(proposal.get("rationale") or "", 400)})
        if len(edit) > 1:
            edits.append(edit)
    add = [{k: v for k, v in l.items() if v is not None} for l in a.get("add_lines") or [] if isinstance(l, dict)]
    for l in add:
        if not l.get("target_column") or not l.get("target_datatype") or not l.get("mapping_type"):
            raise ToolError("each added line needs target_column, target_datatype and mapping_type")
    if not edits and not add:
        raise ToolError("nothing to change in the mapping")
    prior = _open_change(ctx, key)
    if prior:  # build on the open draft for this mapping
        body = prior["operations"][0]["body"]
        edits = _merge_by_column(body.get("edits") or [], edits)
        add = _merge_by_column(body.get("add_lines") or [], add)
    try:
        lines, _ = cat_sttm.apply_edits(entry.get("lines") or [], edits)
        lines, _ = cat_sttm.add_lines(lines, add) if add else (lines, [])
    except AssertionError as exc:
        raise ToolError(str(exc)) from None
    note = a.get("note") or "Copilot draft"
    change = changeset.make(
        key, "STTM", f"Mapping {entry.get('target_entity') or a['catalog_id']}: {note}"[:512],
        [{"method": "POST", "path": f"/api/hub/sttm/{cid}/edit",
          "body": {"base_version": int(entry.get("version") or 1), "edits": edits, "add_lines": add, "note": note[:2000]}}],
        {"sttm": preview.sttm_lines(entry.get("lines") or [], lines)})
    return {"summary": {"drafted": change["title"], "edited": [e["target_column"] for e in edits],
                        "added": [l.get("target_column") for l in add], "ai_rationale": notes,
                        "built_on_earlier_draft": bool(prior),
                        "publishing": "a mapping edit always goes to an STTM approver"},
            "change": change, "steps": [("sttm", "DONE", f"{len(edits)} edited, {len(add)} added")]}


async def register_qa_sql_pack(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from services.qa.pack_files import NAME as QUERY_NAME, SEVERITIES

    domain = _id(a["domain_id"], "domain_id")
    if not a["queries"]:
        raise ToolError("queries is empty: a QA pack needs at least one query")
    queries = []
    for q in a["queries"]:
        if not isinstance(q, dict) or not q.get("query_name") or not q.get("sql"):
            raise ToolError("each query needs query_name and sql")
        name = str(q["query_name"]).strip()
        if not QUERY_NAME.match(name):
            raise ToolError(f"{name!r}: a query name starts with a letter and has only letters, digits and _")
        severity = str(q.get("severity") or "ERROR").upper()
        if severity not in SEVERITIES:
            raise ToolError(f"{name}: severity must be one of {list(SEVERITIES)}")
        queries.append({"query_name": name, "sql_expression": str(q["sql"]).strip().rstrip(";")[:16000],
                        "description": str(q["description"])[:4000] if q.get("description") else None,
                        "severity": severity, "expectation": str(q.get("expectation") or "0 rows")[:1000]})
    dry = []
    for q in queries:  # each query once, read-only, with the caller's role; nothing is stored
        status, data = await call("POST", "/api/qa/execute", {"domain_id": a["domain_id"], "sql": q["sql_expression"]})
        if status >= 400 or not isinstance(data, dict):
            dry.append({"query_name": q["query_name"], "ok": False,
                        "detail": _clip((data or {}).get("detail") if isinstance(data, dict) else data, 400)})
            continue
        result = (data.get("results") or [{}])[0]
        outcome = str(result.get("outcome") or "").upper()
        dry.append({"query_name": q["query_name"], "ok": outcome not in ("ERROR", ""),
                    **_pick(result, ("outcome", "detail", "rows_returned", "failing"))})
    prior_key = f"QA_PACK:{a['pack_id']}" if a.get("pack_id") else f"QA_PACK:new:{domain}:{str(a.get('name') or 'Copilot QA pack').strip().lower()}"
    prior = _open_change(ctx, prior_key)
    if prior:  # keep the queries drafted earlier for this pack
        earlier = {q["query_name"]: q for q in prior["operations"][0]["body"].get("queries") or []}
        queries = list({**earlier, **{q["query_name"]: q for q in queries}}.values())
    old: Dict[str, str] = {}
    body: Dict[str, Any] = {"name": str(a.get("name") or "Copilot QA pack")[:128], "queries": queries,
                            "change_note": (a.get("change_note") or "Copilot draft")[:2000]}
    if a.get("pack_id"):
        pack_id = _id(a["pack_id"], "pack_id")
        found = _ok(*await call("GET", f"/api/qa-packs/{pack_id}"), what="Reading the QA pack")
        pack = found.get("pack") or {}
        existing = {str(q.get("query_name")): q for q in found.get("queries") or []}
        old = {f"{n}.sql": str(q.get("sql_expression") or "") for n, q in existing.items()}
        keep = ("query_name", "sql_expression", "description", "severity", "expectation", "tags")
        merged = {**{n: {k: q.get(k) for k in keep} for n, q in existing.items()},
                  **{q["query_name"]: {**{k: existing.get(q["query_name"], {}).get(k) for k in ("tags",)}, **q} for q in queries}}
        # the pack's own description and tags are kept (an explicit null would erase them)
        body.update({"name": pack.get("name") or body["name"], "queries": list(merged.values()),
                     "base_version": pack.get("version"), "description": pack.get("description"),
                     "tags": pack.get("tags") or []})
        ops = [{"method": "PUT", "path": f"/api/qa-packs/{pack_id}", "body": body},
               {"method": "POST", "path": f"/api/qa-packs/{pack_id}/submit", "body": {"note": body["change_note"]}}]
    else:
        ops = [{"method": "POST", "path": f"/api/domains/{domain}/qa-packs", "body": body},
               {"method": "POST", "path": "/api/qa-packs/{op0.pack_id}/submit", "body": {"note": body["change_note"]}}]
    new = {**old, **{f"{q['query_name']}.sql": q["sql_expression"] for q in queries}}
    change = changeset.make(prior_key, "QA", f"QA pack {body['name']}: {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                            ops, {"files": preview.files_diff(old, new)})
    failed = [d for d in dry if not d["ok"]]
    return {"summary": {"drafted": change["title"], "dry_run": dry,
                        "publishing": "submitted for approval by a QA lead (four eyes)",
                        "next": "fix the failed queries and draft again" if failed else ""},
            "change": change, "steps": [("quality", "DONE", f"{len(queries)} QA queries"),
                                        ("dryrun", "BLOCKED" if failed else "DONE",
                                         f"{len(failed)} QA quer{'y' if len(failed) == 1 else 'ies'} failed to run" if failed else "QA queries ran")]}


DSL_FORMS = ("missing_count(COL) = 0 | missing_percent(COL) < 5% | duplicate_count(COL) = 0 | duplicate_count(A, B) = 0 | "
             "duplicate_percent(COL) < 1% | row_count > 0 | row_count between 1 and 1000 | freshness(COL) < 1d | "
             "min(COL) >= 0 | max(COL) <= 100 | avg(COL) between 1 and 10 (COL is the column name)")

SODA_SEVERITY = {"WARN": "WARN", "WARNING": "WARN", "FAIL": "FAIL", "ERROR": "FAIL", "CRITICAL": "FAIL"}


async def register_soda_checks(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    table = str(a["table"]).strip()
    if not FQN.match(table) or table.count(".") != 2:
        raise ToolError("table must be DATABASE.SCHEMA.TABLE")
    if not a["checks"]:
        raise ToolError("checks is empty")
    ok, problems = [], []
    for c in a["checks"]:
        if not isinstance(c, dict) or not c.get("dsl"):
            raise ToolError("each check needs a one-line SodaCL check in dsl")
        severity = SODA_SEVERITY.get(str(c.get("severity") or "WARN").upper())
        if not severity:
            raise ToolError(f"severity must be WARN or FAIL, not {c.get('severity')!r}")
        body = {"table": table, "column": str(c["column"]) if c.get("column") else None, "severity": severity,
                "dsl": str(c["dsl"])[:2000], "requirement": str(c["requirement"])[:4000] if c.get("requirement") else None,
                **({"domain_id": str(a["domain_id"])} if a.get("domain_id") else {})}
        data = _ok(*await call("POST", "/api/dq/parse", body), what="Checking a SodaCL check")
        if not data.get("ok"):
            problems.append({"dsl": body["dsl"], "problems": data.get("problems")})
            continue
        ok.append({**body, "_headline": data.get("headline") or ""})
    if not ok:
        raise ToolError(f"no check parsed: {_clip(problems, 600)}. Write each dsl as one of: {DSL_FORMS}")
    key = f"DQ:{table.upper()}"
    prior = _open_change(ctx, key)
    if prior:  # keep the checks drafted earlier for this table (same column and check: the newer one wins)
        earlier = [op["body"] for op in prior["operations"]]
        seen = {(str(b.get("column") or "").upper(), b["dsl"]) for b in ok}
        ok = [b for b in earlier if (str(b.get("column") or "").upper(), b["dsl"]) not in seen] + ok
    lines = [f"- {b['dsl']}    # {b.get('column') or 'table'}, {b['severity']}{': ' + b['_headline'] if b.get('_headline') else ''}" for b in ok]
    bodies = [{k: v for k, v in b.items() if k != "_headline"} for b in ok]
    name = table.split(".")[-1].lower()
    change = changeset.make(
        key, "DQ", f"Data quality checks on {table}: {len(bodies)} added",
        [{"method": "POST", "path": "/api/dq/catalog", "body": b} for b in bodies],
        {"files": [preview.file_diff(f"checks/{name}.yml", None, f"checks for {table}:\n" + "\n".join(lines) + "\n")]})
    return {"summary": {"drafted": change["title"], "rejected": problems, "built_on_earlier_draft": bool(prior),
                        "publishing": "added to the table's suite draft; a DQ approver approves the suite"},
            "change": change, "steps": [("quality", "DONE", f"{len(bodies)} checks"), ("dryrun", "DONE", "SodaCL parsed")]}


async def onboard_source_config(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = {k: a[k] for k in ("source", "target", "load", "snaplogic", "tables", "comments") if a.get(k) is not None}
    data = _ok(*await call("POST", "/api/hub/ingestion-control/validate", config), what="Validating the source")
    if not data.get("valid"):
        return {"summary": {"valid": False, "errors": data.get("errors"),
                            "next": "ask the person for the missing or wrong fields (only those), then call this tool again with everything"},
                "steps": [("intake", "RUNNING", f"{len(data.get('errors') or {})} source details still needed")]}
    source = data.get("source") or {}
    name = str(source.get("source_name") or (a.get("source") or {}).get("source_name") or "source")
    change = changeset.make(f"INGESTION:{name.upper()}", "INGESTION",
                            f"SnapLogic ingestion for {name}: {len(data.get('rows') or [])} table(s)",
                            [{"method": "POST", "path": "/api/hub/ingestion-control", "body": config}],
                            {"files": [preview.file_diff(f"ingestion/{name.lower()}.json", None,
                                                         json.dumps({"source": source, "rows": data.get("rows")}, indent=2, default=str))]})
    return {"summary": {"valid": True, "drafted": change["title"]}, "change": change,
            "steps": [("intake", "DONE", f"{name} validated"), ("dryrun", "DONE", "ingestion config validated")]}


async def sync_jira_status(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    key = str(a["key"]).strip().upper()
    if not JIRA_KEY.match(key):
        raise ToolError(f"{key} is not a Jira issue key")
    if not str(a["comment_markdown"]).strip():
        raise ToolError("comment_markdown is empty")
    ops = [{"method": "POST", "path": f"/api/jira/issues/{key}/comment", "body": {"markdown": str(a["comment_markdown"])[:30000]}}]
    moved = None
    if a.get("transition"):
        data = _ok(*await call("GET", f"/api/jira/issues/{key}/transitions"), what=f"Reading {key} transitions")
        want = str(a["transition"]).strip().lower()
        options = data.get("transitions") or []
        pick = next((t for t in options if want in (str(t.get("name") or "").lower(), str(t.get("to") or "").lower())), None)
        if not pick:
            raise ToolError(f"{key} cannot move to {a['transition']!r}; it can move to: "
                            + (", ".join(str(t.get("to") or t.get("name")) for t in options) or "nothing right now"))
        ops.append({"method": "POST", "path": f"/api/jira/issues/{key}/transition", "body": {"transition_id": str(pick["id"])}})
        moved = pick.get("to") or pick.get("name")
    change = changeset.make(f"JIRA:{key}", "JIRA", f"Jira {key}: comment" + (f" and move to {moved}" if moved else ""), ops,
                            {"files": [preview.file_diff(f"jira/{key}-comment.md", None, str(a["comment_markdown"]))]})
    return {"summary": {"drafted": change["title"], "publishing": "posted after you approve, as you"}, "change": change}


# ---------------------------------------------------------------- conversation context, questions and lookups

async def list_attachments(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"summary": {"attachments": (ctx or {}).get("attachments") or []}}


async def read_attachment(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    files = {f["attachment_id"]: f for f in (ctx or {}).get("attachments") or []}
    meta = files.get(a["attachment_id"])
    if not meta or not (ctx or {}).get("read_attachment"):
        raise ToolError(f"no attachment {a['attachment_id']!r} in this conversation")
    text = await ctx["read_attachment"](a["attachment_id"])
    if not text:
        return {"summary": {"file_name": meta.get("file_name"), "text": "", "note": meta.get("note") or "no text could be read"}}
    start = max(0, int(a.get("offset") or 0))
    chunk = text[start:start + 15000]
    nxt = start + len(chunk)
    return {"summary": {"file_name": meta.get("file_name"), "offset": start, "text": chunk, "total_chars": len(text),
                        "next_offset": nxt if nxt < len(text) else None},
            "steps": [("intake", "DONE", f"read {meta.get('file_name')}")]}


async def answer_question(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = {"question": a["question"][:2000], **({"domain": a["domain"]} if a.get("domain") else {})}
    data = _ok(*await call("POST", "/api/knowledge/answer", body), what="Answering from domain knowledge")
    cites = [_pick(c, ("title", "knowledge_id", "domain_name")) if isinstance(c, dict) else c for c in (data.get("citations") or [])]
    return {"summary": {"answer": _clip(data.get("answer") or "", 6000), "citations": cites[:8]},
            "steps": [("context", "DONE", "")]}


async def search_jira(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {"scope": "search", "q": a["q"][:200]} if a.get("q") else {"scope": "mine"}
    data = _ok(*await call("GET", "/api/jira/issues", params=params), what="Searching Jira")
    return {"summary": {"issues": [_pick(i, ("key", "summary", "status", "priority", "issue_type", "assignee"))
                                   for i in (data.get("issues") or [])][:25]}}


async def list_runs(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {"limit": "20", **({"q": a["q"][:100]} if a.get("q") else {})}
    data = _ok(*await call("GET", "/api/runs", params=params), what="Listing runs")
    rows = data.get("runs") if isinstance(data, dict) else data
    return {"summary": {"runs": [_pick(r, ("run_id", "run_name", "current_state", "domain_name", "updated_at", "status"))
                                 for r in (rows or [])][:20]}}


async def list_ingestion_sources(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {"q": a["q"][:100]} if a.get("q") else {}
    data = _ok(*await call("GET", "/api/hub/ingestion-control", params=params), what="Listing SnapLogic configurations")
    rows = [_pick(r, ("master_id", "child_id", "source_name", "source_type", "landing_table", "load_frequency", "is_active"))
            for r in (data.get("controls") or [])][:40]
    return {"summary": {"configurations": rows, "deployed": data.get("deployed", True)}}


def onboarding_catalog() -> Dict[str, Any]:
    from services.source import ingestion_control as ic

    req, opt = "required", "optional"
    return {
        "source": {"source_name": f"{req}: letters, digits and _, starts with a letter",
                   "source_type": f"{req}: one of {list(ic.SOURCE_TYPES)}",
                   "source_file_type": f"{opt}: one of {list(ic.FILE_TYPES)} (TABLE for a database)",
                   "source_sheet_name": "required when source_file_type is XLSX",
                   "source_account": f"{opt}: the SnapLogic account that connects to the source",
                   "source_database_path": f"{opt}: database, service name or folder path",
                   "source_schema_file": f"{opt}: schema name or file pattern"},
        "target": {"landing_database": f"{req}: Snowflake database", "landing_schema": f"{req}: Snowflake schema",
                   "bronze_schema": f"{req}: Snowflake schema", "external_stage": f"{opt}: such as @DB.SCHEMA.STAGE/path",
                   "iceberg": f"{opt}: true for Iceberg landing tables", "external_volume": "required when iceberg is true",
                   "base_location": f"{opt}: storage path", "config_table": f"{opt}: DB.SCHEMA.TABLE", "target_account": opt},
        "load": {"load_type": f"{req}: one of {list(ic.LOAD_TYPES)}",
                 "bronze_load_type": f"{req}: one of {list(ic.BRONZE_LOAD_TYPES)}",
                 "load_frequency": f"{req}: one of {list(ic.FREQUENCIES)}",
                 "job_trigger_time": "required unless ON_DEMAND: five cron fields and an optional time zone, such as 0 2 * * * UTC",
                 "landing_to_bronze_flag": f"{opt}: default true", "is_active": f"{opt}: default true",
                 "last_run_timestamp": f"{opt}: such as 2026-10-01 02:00"},
        "snaplogic": {"snaplogic_project_name": req, "parent_pipeline_name": req, "child_pipeline_name": req,
                      "api_url": f"{opt}: https URL",
                      "api_key": f"{opt}: the NAME of the secret in the vault, such as vault:snaplogic/ingestion; never a token"},
        "tables": {"each": {"source_table_sql": f"{req}: a table name or one read-only SELECT",
                            "source_primary_keys": "required when load_type is SCD2 or bronze_load_type is SCD2 or MERGE: comma separated",
                            "source_delta_key": f"{opt}: the column incremental loads filter on",
                            "source_where_clause": f"{opt}: one condition", "source_audit_columns": f"{opt}: comma separated",
                            "landing_table": f"{opt}: derived from the source name and table when empty",
                            "bronze_table": f"{opt}: defaults to the landing table",
                            "target_previous_load_time": f"{opt}: where the first delta load starts",
                            "source_delete_action": f"{opt}: one of {list(ic.DELETE_ACTIONS)}",
                            "source_delete_key, source_delete_key_value, source_delete_query": "when deletes are REQUIRED"},
                   "max": ic.MAX_TABLES},
        "how_to_ask": "Fill what you can from attachments, Jira and inspect_source; ask the person for the rest in short "
                      "forms (select fields for the fixed choices above), then call onboard_source_config. Repeat with "
                      "its errors until it validates.",
    }


async def landing_targets(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _ok(*await call("GET", "/api/landing/targets"), what="Reading landing targets")
    return {"summary": {"default": data.get("default"), "database": data.get("database"),
                        "schemas": (data.get("schemas") or [])[:100], "iceberg_available": data.get("iceberg_available")}}


async def onboarding_requirements(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"summary": onboarding_catalog(), "steps": [("intake", "RUNNING", "collecting source details")]}


# ---------------------------------------------------------------- catalog

def _s(required: bool = False, max: int = 0, desc: str = "") -> Dict[str, Any]:
    return {"type": "str", "required": required, "max": max or 2000, "desc": desc}


def _l(desc: str, required: bool = True, max: int = 200) -> Dict[str, Any]:
    return {"type": "list", "required": required, "max": max, "desc": desc}


def _d(desc: str) -> Dict[str, Any]:
    return {"type": "dict", "required": False, "desc": desc}


TOOLS: Dict[str, Tool] = {t.name: t for t in [
    Tool("list_attachments", "List the files the person attached to this conversation.", "intake", {}, list_attachments),
    Tool("read_attachment", "Read an attached file's text (15,000 characters at a time; pass next_offset to continue).",
         "intake", {"attachment_id": _s(True, 36), "offset": {"type": "int", "required": False, "desc": "start character"}},
         read_attachment),
    Tool("answer_question", "Answer a question from the platform's domain knowledge, with citations.", "context",
         {"question": _s(True, 2000), "domain": _s(False, 128)}, answer_question),
    Tool("search_jira", "Search Jira issues by key or text (empty q lists the person's open issues).", "intake",
         {"q": _s(False, 200)}, search_jira),
    Tool("list_runs", "List recent pipeline runs, optionally filtered by text.", "context", {"q": _s(False, 100)}, list_runs),
    Tool("list_ingestion_sources", "List the SnapLogic ingestion configurations already onboarded.", "context",
         {"q": _s(False, 100)}, list_ingestion_sources),
    Tool("landing_targets", "The platform's real landing database, its schemas and the default landing schema. Use them "
         "as defaults when onboarding a source.", "context", {}, landing_targets),
    Tool("onboarding_requirements", "Every field a SnapLogic source onboarding needs, with the allowed choices. Call it "
         "before asking the person about a new source.", "intake", {}, onboarding_requirements),
    Tool("fetch_jira_issue", "Read a Jira issue (summary, description, recent comments).", "intake",
         {"key": _s(True, 40, "issue key such as GDP-123")}, fetch_jira_issue),
    Tool("list_domains", "List the business domains registered on the platform.", "context", {}, list_domains),
    Tool("fetch_domain_context", "Search domain knowledge (rules, definitions, patterns, decisions).", "context",
         {"query": _s(True, 2000), "domain": _s(False, 128, "domain name to filter on")}, fetch_domain_context),
    Tool("inspect_source", "List the tables of a Snowflake schema with row counts and profile status.", "context",
         {"database": _s(True, 255), "schema": _s(True, 255)}, inspect_source),
    Tool("find_table_models", "Find table models (one modeled table each) by name or domain.", "context",
         {"search": _s(False, 100), "domain_id": _s(False, 36)}, find_table_models),
    Tool("get_table_model", "Read a table model's columns, keys and grain.", "context",
         {"table_model_id": _s(True, 64)}, get_table_model),
    Tool("update_table_model", "Draft column changes to a table model; renders the DDL and dbt as a dry run. "
         "operations: [{op: add|modify|drop, column, datatype?, nullable?, is_pk?, description?, business_definition?}].",
         "model", {"table_model_id": _s(True, 64), "operations": _l("column operations", max=100),
                   "change_note": _s(False, 2000)}, update_table_model, drafts=True),
    Tool("generate_dbt_models", "Show the dbt and DDL files rendered from a table model.", "model",
         {"table_model_id": _s(True, 64)}, generate_dbt_models),
    Tool("find_sttm_mappings", "Find source-to-target mappings in the STTM Store.", "context",
         {"q": _s(False, 200), "target": _s(False, 200), "domain": _s(False, 128)}, find_sttm_mappings),
    Tool("get_sttm_mapping", "Read a mapping's lines (target column, source, transformation).", "context",
         {"catalog_id": _s(True, 64)}, get_sttm_mapping),
    Tool("generate_sttm_mapping", "Draft mapping changes. edits: [{target_column, transformation? or instruction?, "
         "source_table?, source_column?, business_rule?}] (an instruction asks the platform's AI for the transformation); "
         "add_lines: [{target_column, target_datatype, mapping_type, source_table?, source_column?, transformation?}].",
         "sttm", {"catalog_id": _s(True, 64), "edits": _l("line edits", False, 200),
                  "add_lines": _l("new lines", False, 100), "note": _s(False, 2000)}, generate_sttm_mapping, drafts=True),
    Tool("register_qa_sql_pack", "Draft domain QA queries (each a read-only SELECT that returns failing rows); each "
         "query is dry-run now. queries: [{query_name, sql, description?, severity?, expectation?}].", "quality",
         {"domain_id": _s(True, 64), "name": _s(False, 128), "pack_id": _s(False, 36),
          "queries": _l("QA queries", max=50), "change_note": _s(False, 2000)}, register_qa_sql_pack, drafts=True),
    Tool("register_soda_checks", "Draft data quality checks for a table, each a one-line SodaCL check (checked now). "
         "checks: [{column?, dsl, severity: WARN|FAIL, requirement?}]. dsl is exactly one of: " + DSL_FORMS + ".", "quality",
         {"table": _s(True, 800, "DATABASE.SCHEMA.TABLE"), "domain_id": _s(False, 36),
          "checks": _l("checks", max=50)}, register_soda_checks, drafts=True),
    Tool("onboard_source_config", "Validate and draft a SnapLogic external source onboarding (same fields as the "
         "Onboarding wizard: source, target, load, snaplogic, tables, comments).", "intake",
         {"source": _d("source"), "target": _d("target"), "load": _d("load"), "snaplogic": _d("snaplogic"),
          "tables": _l("tables", False, 200), "comments": _s(False, 4000)}, onboard_source_config, drafts=True),
    Tool("sync_jira_status", "Draft a Jira comment (markdown) and, optionally, a status transition by name.", "publish",
         {"key": _s(True, 40), "comment_markdown": _s(True, 30000), "transition": _s(False, 128)},
         sync_jira_status, drafts=True),
]}


def describe() -> List[Dict[str, Any]]:
    """The catalog as the model sees it."""
    return [{"name": t.name, "description": t.description, "drafts_a_change": t.drafts,
             "args": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "required", "desc") and vv not in ("", False)}
                      for k, v in t.args.items()}} for t in TOOLS.values()]
