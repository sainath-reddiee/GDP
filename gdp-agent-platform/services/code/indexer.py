"""CODE.INDEX_REPO: refresh one repository's index from its Snowflake Git clone (Snowpark, standard library only).

FETCH the clone, list the branch, re-read only files whose stage hash changed, parse them (services/code/dbt_parse),
replace their chunks, edges and file rows, and record an INDEX_RUN. A repository row never holds credentials: the
GIT REPOSITORY object carries them through its secret.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Tuple

from services.code import dbt_parse
from services.common.sql import clip, insert_rows, rows, variant

MAX_FILES = 6000          # files considered per repository
MAX_READS_PER_RUN = 1500  # changed files read per run; the rest are picked up by the next refresh


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


def _branch_files(session, repo_fqn: str, branch: str) -> Dict[str, Tuple[str, int]]:
    """{relative path: (hash, size)} for the branch, filtered to text files."""
    listed = session.sql(f"LIST @{repo_fqn}/branches/{branch_segment(branch)}/").collect()
    out: Dict[str, Tuple[str, int]] = {}
    for row in listed:
        raw = row.as_dict() if hasattr(row, "as_dict") else {"name": row[0], "size": row[1], "md5": row[2]}
        low = {str(k).lower(): v for k, v in raw.items()}
        rel = relative_path(str(low.get("name") or ""), branch)
        if rel:
            # Git stages report sha1 (md5 is empty), so either serves as the change marker
            marker = low.get("sha1") or low.get("md5") or low.get("last_modified") or ""
            out[rel] = (str(marker), int(low.get("size") or 0))
    return out


def _commit(session, repo_fqn: str, branch: str) -> str:
    try:
        for row in session.sql(f"SHOW GIT BRANCHES IN GIT REPOSITORY {repo_fqn}").collect():
            low = {str(k).lower(): v for k, v in (row.as_dict() if hasattr(row, "as_dict") else {}).items()}
            if str(low.get("name") or "").strip("/") == branch:
                return str(low.get("commit_hash") or "")
    except Exception:
        pass
    return ""


def _read(session, repo_fqn: str, branch: str, rel: str) -> str:
    from services.dbt.workspace import read_repo_text

    read = lambda sql: [r[0] for r in session.sql(sql).collect()]  # noqa: E731
    return read_repo_text(read, repo_fqn, branch_segment(branch), rel)


def _arr(v: Any) -> List[str]:
    v = variant(v) if isinstance(v, str) else v
    return [str(x) for x in (v or [])]


def index_repo(session, repo_id: str) -> Dict[str, Any]:
    from services.dbt.workspace import safe_fqn

    found = rows(session, "SELECT * FROM CODE.REPO WHERE REPO_ID = ?", [repo_id])
    assert found, f"repository {repo_id} not found"
    repo = {k.lower(): v for k, v in found[0].items()}
    run_id, started = str(uuid.uuid4()), time.time()
    session.sql("INSERT INTO CODE.INDEX_RUN (INDEX_RUN_ID, REPO_ID, STATUS) VALUES (?, ?, 'RUNNING')",
                params=[run_id, repo_id]).collect()
    session.sql("UPDATE CODE.REPO SET STATUS = 'INDEXING', ERROR = NULL WHERE REPO_ID = ?", params=[repo_id]).collect()
    try:
        fqn = safe_fqn(repo["git_repository"])
        branch = str(repo.get("branch") or "main").strip().strip("/")
        assert re.fullmatch(r"[A-Za-z0-9._/\-]+", branch), f"unsafe branch: {branch}"
        session.sql(f"ALTER GIT REPOSITORY {fqn} FETCH").collect()
        commit = _commit(session, fqn, branch)
        include, exclude = _arr(repo.get("include_globs")), _arr(repo.get("exclude_globs"))
        listed = {p: h for p, h in _branch_files(session, fqn, branch).items()
                  if dbt_parse.wanted(p, include or None, exclude or None) and h[1] <= dbt_parse.MAX_FILE_BYTES}
        listed = dict(sorted(listed.items())[:MAX_FILES])
        known = {r["PATH"]: r["FILE_HASH"] for r in rows(session, "SELECT PATH, FILE_HASH FROM CODE.CODE_FILE WHERE REPO_ID = ?",
                                                        [repo_id])}
        removed = [p for p in known if p not in listed]
        changed = [p for p, (h, _) in listed.items() if known.get(p) != h or not h][:MAX_READS_PER_RUN]
        # dbt_project.yml decides what every file means, so it is always read
        project_files = [p for p in listed if p.split("/")[-1] == "dbt_project.yml"]
        texts: Dict[str, str] = {}
        for p in dict.fromkeys(project_files + changed):
            try:
                texts[p] = _read(session, fqn, branch, p)
            except Exception:
                continue
        dbt = dbt_parse.projects({p: texts[p] for p in project_files if p in texts})
        macros = [r["NAME"] for r in rows(session, """SELECT DISTINCT NAME FROM CODE.CODE_CHUNK
                                                      WHERE REPO_ID = ? AND KIND = 'DBT_MACRO' AND NOT ARRAY_CONTAINS(PATH::VARIANT, PARSE_JSON(?))""",
                                          [repo_id, json.dumps(changed + removed)])]
        chunks, edges, _ = dbt_parse.parse_repo({p: texts[p] for p in changed if p in texts}, repo_id, dbt, macros)
        touched = json.dumps(changed + removed)
        for table in ("CODE_CHUNK", "CODE_EDGE", "CODE_FILE"):
            extra = " AND ORIGIN = 'PARSER'" if table == "CODE_EDGE" else ""
            session.sql(f"DELETE FROM CODE.{table} WHERE REPO_ID = ? AND ARRAY_CONTAINS(PATH::VARIANT, PARSE_JSON(?)){extra}",
                        params=[repo_id, touched]).collect()
        insert_rows(session, "CODE.CODE_CHUNK",
                    ["CHUNK_ID", "REPO_ID", "PATH", "START_LINE", "END_LINE", "KIND", "NAME", "TEXT", "TOKENS", "REFS",
                     "SOURCES", "COLUMNS", "TESTS", "PROJECT", "COMMIT_SHA"],
                    ["?", "?", "?", "?::NUMBER", "?::NUMBER", "?", "NULLIF(?, '')", "?", "?::NUMBER", "PARSE_JSON(?)",
                     "PARSE_JSON(?)", "PARSE_JSON(?)", "PARSE_JSON(?)", "NULLIF(?, '')", "NULLIF(?, '')"],
                    [[c["chunk_id"], repo_id, c["path"], c["start_line"], c["end_line"], c["kind"], c.get("name") or "",
                      c["text"], c["tokens"], c.get("refs") or [], c.get("sources") or [], c.get("columns") or [],
                      c.get("tests") or [], c.get("project") or "", commit] for c in chunks])
        insert_rows(session, "CODE.CODE_EDGE",
                    ["REPO_ID", "FROM_CHUNK_ID", "FROM_NAME", "TO_NAME", "KIND", "PATH", "ORIGIN", "COMMIT_SHA"],
                    ["?", "NULLIF(?, '')", "?", "?", "?", "?", "'PARSER'", "NULLIF(?, '')"],
                    [[repo_id, e.get("chunk") or "", e["from_name"], e["to_name"], e["kind"], e["path"], commit] for e in edges])
        insert_rows(session, "CODE.CODE_FILE", ["REPO_ID", "PATH", "LANG", "FILE_HASH", "SIZE", "COMMIT_SHA"],
                    ["?", "?", "?", "?", "?::NUMBER", "NULLIF(?, '')"],
                    [[repo_id, p, p.rsplit(".", 1)[-1].lower(), listed[p][0], listed[p][1], commit] for p in changed if p in texts])
        stats = _stats(session, repo_id, dbt)
        session.sql("""UPDATE CODE.REPO SET STATUS = 'READY', LAST_COMMIT = NULLIF(?, ''), LAST_INDEXED_AT = CURRENT_TIMESTAMP(),
                              STATS = PARSE_JSON(?), ERROR = NULL WHERE REPO_ID = ?""",
                    params=[commit, json.dumps(stats), repo_id]).collect()
        _summary(session, repo_id, commit, dbt)
        result = {"repo_id": repo_id, "commit": commit, "files_seen": len(listed), "files_changed": len(changed),
                  "files_removed": len(removed), "chunks": len(chunks), "edges": len(edges), "stats": stats,
                  "more_to_read": max(0, len([p for p, (h, _) in listed.items() if known.get(p) != h]) - len(changed))}
        session.sql("""UPDATE CODE.INDEX_RUN SET STATUS = 'SUCCEEDED', FINISHED_AT = CURRENT_TIMESTAMP(), COMMIT_SHA = NULLIF(?, ''),
                              FILES_SEEN = ?, FILES_CHANGED = ?, FILES_REMOVED = ?, CHUNKS = ?, EDGES = ?, DURATION_MS = ?
                        WHERE INDEX_RUN_ID = ?""",
                    params=[commit, len(listed), len(changed), len(removed), len(chunks), len(edges),
                            int((time.time() - started) * 1000), run_id]).collect()
        return result
    except Exception as exc:
        message = clip(str(exc), 4000)
        session.sql("UPDATE CODE.REPO SET STATUS = 'FAILED', ERROR = ? WHERE REPO_ID = ?", params=[message, repo_id]).collect()
        session.sql("""UPDATE CODE.INDEX_RUN SET STATUS = 'FAILED', FINISHED_AT = CURRENT_TIMESTAMP(), ERROR = ?, DURATION_MS = ?
                        WHERE INDEX_RUN_ID = ?""", params=[message, int((time.time() - started) * 1000), run_id]).collect()
        raise


def _stats(session, repo_id: str, dbt: List[Dict[str, Any]]) -> Dict[str, Any]:
    kinds = {r["KIND"]: int(r["N"]) for r in rows(session, "SELECT KIND, COUNT(*) AS N FROM CODE.CODE_CHUNK WHERE REPO_ID = ? GROUP BY 1", [repo_id])}
    files = rows(session, "SELECT COUNT(*) AS N, COUNT(DISTINCT LANG) AS L FROM CODE.CODE_FILE WHERE REPO_ID = ?", [repo_id])[0]
    edges = rows(session, "SELECT COUNT(*) AS N FROM CODE.CODE_EDGE WHERE REPO_ID = ?", [repo_id])[0]
    return {"files": int(files["N"]), "languages": int(files["L"]), "chunks": sum(kinds.values()), "by_kind": kinds,
            "edges": int(edges["N"]), "dbt_projects": [p["name"] for p in dbt]}


def _summary(session, repo_id: str, commit: str, dbt: List[Dict[str, Any]]) -> None:
    """The dbt shape of the repository: projects, model and macro counts, most-used macros, sources."""
    top = rows(session, """SELECT TO_NAME, COUNT(*) AS N FROM CODE.CODE_EDGE WHERE REPO_ID = ? AND KIND = 'MACRO_USE'
                            GROUP BY 1 ORDER BY N DESC LIMIT 15""", [repo_id])
    sources = rows(session, """SELECT DISTINCT TO_NAME FROM CODE.CODE_EDGE WHERE REPO_ID = ? AND KIND = 'SOURCE'
                               ORDER BY 1 LIMIT 100""", [repo_id])
    summary = {"projects": dbt, "top_macros": [{"name": r["TO_NAME"], "uses": int(r["N"])} for r in top],
               "sources": [r["TO_NAME"] for r in sources]}
    session.sql("""MERGE INTO CODE.REPO_SUMMARY T USING (SELECT ? AS REPO_ID, 'DBT' AS KIND) S
                     ON T.REPO_ID = S.REPO_ID AND T.KIND = S.KIND
                   WHEN MATCHED THEN UPDATE SET SUMMARY = PARSE_JSON(?), COMMIT_SHA = NULLIF(?, ''), UPDATED_AT = CURRENT_TIMESTAMP()
                   WHEN NOT MATCHED THEN INSERT (REPO_ID, KIND, SUMMARY, COMMIT_SHA) VALUES (S.REPO_ID, S.KIND, PARSE_JSON(?), NULLIF(?, ''))""",
                params=[repo_id, json.dumps(summary), commit, json.dumps(summary), commit]).collect()


def index_repo_entry(session, repo_id: str) -> Dict[str, Any]:
    """Procedure handler: CALL CODE.INDEX_REPO('<repo id>')."""
    return index_repo(session, repo_id)
