"""Snowflake dbt workspace: git integrations, allowed origins, branch skeleton, project object."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SAFE_QUOTED = re.compile(r"^[A-Za-z0-9_$-]+$")
OWNED_PREFIXES = (
    "models/staging/", "models/intermediate/", "models/marts/",
    "mappings/", "soda/", "release/",
)
TEXT_SUFFIXES = (".sql", ".yml", ".yaml", ".md", ".json", ".csv", ".txt", ".toml")


def safe_ident(name: str) -> str:
    """Quote Snowflake identifiers that are legal but not unquoted (hyphens, etc.)."""
    value = (name or "").strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 3:
        inner = value[1:-1]
        assert '"' not in inner and ";" not in inner and " " not in inner, f"unsafe identifier: {name}"
        assert SAFE_QUOTED.match(inner), f"unsafe identifier: {name}"
        return f'"{inner}"'
    if IDENT.match(value):
        return value
    assert SAFE_QUOTED.match(value), f"unsafe identifier: {name}"
    return f'"{value}"'


def safe_fqn(name: str) -> str:
    parts = [p for p in (name or "").split(".") if p]
    assert parts, "missing object name"
    return ".".join(safe_ident(p) for p in parts)


def origin_allowed(origin: str, prefixes: Iterable[str]) -> bool:
    url = (origin or "").strip().lower()
    allowed = [p.strip().lower() for p in prefixes if p and str(p).strip()]
    if not url:
        return False
    if not allowed:
        return True
    return any(url.startswith(p.rstrip("/")) for p in allowed)


def merge_skeleton(base_files: Dict[str, str], generated: Dict[str, str]) -> Dict[str, str]:
    """Keep existing repo files; STTM-owned paths from this run overwrite."""
    merged = dict(base_files or {})
    for path, content in (generated or {}).items():
        merged[path] = content
    return merged


def _lower_rows(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{str(k).lower(): v for k, v in (row or {}).items()} for row in raw]


def parse_integrations(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in _lower_rows(raw):
        name = str(row.get("name") or row.get("integration_name") or "").strip()
        kind = str(row.get("type") or row.get("category") or "").upper()
        if not name:
            continue
        if kind and "GIT" not in kind and "API" not in kind:
            continue
        out.append({
            "name": name,
            "type": kind or "API",
            "enabled": str(row.get("enabled") or "true").lower() in {"true", "y", "1"},
            "comment": row.get("comment") or "",
            "allowed_prefixes": [],
        })
    return out


def parse_allowed_prefixes(raw: Iterable[Dict[str, Any]]) -> List[str]:
    prefixes: List[str] = []
    for row in _lower_rows(raw):
        prop = str(row.get("property") or row.get("name") or "").upper()
        if "ALLOWED_PREFIX" not in prop and "ALLOWED_URL" not in prop:
            continue
        value = row.get("property_value") or row.get("value") or ""
        if isinstance(value, (list, tuple)):
            prefixes.extend(str(v) for v in value if v)
        else:
            text = str(value).strip().strip("[]")
            prefixes.extend(p.strip().strip("'\"") for p in text.split(",") if p.strip())
    return [p for p in prefixes if p]


def parse_git_repos(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in _lower_rows(raw):
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        database = str(row.get("database_name") or row.get("database") or "").strip()
        schema = str(row.get("schema_name") or row.get("schema") or "").strip()
        fqn = ".".join(p for p in (database, schema, name) if p)
        out.append({
            "name": name,
            "fqn": fqn,
            "origin": str(row.get("origin") or row.get("git_url") or "").strip(),
            "api_integration": str(row.get("api_integration") or "").strip(),
            "last_fetched": str(row.get("last_fetched_at") or row.get("last_fetched") or ""),
            "comment": row.get("comment") or "",
        })
    return out


def parse_dbt_projects(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in _lower_rows(raw):
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        database = str(row.get("database_name") or row.get("database") or "").strip()
        schema = str(row.get("schema_name") or row.get("schema") or "").strip()
        out.append({
            "name": name,
            "fqn": ".".join(p for p in (database, schema, name) if p),
            "comment": row.get("comment") or "",
        })
    return out


Execute = Callable[[str], List[Dict[str, Any]]]

BRANCH_PREFIXES = {"feat", "feature", "fix", "hotfix", "bugfix", "release", "chore", "dev"}


def parse_git_branches(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    out = []
    for row in _lower_rows(raw):
        name = str(row.get("name") or row.get("branch") or row.get("value") or "").strip()
        if not name or name.startswith("@"):
            continue
        out.append({
            "name": name,
            "commit": str(row.get("commit_hash") or row.get("sha") or row.get("commit") or ""),
            "last_modified": str(row.get("last_modified") or row.get("last_commit_time") or row.get("last_fetched") or ""),
            "author": str(row.get("author") or ""),
            "message": str(row.get("comment") or row.get("commit_message") or ""),
        })
    return _unique_branches(out)


def parse_listed_branches(raw: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    found: Dict[str, Dict[str, str]] = {}
    for row in _lower_rows(raw):
        path = str(row.get("name") or "").replace("\\", "/")
        if "/branches/" not in path:
            continue
        after = path.split("/branches/", 1)[1].strip("/")
        parts = [p for p in after.split("/") if p]
        if not parts:
            continue
        name = f"{parts[0]}/{parts[1]}" if parts[0].lower() in BRANCH_PREFIXES and len(parts) > 1 else parts[0]
        prev = found.get(name)
        modified = str(row.get("last_modified") or "")
        if not prev or modified > (prev.get("last_modified") or ""):
            found[name] = {"name": name, "commit": "", "last_modified": modified, "author": "", "message": ""}
    return _unique_branches(list(found.values()))


def _unique_branches(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_name: Dict[str, Dict[str, str]] = {}
    for item in items:
        by_name[item["name"]] = item
    return sorted(by_name.values(), key=lambda b: b.get("last_modified") or "", reverse=True)


def latest_branch(branches: List[Dict[str, str]], fallback: str = "main") -> str:
    dated = [b for b in branches if b.get("last_modified")]
    if dated:
        return dated[0]["name"]
    for preferred in ("main", "master", "develop"):
        if any(b["name"] == preferred for b in branches):
            return preferred
    return branches[0]["name"] if branches else fallback


def list_repo_branches(execute: Execute, repo_fqn: str, do_fetch: bool = True) -> Dict[str, Any]:
    """FETCH the Snowflake git repo, then list remote branches. Never raises."""
    try:
        repo = safe_fqn(repo_fqn)
    except Exception as exc:
        return {
            "repo": repo_fqn,
            "fetched": False,
            "fetch_warning": str(exc)[:400],
            "branches": [],
            "latest": "main",
        }
    fetched = False
    warning = ""
    if do_fetch:
        try:
            execute(f"ALTER GIT REPOSITORY {repo} FETCH")
            fetched = True
        except Exception as exc:
            warning = str(exc)[:400]
    branches: List[Dict[str, str]] = []
    for sql in (
        f"SHOW GIT BRANCHES IN GIT REPOSITORY {repo}",
        f"SHOW GIT BRANCHES IN {repo}",
    ):
        try:
            branches = parse_git_branches(execute(sql) or [])
            if branches:
                break
        except Exception as exc:
            warning = warning or str(exc)[:400]
    if not branches:
        try:
            branches = parse_listed_branches(execute(f"LIST @{repo}/branches/") or [])
        except Exception as exc:
            warning = warning or str(exc)[:400]
    latest = latest_branch(branches)
    return {
        "repo": repo,
        "fetched": fetched,
        "fetch_warning": warning,
        "branches": branches,
        "latest": latest,
    }


def discover(execute: Execute) -> Dict[str, Any]:
    """Read Snowflake git integrations, git repositories, and dbt projects. Never raises."""
    warnings: List[str] = []

    def try_show(sql: str) -> List[Dict[str, Any]]:
        try:
            return execute(sql) or []
        except Exception as exc:
            warnings.append(f"{sql}: {exc}")
            return []

    integrations = parse_integrations(try_show("SHOW INTEGRATIONS"))
    for item in integrations:
        try:
            desc = execute(f"DESC INTEGRATION {safe_ident(item['name'])}") or []
            item["allowed_prefixes"] = parse_allowed_prefixes(desc)
        except Exception as exc:
            warnings.append(f"DESC INTEGRATION {item['name']}: {exc}")
    repos: List[Dict[str, Any]] = []
    for sql in ("SHOW GIT REPOSITORIES IN ACCOUNT", "SHOW GIT REPOSITORIES IN DATABASE", "SHOW GIT REPOSITORIES"):
        repos = parse_git_repos(try_show(sql))
        if repos:
            break
    projects: List[Dict[str, Any]] = []
    for sql in ("SHOW DBT PROJECTS IN ACCOUNT", "SHOW DBT PROJECTS IN DATABASE", "SHOW DBT PROJECTS"):
        projects = parse_dbt_projects(try_show(sql))
        if projects:
            break
    return {
        "integrations": integrations,
        "git_repositories": repos,
        "dbt_projects": projects,
        "warnings": warnings,
        "capabilities": {
            "git_read": bool(repos),
            "git_write": bool(repos),
            "dbt_project": True,
        },
    }


def fetch_branch_files(session, repo_fqn: str, branch: str, limit: int = 80) -> Dict[str, str]:
    repo = safe_fqn(repo_fqn)
    branch_name = (branch or "main").strip().strip("/")
    assert re.fullmatch(r"[A-Za-z0-9._/\-]+", branch_name), f"unsafe branch: {branch}"
    try:
        session.sql(f"ALTER GIT REPOSITORY {repo} FETCH").collect()
    except Exception:
        pass
    listed = session.sql(f"LIST @{repo}/branches/{branch_name}/").collect()
    files: Dict[str, str] = {}
    for row in listed:
        raw = row.as_dict() if hasattr(row, "as_dict") else {"name": row[0]}
        name = str(raw.get("name") or raw.get("NAME") or "")
        rel = name.split(f"/branches/{branch_name}/", 1)[-1].lstrip("/")
        if not rel or not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        if len(files) >= limit:
            break
        try:
            chunks = session.sql(f"SELECT $1 FROM @{repo}/branches/{branch_name}/{rel}").collect()
            text = "\n".join(str(c[0]) for c in chunks if c[0] is not None)
            if text:
                files[rel] = text
        except Exception:
            continue
    return files


def create_dbt_project(session, project_fqn: str, stage_path: str, comment: str) -> Dict[str, Any]:
    name = safe_fqn(project_fqn)
    source = stage_path if stage_path.startswith("@") else f"@{stage_path}"
    assert re.fullmatch(r'@[A-Za-z0-9_./\-"]+', source.replace("'", "")), f"unsafe stage: {stage_path}"
    session.sql(
        f"CREATE OR REPLACE DBT PROJECT {name} FROM '{source}' "
        f"AUTO_COMPILE = FALSE DEFAULT_WRITEBACK = FALSE "
        f"COMMENT = '{comment.replace(chr(39), '')[:200]}'"
    ).collect()
    return {"dbt_project": name, "from": source, "status": "CREATED"}


def push_branch(session, repo_fqn: str, cut_branch: str, stage_path: str) -> Dict[str, Any]:
    repo = safe_fqn(repo_fqn)
    branch = (cut_branch or "").strip().strip("/")
    assert re.fullmatch(r"[A-Za-z0-9._/\-]+", branch), f"unsafe branch: {cut_branch}"
    source = stage_path if stage_path.startswith("@") else f"@{stage_path}"
    try:
        session.sql(f"ALTER GIT REPOSITORY {repo} FETCH").collect()
    except Exception as exc:
        return {"status": "FETCH_FAILED", "detail": str(exc)[:400]}
    try:
        session.sql(
            f"COPY FILES INTO @{repo}/branches/{branch}/ FROM {source} OVERWRITE = TRUE"
        ).collect()
        return {
            "status": "PUSHED",
            "repo": repo,
            "branch": branch,
            "from": source,
            "method": f"COPY FILES INTO @{repo}/branches/{branch}/",
            "pull_request": False,
        }
    except Exception as exc:
        return {
            "status": "STAGE_ONLY",
            "repo": repo,
            "branch": branch,
            "from": source,
            "method": f"COPY FILES INTO @{repo}/branches/{branch}/",
            "pull_request": False,
            "detail": str(exc)[:400],
        }
