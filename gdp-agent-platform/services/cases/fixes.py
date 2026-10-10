"""Acting on a case's proposals (PR Q2): decide, run a reproduction test, mark a fix applied, verify, publish a dbt
patch as a draft pull request, the Jira summary, and the knowledge proposed when a case is resolved.

Rules kept here:
- a reproduction test runs through services.qa.run.execute: the read-only guard (never DML), a LIMIT, and PII masking
  of the sample rows (services.qa.scope.pii_columns; conservative masking without a profile). Only counts are kept on
  the artifact and the timeline;
- a CORRECTION_SQL is text: nothing here runs it;
- a fix is APPLIED only after a person ACCEPTED it, and only a person marks it applied;
- verify runs every ACCEPTED reproduction test: all PASS moves the case to VERIFIED, anything else to IN_PROGRESS with
  the results on the timeline;
- publishing a DBT_PATCH is previewed first. The dry run re-reads the file from the Git clone (refused when it changed
  since the patch was proposed) and returns a preview token: HMAC signed, 10 minutes, bound to the case, the artifact,
  the branch, the patch and the person (services/ops/retry.py pattern). The real publish calls the platform's GitHub
  publisher (CODEGEN.PUBLISH_DBT_PR, case patch mode), which reads the accepted artifact itself, pushes branch
  fix/<case number> and opens a draft pull request; the PR is linked to the case;
- resolving writes CASE_RESOLUTION knowledge (always PROPOSED for the domain's stewards; CONTENT_JSON carries case_id
  and resolved_by, so the resolver cannot approve it) and proposes each accepted reproduction test that passed as
  QA_TEST knowledge. Counts only: no sample values.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

from services.cases import rules
from services.cases.store import SqlStore
from services.cases.triage import TriageError, artifact_out, read_repo_file, sha256, unified_diff
from services.ops.redact import redact
from services.ops.sqlio import as_db, as_session

FIX_TYPES = ("STTM_CHANGE", "CORRECTION_SQL", "DBT_PATCH", "KNOWLEDGE_DRAFT")
VERIFY_FROM = ("FIX_APPLIED", "IN_PROGRESS", "FIX_PROPOSED")
TOKEN_SECONDS = 600
REPLAY_SECONDS = 24 * 3600
_FALLBACK_KEY = secrets.token_hex(32)


class FixError(TriageError):
    pass


def _store(source: Any, store: Any = None) -> Any:
    return store or SqlStore(as_db(source))


def _artifact(store: Any, case: Dict[str, Any], artifact_id: str) -> Dict[str, Any]:
    found = store.artifact(case["case_id"], artifact_id)
    if not found:
        raise FixError("Artifact not found", 404)
    if not isinstance(found.get("content"), dict):
        found["content"] = {}
    return found


def _set_status(store: Any, case: Dict[str, Any], target: str, actor: str, why: str,
                allowed_from: Optional[tuple] = None) -> Dict[str, Any]:
    """A status change that follows from an action (verify, applied): along the transitions, or from allowed_from."""
    current = str(case.get("status") or "").upper()
    if current == target:
        return case
    if target not in rules.TRANSITIONS.get(current, ()) and current not in (allowed_from or ()):
        raise FixError(f"A {current} case cannot move to {target}.", 409)
    if not store.update(case["case_id"], {"STATUS": target}, expect_status=current):
        raise FixError("The case changed in the meantime; reload it and try again.", 409)
    store.event(case["case_id"], "status", actor, {"from": current, "to": target, "note": why})
    return store.get(case["case_id"]) or {**case, "status": target}


# ---------------------------------------------------------------- decide

def decide(source: Any, case: Dict[str, Any], artifact_id: str, decision: str, actor: str, note: Optional[str] = None,
           store: Any = None) -> Dict[str, Any]:
    store = _store(source, store)
    artifact = _artifact(store, case, artifact_id)
    if str(case.get("status")).upper() in rules.DONE_STATUSES:
        raise FixError(f"The case is {case.get('status')}; reopen it first.", 409)
    if artifact.get("status") != "PROPOSED":
        raise FixError(f"This proposal is already {artifact.get('status')}.", 409)
    decision = str(decision or "").lower()
    if decision not in ("accept", "reject"):
        raise FixError("decision must be accept or reject", 422)
    content = dict(artifact["content"])
    if decision == "accept" and artifact.get("type") == "REPRO_TEST" and content.get("valid") is False:
        raise FixError("This test did not pass the read-only guard or does not compile; it cannot be accepted.", 409)
    status = "ACCEPTED" if decision == "accept" else "REJECTED"
    clean_note = redact(note or "").strip()[:2000] or None
    if clean_note:
        content["decision_note"] = clean_note
    if not store.set_artifact(case["case_id"], artifact_id, status, actor, content, expect_status="PROPOSED"):
        raise FixError("The proposal changed in the meantime; reload it and try again.", 409)
    store.event(case["case_id"], "artifact_decided", actor, {"artifact_id": artifact_id, "type": artifact.get("type"),
                                                             "decision": status, "note": clean_note})
    return artifact_out(store.artifact(case["case_id"], artifact_id) or {**artifact, "status": status, "content": content})


# ---------------------------------------------------------------- run a reproduction test

class _Field:
    def __init__(self, name: str):
        self.name = name


class _Frame:
    """Just enough of a Snowpark DataFrame for services.qa.run.execute: schema.fields and collect()."""

    def __init__(self, db: Any, sql: str):
        self.db, self.sql, self._rows, self._columns = db, sql, None, None

    def _run(self) -> None:
        if self._rows is not None:
            return
        conn = getattr(self.db, "conn", None)
        if conn is not None:
            cur = conn.cursor()
            try:
                cur.execute(self.sql)
                self._columns = [d[0] for d in cur.description]
                self._rows = [tuple(r) for r in cur.fetchall()]
            finally:
                cur.close()
            return
        found = self.db.query(self.sql)
        self._columns = [str(k).upper() for k in (found[0].keys() if found else [])]
        self._rows = [tuple(r.values()) for r in found]

    @property
    def schema(self) -> Any:
        self._run()
        return type("Schema", (), {"fields": [_Field(c) for c in self._columns or []]})()

    def collect(self) -> List[tuple]:
        self._run()
        return list(self._rows or [])


class QuerySession:
    """A Db seen as the session services.qa.run.execute runs a test on."""

    def __init__(self, db: Any):
        self.db = db

    def sql(self, sql: str, params: Optional[list] = None) -> _Frame:
        assert not params, "tests run without parameters"
        return _Frame(self.db, sql)


def _run_session(source: Any) -> Any:
    if hasattr(source, "sql") and not hasattr(source, "query"):
        return source        # a Snowpark session
    return QuerySession(as_db(source))


def run_test(source: Any, case: Dict[str, Any], artifact: Dict[str, Any], *,
             table_context: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
             executor: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """execute()'s result for one reproduction test against the case's target table (guarded, masked)."""
    from services.qa import run as qa_run
    from services.qa.scope import table_context as scoped

    content = artifact.get("content") or {}
    tid = content.get("target_table_id") or case.get("target_table_id")
    if not tid:
        raise FixError("The case has no target table, so the test has nothing to run against.", 409)
    ctx = (table_context or scoped)(as_session(source), tid)
    test = {"test_id": artifact.get("artifact_id"), "category": content.get("category") or "CUSTOM",
            "title": artifact.get("title"), "severity": content.get("severity") or "MEDIUM", "origin": "CASE",
            "expected": content.get("expected"), "sql": content.get("sql")}
    pii = set(ctx.get("pii_columns") or [])
    return (executor or qa_run.execute)(_run_session(source), test, ctx.get("allowed") or [], pii,
                                        ctx.get("business_keys") or [], ctx.get("pii_basis") == "conservative")


def run_out(result: Dict[str, Any]) -> Dict[str, Any]:
    return {"outcome": result.get("outcome"), "rows_returned": result.get("rows_returned"),
            "measured": result.get("measured"), "detail": result.get("detail"), "columns": list(result.get("columns") or []),
            "sample": list(result.get("sample") or []), "masked": sorted(result.get("masked") or [])}


def _record_run(store: Any, case: Dict[str, Any], artifact: Dict[str, Any], result: Dict[str, Any], actor: str,
                kind: str = "repro_run") -> None:
    from services.jira.triage import measured_count

    content = dict(artifact.get("content") or {})
    content["last_outcome"] = result.get("outcome")
    content["last_run"] = {"outcome": result.get("outcome"), "rows_returned": result.get("rows_returned"),
                           "measured": measured_count(result.get("measured")) or None, "by": actor,
                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    store.set_artifact(case["case_id"], artifact["artifact_id"], content=content)
    artifact["content"] = content
    if kind:
        store.event(case["case_id"], kind, actor, {"artifact_id": artifact["artifact_id"], "outcome": result.get("outcome"),
                                                   "rows_returned": result.get("rows_returned")})


def run_repro(source: Any, case: Dict[str, Any], artifact_id: str, actor: str, store: Any = None,
              **kw: Any) -> Dict[str, Any]:
    store = _store(source, store)
    artifact = _artifact(store, case, artifact_id)
    if artifact.get("type") != "REPRO_TEST":
        raise FixError("Only reproduction tests run; a correction query is never run by the platform.", 409)
    if artifact.get("status") == "REJECTED":
        raise FixError("This test was rejected.", 409)
    result = run_test(source, case, artifact, **kw)
    _record_run(store, case, artifact, result, actor)
    return run_out(result)


# ---------------------------------------------------------------- applied and verify

def mark_applied(source: Any, case: Dict[str, Any], artifact_id: str, actor: str, note: Optional[str] = None,
                 store: Any = None) -> Dict[str, Any]:
    """{artifact, case}: an ACCEPTED fix becomes APPLIED and the case FIX_APPLIED."""
    store = _store(source, store)
    artifact = _artifact(store, case, artifact_id)
    if artifact.get("type") not in FIX_TYPES:
        raise FixError("Only fixes are applied; reproduction tests are run.", 409)
    if artifact.get("status") != "ACCEPTED":
        raise FixError("Accept the fix before marking it applied.", 409)
    current = str(case.get("status") or "").upper()
    if current in rules.DONE_STATUSES:
        raise FixError(f"The case is {current}; reopen it first.", 409)
    content = dict(artifact["content"])
    clean_note = redact(note or "").strip()[:2000] or None
    if clean_note:
        content["applied_note"] = clean_note
    if not store.set_artifact(case["case_id"], artifact_id, "APPLIED", actor, content, expect_status="ACCEPTED"):
        raise FixError("The fix changed in the meantime; reload it and try again.", 409)
    store.event(case["case_id"], "fix_applied", actor, {"artifact_id": artifact_id, "type": artifact.get("type"),
                                                        "note": clean_note})
    if current in ("NEW", "TRIAGED", "VERIFIED"):
        case = _set_status(store, case, "IN_PROGRESS", actor, "a fix was applied")
    case = _set_status(store, case, "FIX_APPLIED", actor, "a fix was applied")
    return {"artifact": artifact_out(store.artifact(case["case_id"], artifact_id) or artifact), "case": case}


def verify(source: Any, case: Dict[str, Any], actor: str, store: Any = None, **kw: Any) -> Dict[str, Any]:
    """{results:[{artifact_id, title, outcome}], verified, case}."""
    store = _store(source, store)
    current = str(case.get("status") or "").upper()
    if current not in VERIFY_FROM:
        raise FixError(f"Verify a case once a fix is proposed or applied (it is {current}).", 409)
    tests = [a for a in store.artifacts(case["case_id"]) if a.get("type") == "REPRO_TEST" and a.get("status") == "ACCEPTED"]
    if not tests:
        raise FixError("Accept at least one reproduction test before verifying.", 409)
    results = []
    for a in tests:
        a["content"] = a.get("content") if isinstance(a.get("content"), dict) else {}
        try:
            result = run_test(source, case, a, **kw)
        except FixError as exc:
            result = {"outcome": "ERROR", "detail": exc.message}
        _record_run(store, case, a, result, actor, kind="")
        results.append({"artifact_id": a["artifact_id"], "title": a.get("title"), "outcome": result.get("outcome"),
                        "rows_returned": result.get("rows_returned")})
    verified = all(r["outcome"] == "PASS" for r in results)
    store.event(case["case_id"], "verified" if verified else "verify_failed", actor,
                {"results": [{k: r[k] for k in ("artifact_id", "title", "outcome", "rows_returned")} for r in results]})
    if verified:
        case = _set_status(store, case, "VERIFIED", actor, "every accepted reproduction test passes", allowed_from=VERIFY_FROM)
    else:
        case = _set_status(store, case, "IN_PROGRESS", actor, "a reproduction test still fails")
    return {"results": [{k: r[k] for k in ("artifact_id", "title", "outcome")} for r in results], "verified": verified,
            "case": case}


# ---------------------------------------------------------------- publish a dbt patch

def _key(key: Optional[str] = None) -> bytes:
    from services.ops.notify import secret_key

    return ("case-publish:" + (key or secret_key() or _FALLBACK_KEY)).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def branch_name(case: Dict[str, Any]) -> str:
    return f"fix/{(rules.case_ref(case.get('case_number')) or 'case-' + str(case['case_id'])[:8]).lower()}"


def binding(case: Dict[str, Any], artifact: Dict[str, Any], branch: str) -> Dict[str, Any]:
    content = artifact.get("content") or {}
    return {"c": case["case_id"], "a": artifact["artifact_id"], "b": branch,
            "h": sha256(str(content.get("new_content") or "") + "\x00" + str(content.get("base_sha256") or ""))}


def make_token(bound: Dict[str, Any], user: str, now: Optional[float] = None, key: Optional[str] = None) -> str:
    payload = {**bound, "u": str(user or "").upper(), "x": int((now or time.time()) + TOKEN_SECONDS)}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{hmac.new(_key(key), body.encode('ascii'), hashlib.sha256).hexdigest()}"


def check_token(token: Optional[str], bound: Dict[str, Any], user: str, now: Optional[float] = None,
                replay: bool = False, key: Optional[str] = None) -> None:
    if not token or "." not in token:
        raise FixError("Run a dry run first: publishing needs the preview_token it returns.", 422)
    body, _, sig = token.rpartition(".")
    if not hmac.compare_digest(sig, hmac.new(_key(key), body.encode("ascii"), hashlib.sha256).hexdigest()):
        raise FixError("The preview token is not valid. Run the dry run again.", 422)
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        raise FixError("The preview token is not valid. Run the dry run again.", 422) from None
    limit = int(payload.get("x") or 0) + (REPLAY_SECONDS - TOKEN_SECONDS if replay else 0)
    if (now or time.time()) > limit:
        raise FixError("The preview expired (it is valid for 10 minutes). Run the dry run again.", 409)
    if any(payload.get(k) != v for k, v in bound.items()):
        raise FixError("The preview token belongs to another case, patch or branch. Run the dry run again.", 409)
    if not replay and str(payload.get("u") or "") != str(user or "").upper():
        raise FixError("The preview was made by someone else. Run the dry run yourself.", 409)


def _repo(db: Any, repo_id: Optional[str]) -> Dict[str, Any]:
    found = db.query("""SELECT REPO_ID, NAME, GIT_URL, PROVIDER, BRANCH, GIT_REPOSITORY, ENABLED FROM CODE.REPO
                         WHERE REPO_ID = %s""", (repo_id,)) if repo_id else []
    if not found or not found[0].get("enabled"):
        raise FixError("The repository of this patch is no longer connected.", 409)
    if str(found[0].get("provider") or "").upper() != "GITHUB":
        raise FixError(f"{found[0].get('name')} is not on GitHub; pull requests from the platform are GitHub only.", 409)
    return found[0]


def publish(source: Any, case: Dict[str, Any], artifact_id: str, actor: str, dry_run: bool = True,
            preview_token: Optional[str] = None, *, replay: bool = False, store: Any = None,
            publisher: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
            read_file: Optional[Callable[[Any, Dict[str, Any], str], str]] = None) -> Dict[str, Any]:
    """Dry run: {branch, base_branch, repo, path, diff, preview_token}. Real: {pr_url, branch, status}."""
    db = as_db(source)
    store = _store(source, store)
    artifact = _artifact(store, case, artifact_id)
    content = artifact["content"]
    if artifact.get("type") != "DBT_PATCH":
        raise FixError("Only a dbt patch can be published as a pull request.", 409)
    if artifact.get("status") != "ACCEPTED":
        raise FixError("Accept the patch before publishing it.", 409)
    if not content.get("publishable") or not content.get("new_content"):
        raise FixError(content.get("unavailable_reason") or "This patch cannot be published (file not available).", 409)
    repo = _repo(db, content.get("repo_id"))
    path = str(content.get("path"))
    branch = branch_name(case)
    try:
        current = (read_file or read_repo_file)(db, repo, path)
    except Exception:
        raise FixError("The file could not be read from the Git clone; refresh the code index and try again.", 409) from None
    if sha256(current) != content.get("base_sha256"):
        raise FixError("The file changed since the patch was proposed. Triage the case again for a patch against the "
                       "current file.", 409)
    bound = binding(case, artifact, branch)
    if dry_run:
        return {"branch": branch, "base_branch": repo.get("branch") or "main", "repo": repo.get("name"), "path": path,
                "diff": artifact.get("diff") or unified_diff(path, current, str(content["new_content"])),
                "preview_token": make_token(bound, actor)}
    check_token(preview_token, bound, actor, replay=replay)
    number = rules.case_ref(case.get("case_number")) or branch
    payload = {"case_patch": {"case_id": case["case_id"], "artifact_id": artifact_id, "head": branch,
                              "title": f"{number}: {redact(str(case.get('title') or ''))[:150]}",
                              "body": pr_body(case, artifact)}}
    if publisher is None:
        raise FixError("GitHub publishing is not set up.", 409)
    result = publisher(payload) or {}
    status = str(result.get("status") or "FAILED")
    url = (result.get("pull_request") or {}).get("url")
    if status not in ("PUBLISHED", "NO_CHANGES"):
        store.event(case["case_id"], "pr_failed", actor, {"artifact_id": artifact_id, "status": status,
                                                          "detail": redact(str(result.get("detail") or ""))[:500]})
        raise FixError(redact(str(result.get("detail") or f"Publishing failed ({status})."))[:600], 502)
    if url:
        updated = {**content, "pr_url": url, "branch": branch}
        store.set_artifact(case["case_id"], artifact_id, content=updated)
        if store.add_link(case["case_id"], "PR", url, f"{number} {branch}", url, actor):
            store.event(case["case_id"], "link_added", actor, {"kind": "PR", "ref": url})
    store.event(case["case_id"], "pr_published", actor, {"artifact_id": artifact_id, "branch": branch, "status": status,
                                                         "pr_url": url})
    return {"pr_url": url, "branch": branch, "status": status}


def pr_body(case: Dict[str, Any], artifact: Dict[str, Any], base_url: str = "") -> str:
    content = artifact.get("content") or {}
    number = rules.case_ref(case.get("case_number")) or case["case_id"]
    link = f"{base_url.rstrip('/')}{rules.link_url('CASE', case['case_id'])}"
    lines = [f"Fix proposed for case **{number}**: {redact(str(case.get('title') or ''))[:200]}", "",
             f"- Case: {link}", f"- File: `{content.get('path')}`",
             f"- Why: {redact(str(content.get('rationale') or ''))[:1000]}", "",
             "Drafted by the platform's case triage and accepted by a person. Review it before merging."]
    return "\n".join(lines)


# ---------------------------------------------------------------- Jira summary

def jira_text(case: Dict[str, Any], artifacts: List[Dict[str, Any]]) -> str:
    """A case summary for Jira: classification, cause, counts of tests and fixes. No sample rows or data values."""
    ai = case.get("ai") if isinstance(case.get("ai"), dict) else {}
    number = rules.case_ref(case.get("case_number")) or "Case"
    tests = [a for a in artifacts if a.get("type") == "REPRO_TEST"]
    fixes = [a for a in artifacts if a.get("type") in FIX_TYPES and a.get("status") != "REJECTED"]
    passed = sum(1 for a in tests if (a.get("content") or {}).get("last_outcome") == "PASS")
    failed = sum(1 for a in tests if (a.get("content") or {}).get("last_outcome") in ("FAIL", "ERROR"))
    lines = [f"**{number}** is {case.get('status')} (severity {case.get('severity')})."]
    if ai.get("classification"):
        top = (ai.get("hypotheses") or [{}])[0] if ai.get("hypotheses") else {}
        lines.append(f"Triage: {ai['classification']} (confidence {float(ai.get('confidence') or 0):.1f}). "
                     f"{redact(str(ai.get('summary') or ''))[:600]}")
        if top.get("cause"):
            lines.append(f"Most likely cause: {redact(str(top['cause']))[:400]}")
        if ai.get("questions"):
            lines.append("Questions for the reporter:")
            lines += [f"- {redact(str(q))[:300]}" for q in ai["questions"][:5]]
    lines.append(f"Reproduction tests: {len(tests)} ({passed} passing, {failed} failing).")
    if fixes:
        lines.append("Fixes: " + ", ".join(f"{a.get('type')} {str(a.get('status')).lower()}" for a in fixes[:6]) + ".")
    if case.get("resolution"):
        lines.append(f"Resolution: {redact(str(case['resolution']))[:600]}")
    lines.append("Posted from the data platform. Sample rows are never included; open the case to see them.")
    return redact("\n".join(lines))[:4000]


# ---------------------------------------------------------------- knowledge on resolve

def resolution_content(case: Dict[str, Any], artifacts: List[Dict[str, Any]], links: List[Dict[str, Any]]) -> str:
    ai = case.get("ai") if isinstance(case.get("ai"), dict) else {}
    top = (ai.get("hypotheses") or [{}])[0] if ai.get("hypotheses") else {}
    cause = case.get("resolution") or top.get("cause") or "Not recorded."
    applied = [a for a in artifacts if a.get("type") in FIX_TYPES and a.get("status") == "APPLIED"]
    tests = [a for a in artifacts if a.get("type") == "REPRO_TEST" and a.get("status") == "ACCEPTED"]
    lines = [f"Symptom: {redact(str(case.get('title') or ''))[:300]}",
             (f"Details: {redact(str(case.get('description') or ''))[:800]}" if case.get("description") else ""),
             f"Cause: {redact(str(cause))[:1200]}"]
    if top.get("cause") and case.get("resolution"):
        lines.append(f"AI hypothesis: {redact(str(top['cause']))[:400]}")
    lines.append("Fix: " + ("; ".join(f"{a.get('type')} {redact(str(a.get('title') or ''))[:150]}: "
                                      f"{redact(str((a.get('content') or {}).get('rationale') or ''))[:300]}"
                                      for a in applied) or "no platform fix recorded"))
    if tests:
        lines.append("Reproduction tests: " + "; ".join(
            f"{redact(str(a.get('title') or ''))[:120]} ({(a.get('content') or {}).get('last_outcome') or 'not run'})"
            for a in tests))
    if links:
        lines.append("Links: " + ", ".join(f"{link.get('kind')} {link.get('ref')}" for link in links[:20]
                                           if link.get("kind") != "KNOWLEDGE"))
    return redact("\n".join(x for x in lines if x))


def on_resolved(source: Any, case: Dict[str, Any], actor: str, store: Any = None,
                remember: Optional[Callable[..., Optional[str]]] = None) -> Dict[str, Any]:
    """{case_resolution, qa_tests}: the knowledge proposed for the domain's stewards when a case is resolved."""
    from services.knowledge.writer import remember as write

    store = _store(source, store)
    write = remember or write
    sess = as_session(source)
    artifacts = store.artifacts(case["case_id"])
    for a in artifacts:
        a["content"] = a.get("content") if isinstance(a.get("content"), dict) else {}
    links = store.links(case["case_id"])
    number = rules.case_ref(case.get("case_number")) or case["case_id"]
    resolver = str(case.get("resolved_by") or actor or "").upper()
    applied = [a["artifact_id"] for a in artifacts if a.get("type") in FIX_TYPES and a.get("status") == "APPLIED"]
    passed = [a for a in artifacts if a.get("type") == "REPRO_TEST" and a.get("status") == "ACCEPTED"
              and a["content"].get("last_outcome") == "PASS" and a["content"].get("sql")]
    out: Dict[str, Any] = {"case_resolution": None, "qa_tests": []}
    out["case_resolution"] = write(
        sess, domain_id=case.get("domain_id"), kind="CASE_RESOLUTION", key=f"case.resolution.{case['case_id']}",
        title=redact(f"{number}: {case.get('title') or ''}")[:500], content=resolution_content(case, artifacts, links),
        content_json={"case_id": case["case_id"], "case_number": number, "resolved_by": resolver,
                      "classification": (case.get("ai") or {}).get("classification") if isinstance(case.get("ai"), dict) else None,
                      "applied_artifacts": applied, "repro_tests": [a["artifact_id"] for a in passed],
                      "links": [{"kind": link.get("kind"), "ref": link.get("ref")} for link in links[:20]]},
        tags=["case"], origin="CASE", change_note=f"Proposed when {number} was resolved", mode="review")
    for a in passed:
        c = a["content"]
        kid = write(sess, domain_id=case.get("domain_id"), kind="QA_TEST", key=f"case.test.{a['artifact_id']}",
                    title=redact(str(a.get("title") or "Reproduction test"))[:500],
                    content=redact(f"{c.get('sql')}\n-- expected: {c.get('expected') or ''}"),
                    content_json={"case_id": case["case_id"], "case_number": number, "resolved_by": resolver,
                                  "sql": c.get("sql"), "expected": c.get("expected"), "severity": c.get("severity"),
                                  "category": c.get("category"), "target_table_id": c.get("target_table_id")},
                    tags=["case", "repro"], origin="CASE", change_note=f"Reproduction test of {number}", mode="review")
        if kid:
            out["qa_tests"].append(kid)
    ids = [k for k in [out["case_resolution"], *out["qa_tests"]] if k]
    for kid in ids:
        store.add_link(case["case_id"], "KNOWLEDGE", kid, "Proposed knowledge", None, actor)
    store.event(case["case_id"], "knowledge_proposed", actor, {"case_resolution": out["case_resolution"],
                                                               "qa_tests": out["qa_tests"]})
    return out

