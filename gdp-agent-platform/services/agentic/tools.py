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
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from services.agentic import changeset, preview

Call = Callable[..., Awaitable[Tuple[int, Any]]]

ID = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,127}$")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,30}-\d{1,9}$")
JIRA_PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{0,30}$")
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
    out: Dict[str, Any] = {"database": db, "schema": schema, "tables": tables,
                           "domain_candidates": (data.get("domain_candidates") or [])[:3]}
    if a.get("table"):
        table = str(a["table"]).strip()
        if not NAME.match(table.strip('"')):
            raise ToolError(f"{table!r} is not a table name")
        out["profile"] = await _column_profile(call, db, schema, table)
    return {"summary": out, "steps": [("context", "DONE", f"{db}.{schema}: {len(tables)} tables"
                                                       + (f", {a['table']} profiled" if a.get("table") else ""))]}


PROFILE_STATS = ("null_count", "null_percentage", "distinct_count", "distinct_percentage", "min", "max", "mean",
                 "min_length", "max_length", "row_count")


async def _column_profile(call: Call, db: str, schema: str, table: str) -> Dict[str, Any]:
    """One table's staged profile: per column its type, nulls, distinct values and range, with the suggested checks
    and the scorecard. A table that was never profiled says so (profile it from the Profiling store)."""
    status, data = await call("GET", "/api/catalog/profile", params={"database": db, "schema": schema, "table": table})
    if status == 404:
        return {"table": table, "profiled": False,
                "note": _clip((data or {}).get("detail") if isinstance(data, dict) else data, 300)}
    data = _ok(status, data, f"Reading the profile of {table}")
    doc = data.get("profile") or {}
    columns = []
    for c in (doc.get("columns") or [])[:150]:
        stats = c.get("statistics") or {}
        columns.append({"name": c.get("name") or c.get("column_name"), "datatype": c.get("data_type") or c.get("datatype"),
                        **{k: stats[k] for k in PROFILE_STATS if stats.get(k) not in (None, "")},
                        **({"semantic_type": c["semantic_type"]} if c.get("semantic_type") else {})})
    return {"table": table, "profiled": True, "row_count": doc.get("row_count"), "columns": columns,
            "scorecard": _pick(data.get("scorecard") or {}, ("score", "grade", "dimensions")),
            "suggested_checks": [_pick(k, ("check", "column", "reason")) for k in (data.get("checks") or [])][:30],
            "drift": (data.get("drift") or {}).get("summary") if isinstance(data.get("drift"), dict) else None}


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
    pr_ops = [op for op in ((prior or {}).get("operations") or [])[1:] if str(op.get("path") or "").endswith("/publish")]
    # a pull request drafted earlier for the saved version moves onto this draft: one pull request, for the new version
    standalone = _open_change(ctx, f"GIT:TABLE_MODEL:{a['table_model_id']}")
    if standalone and not pr_ops:
        body = (standalone.get("operations") or [{}])[0].get("body") or {}
        pr_ops = [{"method": "POST", "path": f"/api/hub/table-models/{model_id}/versions/{{op0.version}}/publish", "body": body}]
    change = changeset.make(
        key, "MODEL", (f"Table model {header.get('table_name') or a['table_model_id']}: {', '.join(parts) or note}"
                       + (", with a pull request" if pr_ops else ""))[:512],
        [{"method": "PUT", "path": f"/api/hub/table-models/{model_id}",
          "body": {"spec": rendered["spec"], "etag": header.get("etag"), "change_note": note[:2000]}}, *pr_ops],
        # the preview is always against the saved version, so it shows everything this draft would change
        {"model": {**preview.columns(current["spec"], rendered["spec"]),
                   "diagram": preview.diagram(current["spec"], rendered["spec"])},
         "files": preview.files_diff(preview.rendered_files(current), preview.rendered_files(rendered))})
    counts = change["preview"]["model"]["counts"]
    warnings = [f"{i.get('path')}: {i.get('message')}" for i in rendered.get("issues") or [] if i.get("severity") != "ERROR"][:8]
    return {"summary": {"drafted": change["title"], "columns_vs_saved_version": counts, "warnings": warnings,
                        "built_on_earlier_draft": bool(prior), "ddl_and_dbt_rendered": True,
                        **({"pull_request": "the pull request drafted for the saved version now opens for this draft's version"}
                           if standalone else {})},
            "supersedes": [standalone["change_key"]] if standalone else [],
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
                        "on_approve_and_publish": "the mapping edit goes to an STTM approver"},
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
    old_names: Dict[str, bool] = {}
    existing: Dict[str, Dict[str, Any]] = {}
    body: Dict[str, Any] = {"name": str(a.get("name") or "Copilot QA pack")[:128], "queries": queries,
                            "change_note": (a.get("change_note") or "Copilot draft")[:2000]}
    if a.get("pack_id"):
        pack_id = _id(a["pack_id"], "pack_id")
        found = _ok(*await call("GET", f"/api/qa-packs/{pack_id}"), what="Reading the QA pack")
        pack = found.get("pack") or {}
        existing = {str(q.get("query_name")): q for q in found.get("queries") or []}
        old_names = {n: True for n in existing}
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
    from services.qa.pack_files import render as render_pack

    keep = ("query_name", "sql_expression", "description", "severity", "expectation", "tags")
    after = {str(q["query_name"]): {k: q.get(k) for k in keep} for q in body["queries"]}
    before_pack = {"name": body["name"], "description": body.get("description"), "tags": body.get("tags") or [],
                   "queries": [{k: q.get(k) for k in keep} for q in existing.values()]} if a.get("pack_id") else None
    after_pack = {"name": body["name"], "description": body.get("description"), "tags": body.get("tags") or [],
                  "queries": list(after.values())}
    file_name = f"qa/{re.sub(r'[^a-z0-9]+', '_', body['name'].lower()).strip('_') or 'pack'}.sql"
    ran = {d["query_name"]: d for d in dry}
    shown = [{"query_name": n, "sql": q["sql_expression"], "severity": q.get("severity"),
              "description": q.get("description"), "expectation": q.get("expectation"),
              "dry_run": {k: v for k, v in ran.get(n, {}).items() if k != "query_name"} or None,
              "state": "added" if n not in {**old_names} else "changed"}
             for n, q in after.items() if n in {q2["query_name"] for q2 in queries}]
    change = changeset.make(prior_key, "QA", f"QA pack {body['name']}: {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                            ops, {"files": [preview.file_diff(file_name, render_pack(before_pack) if before_pack else None,
                                                              render_pack(after_pack))],
                                  "queries": shown})
    failed = [d for d in dry if not d["ok"]]
    return {"summary": {"drafted": change["title"], "dry_run": dry,
                        "on_approve_and_publish": "the pack is saved and submitted to a QA lead for approval (four eyes)",
                        "next": "fix the failed queries and draft again" if failed else ""},
            "cautions": [f"QA query {d['query_name']} did not run in the dry run ({_clip(d.get('detail') or d.get('outcome') or 'error', 300)}); "
                         "it was not checked and fails the same way when the pack runs" for d in failed],
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
        earlier = [op["body"] for op in prior["operations"] if op.get("path") == "/api/dq/catalog"]
        seen = {(str(b.get("column") or "").upper(), b["dsl"]) for b in ok}
        ok = [b for b in earlier if (str(b.get("column") or "").upper(), b["dsl"]) not in seen] + ok
    lines = [f"- {b['dsl']}    # {b.get('column') or 'table'}, {b['severity']}{': ' + b['_headline'] if b.get('_headline') else ''}" for b in ok]
    bodies = [{k: v for k, v in b.items() if k != "_headline"} for b in ok]
    name = table.split(".")[-1].lower()
    submit = a.get("submit", True)
    ops = [{"method": "POST", "path": "/api/dq/catalog", "body": b} for b in bodies]
    if submit:   # then the suite draft goes to a DQ approver, as Submit does in the Quality center
        ops.append({"method": "POST", "path": "/api/dq/suites/submit",
                    "body": {"table": table, "note": str(a.get("note") or "Copilot draft")[:2000]}})
    change = changeset.make(
        key, "DQ", f"Data quality checks on {table}: {len(bodies)} added" + ("" if submit else " (not submitted)"),
        ops,
        {"files": [preview.file_diff(f"checks/{name}.yml", None, f"checks for {table}:\n" + "\n".join(lines) + "\n")]})
    return {"summary": {"drafted": change["title"], "rejected": problems, "built_on_earlier_draft": bool(prior),
                        "saved_now": False,
                        "on_approve_and_publish": "added to the table's suite draft, then the suite is submitted to a DQ approver"
                                                  if submit else "added to the table's suite draft, not submitted"},
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
    markdown = str(a["comment_markdown"])[:28000]
    report = a.get("report_publish", True) and any(c.get("status") == "PROPOSED" and c.get("kind") != "JIRA"
                                                   for c in (ctx or {}).get("changes") or [])
    if report and PUBLISH_SUMMARY not in markdown:   # filled in at publish time with what actually happened
        markdown += "\n\n" + PUBLISH_SUMMARY
    ops = [{"method": "POST", "path": f"/api/jira/issues/{key}/comment", "body": {"markdown": markdown}}]
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
    shown = markdown.replace(PUBLISH_SUMMARY, "_(what publishing did is added here: each change applied, sent for approval "
                                                "or failed, with request ids and pull request links)_")
    change = changeset.make(f"JIRA:{key}", "JIRA", f"Jira {key}: comment" + (f" and move to {moved}" if moved else ""), ops,
                            {"files": [preview.file_diff(f"jira/{key}-comment.md", None, shown)]})
    return {"summary": {"drafted": change["title"], "on_approve_and_publish": "the comment (and move) is posted as the person"}, "change": change}


async def create_git_pr(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A pull request with a table model's DDL and dbt files, opened when the person publishes. With an open model
    draft in this conversation, the pull request is for the version that draft saves; otherwise for the saved version."""
    model_id = _id(a["table_model_id"], "table_model_id")
    found = _ok(*await call("GET", "/api/source-control/prs", params={"asset": a["table_model_id"]}),
                what="Reading source control")
    if not found.get("deployed", True):
        raise ToolError("source control is not deployed yet (migration V043)")
    if not found.get("publisher_ready"):
        raise ToolError("GitHub publishing is not set up. An admin sets it up in Admin, Integrations (GitHub publishing).")
    repos = [r for r in found.get("repos") or [] if r.get("repo_id")]
    if not repos:
        raise ToolError("no GitHub repository is connected for dbt. An admin connects one in Admin, Integrations.")
    open_prs = [_pick(p, ("version", "state", "pr_url", "branch")) for p in found.get("prs") or []
                if str(p.get("state") or "").upper() == "OPEN"][:5]
    wanted = str(a.get("repo_id") or "").strip()
    repo = next((r for r in repos if r["repo_id"] == wanted), None) if wanted else (repos[0] if len(repos) == 1 else None)
    if not repo:
        return {"summary": {"repositories": [_pick(r, ("repo_id", "name", "git_url", "branch")) for r in repos],
                            "open_pull_requests": open_prs,
                            "next": ("that repository is not connected; " if wanted else "")
                                    + "ask the person which repository (a select field with these names), then call again with repo_id"}}
    body = {"repo_id": repo["repo_id"], **{k: str(a[k])[:200] for k in ("base", "title") if a.get(k)}}
    key = f"TABLE_MODEL:{a['table_model_id']}"
    draft = _open_change(ctx, key)
    if draft:   # publish the version the model draft saves: its PUT returns the version number
        publish_op = {"method": "POST", "path": f"/api/hub/table-models/{model_id}/versions/{{op0.version}}/publish", "body": body}
        change = dict(draft)
        change["operations"] = [draft["operations"][0], publish_op]
        if ", with a pull request" not in change["title"]:
            change["title"] = (change["title"] + ", with a pull request")[:512]
        version_note = "the version the model draft saves"
        superseded = _open_change(ctx, f"GIT:TABLE_MODEL:{a['table_model_id']}")
    else:
        data = await _table_model(call, a["table_model_id"])
        version = int(data["model"].get("current_version") or data["current"].get("version") or 0)
        if not version:
            raise ToolError("the table model has no saved version to publish")
        name = data["model"].get("table_name") or a["table_model_id"]
        change = changeset.make(f"GIT:TABLE_MODEL:{a['table_model_id']}", "GIT",
                                f"Pull request for table model {name} v{version} to {repo.get('name')}",
                                [{"method": "POST", "path": f"/api/hub/table-models/{model_id}/versions/{version}/publish", "body": body}],
                                {"files": preview.files_diff({}, preview.rendered_files(data["current"]))})
        version_note = f"version {version}"
        superseded = None
    return {"supersedes": [superseded["change_key"]] if superseded else [],
            "summary": {"drafted": change["title"], "repository": repo.get("name"), "base": body.get("base") or repo.get("branch"),
                        "version": version_note, "open_pull_requests": open_prs,
                        "on_approve_and_publish": "the pull request is opened as the person; the version becomes PUBLISHED when it merges"},
            "change": change, "steps": [("publish", "RUNNING", f"pull request to {repo.get('name')} drafted")]}


async def find_domain_models(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _ok(*await call("GET", "/api/hub/domain-models"), what="Listing domain models")
    want = str(a.get("q") or "").strip().lower()
    rows = [_pick(m, ("model_id", "domain_id", "domain_name", "approved_version", "open_version", "open_status", "counts"))
            for m in data.get("models") or [] if not want or want in str(m.get("domain_name") or "").lower()]
    return {"summary": {"domain_models": rows[:40], "note": "a domain without model_id has no canonical model yet"}}


async def _domain_model(call: Call, model_id: str) -> Dict[str, Any]:
    data = _ok(*await call("GET", f"/api/hub/domain-models/{_id(model_id, 'model_id')}"), what="Reading the domain model")
    if not data.get("current"):
        raise ToolError("the domain model has no version yet")
    return data


async def get_domain_model(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = await _domain_model(call, a["model_id"])
    spec = data["current"]["spec"]
    want = str(a.get("entity") or "").upper()
    entities = ((spec.get("design") or {}).get("entities")) or []
    shown = [e for e in entities if not want or str(e.get("entity_name") or "").upper() == want]
    if want and not shown:
        raise ToolError(f"{a['entity']} is not an entity of this model; it has: "
                        + ", ".join(str(e.get("entity_name")) for e in entities[:60]))
    out = [{"entity_name": e.get("entity_name"), "kind": e.get("kind"), "grain": e.get("grain"),
            "columns": [_pick(c, ("name", "datatype", "nullable", "is_pk")) for c in e.get("attributes") or []][:120 if want else 40]}
           for e in shown[:60]]
    opened = data.get("open") or {}
    return {"summary": {"model": _pick(data["model"], ("model_id", "domain_name", "approved_version")),
                        "showing_version": data["current"].get("version"), "showing_status": data["current"].get("status"),
                        "open_version": _pick(opened, ("version", "status")), "entities": out,
                        "relationships": len(spec.get("relationships") or [])},
            "steps": [("context", "DONE", "")]}


def _apply_entities(spec: Dict[str, Any], operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Column and entity operations on a domain model specification (each names its entity)."""
    if not operations:
        raise ToolError("operations is empty: say which entities or columns to add, modify or drop")
    spec = copy.deepcopy(spec)
    entities = spec.setdefault("design", {}).setdefault("entities", [])
    by_entity: Dict[str, List[Dict[str, Any]]] = {}
    for op in operations:
        if not isinstance(op, dict):
            raise ToolError("each operation must be an object")
        name = str(op.get("entity") or "").strip()
        if not NAME.match(name):
            raise ToolError(f"{name!r} is not an entity name")
        kind = str(op.get("op") or "").lower()
        index = {str(e.get("entity_name") or "").upper(): e for e in entities}
        if kind == "add_entity":
            if name.upper() in index:
                raise ToolError(f"{name} already exists; add columns to it instead")
            entities.append({"entity_name": name.upper(), "kind": str(op.get("kind") or "TABLE").upper(),
                             "purpose": str(op.get("purpose") or op.get("description") or "")[:2000],
                             "grain": str(op.get("grain") or "")[:2000], "business_keys": [], "attributes": []})
            continue
        if name.upper() not in index:
            raise ToolError(f"{name} is not an entity of this model; add it with add_entity first")
        by_entity.setdefault(name.upper(), []).append(op)
    for key, ops in by_entity.items():
        entity = next(e for e in entities if str(e.get("entity_name") or "").upper() == key)
        single = {"design": {"entities": [entity]}}
        changed = _apply_columns(single, [{k: v for k, v in op.items() if k != "entity"} for op in ops])
        entity.clear()
        entity.update(changed["design"]["entities"][0])
    return spec


async def update_domain_model(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Draft a change to a domain's canonical model: validated now; publishing saves it as the open draft (opening one
    from the approved version when there is none) and submits it for review. A modeler approves it; the copilot never does."""
    data = await _domain_model(call, a["model_id"])
    model_id = _id(a["model_id"], "model_id")
    header, opened = data["model"], data.get("open") or {}
    if str(opened.get("status") or "") == "IN_REVIEW":
        raise ToolError(f"version {opened.get('version')} of this model is in review; it must be decided (or withdrawn "
                        "in the Model Workbench) before another change can be drafted")
    approved = int(header.get("approved_version") or 0)
    if not opened and not approved:
        raise ToolError("the model has neither an approved version nor a draft")
    key = f"DOMAIN_MODEL:{a['model_id']}"
    prior = _open_change(ctx, key)
    base_spec = data["current"]["spec"]
    start = next((op.get("body", {}).get("spec") for op in (prior or {}).get("operations") or []
                  if op.get("method") == "PUT"), None) or base_spec
    draft = _apply_entities(start, a["operations"])
    checked = _ok(*await call("POST", f"/api/hub/domain-models/{model_id}/draft/validate", {"spec": draft}),
                  what="Validating the model")
    errors = [i for i in checked.get("issues") or [] if i.get("severity") == "ERROR"]
    if errors:
        raise ToolError("the changed model does not validate: " + "; ".join(f"{i.get('path')}: {i.get('message')}" for i in errors[:8]))
    note = str(a.get("change_note") or "Copilot draft")[:2000]
    save = {"spec": checked["spec"], "change_note": note}
    if opened:   # edit the open draft (its etag guards against someone else's save in between)
        ops = [{"method": "PUT", "path": f"/api/hub/domain-models/{model_id}/draft", "body": {**save, "etag": opened.get("etag")}}]
    else:        # open the next version from the approved one, then save into it
        ops = [{"method": "POST", "path": f"/api/hub/domain-models/{model_id}/draft", "body": {"note": note}},
               {"method": "PUT", "path": f"/api/hub/domain-models/{model_id}/draft", "body": {**save, "etag": "{op0.etag}"}}]
    if a.get("submit", True):
        ops.append({"method": "POST", "path": f"/api/hub/domain-models/{model_id}/submit", "body": {"note": note}})
    # always compared with the approved version, which is what the reviewer sees
    old = base_spec if not opened else None
    if opened and approved:
        got = _ok(*await call("GET", f"/api/hub/domain-models/{model_id}", params={"version": str(approved)}),
                  what="Reading the approved version")
        old = (got.get("current") or {}).get("spec")
    shown = preview.model_changes(old, checked["spec"], f"{header.get('domain_name') or a['model_id']} domain model")
    counts = shown["counts"]
    parts = [f"{n} {k}" for k, n in counts.items() if n]
    change = changeset.make(key, "MODEL", f"Domain model {header.get('domain_name') or a['model_id']}: {', '.join(parts) or note}"[:512],
                            ops, {"model": shown})
    warnings = [f"{i.get('path')}: {i.get('message')}" for i in checked.get("issues") or [] if i.get("severity") != "ERROR"][:8]
    return {"summary": {"drafted": change["title"], "changes_vs_approved": counts, "warnings": warnings,
                        "built_on_earlier_draft": bool(prior),
                        "saves_into": f"open draft v{opened.get('version')}" if opened else f"a new draft from approved v{approved}",
                        "saved_now": False,
                        "on_approve_and_publish": "saved into the Model Workbench draft and submitted for a modeler's review"
                                                  if a.get("submit", True) else "saved into the Model Workbench draft, not submitted"},
            "change": change, "steps": [("model", "DONE", ", ".join(parts) or "drafted"), ("dryrun", "DONE", "model validated")]}


async def search_code(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = {"q": a["q"][:500], "limit": "10", **({"kind": str(a["kind"]).upper()[:40]} if a.get("kind") else {})}
    data = _ok(*await call("GET", "/api/code/search", params=params), what="Searching the code")
    hits = [{**_pick(h, ("repo_name", "path", "start_line", "end_line", "kind", "name")),
             "text": _clip(h.get("text") or "", 600)} for h in data.get("hits") or []][:10]
    return {"summary": {"hits": hits, "note": "" if hits else "no connected repository has matching code"},
            "steps": [("context", "DONE", f"{len(hits)} code matches")]}


async def code_impact(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    name = str(a["name"]).strip()
    if not FQN.match(name) and not NAME.match(name):
        raise ToolError(f"{name!r} is not a model, table or macro name")
    data = _ok(*await call("GET", "/api/code/impact", params={"name": name, "depth": "3"}), what="Reading code impact")
    impact = [_pick(i, ("name", "kind", "depth", "path", "repo_name")) for i in data.get("impact") or []][:40]
    return {"summary": {"name": name, "known": data.get("known"), "it_depends_on": (data.get("uses") or [])[:20],
                        "depends_on_it_directly": data.get("direct"), "depends_on_it_total": data.get("total"),
                        "impact": impact},
            "steps": [("context", "DONE", f"{data.get('total') or 0} code objects depend on {name}")]}


PUBLISH_SUMMARY = "{publish_summary}"


async def jira_projects(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = _ok(*await call("GET", "/api/jira/projects", params={"q": str(a.get("q") or "")[:100]}), what="Listing Jira projects")
    out: Dict[str, Any] = {"projects": [_pick(p, ("key", "name")) for p in data.get("projects") or []][:50],
                           "default_project": data.get("default")}
    if a.get("project"):
        project = str(a["project"]).strip().upper()
        if not JIRA_PROJECT.match(project):
            raise ToolError(f"{project!r} is not a Jira project key")
        types = _ok(*await call("GET", f"/api/jira/projects/{project}/issue-types"), what=f"Reading {project} issue types")
        out["issue_types"] = [t.get("name") for t in types.get("types") or [] if not t.get("subtask")]
    return {"summary": out}


async def _sprint(call: Call, project: str, wanted: Any) -> Dict[str, Any]:
    """An active or future sprint of the project's boards, by id or by name (case-insensitive)."""
    data = _ok(*await call("GET", "/api/jira/sprint-choices", params={"project": project}), what=f"Reading {project} sprints")
    if not data.get("sprint_field"):
        raise ToolError("this Jira site has no Sprint field (Jira Software), so issues cannot be put in a sprint")
    sprints = data.get("sprints") or []
    text = str(wanted).strip().lower()
    pick = next((s for s in sprints if str(s.get("id")) == text or str(s.get("name") or "").lower() == text), None)
    if not pick:
        raise ToolError(f"{project} has no active or future sprint {wanted!r}; it has: "
                        + (", ".join(f"{s.get('name')} ({s.get('state')})" for s in sprints) or "none"))
    return pick


async def jira_sprints(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    project = str(a.get("project") or "").strip().upper()
    if project and not JIRA_PROJECT.match(project):
        raise ToolError(f"{project!r} is not a Jira project key")
    data = _ok(*await call("GET", "/api/jira/sprint-choices", params={"project": project}), what="Reading sprints")
    return {"summary": {"project": data.get("project"), "can_set_sprint": bool(data.get("sprint_field")),
                        "sprints": [_pick(s, ("id", "name", "state", "board")) for s in data.get("sprints") or []][:30]}}


async def move_to_sprint(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Draft moving issues into a sprint (or to the backlog); done as the person when they publish."""
    keys = list(dict.fromkeys(str(k).strip().upper() for k in a["keys"] if str(k).strip()))
    bad = [k for k in keys if not JIRA_KEY.match(k)]
    if not keys or bad:
        raise ToolError(f"keys must be Jira issue keys{': ' + ', '.join(bad[:5]) if bad else ''}")
    if len(keys) > 50:
        raise ToolError("at most 50 issues at a time")
    backlog = str(a.get("sprint") or "").strip().lower() in ("", "backlog", "none")
    project = keys[0].split("-")[0]
    pick = None if backlog else await _sprint(call, project, a["sprint"])
    where = "the backlog" if backlog else f"sprint {pick['name']} ({pick['state']})"
    change = changeset.make(
        f"JIRA_SPRINT:{','.join(sorted(keys))[:400]}", "JIRA", f"Move {len(keys)} Jira issue{'s' if len(keys) != 1 else ''} to {where}"[:512],
        [{"method": "POST", "path": "/api/jira/sprint-moves", "body": {"keys": keys, "sprint_id": None if backlog else int(pick["id"])}}],
        {"files": [preview.file_diff("jira/sprint-move.md", None, f"Move to {where}:\n" + "\n".join(f"- {k}" for k in keys) + "\n")]})
    return {"summary": {"drafted": change["title"], "on_approve_and_publish": "moved as the person, one result per issue"},
            "change": change}


async def create_jira_issue(call: Call, a: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Draft a new Jira issue; it is created in the person's own Jira account when they publish."""
    project = str(a["project"]).strip().upper()
    if not JIRA_PROJECT.match(project):
        raise ToolError(f"{project!r} is not a Jira project key")
    summary_text = " ".join(str(a["summary"]).split())
    if not summary_text:
        raise ToolError("summary is empty")
    types = _ok(*await call("GET", f"/api/jira/projects/{project}/issue-types"), what=f"Reading {project} issue types")
    names = [str(t.get("name")) for t in types.get("types") or [] if not t.get("subtask")]
    wanted = str(a.get("issue_type") or "Task").strip()
    pick = next((n for n in names if n.lower() == wanted.lower()), None)
    if not pick:
        raise ToolError(f"{project} has no issue type {wanted!r}; it has: {', '.join(names) or 'none you can create'}")
    labels = [str(x).strip() for x in a.get("labels") or [] if str(x).strip()][:10]
    description = str(a.get("description_markdown") or "")[:30000]
    sprint = await _sprint(call, project, a["sprint"]) if a.get("sprint") else None
    slug = re.sub(r"[^a-z0-9]+", "-", summary_text.lower()).strip("-")[:60] or "issue"
    change = changeset.make(
        f"JIRA_NEW:{project}:{slug}", "JIRA", f"New Jira {pick} in {project}: {summary_text}"[:512],
        [{"method": "POST", "path": "/api/jira/issues",
          "body": {"project_key": project, "issue_type": pick, "summary": summary_text[:255], "description_markdown": description,
                   "labels": labels, "idempotency_key": str(uuid.uuid4()),
                   **({"sprint_id": int(sprint["id"])} if sprint else {})}}],
        {"files": [preview.file_diff(f"jira/new-{project.lower()}-{slug[:40]}.md", None,
                                     f"# {summary_text}\n\n{pick} in {project}" + (f", labels: {', '.join(labels)}" if labels else "")
                                     + (f", sprint: {sprint['name']}" if sprint else "")
                                     + f"\n\n{description}\n")]})
    return {"summary": {"drafted": change["title"], "on_approve_and_publish": "created in the person's own Jira account"},
            "change": change, "steps": [("intake", "DONE", f"{pick} for {project} drafted")]}


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
    Tool("inspect_source", "List the tables of a Snowflake schema with row counts and profile status. With table, also "
         "that table's column profile (types, nulls, distinct values, ranges, suggested checks): use it before choosing "
         "keys or writing checks.", "context",
         {"database": _s(True, 255), "schema": _s(True, 255), "table": _s(False, 255)}, inspect_source),
    Tool("find_table_models", "Find table models (one modeled table each) by name or domain.", "context",
         {"search": _s(False, 100), "domain_id": _s(False, 36)}, find_table_models),
    Tool("get_table_model", "Read a table model's columns, keys and grain.", "context",
         {"table_model_id": _s(True, 64)}, get_table_model),
    Tool("update_table_model", "Draft column changes to a table model; renders the DDL and dbt as a dry run. "
         "operations: [{op: add|modify|drop, column, datatype?, nullable?, is_pk?, description?, business_definition?}].",
         "model", {"table_model_id": _s(True, 64), "operations": _l("column operations", max=100),
                   "change_note": _s(False, 2000)}, update_table_model, drafts=True),
    Tool("find_domain_models", "List the domains' canonical models (Model Workbench), optionally filtered by domain name.",
         "context", {"q": _s(False, 100)}, find_domain_models),
    Tool("get_domain_model", "Read a domain model: its entities with their columns (pass entity for one entity in full).",
         "context", {"model_id": _s(True, 64), "entity": _s(False, 255)}, get_domain_model),
    Tool("update_domain_model", "Draft a change to a domain's canonical model; validated now. operations: [{op: "
         "add_entity|add|modify|drop, entity, column?, datatype?, nullable?, is_pk?, description?, kind?, grain?, purpose?}] "
         "(add_entity creates an entity; add, modify and drop change one of its columns). Publishing saves the draft and "
         "submits it for a modeler's review (submit false only saves it).", "model",
         {"model_id": _s(True, 64), "operations": _l("entity and column operations", max=100),
          "change_note": _s(False, 2000), "submit": {"type": "bool", "required": False, "desc": "default true"}},
         update_domain_model, drafts=True),
    Tool("create_git_pr", "Draft a GitHub pull request with a table model's DDL and dbt files (for the version the open "
         "model draft saves, else the saved version). Without repo_id it lists the connected repositories to choose from. "
         "Domain models, QA packs, DQ suites and mappings are published to Git from their stores after approval.",
         "publish", {"table_model_id": _s(True, 64), "repo_id": _s(False, 36), "base": _s(False, 200),
                     "title": _s(False, 200)}, create_git_pr, drafts=True),
    Tool("search_code", "Search the client's connected code repositories (dbt models, macros, tests, SQL) by name or meaning.",
         "context", {"q": _s(True, 500), "kind": _s(False, 40, "DBT_MODEL, DBT_MACRO, DBT_TEST, DBT_SCHEMA_YML, SQL ...")},
         search_code),
    Tool("code_impact", "What a dbt model, table or macro depends on and everything that depends on it in the code: what "
         "may break when it changes.", "context", {"name": _s(True, 255)}, code_impact),
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
          "checks": _l("checks", max=50), "note": _s(False, 2000),
          "submit": {"type": "bool", "required": False, "desc": "default true: submit the suite to a DQ approver"}},
         register_soda_checks, drafts=True),
    Tool("onboard_source_config", "Validate and draft a SnapLogic external source onboarding (same fields as the "
         "Onboarding wizard: source, target, load, snaplogic, tables, comments).", "intake",
         {"source": _d("source"), "target": _d("target"), "load": _d("load"), "snaplogic": _d("snaplogic"),
          "tables": _l("tables", False, 200), "comments": _s(False, 4000)}, onboard_source_config, drafts=True),
    Tool("sync_jira_status", "Draft a Jira comment (markdown) and, optionally, a status transition by name. When other "
         "changes are drafted, the comment ends with what publishing actually did (applied, sent for approval with the "
         "request, failed; pull request links), filled in at publish time; report_publish false leaves it out.", "publish",
         {"key": _s(True, 40), "comment_markdown": _s(True, 30000), "transition": _s(False, 128),
          "report_publish": {"type": "bool", "required": False, "desc": "default true"}},
         sync_jira_status, drafts=True),
    Tool("jira_projects", "The Jira projects the person can create issues in, and the default project; with project, "
         "also its issue types. Call it before create_jira_issue.", "intake",
         {"q": _s(False, 100), "project": _s(False, 40)}, jira_projects),
    Tool("create_jira_issue", "Draft a new Jira issue (created in the person's own Jira account when they publish). "
         "issue_type is a name the project has, such as Task, Story or Bug.", "intake",
         {"project": _s(True, 40), "issue_type": _s(False, 64), "summary": _s(True, 255),
          "description_markdown": _s(False, 30000), "labels": _l("labels", False, 10),
          "sprint": _s(False, 120, "an active or future sprint, by name or id (see jira_sprints)")}, create_jira_issue, drafts=True),
    Tool("jira_sprints", "The active and future sprints of a Jira project's boards (the default project when none is "
         "given), and whether issues can be put in a sprint.", "intake", {"project": _s(False, 40)}, jira_sprints),
    Tool("move_to_sprint", "Draft moving Jira issues (one project, at most 50) into an active or future sprint by name or "
         "id, or to the backlog with sprint 'backlog'. Done as the person when they publish.", "publish",
         {"keys": _l("issue keys", True, 50), "sprint": _s(True, 120)}, move_to_sprint, drafts=True),
]}


def describe() -> List[Dict[str, Any]]:
    """The catalog as the model sees it."""
    return [{"name": t.name, "description": t.description, "drafts_a_change": t.drafts,
             "args": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "required", "desc") and vv not in ("", False)}
                      for k, v in t.args.items()}} for t in TOOLS.values()]
