"""The copilot's tools: thin adapters over the platform's existing API routes.

Each tool gets `call(method, path, body=None, params=None) -> (status, data)`, an in-process request to the API made
with the signed-in person's own session, so the API's privilege checks apply to everything a tool reads or tries.
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

ID = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
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
    run: Callable[[Call, Dict[str, Any]], Awaitable[Dict[str, Any]]] = field(repr=False)
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

async def fetch_jira_issue(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    key = str(a["key"]).strip().upper()
    if not JIRA_KEY.match(key):
        raise ToolError(f"{key} is not a Jira issue key")
    data = _ok(*await call("GET", f"/api/jira/issues/{key}"), what=f"Reading {key}")
    issue = _pick(data, ("key", "summary", "status", "issue_type", "type", "priority", "assignee", "labels", "url"))
    issue["description"] = _clip(data.get("description") or "", 6000)
    issue["recent_comments"] = [_clip(c.get("text") or "", 600) for c in (data.get("comments") or [])[-5:]]
    return {"summary": {"issue": issue}, "steps": [("intake", "DONE", f"Read {key}")], "jira_key": key}


async def list_domains(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    data = _ok(*await call("GET", "/api/domains"), what="Listing domains")
    rows = data.get("domains") if isinstance(data, dict) else data
    return {"summary": {"domains": [_pick(r, ("domain_id", "domain_name", "description", "active_flag")) for r in (rows or [])][:50]}}


async def fetch_domain_context(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    body = {"query": a["query"][:2000], "limit": 6, **({"domain": a["domain"]} if a.get("domain") else {})}
    data = _ok(*await call("POST", "/api/knowledge/search", body), what="Knowledge search")
    rows = (data or {}).get("results") if isinstance(data, dict) else data
    hits = [{**_pick(r, ("title", "knowledge_type", "domain_name", "source_reference")),
             "content": _clip({str(k).lower(): v for k, v in r.items()}.get("content") or "", 900)} for r in (rows or [])]
    return {"summary": {"knowledge": hits}, "steps": [("context", "DONE", f"{len(hits)} knowledge items")]}


async def inspect_source(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
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


async def find_table_models(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
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


async def get_table_model(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    entity = _entity(data["current"]["spec"])
    return {"summary": {"model": _pick(data["model"], ("table_model_id", "table_name", "domain_name", "model_layer",
                                                       "table_type", "current_version")),
                        "entity": entity.get("entity_name"), "grain": entity.get("grain"),
                        "columns": [_pick(c, ("name", "datatype", "nullable", "is_pk", "description"))
                                    for c in entity.get("attributes") or []][:200]},
            "steps": [("context", "DONE", "")]}


def _apply_columns(spec: Dict[str, Any], operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    spec = copy.deepcopy(spec)
    entity = _entity(spec)
    attrs = entity.setdefault("attributes", [])
    index = {str(c.get("name") or "").upper(): c for c in attrs}
    for op in operations:
        if not isinstance(op, dict):
            raise ToolError("each operation must be an object")
        kind, name = str(op.get("op") or "").lower(), str(op.get("column") or "").strip()
        if not NAME.match(name):
            raise ToolError(f"{name!r} is not a column name")
        fields = {k: op[k] for k in ("datatype", "nullable", "is_pk", "description", "business_definition")
                  if op.get(k) is not None}
        existing = index.get(name.upper())
        if kind == "add":
            if existing:
                raise ToolError(f"{name} already exists; use modify")
            if not fields.get("datatype"):
                raise ToolError(f"adding {name} needs a datatype")
            attrs.append({"name": name.upper(), "nullable": True, **fields})
        elif kind == "modify":
            if not existing:
                raise ToolError(f"{name} is not a column of {entity.get('entity_name')}")
            existing.update(fields)
        elif kind == "drop":
            if not existing:
                raise ToolError(f"{name} is not a column of {entity.get('entity_name')}")
            attrs.remove(existing)
        else:
            raise ToolError(f"unknown operation {kind!r}; use add, modify or drop")
    return spec


async def update_table_model(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    header, current = data["model"], data["current"]
    model_id = _id(a["table_model_id"], "table_model_id")
    draft = _apply_columns(current["spec"], a["operations"])
    rendered = _ok(*await call("POST", f"/api/hub/table-models/{model_id}/render",
                               {"spec": draft, "table_type": header.get("table_type") or "STANDARD",
                                "domain": header.get("domain_name") or None}), what="Rendering the model")
    errors = [i for i in rendered.get("issues") or [] if i.get("severity") == "ERROR"]
    if errors:
        raise ToolError("the changed model does not validate: " + "; ".join(f"{i.get('path')}: {i.get('message')}" for i in errors[:8]))
    note = a.get("change_note") or "Copilot draft"
    change = changeset.make(
        f"TABLE_MODEL:{a['table_model_id']}", "MODEL", f"Table model {header.get('table_name') or a['table_model_id']}: {note}"[:512],
        [{"method": "PUT", "path": f"/api/hub/table-models/{model_id}",
          "body": {"spec": rendered["spec"], "etag": header.get("etag"), "change_note": note[:2000]}}],
        {"model": preview.columns(current["spec"], rendered["spec"]),
         "files": preview.files_diff(preview.rendered_files(current), preview.rendered_files(rendered))})
    counts = change["preview"]["model"]["counts"]
    warnings = [f"{i.get('path')}: {i.get('message')}" for i in rendered.get("issues") or [] if i.get("severity") != "ERROR"][:8]
    return {"summary": {"drafted": change["title"], "columns": counts, "warnings": warnings,
                        "ddl_and_dbt_rendered": True},
            "change": change, "steps": [("model", "DONE", f"{counts}"), ("dryrun", "DONE", "DDL and dbt rendered")]}


async def generate_dbt_models(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    data = await _table_model(call, a["table_model_id"])
    files = preview.rendered_files(data["current"])
    return {"summary": {"files": {p: _clip(t, 1500) for p, t in list(files.items())[:8]},
                        "note": "dbt and DDL are rendered from the table model; change the model to change them"},
            "steps": [("model", "DONE", "")]}


async def find_sttm_mappings(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    params = {k: str(a[k])[:200] for k in ("q", "target", "domain") if a.get(k)}
    data = _ok(*await call("GET", "/api/hub/sttm", params=params), what="Listing mappings")
    rows = [_pick(m, ("catalog_id", "version", "target_entity", "source_system", "domain_name", "status", "line_count"))
            for m in (data.get("mappings") or [])][:20]
    return {"summary": {"mappings": rows}}


async def _mapping(call: Call, catalog_id: str) -> Dict[str, Any]:
    data = _ok(*await call("GET", f"/api/hub/sttm/{_id(catalog_id, 'catalog_id')}"), what="Reading the mapping")
    return data.get("mapping") or {}


LINE_KEYS = ("target_column", "target_datatype", "mapping_type", "source_table", "source_column", "transformation")


async def get_sttm_mapping(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    entry = await _mapping(call, a["catalog_id"])
    return {"summary": {"catalog_id": entry.get("catalog_id"), "version": entry.get("version"),
                        "target_entity": entry.get("target_entity"), "status": entry.get("status"),
                        "lines": [_pick(l, LINE_KEYS) for l in entry.get("lines") or []][:150]},
            "steps": [("context", "DONE", "")]}


async def generate_sttm_mapping(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    from services.catalog import sttm as cat_sttm

    entry = await _mapping(call, a["catalog_id"])
    cid = _id(a["catalog_id"], "catalog_id")
    current = {str(l.get("target_column") or "").upper(): l for l in entry.get("lines") or []}
    edits, notes = [], []
    for e in a.get("edits") or []:
        if not isinstance(e, dict) or not e.get("target_column"):
            raise ToolError("each edit needs a target_column")
        col = str(e["target_column"])
        edit = {"target_column": col, **{k: v for k, v in e.items() if k in cat_sttm.EDITABLE and v is not None}}
        if e.get("instruction") and not e.get("transformation"):
            line = current.get(col.upper()) or {}
            status, proposal = await call("POST", f"/api/hub/sttm/{cid}/refine", {
                "target_column": col, "prompt": str(e["instruction"])[:2000],
                "current_transformation": line.get("transformation"), "source_table": line.get("source_table"),
                "source_column": line.get("source_column")})
            if status >= 400 or not isinstance(proposal, dict) or not proposal.get("transformation"):
                raise ToolError(f"No AI proposal for {col}: {(proposal or {}).get('detail') if isinstance(proposal, dict) else proposal}")
            edit["transformation"] = proposal["transformation"]
            notes.append({"target_column": col, "rationale": _clip(proposal.get("rationale") or "", 400)})
        if len(edit) > 1:
            edits.append(edit)
    add = [l for l in a.get("add_lines") or [] if isinstance(l, dict)]
    if not edits and not add:
        raise ToolError("nothing to change in the mapping")
    try:
        lines, _ = cat_sttm.apply_edits(entry.get("lines") or [], edits)
        lines, _ = cat_sttm.add_lines(lines, add) if add else (lines, [])
    except AssertionError as exc:
        raise ToolError(str(exc)) from None
    note = a.get("note") or "Copilot draft"
    change = changeset.make(
        f"STTM:{a['catalog_id']}", "STTM", f"Mapping {entry.get('target_entity') or a['catalog_id']}: {note}"[:512],
        [{"method": "POST", "path": f"/api/hub/sttm/{cid}/edit",
          "body": {"base_version": int(entry.get("version") or 1), "edits": edits, "add_lines": add, "note": note[:2000]}}],
        {"sttm": preview.sttm_lines(entry.get("lines") or [], lines)})
    return {"summary": {"drafted": change["title"], "edited": [e["target_column"] for e in edits],
                        "added": [l.get("target_column") for l in add], "ai_rationale": notes,
                        "publishing": "a mapping edit always goes to an STTM approver"},
            "change": change, "steps": [("sttm", "DONE", f"{len(edits)} edited, {len(add)} added")]}


async def register_qa_sql_pack(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    domain = _id(a["domain_id"], "domain_id")
    queries = []
    for q in a["queries"]:
        if not isinstance(q, dict) or not q.get("query_name") or not q.get("sql"):
            raise ToolError("each query needs query_name and sql")
        queries.append({"query_name": str(q["query_name"])[:128], "sql_expression": str(q["sql"])[:16000],
                        "description": q.get("description"), "severity": str(q.get("severity") or "ERROR")[:16],
                        "expectation": q.get("expectation") or "0 rows"})
    dry = []
    for q in queries:  # each query once, read-only, with the caller's role; nothing is stored
        status, data = await call("POST", "/api/qa/execute", {"domain_id": a["domain_id"], "sql": q["sql_expression"]})
        result = ((data or {}).get("results") or [{}])[0] if isinstance(data, dict) and status < 400 else {}
        dry.append({"query_name": q["query_name"], "ok": status < 400,
                    "outcome": _pick(result, ("status", "row_count", "error", "message")) if status < 400
                    else _clip((data or {}).get("detail") if isinstance(data, dict) else data, 400)})
    old: Dict[str, str] = {}
    body: Dict[str, Any] = {"name": str(a.get("name") or "Copilot QA pack")[:128], "queries": queries,
                            "change_note": (a.get("change_note") or "Copilot draft")[:2000]}
    if a.get("pack_id"):
        pack_id = _id(a["pack_id"], "pack_id")
        found = _ok(*await call("GET", f"/api/qa-packs/{pack_id}"), what="Reading the QA pack")
        pack = found.get("pack") or {}
        existing = {str(q.get("query_name")): q for q in found.get("queries") or []}
        old = {f"{n}.sql": str(q.get("sql_expression") or "") for n, q in existing.items()}
        merged = {**{n: {k: q.get(k) for k in ("query_name", "sql_expression", "description", "severity", "expectation")}
                     for n, q in existing.items()}, **{q["query_name"]: q for q in queries}}
        body.update({"name": pack.get("name") or body["name"], "queries": list(merged.values()),
                     "base_version": pack.get("version")})
        ops = [{"method": "PUT", "path": f"/api/qa-packs/{pack_id}", "body": body},
               {"method": "POST", "path": f"/api/qa-packs/{pack_id}/submit", "body": {"note": body["change_note"]}}]
        key = f"QA_PACK:{a['pack_id']}"
    else:
        ops = [{"method": "POST", "path": f"/api/domains/{domain}/qa-packs", "body": body},
               {"method": "POST", "path": "/api/qa-packs/{op0.pack_id}/submit", "body": {"note": body["change_note"]}}]
        key = f"QA_PACK:new:{domain}:{body['name']}"
    new = {**old, **{f"{q['query_name']}.sql": q["sql_expression"] for q in queries}}
    change = changeset.make(key, "QA", f"QA pack {body['name']}: {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                            ops, {"files": preview.files_diff(old, new)})
    failed = [d for d in dry if not d["ok"]]
    return {"summary": {"drafted": change["title"], "dry_run": dry,
                        "publishing": "submitted for approval by a QA lead (four eyes)"},
            "change": change, "steps": [("quality", "DONE", f"{len(queries)} QA queries"),
                                        ("dryrun", "BLOCKED" if failed else "DONE", f"{len(failed)} QA queries failed to run" if failed else "QA queries ran")]}


async def register_soda_checks(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    table = str(a["table"])
    if not FQN.match(table) or table.count(".") != 2:
        raise ToolError("table must be DATABASE.SCHEMA.TABLE")
    ok, problems, lines = [], [], []
    for c in a["checks"]:
        if not isinstance(c, dict) or not c.get("dsl"):
            raise ToolError("each check needs a one-line SodaCL check in dsl")
        body = {"table": table, "column": c.get("column"), "severity": c.get("severity") if c.get("severity") in ("WARN", "FAIL") else "WARN",
                "dsl": str(c["dsl"])[:2000], "requirement": c.get("requirement"),
                **({"domain_id": a["domain_id"]} if a.get("domain_id") else {})}
        data = _ok(*await call("POST", "/api/dq/parse", body), what="Checking a SodaCL check")
        if not data.get("ok"):
            problems.append({"dsl": body["dsl"], "problems": data.get("problems")})
            continue
        ok.append(body)
        lines.append(f"- {body['dsl']}    # {body['column'] or 'table'}, {body['severity']}: {data.get('headline') or ''}")
    if not ok:
        raise ToolError(f"no check parsed: {_clip(problems, 800)}")
    name = table.split(".")[-1].lower()
    change = changeset.make(
        f"DQ:{table.upper()}", "DQ", f"Data quality checks on {table}: {len(ok)} added",
        [{"method": "POST", "path": "/api/dq/catalog", "body": b} for b in ok],
        {"files": [preview.file_diff(f"checks/{name}.yml", None, f"checks for {table}:\n" + "\n".join(lines) + "\n")]})
    return {"summary": {"drafted": change["title"], "rejected": problems,
                        "publishing": "added to the table's suite draft; a DQ approver approves the suite"},
            "change": change, "steps": [("quality", "DONE", f"{len(ok)} checks"), ("dryrun", "DONE", "SodaCL parsed")]}


async def onboard_source_config(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    config = {k: a[k] for k in ("source", "target", "load", "snaplogic", "tables", "comments") if a.get(k) is not None}
    data = _ok(*await call("POST", "/api/hub/ingestion-control/validate", config), what="Validating the source")
    if not data.get("valid"):
        return {"summary": {"valid": False, "errors": data.get("errors"),
                            "next": "ask the user for the missing or wrong fields, then call this tool again"},
                "steps": [("intake", "RUNNING", "source configuration incomplete")]}
    source = data.get("source") or {}
    name = str(source.get("source_name") or (a.get("source") or {}).get("source_name") or "source")
    change = changeset.make(f"INGESTION:{name.upper()}", "INGESTION",
                            f"SnapLogic ingestion for {name}: {len(data.get('rows') or [])} table(s)",
                            [{"method": "POST", "path": "/api/hub/ingestion-control", "body": config}],
                            {"files": [preview.file_diff(f"ingestion/{name.lower()}.json", None,
                                                         json.dumps({"source": source, "rows": data.get("rows")}, indent=2, default=str))]})
    return {"summary": {"valid": True, "drafted": change["title"]}, "change": change,
            "steps": [("intake", "DONE", f"{name} validated"), ("dryrun", "DONE", "ingestion config validated")]}


async def sync_jira_status(call: Call, a: Dict[str, Any]) -> Dict[str, Any]:
    key = str(a["key"]).strip().upper()
    if not JIRA_KEY.match(key):
        raise ToolError(f"{key} is not a Jira issue key")
    ops = [{"method": "POST", "path": f"/api/jira/issues/{key}/comment", "body": {"markdown": str(a["comment_markdown"])[:30000]}}]
    moved = None
    if a.get("transition"):
        data = _ok(*await call("GET", f"/api/jira/issues/{key}/transitions"), what=f"Reading {key} transitions")
        want = str(a["transition"]).strip().lower()
        options = data.get("transitions") or []
        pick = next((t for t in options if want in (str(t.get("name") or "").lower(), str(t.get("to") or "").lower())), None)
        if not pick:
            raise ToolError(f"{key} cannot move to {a['transition']!r}; it can move to: "
                            + ", ".join(str(t.get("to") or t.get("name")) for t in options))
        ops.append({"method": "POST", "path": f"/api/jira/issues/{key}/transition", "body": {"transition_id": str(pick["id"])}})
        moved = pick.get("to") or pick.get("name")
    change = changeset.make(f"JIRA:{key}", "JIRA", f"Jira {key}: comment" + (f" and move to {moved}" if moved else ""), ops,
                            {"files": [preview.file_diff(f"jira/{key}-comment.md", None, str(a["comment_markdown"]))]})
    return {"summary": {"drafted": change["title"]}, "change": change}


# ---------------------------------------------------------------- catalog

def _s(required: bool = False, max: int = 0, desc: str = "") -> Dict[str, Any]:
    return {"type": "str", "required": required, "max": max or 2000, "desc": desc}


def _l(desc: str, required: bool = True, max: int = 200) -> Dict[str, Any]:
    return {"type": "list", "required": required, "max": max, "desc": desc}


def _d(desc: str) -> Dict[str, Any]:
    return {"type": "dict", "required": False, "desc": desc}


TOOLS: Dict[str, Tool] = {t.name: t for t in [
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
         "checks: [{column?, dsl, severity: WARN|FAIL, requirement?}].", "quality",
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
