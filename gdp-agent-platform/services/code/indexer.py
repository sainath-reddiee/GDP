"""CODE.INDEX_REPO: refresh one repository's index from its Snowflake Git clone (Snowpark, standard library only).

FETCH the clone, list the branch, re-read only files whose blob hash changed, parse them (services/code/dbt_parse),
replace their chunks, edges and file rows, and record an INDEX_RUN. A repository row never holds credentials: the
GIT REPOSITORY object carries them through its secret.

Safety rules:
- one run at a time per repository: a lock on CODE.REPO (LOCK_RUN_ID) that expires after LOCK_MINUTES, so a run killed
  by a timeout never blocks the repository for good;
- a run writes only while it still holds the lock and the branch it started on is still the configured branch, so a
  disconnect or a branch switch during a run never leaves stale rows behind;
- a branch missing on the remote, or a listing that suddenly comes back empty, fails the run instead of deleting the
  index;
- files that cannot be read (unsafe path, unreadable bytes) are remembered with their hash and skipped until they change.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.code import dbt_parse
from services.common.sql import clip, insert_rows, rows, variant

MAX_FILES = 6000          # files considered per repository
MAX_READS_PER_RUN = 1500  # changed files read per pass; the entry point runs further passes until done
MAX_PASSES = 6
LOCK_MINUTES = 120
SAFE_PATH = re.compile(r"[\w./@+\-]+")  # paths are spliced into stage SQL; anything else is skipped, never read
SAFE_BRANCH = re.compile(r"[A-Za-z0-9._/\-]+")


class Cancelled(RuntimeError):
    """The repository was disconnected, switched branch or taken over by a newer run while this run was reading."""


def branch_segment(branch: str) -> str:
    """Stage path segment for a branch; names with a slash (feat/x) must be double quoted."""
    return f'"{branch}"' if "/" in branch else branch


def relative_path(listed_name: str, branch: str) -> str:
    """Path inside the branch from a LIST name such as repo/branches/main/models/a.sql or repo/branches/"feat/x"/a.sql."""
    after = listed_name.split("/branches/", 1)[-1]
    for prefix in (f'"{branch}"/', f"{branch}/"):
        if after.startswith(prefix):
            return after[len(prefix):].lstrip("/")
    return ""


def safe_path(rel: str) -> bool:
    return bool(SAFE_PATH.fullmatch(rel)) and ".." not in rel.split("/")


def _low(row: Any) -> Dict[str, Any]:
    raw = row.as_dict() if hasattr(row, "as_dict") else dict(row)
    return {str(k).lower(): v for k, v in raw.items()}


def _branch_files(session, repo_fqn: str, branch: str) -> Dict[str, Tuple[str, int]]:
    """{relative path: (hash, size)} for the branch."""
    out: Dict[str, Tuple[str, int]] = {}
    for row in session.sql(f"LIST @{repo_fqn}/branches/{branch_segment(branch)}/").collect():
        low = _low(row) if hasattr(row, "as_dict") else {"name": row[0], "size": row[1], "md5": row[2]}
        rel = relative_path(str(low.get("name") or ""), branch)
        if rel:
            # Git stages report sha1 (md5 is empty), so either serves as the change marker
            marker = low.get("sha1") or low.get("md5") or low.get("last_modified") or ""
            # the parser version is part of the stored hash, so a parser upgrade re-parses each file once (in passes)
            out[rel] = (f"{marker}#p{dbt_parse.PARSER_VERSION}" if marker else "", int(low.get("size") or 0))
    return out


def branches(session, repo_fqn: str) -> List[Dict[str, str]]:
    """Branches of the clone, as last fetched: [{name, commit}]."""
    out = []
    for row in session.sql(f"SHOW GIT BRANCHES IN GIT REPOSITORY {repo_fqn}").collect():
        low = _low(row)
        out.append({"name": str(low.get("name") or "").strip("/"), "commit": str(low.get("commit_hash") or "")})
    return out


def _read(session, repo_fqn: str, branch: str, rel: str) -> str:
    from services.dbt.workspace import read_repo_text

    read = lambda sql: [r[0] for r in session.sql(sql).collect()]  # noqa: E731
    return read_repo_text(read, repo_fqn, branch_segment(branch), rel)


def _arr(v: Any) -> List[str]:
    v = variant(v) if isinstance(v, str) else v
    return [str(x) for x in (v or [])]


def _changed_count(result: list) -> int:
    try:
        return int(result[0][0]) if result else 0
    except (TypeError, ValueError, IndexError):
        return 0


def _acquire(session, repo_id: str, run_id: str) -> bool:
    """Take the repository's index lock unless another live run holds it (an expired lock is taken over)."""
    done = session.sql(f"""UPDATE CODE.REPO SET LOCK_RUN_ID = ?, LOCKED_AT = CURRENT_TIMESTAMP(), STATUS = 'INDEXING', ERROR = NULL
                            WHERE REPO_ID = ? AND (LOCK_RUN_ID IS NULL
                                                   OR LOCKED_AT < DATEADD(minute, -{LOCK_MINUTES}, CURRENT_TIMESTAMP()))""",
                       params=[run_id, repo_id]).collect()
    return _changed_count(done) > 0


def _still_mine(session, repo_id: str, run_id: str, branch: str) -> None:
    found = rows(session, "SELECT LOCK_RUN_ID, BRANCH FROM CODE.REPO WHERE REPO_ID = ?", [repo_id])
    if not found:
        raise Cancelled("the repository was disconnected during the run")
    if found[0]["LOCK_RUN_ID"] != run_id:
        raise Cancelled("a newer run took over this repository")
    if str(found[0]["BRANCH"] or "").strip().strip("/") != branch:
        raise Cancelled(f"the branch changed from {branch} during the run; the next run indexes the new branch")


def _release(session, repo_id: str, run_id: str, status: str, error: Optional[str] = None, **fields: Any) -> None:
    # a None parameter would be bound as the text 'None': send '' and store NULL
    sets, params = ["LOCK_RUN_ID = NULL", "LOCKED_AT = NULL", "STATUS = ?", "ERROR = NULLIF(?, '')"], [status, error or ""]
    for column, value in fields.items():
        if column == "STATS":
            sets.append("STATS = PARSE_JSON(?)")
            value = json.dumps(value)
        elif column == "LAST_INDEXED_AT":
            sets.append("LAST_INDEXED_AT = CURRENT_TIMESTAMP()")
            continue
        else:
            sets.append(f"{column} = NULLIF(?, '')")
        params.append(value)
    session.sql(f"UPDATE CODE.REPO SET {', '.join(sets)} WHERE REPO_ID = ? AND LOCK_RUN_ID = ?",
                params=[*params, repo_id, run_id]).collect()


def _finish_run(session, run_id: str, status: str, started: float, error: Optional[str] = None, **counts: Any) -> None:
    session.sql("""UPDATE CODE.INDEX_RUN SET STATUS = ?, FINISHED_AT = CURRENT_TIMESTAMP(), ERROR = NULLIF(?, ''), DURATION_MS = ?,
                          COMMIT_SHA = NULLIF(?, ''), FILES_SEEN = ?, FILES_CHANGED = ?, FILES_REMOVED = ?, CHUNKS = ?, EDGES = ?
                    WHERE INDEX_RUN_ID = ?""",
                params=[status, error or "", int((time.time() - started) * 1000), counts.get("commit", ""), counts.get("seen", 0),
                        counts.get("changed", 0), counts.get("removed", 0), counts.get("chunks", 0), counts.get("edges", 0),
                        run_id]).collect()


def plan_changes(listed: Dict[str, Tuple[str, int]], known: Dict[str, str], budget: int = MAX_READS_PER_RUN
                 ) -> Tuple[List[str], List[str], int, List[str]]:
    """(paths to read, paths removed, how many changed paths are left for the next pass, the paths left over). A
    changed dbt_project.yml pulls in every file under its folder, since the project decides what each file means.
    The project file's new hash is stored in this pass, so the run marks the left-over paths stale (mark_stale): the
    next pass then re-reads them even though their own hash did not change."""
    removed = [p for p in known if p not in listed]
    changed = [p for p, (h, _) in listed.items() if known.get(p) != h or not h]
    roots = [p.rsplit("/", 1)[0] if "/" in p else "" for p in changed if p.split("/")[-1] == "dbt_project.yml"]
    if roots:
        extra = [p for p in listed if p not in changed and any(not r or p.startswith(r + "/") for r in roots)]
        changed = changed + extra
    # project files first, so a capped pass still knows the layout
    changed.sort(key=lambda p: (p.split("/")[-1] != "dbt_project.yml", p))
    return changed[:budget], removed, max(0, len(changed) - budget), changed[budget:]


def mark_stale(session, repo_id: str, paths: List[str]) -> None:
    """Forget the stored hash of files that still need a re-read (a capped fan-out or re-link), so the next pass
    picks them up as changed."""
    if paths:
        session.sql("UPDATE CODE.CODE_FILE SET FILE_HASH = NULL WHERE REPO_ID = ? AND ARRAY_CONTAINS(PATH::VARIANT, PARSE_JSON(?))",
                    params=[repo_id, json.dumps(paths)]).collect()


def index_repo(session, repo_id: str) -> Dict[str, Any]:
    from services.dbt.workspace import safe_fqn

    found = rows(session, "SELECT * FROM CODE.REPO WHERE REPO_ID = ?", [repo_id])
    if not found:
        return {"repo_id": repo_id, "skipped": True, "reason": "repository not found (disconnected)"}
    repo = {k.lower(): v for k, v in found[0].items()}
    run_id, started = str(uuid.uuid4()), time.time()
    if not _acquire(session, repo_id, run_id):
        return {"repo_id": repo_id, "skipped": True, "reason": "another index run is in progress"}
    session.sql("INSERT INTO CODE.INDEX_RUN (INDEX_RUN_ID, REPO_ID, STATUS) VALUES (?, ?, 'RUNNING')",
                params=[run_id, repo_id]).collect()
    branch = str(repo.get("branch") or "main").strip().strip("/")
    counts: Dict[str, Any] = {}
    try:
        fqn = safe_fqn(repo["git_repository"])
        if not SAFE_BRANCH.fullmatch(branch):
            raise ValueError(f"unsafe branch name: {branch}")
        session.sql(f"ALTER GIT REPOSITORY {fqn} FETCH").collect()
        remote = branches(session, fqn)
        head = next((b for b in remote if b["name"] == branch), None)
        if head is None:
            names = ", ".join(b["name"] for b in remote[:15]) or "none"
            raise RuntimeError(f"Branch '{branch}' is not on the remote (it may have been deleted or renamed). "
                               f"Pick another branch in Settings. Branches: {names}")
        commit = head["commit"]
        counts["commit"] = commit
        include, exclude = _arr(repo.get("include_globs")), _arr(repo.get("exclude_globs"))
        everything = _branch_files(session, fqn, branch)
        known = {r["PATH"]: r["FILE_HASH"] for r in rows(session, "SELECT PATH, FILE_HASH FROM CODE.CODE_FILE WHERE REPO_ID = ?",
                                                        [repo_id])}
        if not everything and known:
            raise RuntimeError(f"The clone listed no files on '{branch}'; the index was kept. Refresh again, or check the branch.")
        listed = {p: h for p, h in everything.items()
                  if dbt_parse.wanted(p, include or None, exclude or None) and h[1] <= dbt_parse.MAX_FILE_BYTES}
        listed = dict(sorted(listed.items())[:MAX_FILES])
        changed, removed, more, left = plan_changes(listed, known)

        texts: Dict[str, str] = {}
        skipped: Dict[str, str] = {}
        project_files = [p for p in listed if p.split("/")[-1] == "dbt_project.yml"]
        for p in dict.fromkeys(project_files + changed):
            if not safe_path(p):
                skipped[p] = "path has characters that cannot be read safely"
                continue
            try:
                texts[p] = _read(session, fqn, branch, p)
            except Exception as exc:  # unreadable file: remembered with its hash, retried only when it changes
                skipped[p] = clip(str(exc), 300)
        dbt = dbt_parse.projects({p: texts[p] for p in project_files if p in texts})
        touched = changed + removed
        def names(kind: str, outside: Optional[List[str]] = None) -> set:
            extra = " AND NOT ARRAY_CONTAINS(PATH::VARIANT, PARSE_JSON(?))" if outside is not None else ""
            return {r["NAME"] for r in rows(session, f"SELECT DISTINCT NAME FROM CODE.CODE_CHUNK WHERE REPO_ID = ? AND KIND = ?{extra}",
                                            [repo_id, kind, *([json.dumps(outside)] if outside is not None else [])]) if r["NAME"]}

        before = {"DBT_MACRO": names("DBT_MACRO"), "PY_FUNC": names("PY_FUNC")}
        kept = {"DBT_MACRO": names("DBT_MACRO", touched), "PY_FUNC": names("PY_FUNC", touched)}
        parse_now = {p: texts[p] for p in changed if p in texts}
        chunks, edges, _ = dbt_parse.parse_repo(parse_now, repo_id, dbt, kept["DBT_MACRO"], kept["PY_FUNC"])
        after = {k: kept[k] | {c["name"] for c in chunks if c["kind"] == k and c.get("name")} for k in kept}
        # a changed set of macros (or Python functions) changes what unchanged files call: re-link those files too
        suffixes = tuple(s for k, s in (("DBT_MACRO", ".sql"), ("PY_FUNC", ".py")) if after[k] != before[k] and (after[k] | before[k]))
        if suffixes:
            candidates = [p for p in listed if p.endswith(suffixes) and p not in parse_now and safe_path(p)]
            relink = candidates[:max(0, MAX_READS_PER_RUN - len(changed))]
            # re-links that do not fit this pass are marked stale below, so the next pass still re-reads them
            pending = set(left)
            relink_left = [p for p in candidates[len(relink):] if p not in pending]
            left += relink_left
            more += len(relink_left)
            for p in relink:
                try:
                    parse_now[p] = _read(session, fqn, branch, p)
                except Exception as exc:
                    skipped[p] = clip(str(exc), 300)
            touched += relink
            changed += relink
            chunks, edges, _ = dbt_parse.parse_repo(parse_now, repo_id, dbt, after["DBT_MACRO"], after["PY_FUNC"])

        _still_mine(session, repo_id, run_id, branch)
        # one transaction: readers never see a file's old rows deleted and its new rows missing, and a failure (or a
        # run taken over meanwhile) leaves the previous index as it was
        session.sql("BEGIN TRANSACTION").collect()
        try:
            for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE"):
                extra = " AND ORIGIN = 'PARSER'" if table == "CODE_EDGE" else ""
                session.sql(f"DELETE FROM CODE.{table} WHERE REPO_ID = ? AND ARRAY_CONTAINS(PATH::VARIANT, PARSE_JSON(?)){extra}",
                            params=[repo_id, json.dumps(touched)]).collect()
            # names are clipped to the column sizes in V024__code_context.sql
            insert_rows(session, "CODE.CODE_CHUNK",
                        ["CHUNK_ID", "REPO_ID", "PATH", "START_LINE", "END_LINE", "KIND", "NAME", "TEXT", "TOKENS", "REFS",
                         "SOURCES", "COLUMNS", "TESTS", "PROJECT", "COMMIT_SHA"],
                        ["?", "?", "?", "?::NUMBER", "?::NUMBER", "?", "NULLIF(?, '')", "?", "?::NUMBER", "PARSE_JSON(?)",
                         "PARSE_JSON(?)", "PARSE_JSON(?)", "PARSE_JSON(?)", "NULLIF(?, '')", "NULLIF(?, '')"],
                        [[c["chunk_id"], repo_id, c["path"], c["start_line"], c["end_line"], c["kind"], clip(c.get("name"), 512),
                          c["text"], c["tokens"], c.get("refs") or [], c.get("sources") or [], c.get("columns") or [],
                          c.get("tests") or [], clip(c.get("project"), 256), commit] for c in chunks])
            insert_rows(session, "CODE.CODE_EDGE",
                        ["REPO_ID", "FROM_CHUNK_ID", "FROM_NAME", "TO_NAME", "KIND", "PATH", "ORIGIN", "COMMIT_SHA"],
                        ["?", "NULLIF(?, '')", "?", "?", "?", "?", "'PARSER'", "NULLIF(?, '')"],
                        [[repo_id, e.get("chunk") or "", clip(e["from_name"], 512), clip(e["to_name"], 512), e["kind"], e["path"],
                          commit] for e in edges])
            file_rows = [[repo_id, p, p.rsplit(".", 1)[-1].lower() if "." in p else "", listed[p][0], listed[p][1], commit,
                          skipped.get(p, "")]
                         for p in dict.fromkeys(changed) if p in listed and (p in parse_now or p in skipped)]
            insert_rows(session, "CODE.CODE_FILE", ["REPO_ID", "PATH", "LANG", "FILE_HASH", "SIZE", "COMMIT_SHA", "SKIPPED_REASON"],
                        ["?", "?", "?", "?", "?::NUMBER", "NULLIF(?, '')", "NULLIF(?, '')"], file_rows)
            mark_stale(session, repo_id, [p for p in left if p not in parse_now and p not in skipped])
            # unchanged files are identical at the new commit, so citations point at the commit that was indexed
            for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE"):
                session.sql(f"UPDATE CODE.{table} SET COMMIT_SHA = NULLIF(?, '') WHERE REPO_ID = ? AND COMMIT_SHA IS DISTINCT FROM NULLIF(?, '')",
                            params=[commit, repo_id, commit]).collect()
            _still_mine(session, repo_id, run_id, branch)
            session.sql("COMMIT").collect()
        except Exception:
            session.sql("ROLLBACK").collect()
            raise
        stats = _stats(session, repo_id, dbt)
        stats["pending_files"] = more
        stats["skipped_files"] = len(skipped) + int(stats.pop("_skipped_known", 0))
        _summary(session, repo_id, commit, dbt)
        counts.update(seen=len(listed), changed=len(changed), removed=len(removed), chunks=len(chunks), edges=len(edges))
        _release(session, repo_id, run_id, "READY", None, LAST_COMMIT=commit, STATS=stats, LAST_INDEXED_AT=True)
        _finish_run(session, run_id, "SUCCEEDED", started, None, **counts)
        return {"repo_id": repo_id, "commit": commit, "files_seen": len(listed), "files_changed": len(changed),
                "files_removed": len(removed), "files_skipped": len(skipped), "chunks": len(chunks), "edges": len(edges),
                "stats": stats, "more_to_read": more}
    except Cancelled as exc:
        if not rows(session, "SELECT 1 FROM CODE.REPO WHERE REPO_ID = ?", [repo_id]):
            for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE", "REPO_SUMMARY"):
                session.sql(f"DELETE FROM CODE.{table} WHERE REPO_ID = ?", params=[repo_id]).collect()
        _release(session, repo_id, run_id, "NEW", None)
        _finish_run(session, run_id, "CANCELLED", started, str(exc), **counts)
        return {"repo_id": repo_id, "skipped": True, "reason": str(exc)}
    except Exception as exc:
        message = clip(str(exc), 4000)
        _release(session, repo_id, run_id, "FAILED", message)
        _finish_run(session, run_id, "FAILED", started, message, **counts)
        raise


def _stats(session, repo_id: str, dbt: List[Dict[str, Any]]) -> Dict[str, Any]:
    kinds = {r["KIND"]: int(r["N"]) for r in rows(session, "SELECT KIND, COUNT(*) AS N FROM CODE.CODE_CHUNK WHERE REPO_ID = ? GROUP BY 1", [repo_id])}
    files = rows(session, """SELECT COUNT_IF(SKIPPED_REASON IS NULL) AS N, COUNT_IF(SKIPPED_REASON IS NOT NULL) AS S,
                                    COUNT(DISTINCT IFF(SKIPPED_REASON IS NULL, LANG, NULL)) AS L
                               FROM CODE.CODE_FILE WHERE REPO_ID = ?""", [repo_id])[0]
    edges = rows(session, "SELECT COUNT(*) AS N FROM CODE.CODE_EDGE WHERE REPO_ID = ?", [repo_id])[0]
    return {"files": int(files["N"] or 0), "languages": int(files["L"] or 0), "chunks": sum(kinds.values()), "by_kind": kinds,
            "edges": int(edges["N"] or 0), "dbt_projects": [p["name"] for p in dbt],
            "dbt_project_roots": [{"name": p["name"], "root": p["root"].rstrip("/")} for p in dbt],
            "_skipped_known": int(files["S"] or 0)}


def _summary(session, repo_id: str, commit: str, dbt: List[Dict[str, Any]]) -> None:
    """The dbt shape of the repository: projects, model and macro counts, most-used macros, sources."""
    top = rows(session, """SELECT TO_NAME, COUNT(*) AS N FROM CODE.CODE_EDGE WHERE REPO_ID = ? AND KIND = 'MACRO_USE'
                            GROUP BY 1 ORDER BY N DESC LIMIT 15""", [repo_id])
    sources = rows(session, """SELECT DISTINCT TO_NAME FROM CODE.CODE_EDGE WHERE REPO_ID = ? AND KIND = 'SOURCE'
                               ORDER BY 1 LIMIT 100""", [repo_id])
    summary = {"projects": dbt, "top_macros": [{"name": r["TO_NAME"], "uses": int(r["N"])} for r in top],
               "sources": [r["TO_NAME"] for r in sources]}
    _put_summary(session, repo_id, "DBT", summary, commit)
    # the repository at a glance, from the code graph: languages, folders, dbt layers, hotspots, hard-coded tables
    from services.code import graph as code_graph

    files = rows(session, "SELECT PATH, LANG FROM CODE.CODE_FILE WHERE REPO_ID = ? AND SKIPPED_REASON IS NULL", [repo_id])
    kinds = {r["KIND"]: int(r["N"]) for r in rows(session, "SELECT KIND, COUNT(*) AS N FROM CODE.CODE_CHUNK WHERE REPO_ID = ? GROUP BY 1",
                                                     [repo_id])}
    g = code_graph.load(lambda sql, params: rows(session, sql, params), [repo_id])
    _put_summary(session, repo_id, "ARCHITECTURE",
                 code_graph.architecture([{"path": f["PATH"], "lang": f["LANG"]} for f in files], kinds, g, dbt), commit)


def _put_summary(session, repo_id: str, kind: str, summary: Dict[str, Any], commit: str) -> None:
    session.sql("""MERGE INTO CODE.REPO_SUMMARY T USING (SELECT ? AS REPO_ID, ? AS KIND) S
                     ON T.REPO_ID = S.REPO_ID AND T.KIND = S.KIND
                   WHEN MATCHED THEN UPDATE SET SUMMARY = PARSE_JSON(?), COMMIT_SHA = NULLIF(?, ''), UPDATED_AT = CURRENT_TIMESTAMP()
                   WHEN NOT MATCHED THEN INSERT (REPO_ID, KIND, SUMMARY, COMMIT_SHA) VALUES (S.REPO_ID, S.KIND, PARSE_JSON(?), NULLIF(?, ''))""",
                params=[repo_id, kind, json.dumps(summary, default=str), commit, json.dumps(summary, default=str), commit]).collect()


def index_repo_entry(session, repo_id: str) -> Dict[str, Any]:
    """Procedure handler: CALL CODE.INDEX_REPO('<repo id>'). Runs further passes while a large repository still has
    changed files left (each pass reads at most MAX_READS_PER_RUN files)."""
    result: Dict[str, Any] = {}
    for _ in range(MAX_PASSES):
        result = index_repo(session, repo_id)
        if result.get("skipped") or not result.get("more_to_read"):
            break
    return result
