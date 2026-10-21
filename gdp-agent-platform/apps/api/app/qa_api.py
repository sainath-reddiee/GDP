"""QA workspace: tests that target a domain's target table, with or without a run.

A QA engineer starts from a table (or a Jira ticket that resolves to one), not from a run. Tests are grouped in suites
per target table, run in Snowflake with the signed-in role (PII masked in samples) and keep their results with
RUN_ID NULL, so a run's QA lane and sign-off gate are untouched. The logic lives in services/qa (scope, procedures,
run); this router only validates input, calls it on the caller's session and maps errors.
"""

from __future__ import annotations

import importlib
import json
import re
from functools import partial
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import jira_api as jira
from app.db import Db
from app.main import _json, _snowflake_error, current_db
from app.perf import cached
from app.source_invoke import USE_CALLER, invoke_source

router = APIRouter()
# context keys worth showing in the workspace; the rest (STTM lines, prompt text, code context) can be large
SUMMARY_KEYS = ("target_table_id", "domain_id", "sttm_id", "target", "sources", "business_keys", "allowed", "spec",
                "columns", "pii_columns", "pii_basis", "run_id", "scope", "retired")


def _svc(name: str) -> Any:
    """services.qa.<name>, looked up at call time so the services can evolve (and be faked in tests)."""
    return importlib.import_module(f"services.qa.{name}")


def _call(db: Db, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a QA service on the caller's session: AssertionError is the caller's problem (404 when something is not
    found or does not exist, otherwise 409); Snowflake errors keep their usual mapping."""
    try:
        return invoke_source(db, partial(fn, **kwargs) if kwargs else fn, *args)
    except HTTPException:
        raise
    except AssertionError as exc:
        message = str(exc) or "QA request rejected"
        lower = message.lower()
        raise HTTPException(404 if "not found" in lower or "does not exist" in lower else 409, message) from exc
    except Exception as exc:
        raise _snowflake_error(exc) from exc


# ---------------------------------------------------------------- models

def _text(value: Optional[str]) -> Optional[str]:
    """None keeps a field (not sent); an empty string clears it."""
    return None if value is None else value.strip()


class QaTableTest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    sql: str = Field(min_length=6, max_length=16000)
    objective: Optional[str] = Field(default=None, max_length=4000)
    expected: Optional[str] = Field(default=None, max_length=1000)
    category: Optional[str] = Field(default="CUSTOM", max_length=32)
    severity: Optional[str] = Field(default="MEDIUM", max_length=16)
    target_column: Optional[str] = Field(default=None, max_length=256)
    prompt: Optional[str] = Field(default=None, max_length=4000)
    suite_id: Optional[str] = Field(default=None, max_length=36)


class SuiteIn(BaseModel):
    target_table_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class SuiteEdit(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class TableRunIn(BaseModel):
    test_ids: Optional[list[str]] = Field(default=None, max_length=500)
    suite_id: Optional[str] = Field(default=None, max_length=36)


class SuiteRunIn(BaseModel):
    test_ids: Optional[list[str]] = Field(default=None, max_length=500)


class TableAsk(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


# ---------------------------------------------------------------- tables

@router.get("/api/qa/tables")
def list_tables(domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    """Target tables a QA engineer can test, optionally for one domain, with their suites and last outcome."""
    return {"tables": _call(db, _svc("scope").list_tables, domain_id or None)}


@router.get("/api/qa/tables/{target_table_id}")
def table(target_table_id: str, db: Db = Depends(current_db)):
    """What a test on this table may read: target, sources, keys, allowed tables and masked columns."""
    ctx = _call(db, _svc("scope").table_context, target_table_id) or {}
    out = {k: ctx[k] for k in SUMMARY_KEYS if k in ctx}
    out["target_table_id"] = out.get("target_table_id") or target_table_id
    out["lines"] = len(ctx.get("lines") or [])
    return out


@router.get("/api/qa/tables/{target_table_id}/suite")
def table_suite(target_table_id: str, db: Db = Depends(current_db)):
    """Generated tests for the table plus every saved table test, grouped by suite."""
    return _call(db, _svc("procedures").table_suite, target_table_id)


@router.post("/api/qa/tables/{target_table_id}/ask")
def table_ask(target_table_id: str, body: TableAsk, db: Db = Depends(current_db)):
    """Natural language to one guarded, read-only test query on this table (compiled, never executed)."""
    return _call(db, _svc("procedures").table_ask, target_table_id, body.question)


class TablePlan(BaseModel):
    focus: str = Field(default="", max_length=2000)


@router.post("/api/qa/tables/{target_table_id}/plan")
def table_plan(target_table_id: str, body: TablePlan, db: Db = Depends(current_db)):
    """An AI test plan for this table: guarded and compiled, never saved."""
    return _call(db, _svc("procedures").table_plan, target_table_id, body.focus)


@router.post("/api/qa/tables/{target_table_id}/tests")
def table_save(target_table_id: str, body: QaTableTest, db: Db = Depends(current_db)):
    return _call(db, _svc("procedures").table_save, target_table_id, body.model_dump_json())


@router.put("/api/qa/tests/{test_id}")
def table_test_update(test_id: str, body: QaTableTest, db: Db = Depends(current_db)):
    """Edit a saved table test; checked by the guard and compiled again."""
    return _call(db, _svc("procedures").update_table_test, test_id, body.model_dump_json())


@router.delete("/api/qa/tests/{test_id}")
def table_test_delete(test_id: str, db: Db = Depends(current_db)):
    return _call(db, _svc("procedures").delete_table_test, test_id)


@router.post("/api/qa/tables/{target_table_id}/run")
def table_run(target_table_id: str, body: TableRunIn, db: Db = Depends(current_db)):
    """Run the table's tests (all, one suite, or the chosen ones) with the signed-in role; RUN_ID stays NULL."""
    result = _call(db, _svc("run").run_scope, target_table_id=target_table_id, suite_id=body.suite_id or None,
                   test_ids=body.test_ids, triggered_by="UI")
    return {k: v for k, v in (result or {}).items() if k != "results"}


@router.get("/api/qa/tables/{target_table_id}/history")
def table_history(target_table_id: str, limit: int = 20, db: Db = Depends(current_db)):
    """Recent QA runs on the table."""
    try:
        runs = _svc("run").history(db.query, target_table_id, max(1, min(int(limit), 100)))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return runs if isinstance(runs, dict) else {"runs": runs or []}


@router.get("/api/qa/results")
def results(target_table_id: str, suite_id: Optional[str] = None, db: Db = Depends(current_db)):
    """The latest QA run on the table (or one suite) with the latest result of every test."""
    try:
        last, found = _svc("run").latest_table(db.query, target_table_id, suite_id or None)
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    for r in found:
        r["sample"] = _json(r.pop("sample_rows", None))
        r["columns"] = _json(r.get("columns"))
    return {"run": last, "results": found}


# ---------------------------------------------------------------- suites

@router.get("/api/qa/suites")
def suites(target_table_id: Optional[str] = None, domain_id: Optional[str] = None, db: Db = Depends(current_db)):
    return {"suites": _call(db, _svc("procedures").list_suites, target_table_id=target_table_id or None,
                            domain_id=domain_id or None)}


@router.post("/api/qa/suites")
def suite_create(body: SuiteIn, db: Db = Depends(current_db)):
    return _call(db, _svc("procedures").create_suite, body.target_table_id, body.name.strip(), _text(body.description))


@router.put("/api/qa/suites/{suite_id}")
def suite_update(suite_id: str, body: SuiteEdit, db: Db = Depends(current_db)):
    return _call(db, _svc("procedures").update_suite, suite_id, body.name.strip(), _text(body.description))


@router.delete("/api/qa/suites/{suite_id}")
def suite_delete(suite_id: str, db: Db = Depends(current_db)):
    return _call(db, _svc("procedures").delete_suite, suite_id)


@router.post("/api/qa/suites/{suite_id}/run")
def suite_run(suite_id: str, body: SuiteRunIn, db: Db = Depends(current_db)):
    result = _call(db, _svc("run").run_scope, suite_id=suite_id, test_ids=body.test_ids, triggered_by="UI")
    return {k: v for k, v in (result or {}).items() if k != "results"}


# ---------------------------------------------------------------- Jira tickets: which table, triage, links

def _proc(db: Db, proc: str, handler: Callable[..., Any], *args: Any) -> Any:
    """A JIRA procedure: its Python handler on the caller's session in caller mode, otherwise the procedure."""
    if USE_CALLER:
        result = _call(db, handler, *args)
    else:
        try:
            result = db.call(proc, args)
        except Exception as exc:
            raise _snowflake_error(exc) from exc
    return _json(result) if isinstance(result, str) else result


class ResolveIn(BaseModel):
    key: str = Field(min_length=3, max_length=64)


def _ticket_links(db: Db, key: str) -> list[dict]:
    try:
        return db.query("""SELECT RUN_ID, TARGET_TABLE, TARGET_TABLE_ID, SUITE_ID, QA_TEST_ID FROM CORE.ISSUE_LINK
                            WHERE ISSUE_KEY = %s AND NOT IS_DELETED""", (key,))
    except Exception as exc:
        if "invalid identifier" not in str(exc).lower():
            raise _snowflake_error(exc) from exc
        return db.query("SELECT RUN_ID, TARGET_TABLE, QA_TEST_ID FROM CORE.ISSUE_LINK WHERE ISSUE_KEY = %s AND NOT IS_DELETED", (key,))


@router.post("/api/qa/triage/resolve")
def triage_resolve(body: ResolveIn, db: Db = Depends(current_db)):
    """The target tables a ticket is likely about (linked, named in it, then ranked by AI) and every run linked to it.
    The engineer picks the table; nothing is saved."""
    from services.jira.triage import resolve_entry

    client, conn, _ = jira._client(db)
    key = jira._key(body.key)
    info, payload = jira._issue_payload(client, conn, key)
    linked = [{k: v for k, v in r.items() if v} for r in _ticket_links(db, key)]
    result = _proc(db, "CALL CORE.RESOLVE_TARGETS(%s)", resolve_entry, json.dumps({"issue": payload, "linked": linked}, default=str)) or {}
    return {"key": key, "summary": info.get("summary") or "", "candidates": result.get("candidates") or [],
            "runs": result.get("runs") or [], "model": result.get("model")}


@router.post("/api/qa/tables/{target_table_id}/triage/{key}")
def table_triage(target_table_id: str, key: str, db: Db = Depends(current_db)):
    """Diagnosis of a ticket against a target table, with guarded, compiled tests to reproduce it (not saved)."""
    from services.jira.triage import triage_table_entry

    client, conn, _ = jira._client(db)
    info, payload = jira._issue_payload(client, conn, key)
    try:
        result = _proc(db, "CALL CORE.TRIAGE_TABLE(%s, %s)", triage_table_entry, target_table_id, json.dumps(payload, default=str))
    except HTTPException as exc:
        jira._log(db, info["key"], "TRIAGE", "FAILED", conn["cloud_id"], None, {"target_table_id": target_table_id}, str(exc.detail)[:500])
        raise
    result = result or {}
    jira._log(db, info["key"], "TRIAGE", "DONE", conn["cloud_id"], None,
              {"target_table_id": target_table_id, "tests": len(result.get("tests") or []), "reproducible": result.get("reproducible")})
    return result


class QaLinkIn(BaseModel):
    key: str = Field(min_length=3, max_length=64)
    target_table_id: Optional[str] = Field(default=None, max_length=36)
    suite_id: Optional[str] = Field(default=None, max_length=36)
    test_id: Optional[str] = Field(default=None, max_length=64)
    run_id: Optional[str] = Field(default=None, max_length=36)
    remote_link: bool = False


LINK_SQL = """SELECT L.LINK_ID, L.ISSUE_KEY, L.RUN_ID, L.TARGET_TABLE_ID, L.SUITE_ID, L.QA_TEST_ID, T.TITLE AS TEST_TITLE,
                     L.DOMAIN_ID, L.TARGET_TABLE, L.SUMMARY, L.STATUS, L.STATUS_CATEGORY, COALESCE(L.ORIGIN, 'LINK') AS ORIGIN,
                     COALESCE(L.ISSUE_STATE, 'OK') AS ISSUE_STATE, L.LINKED_BY, L.LINKED_AT::VARCHAR AS LINKED_AT
                FROM CORE.ISSUE_LINK L LEFT JOIN CORE.QA_TEST_CASE T ON T.TEST_ID = L.QA_TEST_ID
               WHERE NOT L.IS_DELETED AND {where}
               ORDER BY L.LINKED_AT DESC LIMIT 500"""


def _with_urls(db: Db, rows: list[dict]) -> list[dict]:
    site = (jira._config(db).get("site_url") or "").rstrip("/")
    for r in rows:
        r["url"] = f"{site}/browse/{r['issue_key']}" if site else None
    return rows


@router.post("/api/qa/links")
def link_create(body: QaLinkIn, db: Db = Depends(current_db)):
    """Link a ticket to a table, suite, test or run (once). With remote_link, Jira also gets a link back here."""
    client, conn, cfg = jira._client(db)
    key = jira._key(body.key)
    target = jira._link_target(db, body.target_table_id, body.suite_id, body.test_id, body.run_id)
    try:
        issue = jira.summary(client.issue(key), conn.get("site_url") or "")
    except jira.JiraError as exc:
        raise jira._jira_error(exc) from None
    link_id, already = jira._save_link(
        db, key, conn["cloud_id"], issue, run_id=target["run_id"], qa_test_id=target["qa_test_id"], target_table=target["target_table"],
        target_table_id=target["target_table_id"], suite_id=target["suite_id"], domain_id=target["domain_id"],
        issue_id=issue.get("id"), status_category=issue.get("status_category"))
    remote = None
    if body.remote_link:
        if target["target_table_id"]:
            url = jira._qa_url(cfg, target["target_table_id"], target["suite_id"])
            kind, ident = next((k, target[f]) for k, f in (("test", "qa_test_id"), ("suite", "suite_id"), ("table", "target_table_id"))
                               if target[f])
            title = f"QA {kind}: {target['target_table'] or ident}"
            remote = jira._remote_link(db, client, conn["cloud_id"], key, url, title, f"gdp:qa:{kind}:{ident}", target["run_id"])
        elif not target["run_id"]:
            remote = "not added: nothing in the platform to link back to"
        else:
            remote = jira._remote_link(db, client, conn["cloud_id"], key, f"{jira._web_base(cfg)}/runs/{target['run_id']}/qa",
                                       f"QA run: {target['run_name']}", f"agentic-pipeline:run:{target['run_id']}", target["run_id"])
    found = db.query(LINK_SQL.format(where="L.LINK_ID = %s"), (link_id,))
    row = _with_urls(db, found)[0] if found else {"link_id": link_id, "issue_key": key}
    return {**row, "already": already, "remote_link": remote}


@router.get("/api/qa/links")
def links(target_table_id: Optional[str] = None, key: Optional[str] = None, db: Db = Depends(current_db)):
    """Jira links of a table and/or a ticket, within the caller's Jira permissions: only issues they can see (with the
    live summary and status) when connected, keys and stored status only when not (jira.visible_links)."""
    if not target_table_id and not key:
        raise HTTPException(400, "target_table_id or key is required")
    issue_key = jira._key(key) if key else ""
    try:
        found = db.query(LINK_SQL.format(where="(%s = '' OR L.TARGET_TABLE_ID = %s) AND (%s = '' OR L.ISSUE_KEY = %s)"),
                         (target_table_id or "", target_table_id or "", issue_key, issue_key))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"links": _with_urls(db, jira.visible_links(db, found))}


@router.delete("/api/qa/links/{link_id}")
def link_delete(link_id: str, db: Db = Depends(current_db)):
    found = db.query("SELECT ISSUE_KEY, CLOUD_ID, RUN_ID FROM CORE.ISSUE_LINK WHERE LINK_ID = %s AND NOT IS_DELETED", (link_id,))
    if not found:
        raise HTTPException(404, "link not found")
    db.execute("UPDATE CORE.ISSUE_LINK SET IS_DELETED = TRUE WHERE LINK_ID = %s", (link_id,))
    jira._log(db, found[0]["issue_key"], "UNLINK", "DONE", found[0].get("cloud_id"), found[0].get("run_id"), {"link_id": link_id})
    return {"unlinked": link_id}


# ---------------------------------------------------------------- QA Store: domain test packages (services/qa/packs.py)

UUID_LIKE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _domain_id(db: Db, value: Optional[str]) -> Optional[str]:
    """A domain id, or a domain name resolved to its id (the dashboard's domain switcher sends either)."""
    if not value:
        return None
    if UUID_LIKE.match(value):
        return value
    found = db.query("SELECT DOMAIN_ID FROM CORE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = UPPER(%s) LIMIT 1", (value,))
    if not found:
        raise HTTPException(404, f"domain {value} not found")
    return found[0]["domain_id"]


class PackQuery(BaseModel):
    query_name: str = Field(min_length=1, max_length=128)
    sql_expression: str = Field(min_length=6, max_length=16000)
    description: Optional[str] = Field(default=None, max_length=4000)
    severity: str = Field(default="ERROR", max_length=16)
    expectation: Optional[str] = Field(default="0 rows", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class PackIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    queries: list[PackQuery] = Field(min_length=1, max_length=200)
    change_note: Optional[str] = Field(default=None, max_length=2000)
    base_version: Optional[int] = Field(default=None, ge=1)


class PackNote(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


class ExecuteIn(BaseModel):
    domain_id: str = Field(min_length=1, max_length=128)
    pack_id: Optional[str] = Field(default=None, max_length=36)
    version: Optional[int] = Field(default=None, ge=1)
    query_ids: Optional[list[str]] = Field(default=None, max_length=200)
    sql: Optional[str] = Field(default=None, min_length=6, max_length=16000)
    # another configured environment (Admin, Environments); none is this deployment's own
    environment: Optional[str] = Field(default=None, max_length=32)
    # resolve, guard and EXPLAIN each query without running it (pack runs only; nothing is stored)
    dry_run: bool = False


@router.get("/api/qa-packs")
def qa_packs(domain: Optional[str] = None, db: Db = Depends(current_db)):
    try:
        found = _call(db, _svc("packs").list_packs, _domain_id(db, domain))
        try:  # repository conflicts waiting to be resolved (V042)
            open_ids = {c["pack_id"] for c in invoke_source(db, _svc("pack_git").open_conflicts)}
        except Exception:
            open_ids = set()
        for p in found:
            p["repo_conflict"] = p.get("pack_id") in open_ids
        return {"packs": found, "deployed": True}
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        return {"packs": [], "deployed": False}


def _run_tests(db: Db, domain_id: Optional[str]) -> int:
    """Tests saved in runs and tables (CORE.QA_TEST_CASE), shown beside the domain packs."""
    clause = "AND (DOMAIN_ID = %s OR RUN_ID IN (SELECT RUN_ID FROM CORE.WORKFLOW_RUN WHERE DOMAIN_ID = %s))" if domain_id else ""
    try:
        return int(db.query(f"SELECT COUNT(*) AS N FROM CORE.QA_TEST_CASE WHERE NOT COALESCE(IS_DELETED, FALSE) {clause}",
                            (domain_id, domain_id) if domain_id else ())[0]["n"])
    except Exception:
        return 0


@router.get("/api/qa-packs/summary")
@cached("qa-packs-summary", 20)
def qa_packs_summary(domain: Optional[str] = None, db: Db = Depends(current_db)):
    did = _domain_id(db, domain)
    try:
        return {**_call(db, _svc("packs").summary, did), "run_tests": _run_tests(db, did)}
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        return {"packs": 0, "approved_packs": 0, "rules": 0, "pending": 0, "domains": 0, "top_tags": [],
                "pass_rate": None, "deployed": False, "run_tests": _run_tests(db, did)}


@router.get("/api/domains/{domain_id}/qa-packs")
def domain_qa_packs(domain_id: str, db: Db = Depends(current_db)):
    return {"packs": _call(db, _svc("packs").list_packs, _domain_id(db, domain_id))}


@router.post("/api/domains/{domain_id}/qa-packs")
def create_qa_pack(domain_id: str, body: PackIn, db: Db = Depends(current_db)):
    return _call(db, _svc("packs").create_pack, _domain_id(db, domain_id), json.dumps(body.model_dump()))


class PackImportIn(BaseModel):
    filename: str = Field(default="", max_length=255)
    text: str = Field(min_length=6, max_length=500_000)
    name: Optional[str] = Field(default=None, max_length=128)


@router.post("/api/domains/{domain_id}/qa-packs/import")
def import_qa_pack(domain_id: str, body: PackImportIn, db: Db = Depends(current_db)):
    """A new draft pack from a .sql file: the platform's annotated pack format, or plain read-only queries separated
    by ';' (a comment above a query names it). Every query passes the same read-only guard as a typed one; the draft
    is then submitted and approved like any pack."""
    from services.qa.pack_files import parse_any

    stem = (body.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0].replace("_", " ").strip()
    try:
        parsed = parse_any(body.text, body.name or stem)
    except ValueError as exc:
        raise HTTPException(400, f"could not read {body.filename or 'the file'}: {exc}") from exc
    if body.name:
        parsed["name"] = body.name
    if len(parsed["queries"]) > 200:
        raise HTTPException(400, f"a pack holds at most 200 queries; the file has {len(parsed['queries'])}")
    payload = {k: parsed[k] for k in ("name", "description", "tags", "queries")}
    payload["change_note"] = f"Imported from {body.filename or 'a .sql file'}"
    created = _call(db, _svc("packs").create_pack, _domain_id(db, domain_id), json.dumps(payload))
    return {**created, "name": parsed["name"], "skipped": parsed["skipped"]}


@router.get("/api/qa-packs/{pack_id}")
def get_qa_pack(pack_id: str, version: Optional[int] = None, db: Db = Depends(current_db)):
    return _call(db, _svc("packs").get_pack, pack_id, version)


@router.put("/api/qa-packs/{pack_id}")
def save_qa_pack(pack_id: str, body: PackIn, db: Db = Depends(current_db)):
    return _call(db, _svc("packs").save_draft, pack_id, json.dumps(body.model_dump()))


@router.post("/api/qa-packs/{pack_id}/submit")
def submit_qa_pack(pack_id: str, body: PackNote = PackNote(), db: Db = Depends(current_db)):
    return _call(db, _svc("packs").submit, pack_id, body.note or "")


@router.post("/api/qa-packs/{pack_id}/reject")
def reject_qa_pack(pack_id: str, body: PackNote = PackNote(), db: Db = Depends(current_db)):
    return _call(db, _svc("packs").reject, pack_id, body.note or "")


@router.post("/api/qa-packs/{pack_id}/approve")
def approve_qa_pack(pack_id: str, db: Db = Depends(current_db)):
    """PACKAGE.APPROVE; by default policy four eyes, so the governance middleware turns the call into a change request
    that another QA lead approves (and replays)."""
    return _call(db, _svc("packs").approve, pack_id)


@router.post("/api/qa-packs/{pack_id}/retire")
def retire_qa_pack(pack_id: str, db: Db = Depends(current_db)):
    return _call(db, _svc("packs").retire, pack_id)


@router.get("/api/qa-packs/{pack_id}/runs")
def qa_pack_runs(pack_id: str, limit: int = 20, db: Db = Depends(current_db)):
    return {"runs": _call(db, _svc("packs").runs, pack_id, limit)}


@router.get("/api/qa-packs/{pack_id}/file")
def qa_pack_file(pack_id: str, version: Optional[int] = None, logical: bool = False, db: Db = Depends(current_db)):
    """The version as a domain/<name>/qa/<pack>.sql file, ready to commit. `logical` writes its table names through
    this environment's bindings ({{silver}}.SCHEMA.TABLE), so the file runs in any environment."""
    from fastapi.responses import PlainTextResponse
    from services.common import environments as envs
    from services.qa.pack_files import portable, render

    detail = _call(db, _svc("packs").get_pack, pack_id, version)
    pack = detail["pack"]
    content = {"name": pack["name"], "description": pack.get("description"), "tags": pack.get("tags") or [],
               "queries": detail["queries"]}
    if logical:
        from app.main import _config

        content = portable(content, envs.bindings_of(envs.validate(_config(db, envs.CONFIG_KEY, None))[0]))
    text = render(content)
    name = re.sub(r"[^a-z0-9]+", "_", pack["name"].lower()).strip("_") or "pack"
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{name}.sql"'})


@router.post("/api/qa/execute")
def execute_qa(body: ExecuteIn, db: Db = Depends(current_db)):
    """Run a pack version (the approved one by default), some of its queries, or one ad-hoc query against Snowflake
    with the caller's role. Pack runs are stored in CORE.QA_RUN / QA_RESULT (SCOPE 'DOMAIN')."""
    if not body.pack_id and not body.sql:
        raise HTTPException(400, "send a pack_id or a sql query")
    domain_id = _domain_id(db, body.domain_id)
    if body.dry_run:
        if not body.pack_id or body.sql:
            raise HTTPException(400, "a dry run checks a pack: send a pack_id")
        return _call(db, _svc("packs").dry_run, domain_id, body.pack_id, body.version, body.environment, body.query_ids)
    return _call(db, _svc("packs").execute, domain_id, body.pack_id, body.version, body.query_ids, body.sql,
                 **({"environment": body.environment} if body.environment else {}))


# ---------------------------------------------------------------- QA packs and Git (V042)


@router.get("/api/qa-packs/{pack_id}/sync")
def qa_pack_sync(pack_id: str, db: Db = Depends(current_db)):
    """The pack's open repository conflict (the repository file changed since the pack was edited in the app)."""
    return {"conflict": _call(db, _svc("pack_git").conflict, pack_id)}


@router.post("/api/qa-packs/{pack_id}/sync/{sync_id}/take-repo")
def qa_pack_take_repo(pack_id: str, sync_id: str, db: Db = Depends(current_db)):
    """The repository version becomes the pack's newest draft, to submit and approve like any change."""
    return _call(db, _svc("pack_git").take_repo, pack_id, sync_id, str(db.user or "").upper())


@router.post("/api/qa-packs/{pack_id}/sync/{sync_id}/keep-app")
def qa_pack_keep_app(pack_id: str, sync_id: str, db: Db = Depends(current_db)):
    return _call(db, _svc("pack_git").keep_app, pack_id, sync_id, str(db.user or "").upper())


class PackPublishIn(BaseModel):
    version: Optional[int] = Field(default=None, ge=1)
    repo_id: str = Field(min_length=1, max_length=64)
    base: Optional[str] = Field(default=None, max_length=200)


@router.post("/api/qa-packs/{pack_id}/publish")
def qa_pack_publish(pack_id: str, body: PackPublishIn, db: Db = Depends(current_db)):
    """A pull request with the approved version's pack file (feature/qa-<pack>-v<version>)."""
    from app.main import _dbt_repos, _github_publisher_ready

    detail = _call(db, _svc("packs").get_pack, pack_id, body.version)
    version = body.version or detail.get("approved_version")
    if not version:
        raise HTTPException(409, "The pack has no approved version to publish.")
    if body.version:
        if detail["pack"]["status"] != "APPROVED":
            raise HTTPException(409, "Only an approved version is published.")
    repo = next((r for r in _dbt_repos(db, None) if r["repo_id"] == body.repo_id), None)
    if not repo or (repo.get("provider") or "") != "GITHUB":
        raise HTTPException(409, "Choose a GitHub repository connected in Admin, Integrations.")
    if not _github_publisher_ready(db):
        return {"status": "NOT_CONFIGURED", "detail": "GitHub publishing is not set up. An admin can set it up in the GitHub publishing card."}
    slug = re.sub(r"[^a-z0-9]+", "-", str(detail["pack"]["name"]).lower()).strip("-") or "pack"
    head = f"feature/qa-{slug}-v{int(version)}"
    request = {"pack_id": pack_id, "version": int(version), "repo_id": body.repo_id, "head": head,
               "base": body.base or repo.get("branch")}
    try:
        result = db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)", ("", json.dumps({"qa_pack": request})))
    except Exception as exc:
        return {"status": "FAILED", "detail": str(exc)[:1000], "head_branch": head}
    return {**(result or {}), "head_branch": head}


class PackSyncIn(BaseModel):
    force: bool = False


@router.post("/api/qa-packs/{pack_id}/pr-sync")
def qa_pack_pr_sync(pack_id: str, body: PackSyncIn = PackSyncIn(), db: Db = Depends(current_db)):
    from app.main import _github_publisher_ready

    if not _github_publisher_ready(db):
        return {"checked": 0, "merged": [], "closed": [], "errors": [], "detail": "GitHub publishing is not set up"}
    try:
        return db.call("CALL CORE.PUBLISH_DBT_PR(%s, %s)",
                       ("", json.dumps({"sync_qa_prs": {"pack_id": pack_id, "force": body.force}}))) or {}
    except Exception as exc:
        raise _snowflake_error(exc) from exc


# ---------------------------------------------------------------- test history (V042)


@router.get("/api/qa/tests/{test_id}/versions")
def qa_test_versions(test_id: str, db: Db = Depends(current_db)):
    try:
        return _call(db, _svc("test_versions").history, test_id)
    except HTTPException as exc:
        if exc.status_code >= 500:
            return {"test_id": test_id, "versions": [], "deployed": False}
        raise


class RestoreIn(BaseModel):
    version: int = Field(ge=1)


@router.post("/api/qa/tests/{test_id}/restore")
def qa_test_restore(test_id: str, body: RestoreIn, db: Db = Depends(current_db)):
    """Put an older version of a saved test back; its SQL is checked again as if typed now."""
    return _call(db, _svc("test_versions").restore, test_id, body.version)
