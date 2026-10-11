"""QA Store: domain test packages (V037, privileges PACKAGE.EDIT and PACKAGE.APPROVE).

A pack is a named, versioned set of deterministic SQL assertions owned by a domain: reconciliations between tables,
orphan keys, duplicate business keys, joins that must resolve. Versions move DRAFT -> PENDING_APPROVAL -> APPROVED
(the previous approved version becomes SUPERSEDED). Saving while the newest version is a DRAFT replaces that draft;
saving on top of a submitted or approved version starts a new DRAFT, so an approved version never changes.

Queries run through the platform's read-only QA guard (services/qa/guard.py) and runner (services/qa/run.execute),
limited to the domain's tables: its registered target tables and the landing tables of its runs. Results are stored
in QUALITY.QA_RUN / QA_RESULT with SCOPE 'DOMAIN'. Approving a version publishes its queries as ACTIVE QA_TEST
knowledge, which Cortex Search (KNOWLEDGE.KNOWLEDGE_SEARCH) and the QA ask and plan prompts already read.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.qa.pack_files import NAME, SEVERITIES

PACKS = "GOVERNANCE.DOMAIN_QA_PACKS"
QUERIES = "GOVERNANCE.DOMAIN_QA_QUERIES"
STATUSES = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "SUPERSEDED", "RETIRED")
TEST_SEVERITY = {"ERROR": "HIGH", "WARN": "MEDIUM", "INFO": "LOW"}
_NS = uuid.UUID("5f0c1a7e-3d2b-4c8e-9a61-2b7d0e4f9c11")
MAX_QUERIES = 200


def pack_id_for(domain_id: str, name: str) -> str:
    """Stable id of a repository pack, so re-seeding finds the same pack."""
    return str(uuid.uuid5(_NS, f"pack:{domain_id}:{name.strip().lower()}"))


def query_id_for(pack_id: str, query_name: str) -> str:
    """Stable across versions: results and knowledge of a query survive edits to its SQL."""
    return str(uuid.uuid5(_NS, f"query:{pack_id}:{query_name.strip().lower()}"))


def knowledge_key(pack_id: str, query_name: str) -> str:
    return f"qa.pack.{pack_id}.{query_name.strip().lower()}"


# ---------------------------------------------------------------- pure


def clean_tags(tags: Any) -> List[str]:
    from services.common.tags import normalize_tags

    if isinstance(tags, str):
        tags = [t for t in tags.replace(",", " ").split()]
    return normalize_tags(tags or [])


def clean_queries(queries: Sequence[Dict[str, Any]], allowed: Sequence[str]) -> List[Dict[str, Any]]:
    """Validated queries in order. Every SQL must pass the read-only guard against the domain's tables; problems
    raise AssertionError naming the query."""
    from services.qa.guard import check

    assert queries, "a pack needs at least one query"
    assert len(queries) <= MAX_QUERIES, f"a pack holds at most {MAX_QUERIES} queries"
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, q in enumerate(queries):
        name = str(q.get("query_name") or "").strip()
        assert NAME.match(name), f"query {i + 1}: names are letters, digits and _ (starting with a letter)"
        assert name.lower() not in seen, f"query {name} appears twice"
        seen.add(name.lower())
        severity = str(q.get("severity") or "ERROR").upper()
        assert severity in SEVERITIES, f"{name}: severity must be one of {list(SEVERITIES)}"
        ok, problems, sql = check(str(q.get("sql_expression") or ""), allowed)
        assert ok, f"{name}: " + "; ".join(problems)
        out.append({"query_name": name, "severity": severity, "sql_expression": sql,
                    "description": clip(q.get("description"), 4000) or None,
                    "expectation": clip(q.get("expectation") or "0 rows", 1000), "tags": clean_tags(q.get("tags")),
                    "ordinal": i + 1})
    return out


def to_test(pack: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
    """A pack query in the shape services/qa/run.execute and evaluate expect."""
    return {"test_id": query.get("query_id"), "category": "DOMAIN_PACK",
            "title": f"{pack.get('name')}: {query['query_name']}",
            "severity": TEST_SEVERITY.get(str(query.get("severity") or "ERROR").upper(), "HIGH"),
            "origin": "PACK", "expected": query.get("expectation") or "0 rows", "sql": query["sql_expression"],
            "target_column": None}


def diff(old: Sequence[Dict[str, Any]], new: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    a = {q["query_name"].lower(): q for q in old}
    b = {q["query_name"].lower(): q for q in new}
    changed = []
    for k in sorted(set(a) & set(b)):
        fields = [f for f in ("sql_expression", "severity", "expectation", "description", "tags")
                  if (a[k].get(f) or None) != (b[k].get(f) or None)]
        if fields:
            changed.append({"query_name": b[k]["query_name"], "fields": fields,
                            "from": {f: a[k].get(f) for f in fields}, "to": {f: b[k].get(f) for f in fields}})
    return {"added": [b[k]["query_name"] for k in sorted(set(b) - set(a))],
            "removed": [a[k]["query_name"] for k in sorted(set(a) - set(b))], "changed": changed}


# ---------------------------------------------------------------- reads


def allowed_tables(session, domain_id: str) -> List[str]:
    """The domain's tables a query may read: active registered targets and the landing tables of its runs."""
    out: List[str] = []
    for r in rows(session, """SELECT TARGET_DATABASE || '.' || TARGET_SCHEMA || '.' || TARGET_TABLE AS FQN
                                FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = ? AND ACTIVE_FLAG""", [domain_id]):
        out.append(r["FQN"])
    try:
        for r in rows(session, """SELECT DISTINCT L.LANDING_DATABASE || '.' || L.LANDING_SCHEMA || '.' || L.LANDING_TABLE AS FQN
                                    FROM SOURCE.LANDING_TABLE_REGISTRY L
                                    JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = L.RUN_ID
                                   WHERE R.DOMAIN_ID = ? AND L.INGESTION_STATUS = 'COMPLETE'""", [domain_id]):
            out.append(r["FQN"])
    except Exception:
        pass
    return list(dict.fromkeys(out))


def _pack_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {k.lower(): v for k, v in r.items()}
    out["tags"] = variant(out.get("tags")) or []
    for k in ("created_at", "submitted_at", "approved_at"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return out


def _query_rows(session, pack_id: str, version: int) -> List[Dict[str, Any]]:
    out = []
    for r in rows(session, f"""SELECT QUERY_ID, QUERY_NAME, DESCRIPTION, SQL_EXPRESSION, SEVERITY, EXPECTATION, TAGS,
                                      ORDINAL, CREATED_BY, UPDATED_AT
                                 FROM {QUERIES} WHERE PACK_ID = ? AND PACK_VERSION = ? ORDER BY ORDINAL""",
                  [pack_id, int(version)]):
        q = {k.lower(): v for k, v in r.items()}
        q["tags"] = variant(q.get("tags")) or []
        q["updated_at"] = str(q["updated_at"]) if q.get("updated_at") is not None else None
        out.append(q)
    return out


def _versions(session, pack_id: str) -> List[Dict[str, Any]]:
    return [_pack_row(r) for r in rows(session, f"SELECT * FROM {PACKS} WHERE PACK_ID = ? ORDER BY VERSION DESC",
                                       [pack_id])]


def get_pack(session, pack_id: str, version: Optional[int] = None) -> Dict[str, Any]:
    versions = _versions(session, pack_id)
    assert versions, "pack not found"
    if version is not None:
        chosen = next((v for v in versions if int(v["version"]) == int(version)), None)
        assert chosen, f"version {version} of this pack does not exist"
    else:
        chosen = versions[0]
    approved = next((v for v in versions if v["status"] == "APPROVED"), None)
    return {"pack": chosen, "queries": _query_rows(session, pack_id, chosen["version"]),
            "approved_version": approved["version"] if approved else None,
            "versions": [{k: v.get(k) for k in ("version", "status", "source", "change_note", "created_by", "created_at",
                                            "approved_by", "approved_at", "repo_path", "pr_state", "pr_url",
                                            "pr_number", "branch")} for v in versions]}


def list_packs(session, domain_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest version of every pack with its approved version, query count and latest execution."""
    where, params = ("WHERE P.DOMAIN_ID = ?", [domain_id]) if domain_id else ("", [])
    found = rows(session, f"""
        WITH V AS (
            SELECT P.*, ROW_NUMBER() OVER (PARTITION BY P.PACK_ID ORDER BY P.VERSION DESC) AS RN,
                   MAX(IFF(P.STATUS = 'APPROVED', P.VERSION, NULL)) OVER (PARTITION BY P.PACK_ID) AS APPROVED_VERSION
              FROM {PACKS} P {where}
        )
        SELECT V.PACK_ID, V.VERSION, V.DOMAIN_ID, D.DOMAIN_NAME, V.NAME, V.DESCRIPTION, V.TAGS, V.STATUS, V.SOURCE,
               V.APPROVED_VERSION, V.CREATED_BY, V.CREATED_AT::VARCHAR AS CREATED_AT,
               (SELECT COUNT(*) FROM {QUERIES} Q WHERE Q.PACK_ID = V.PACK_ID AND Q.PACK_VERSION = V.VERSION) AS QUERIES
          FROM V LEFT JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = V.DOMAIN_ID
         WHERE V.RN = 1
         ORDER BY D.DOMAIN_NAME, V.NAME""", params)
    out = [_pack_row(r) for r in found]
    last = _last_runs(session, [p["pack_id"] for p in out])
    for p in out:
        p["last_run"] = last.get(p["pack_id"])
    return out


def _last_runs(session, pack_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not pack_ids:
        return {}
    try:
        found = rows(session, """SELECT TRIGGER_DETAIL:pack_id::VARCHAR AS PACK_ID, TESTS, PASSED, FAILED, ERRORS, REVIEW,
                                        STARTED_AT::VARCHAR AS STARTED_AT
                                   FROM QUALITY.QA_RUN
                                  WHERE SCOPE = 'DOMAIN' AND ARRAY_CONTAINS(TRIGGER_DETAIL:pack_id, PARSE_JSON(?))
                                QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIGGER_DETAIL:pack_id ORDER BY STARTED_AT DESC) = 1""",
                     [json.dumps(pack_ids)])
    except Exception:
        return {}
    return {r["PACK_ID"]: {k.lower(): v for k, v in r.items() if k != "PACK_ID"} for r in found}


def runs(session, pack_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return [{k.lower(): v for k, v in r.items()} for r in rows(session, f"""
        SELECT QA_RUN_ID, TRIGGER_DETAIL:version::NUMBER AS VERSION, TESTS, PASSED, FAILED, REVIEW, ERRORS, NOT_RUN,
               DURATION_MS, TRIGGERED_BY, CREATED_BY, STARTED_AT::VARCHAR AS STARTED_AT
          FROM QUALITY.QA_RUN WHERE SCOPE = 'DOMAIN' AND TRIGGER_DETAIL:pack_id::VARCHAR = ?
         ORDER BY STARTED_AT DESC LIMIT {int(max(1, min(limit, 100)))}""", [pack_id])]


def summary(session, domain_id: Optional[str] = None) -> Dict[str, Any]:
    """For the dashboard: approved packs, rules in them, top tags and the pass rate of their latest executions."""
    packs = list_packs(session, domain_id)
    live = [p for p in packs if p.get("approved_version")]
    rules = 0
    tags: Dict[str, int] = {}
    for p in live:
        rules += len(_query_rows(session, p["pack_id"], p["approved_version"]))
        for t in p.get("tags") or []:
            tags[t] = tags.get(t, 0) + 1
    tests = sum(int((p.get("last_run") or {}).get("tests") or 0) for p in live)
    passed = sum(int((p.get("last_run") or {}).get("passed") or 0) for p in live)
    return {"packs": len(packs), "approved_packs": len(live), "rules": rules,
            "pending": sum(1 for p in packs if p["status"] == "PENDING_APPROVAL"),
            "domains": len({p["domain_id"] for p in live}),
            "top_tags": [t for t, _ in sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:6]],
            "pass_rate": round(100 * passed / tests, 1) if tests else None}


# ---------------------------------------------------------------- writes


def _insert_version(session, pack_id: str, version: int, domain_id: str, meta: Dict[str, Any],
                    queries: List[Dict[str, Any]], status: str, source: str, note: str,
                    repo: Optional[Dict[str, Any]] = None) -> None:
    """`repo` ({repo_sha, repo_path}, V042) is the repository file the version descends from, carried forward so the
    deploy can tell an app edit that is ahead of the repository from one the repository has since overtaken."""
    columns = ["PACK_ID", "VERSION", "DOMAIN_ID", "NAME", "DESCRIPTION", "TAGS", "STATUS", "SOURCE", "CHANGE_NOTE",
               "APPROVED_BY", "APPROVED_AT"]
    exprs = ["?", "?::NUMBER", "?", "?", "NULLIF(?, '')", "PARSE_JSON(?)::ARRAY", "?", "?", "NULLIF(?, '')",
             "IFF(? = 'APPROVED', CURRENT_USER(), NULL)", "IFF(? = 'APPROVED', CURRENT_TIMESTAMP(), NULL)"]
    values = [pack_id, version, domain_id, clip(meta["name"], 128), meta.get("description") or "",
              json.dumps(meta.get("tags") or []), status, source, clip(note, 2000), status, status]
    if repo and repo.get("repo_sha"):
        columns += ["REPO_SHA", "REPO_PATH"]
        exprs += ["?", "NULLIF(?, '')"]
        values += [repo["repo_sha"], clip(repo.get("repo_path") or "", 512)]
    insert_rows(session, PACKS, columns, exprs, [values])
    insert_rows(session, QUERIES,
                ["QUERY_ID", "PACK_ID", "PACK_VERSION", "QUERY_NAME", "DESCRIPTION", "SQL_EXPRESSION", "SEVERITY",
                 "EXPECTATION", "TAGS", "ORDINAL"],
                ["?", "?", "?::NUMBER", "?", "NULLIF(?, '')", "?", "?", "?", "PARSE_JSON(?)::ARRAY", "?::NUMBER"],
                [[query_id_for(pack_id, q["query_name"]), pack_id, version, q["query_name"], q.get("description") or "",
                  q["sql_expression"], q["severity"], q["expectation"], json.dumps(q.get("tags") or []), q["ordinal"]]
                 for q in queries])


def _meta(payload: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = base or {}
    name = str(payload.get("name") or base.get("name") or "").strip()
    assert name, "a pack needs a name"
    return {"name": name[:128], "description": clip(payload.get("description", base.get("description")), 4000),
            "tags": clean_tags(payload.get("tags", base.get("tags")))}


def create_pack(session, domain_id: str, payload_json: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    assert scalar(session, "SELECT COUNT(*) FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id]), \
        "domain not found"
    meta = _meta(payload)
    taken = rows(session, f"SELECT PACK_ID FROM {PACKS} WHERE DOMAIN_ID = ? AND LOWER(NAME) = LOWER(?) LIMIT 1",
                 [domain_id, meta["name"]])
    assert not taken, f"this domain already has a pack named {meta['name']}"
    queries = clean_queries(payload.get("queries") or [], allowed_tables(session, domain_id))
    pack_id = str(uuid.uuid4())
    _insert_version(session, pack_id, 1, domain_id, meta, queries, "DRAFT", "UI", payload.get("change_note") or "Created")
    return {"pack_id": pack_id, "version": 1, "status": "DRAFT", "queries": len(queries)}


def save_draft(session, pack_id: str, payload_json: str) -> Dict[str, Any]:
    """Replace the newest DRAFT, or start a new DRAFT on top of a submitted or approved version. `base_version`
    must be the newest version (someone else may have saved since)."""
    payload = json.loads(payload_json or "{}")
    versions = _versions(session, pack_id)
    assert versions, "pack not found"
    newest = versions[0]
    assert newest["status"] != "RETIRED", "the pack is retired"
    if payload.get("base_version") is not None:
        assert int(payload["base_version"]) == int(newest["version"]), \
            f"version {newest['version']} was saved since you opened version {payload['base_version']}; reload first"
    meta = _meta(payload, newest)
    queries = clean_queries(payload.get("queries") or [], allowed_tables(session, newest["domain_id"]))
    from services.common.sql import atomic

    note = payload.get("change_note") or ""
    repo = {"repo_sha": payload.get("repo_sha") or newest.get("repo_sha"),
            "repo_path": payload.get("repo_path") or newest.get("repo_path")}
    replace = newest["status"] == "DRAFT"
    version = int(newest["version"]) if replace else int(newest["version"]) + 1

    def write() -> None:
        if replace:
            session.sql(f"DELETE FROM {QUERIES} WHERE PACK_ID = ? AND PACK_VERSION = ?", params=[pack_id, version]).collect()
            session.sql(f"DELETE FROM {PACKS} WHERE PACK_ID = ? AND VERSION = ?", params=[pack_id, version]).collect()
        _insert_version(session, pack_id, version, newest["domain_id"], meta, queries, "DRAFT", "UI",
                        note or f"Edited from version {newest['version']}", **({"repo": repo} if repo["repo_sha"] else {}))

    atomic(session, write)
    previous = next((v for v in versions if v["status"] == "APPROVED"), None)
    changes = diff(_query_rows(session, pack_id, previous["version"]), queries) if previous else None
    return {"pack_id": pack_id, "version": version, "status": "DRAFT", "queries": len(queries), "diff": changes}


def _set_status(session, pack_id: str, version: int, status: str, extra: str = "") -> None:
    session.sql(f"UPDATE {PACKS} SET STATUS = ?{extra} WHERE PACK_ID = ? AND VERSION = ?",
                params=[status, pack_id, int(version)]).collect()


def submit(session, pack_id: str, note: str = "") -> Dict[str, Any]:
    newest = (_versions(session, pack_id) or [None])[0]
    assert newest, "pack not found"
    assert newest["status"] == "DRAFT", f"only a draft can be submitted; the newest version is {newest['status'].lower()}"
    session.sql(f"""UPDATE {PACKS} SET STATUS = 'PENDING_APPROVAL', SUBMITTED_BY = CURRENT_USER(),
                           SUBMITTED_AT = CURRENT_TIMESTAMP(), CHANGE_NOTE = COALESCE(NULLIF(?, ''), CHANGE_NOTE)
                     WHERE PACK_ID = ? AND VERSION = ?""", params=[clip(note, 2000), pack_id, newest["version"]]).collect()
    return {"pack_id": pack_id, "version": newest["version"], "status": "PENDING_APPROVAL"}


def reject(session, pack_id: str, note: str = "") -> Dict[str, Any]:
    newest = (_versions(session, pack_id) or [None])[0]
    assert newest and newest["status"] == "PENDING_APPROVAL", "there is no version waiting for approval"
    session.sql(f"UPDATE {PACKS} SET STATUS = 'DRAFT', CHANGE_NOTE = ? WHERE PACK_ID = ? AND VERSION = ?",
                params=[clip(f"Sent back: {note}" if note else "Sent back for changes", 2000), pack_id,
                        newest["version"]]).collect()
    return {"pack_id": pack_id, "version": newest["version"], "status": "DRAFT"}


def approve(session, pack_id: str) -> Dict[str, Any]:
    """The submitted version becomes the pack's approved version; its queries become ACTIVE QA_TEST knowledge and
    the queries the previous approved version had but this one dropped stop being used."""
    from services.common.sql import atomic

    versions = _versions(session, pack_id)
    assert versions, "pack not found"
    newest = versions[0]
    assert newest["status"] == "PENDING_APPROVAL", "submit the pack for approval first"
    previous = next((v for v in versions if v["status"] == "APPROVED"), None)

    def write() -> None:
        session.sql(f"UPDATE {PACKS} SET STATUS = 'SUPERSEDED' WHERE PACK_ID = ? AND STATUS = 'APPROVED'",
                    params=[pack_id]).collect()
        _set_status(session, pack_id, newest["version"], "APPROVED",
                    ", APPROVED_BY = CURRENT_USER(), APPROVED_AT = CURRENT_TIMESTAMP()")

    atomic(session, write)
    queries = _query_rows(session, pack_id, newest["version"])
    old = _query_rows(session, pack_id, previous["version"]) if previous else []
    published = publish_knowledge(session, newest, queries, old)
    return {"pack_id": pack_id, "version": newest["version"], "status": "APPROVED", "knowledge": published}


def publish_knowledge(session, pack: Dict[str, Any], queries: List[Dict[str, Any]],
                      dropped_from: Sequence[Dict[str, Any]] = ()) -> int:
    """Approved queries as ACTIVE QA_TEST knowledge (never fails the approval)."""
    from services.knowledge.writer import forget, remember

    count = 0
    for q in queries:
        content = (f"{pack['name']}: {q['query_name']}. {q.get('description') or ''} "
                   f"Passes when: {q.get('expectation') or '0 rows'}. Severity {q.get('severity')}.\n\n{q['sql_expression']}")
        try:
            remember(session, domain_id=pack["domain_id"], kind="QA_TEST", key=knowledge_key(pack["pack_id"], q["query_name"]),
                     title=f"{pack['name']}: {q['query_name']}", content=content,
                     content_json={"pack_id": pack["pack_id"], "pack": pack["name"], "version": pack["version"],
                                   "query_id": q.get("query_id"), "sql": q["sql_expression"],
                                   "expected": q.get("expectation"), "severity": q.get("severity"),
                                   "tags": q.get("tags") or []},
                     tags=["QA", "PACK", *(q.get("tags") or [])][:20], origin="QA", status="ACTIVE")
            count += 1
        except Exception:
            continue
    kept = {q["query_name"].lower() for q in queries}
    for q in dropped_from:
        if q["query_name"].lower() not in kept:
            try:
                forget(session, pack["domain_id"], knowledge_key(pack["pack_id"], q["query_name"]))
            except Exception:
                pass
    return count


def retire(session, pack_id: str) -> Dict[str, Any]:
    from services.knowledge.writer import forget

    versions = _versions(session, pack_id)
    assert versions, "pack not found"
    approved = next((v for v in versions if v["status"] == "APPROVED"), None)
    session.sql(f"UPDATE {PACKS} SET STATUS = 'RETIRED' WHERE PACK_ID = ? AND STATUS IN ('APPROVED', 'DRAFT', 'PENDING_APPROVAL')",
                params=[pack_id]).collect()
    if approved:
        for q in _query_rows(session, pack_id, approved["version"]):
            try:
                forget(session, approved["domain_id"], knowledge_key(pack_id, q["query_name"]))
            except Exception:
                pass
    return {"pack_id": pack_id, "status": "RETIRED"}


# ---------------------------------------------------------------- execution


def _pii(session, allowed: Sequence[str]) -> tuple[set, bool]:
    """(PII column names, conservative). Without any profile of the domain's tables every non-key value is masked."""
    if not allowed:
        return set(), True
    try:
        found = rows(session, """SELECT DISTINCT UPPER(COLUMN_NAME) AS C, PII_CLASSIFICATION AS P
                                   FROM GOVERNANCE.DATA_PROFILES
                                  WHERE ARRAY_CONTAINS(UPPER(DATABASE_NAME || '.' || SCHEMA_NAME || '.' || TABLE_NAME)::VARIANT,
                                                       PARSE_JSON(?))""",
                     [json.dumps([a.upper() for a in allowed])])
    except Exception:
        return set(), True
    if not found:
        return set(), True
    return {r["C"] for r in found if (r["P"] or "NONE") != "NONE"}, False


def execute(session, domain_id: str, pack_id: Optional[str] = None, version: Optional[int] = None,
            query_ids: Optional[List[str]] = None, sql: Optional[str] = None, triggered_by: str = "UI") -> Dict[str, Any]:
    """Run a pack version (the approved one by default), some of its queries, or one ad-hoc query, with the
    caller's role. Pack runs are stored (SCOPE 'DOMAIN'); an ad-hoc query is only checked and returned."""
    from services.qa.run import _store, blocking, execute as run_one, summary as count

    allowed = allowed_tables(session, domain_id)
    pii, conservative = _pii(session, allowed)
    if sql is not None:
        test = {"test_id": "ADHOC", "category": "DOMAIN_PACK", "title": "Ad-hoc query", "severity": "MEDIUM",
                "origin": "USER", "expected": "0 rows", "sql": sql}
        result = run_one(session, test, allowed, pii, (), conservative)
        return {"stored": False, "allowed": allowed, "results": [result]}
    assert pack_id, "choose a pack or send a query"
    detail = get_pack(session, pack_id, version if version is not None else None)
    pack = detail["pack"]
    if version is None:
        assert detail["approved_version"], "this pack has no approved version yet; test a draft by its version"
        detail = get_pack(session, pack_id, detail["approved_version"])
        pack = detail["pack"]
    assert pack["domain_id"] == domain_id, "the pack belongs to another domain"
    queries = detail["queries"]
    if query_ids:
        wanted = set(query_ids)
        queries = [q for q in queries if q["query_id"] in wanted]
    assert queries, "there are no queries to run"
    started = time.time()
    results = []
    for q in queries:
        r = run_one(session, to_test(pack, q), allowed, pii, (), conservative)
        r["pack_severity"] = q.get("severity")
        results.append(r)
    counts = count(results)
    qa_run = {"qa_run_id": str(uuid.uuid4()), "run_id": None, "sttm_id": None, "target": f"domain pack {pack['name']}",
              "target_built": True, "tests": len(results), "passed": counts["PASS"], "failed": counts["FAIL"],
              "review": counts["REVIEW"], "not_run": counts["NOT_RUN"], "errors": counts["ERROR"],
              "duration_ms": int((time.time() - started) * 1000), "triggered_by": triggered_by,
              "blocking": len(blocking(results)), "scope": "DOMAIN", "target_table_id": None, "domain_id": domain_id,
              "suite_id": None, "trigger_detail": {"pack_id": pack_id, "version": int(pack["version"]),
                                                   "status": pack["status"]}}
    _store(session, qa_run, results)
    return {**qa_run, "stored": True, "allowed": allowed, "results": results}


# ---------------------------------------------------------------- repository packs


def seed_rows(domain_id: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Pack id, header and queries for a parsed repository pack (validated with the guard at execution, since the
    seeding connection may not see every table)."""
    pack_id = pack_id_for(domain_id, parsed["name"])
    queries = []
    for i, q in enumerate(parsed["queries"]):
        queries.append({**q, "ordinal": i + 1, "tags": clean_tags(q.get("tags"))})
    return {"pack_id": pack_id, "meta": {"name": parsed["name"], "description": parsed.get("description") or "",
                                         "tags": clean_tags(parsed.get("tags"))}, "queries": queries}
