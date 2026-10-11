"""QA packs and Git (V042): which repository file a pack version descends from, what the deploy does when the
repository and the app disagree, and resolving such a conflict.

Every pack version carries REPO_PATH and REPO_SHA: for a version seeded from domain/<domain>/qa/<pack>.sql the file
and the hash of its text; for a version made in the app, the file and hash the edits started from (carried forward
from the version before). At deploy (infrastructure/seed_knowledge.seed_qa_packs) each repository file is compared
with the pack's newest version (sync_decision):

  SEED       the pack does not exist yet                         -> approved version 1 from the file
  UNCHANGED  the newest version came from this exact file        -> nothing to do
  UPDATE     the newest version came from the repository and the file changed -> a new approved REPO version
  AHEAD      the app edited the pack and the file is still the one the edits started from -> keep the app version
             (publish it to Git to bring the repository up to date)
  IN_SYNC    the file now holds what the app's newest version holds (its pull request was merged) -> record the hash
  CONFLICT   the file changed since the app edits and differs from them -> nothing is overwritten; a
             GOVERNANCE.QA_PACK_SYNC row records the repository version for someone to resolve: take the repository
             version as a new draft (reviewed and approved like any change), or keep the app version.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.common.sql import clip, rows

SYNC = "GOVERNANCE.QA_PACK_SYNC"


def repo_sha(text: str) -> str:
    """Hash of a pack file's text, insensitive to line endings and trailing spaces."""
    norm = "\n".join(line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_") or "pack"


def pack_path(domain_name: str, pack_name: str) -> str:
    return f"domain/{_slug(domain_name)}/qa/{_slug(pack_name)}.sql"


def signature(queries: List[Dict[str, Any]]) -> List[Tuple[str, str, str, str]]:
    return [(str(q["query_name"]).lower(), " ".join(str(q["sql_expression"]).split()), str(q.get("severity") or "ERROR"),
             str(q.get("expectation") or "0 rows")) for q in queries]


def sync_decision(newest: Optional[Dict[str, Any]], current_queries: List[Dict[str, Any]], file_sha: str,
                  file_queries: List[Dict[str, Any]]) -> str:
    """What the deploy does with a repository pack file, given the pack's newest version (None when the pack does
    not exist) and that version's queries."""
    if not newest:
        return "SEED"
    same_content = signature(current_queries) == signature(file_queries)
    if str(newest.get("source") or "").upper() == "REPO":
        return "UNCHANGED" if same_content else "UPDATE"
    if same_content:
        return "IN_SYNC"
    if newest.get("repo_sha") and newest["repo_sha"] == file_sha:
        return "AHEAD"
    return "CONFLICT"


# ---------------------------------------------------------------- Snowpark: conflicts


def _sync_row(r: Dict[str, Any]) -> Dict[str, Any]:
    s = {str(k).lower(): v for k, v in r.items()}
    for k in ("detected_at", "resolved_at"):
        s[k] = str(s[k])[:19] if s.get(k) else None
    return s


def open_conflicts(session, pack_id: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        found = rows(session, f"""SELECT SYNC_ID, PACK_ID, DOMAIN_ID, REPO_PATH, REPO_SHA, APP_VERSION, BASE_REPO_SHA, STATUS,
                                         DETECTED_AT FROM {SYNC} WHERE STATUS = 'OPEN'"""
                     + (" AND PACK_ID = ?" if pack_id else "") + " ORDER BY DETECTED_AT DESC", [pack_id] if pack_id else [])
    except Exception:  # before V042
        return []
    return [_sync_row(r) for r in found]


def conflict(session, pack_id: str) -> Optional[Dict[str, Any]]:
    """The pack's open conflict: the repository version parsed, the app's newest version, and what differs."""
    from services.qa import packs
    from services.qa.pack_files import parse

    found = rows(session, f"SELECT * FROM {SYNC} WHERE PACK_ID = ? AND STATUS = 'OPEN' ORDER BY DETECTED_AT DESC LIMIT 1",
                 [pack_id])
    if not found:
        return None
    s = _sync_row(found[0])
    repo = parse(s.get("repo_content") or "", default_name="Repository pack")
    repo_queries = [{**q, "ordinal": i + 1, "tags": packs.clean_tags(q.get("tags"))} for i, q in enumerate(repo["queries"])]
    app = packs.get_pack(session, pack_id)
    return {"sync": {k: v for k, v in s.items() if k != "repo_content"}, "repo": {**repo, "queries": repo_queries},
            "app": {"version": app["pack"]["version"], "status": app["pack"]["status"], "queries": app["queries"]},
            "diff": packs.diff(app["queries"], repo_queries)}


def _open(session, pack_id: str, sync_id: str) -> Dict[str, Any]:
    found = rows(session, f"SELECT * FROM {SYNC} WHERE SYNC_ID = ? AND PACK_ID = ?", [sync_id, pack_id])
    assert found, "conflict not found"
    s = _sync_row(found[0])
    assert s["status"] == "OPEN", "the conflict is already resolved"
    return s


def take_repo(session, pack_id: str, sync_id: str, user: str) -> Dict[str, Any]:
    """The repository version becomes the pack's newest DRAFT (submit and approve it like any change); the draft
    records the repository file it came from, so the next deploy sees the pack as in step."""
    from services.qa import packs
    from services.qa.pack_files import parse

    s = _open(session, pack_id, sync_id)
    repo = parse(s.get("repo_content") or "", default_name="Repository pack")
    newest = packs.get_pack(session, pack_id)
    payload = {"name": repo["name"] or newest["pack"]["name"], "description": repo.get("description") or "",
               "tags": repo.get("tags") or [], "base_version": newest["pack"]["version"],
               "queries": [{**q, "expectation": q.get("expectation") or "0 rows"} for q in repo["queries"]],
               "change_note": f"Repository version of {s.get('repo_path') or 'the pack file'} (conflict resolved by {user.upper()})",
               "repo_sha": s["repo_sha"], "repo_path": s.get("repo_path")}
    saved = packs.save_draft(session, pack_id, json.dumps(payload))
    session.sql(f"""UPDATE {SYNC} SET STATUS = 'RESOLVED', RESOLUTION = 'TAKE_REPO', DRAFT_VERSION = ?, RESOLVED_BY = ?,
                           RESOLVED_AT = CURRENT_TIMESTAMP() WHERE SYNC_ID = ?""",
                params=[int(saved["version"]), user.upper(), sync_id]).collect()
    return {"pack_id": pack_id, "sync_id": sync_id, "resolution": "TAKE_REPO", "draft_version": saved["version"],
            "diff": saved.get("diff")}


def keep_app(session, pack_id: str, sync_id: str, user: str) -> Dict[str, Any]:
    """Keep the app's version: its newest version now counts as descending from the current repository file, so the
    deploy treats the app as ahead until the file changes again (publish the pack to Git to bring the file up to
    date)."""
    s = _open(session, pack_id, sync_id)
    session.sql("""UPDATE GOVERNANCE.DOMAIN_QA_PACKS SET REPO_SHA = ?, REPO_PATH = COALESCE(REPO_PATH, NULLIF(?, ''))
                    WHERE PACK_ID = ? AND VERSION = (SELECT MAX(VERSION) FROM GOVERNANCE.DOMAIN_QA_PACKS WHERE PACK_ID = ?)""",
                params=[s["repo_sha"], s.get("repo_path") or "", pack_id, pack_id]).collect()
    session.sql(f"""UPDATE {SYNC} SET STATUS = 'RESOLVED', RESOLUTION = 'KEEP_APP', RESOLVED_BY = ?,
                           RESOLVED_AT = CURRENT_TIMESTAMP() WHERE SYNC_ID = ?""", params=[user.upper(), sync_id]).collect()
    return {"pack_id": pack_id, "sync_id": sync_id, "resolution": "KEEP_APP"}


def conflict_row(pack_id: str, domain_id: str, path: str, file_sha: str, content: str, app_version: int,
                 base_sha: Optional[str]) -> List[Any]:
    """Values for a new GOVERNANCE.QA_PACK_SYNC row (the deploy inserts it with its connector cursor)."""
    return [str(uuid.uuid4()), pack_id, domain_id, clip(path, 512), file_sha, content, int(app_version), base_sha or None]
