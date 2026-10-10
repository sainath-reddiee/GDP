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
    """Read Snowflake git integrations, git repositories, and dbt projects. Never raises.

    Only GIT_HTTPS_API integrations are returned. `usable` means this role could DESCRIBE it (USAGE);
    a repository is usable when its integration is, otherwise `grant_sql` is the fix to show.
    """
    warnings: List[str] = []

    def try_show(sql: str) -> List[Dict[str, Any]]:
        try:
            return execute(sql) or []
        except Exception as exc:
            warnings.append(f"{sql}: {exc}")
            return []

    role_rows = try_show("SELECT CURRENT_ROLE() AS ROLE")
    role = str((_lower_rows(role_rows)[0].get("role") if role_rows else "") or "")
    integrations: List[Dict[str, Any]] = []
    for item in parse_integrations(try_show("SHOW API INTEGRATIONS") or try_show("SHOW INTEGRATIONS")):
        try:
            desc = execute(f"DESC INTEGRATION {quote_exact(item['name'])}") or []
        except Exception as exc:
            item.update(usable=False, provider="", detail=str(exc)[:300])
            integrations.append(item)
            continue
        info = parse_integration_desc(desc)
        if info["provider"] and info["provider"] != "GIT_HTTPS_API":
            continue
        item.update(usable=True, **info)
        integrations.append(item)
    usable = {i["name"].upper() for i in integrations if i.get("usable")}
    repos: List[Dict[str, Any]] = []
    for sql in ("SHOW GIT REPOSITORIES IN ACCOUNT", "SHOW GIT REPOSITORIES IN DATABASE", "SHOW GIT REPOSITORIES"):
        repos = parse_git_repos(try_show(sql))
        if repos:
            break
    for repo in repos:
        repo["usable"] = repo["api_integration"].upper() in usable
        repo["grant_sql"] = None if repo["usable"] else grant_sql(repo["api_integration"], role)
    projects: List[Dict[str, Any]] = []
    for sql in ("SHOW DBT PROJECTS IN ACCOUNT", "SHOW DBT PROJECTS IN DATABASE", "SHOW DBT PROJECTS"):
        projects = parse_dbt_projects(try_show(sql))
        if projects:
            break
    publisher = bool(try_show("SHOW PROCEDURES LIKE 'PUBLISH_DBT_PR' IN SCHEMA CODEGEN"))
    return {
        "role": role,
        "integrations": integrations,
        "git_repositories": repos,
        "dbt_projects": projects,
        "warnings": [w for w in warnings if not w.startswith("SHOW GIT REPOSITORIES IN")],
        "capabilities": {
            "git_read": any(r["usable"] for r in repos),
            # Snowflake git repository clones are read-only; branches/PRs go through the GitHub API.
            "git_write": publisher,
            "github_publish": publisher,
            "dbt_project": True,
        },
    }


def quote_exact(name: str) -> str:
    """Quote a name exactly as SHOW returned it, so mixed-case identifiers keep their case."""
    value = (name or "").strip().strip('"')
    assert value and '"' not in value and ";" not in value, f"unsafe identifier: {name}"
    return f'"{value}"'


def parse_integration_desc(raw: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    props = {str(r.get("property") or "").upper(): r.get("property_value") for r in _lower_rows(raw)}

    def listed(key: str) -> List[str]:
        text = str(props.get(key) or "").strip().strip("[]")
        return [p.strip().strip("'\"") for p in text.split(",") if p.strip()]

    return {
        "provider": str(props.get("API_PROVIDER") or "").upper(),
        "allowed_prefixes": parse_allowed_prefixes(raw),
        "allowed_secrets": listed("ALLOWED_AUTHENTICATION_SECRETS"),
        "enabled": str(props.get("ENABLED") or "true").lower() == "true",
    }


def grant_sql(integration: str, role: str) -> str:
    return f"GRANT USAGE ON INTEGRATION {quote_exact(integration)} TO ROLE {role or '<your_role>'};"


INTEGRATION_ERROR = re.compile(r"Integration '([^']+)'", re.I)


def grant_hint(error: str, role: str) -> Optional[str]:
    """Turn 'Insufficient privileges to operate on Integration X' into the GRANT that fixes it."""
    match = INTEGRATION_ERROR.search(error or "")
    if match and "privilege" in (error or "").lower():
        return grant_sql(match.group(1), role)
    return None


def read_repo_text(read: Callable[[str], List[Any]], repo: str, branch: str, rel: str) -> str:
    """Whole-file read via CODEGEN.RAW_TEXT_FORMAT; falls back to line reads where the format is missing."""
    path = f"@{repo}/branches/{branch}/{rel}"
    try:
        chunks = read(f"SELECT $1 FROM {path} (FILE_FORMAT => 'CODEGEN.RAW_TEXT_FORMAT')")
    except Exception:
        chunks = read(f"SELECT $1 FROM {path}")
    return "\n".join(str(c) for c in chunks if c is not None)


def branch_segment(branch: str) -> str:
    """Stage path segment for a branch: names with a slash (feat/x) must be double quoted."""
    return f'"{branch}"' if "/" in branch else branch


def clean_project_dir(project_dir: Optional[str]) -> str:
    """The dbt project's folder inside the repository ('' for the root)."""
    value = (project_dir or "").replace("\\", "/").strip().strip("/")
    assert not value or (re.fullmatch(r"[\w.\-/]+", value) and ".." not in value.split("/")), \
        f"invalid project folder: {project_dir}"
    return value


def branch_relative(listed_name: str, branch: str) -> str:
    """Path inside the branch from a LIST name: repo/branches/main/a.sql or repo/branches/"feat/x"/a.sql."""
    after = listed_name.split("/branches/", 1)[-1]
    for prefix in (f'"{branch}"/', f"{branch}/"):
        if after.startswith(prefix):
            return after[len(prefix):].lstrip("/")
    return ""


def skeleton_from_listing(names: List[str], branch: str, project_dir: str, read: Callable[[str], str],
                          limit: int = 120) -> Dict[str, str]:
    """{project-relative path: text} for the text files under the project folder. `read(path from branch root)`."""
    folder = clean_project_dir(project_dir)
    files: Dict[str, str] = {}
    for name in names:
        rel = branch_relative(name, branch)
        if folder:
            if not rel.startswith(folder + "/"):
                continue
            inner = rel[len(folder) + 1:]
        else:
            inner = rel
        if not inner or not inner.lower().endswith(TEXT_SUFFIXES):
            continue
        if len(files) >= limit:
            break
        try:
            text = read(rel)
            if text:
                files[inner] = text
        except Exception:
            continue
    return files


def fetch_branch_files(session, repo_fqn: str, branch: str, limit: int = 120, project_dir: str = "") -> Dict[str, str]:
    """The base branch's text files (the skeleton), relative to the dbt project folder."""
    repo = safe_fqn(repo_fqn)
    branch_name = (branch or "main").strip().strip("/")
    assert re.fullmatch(r"[A-Za-z0-9._/\-]+", branch_name), f"unsafe branch: {branch}"
    folder = clean_project_dir(project_dir)
    fetch_error = ""
    try:
        session.sql(f"ALTER GIT REPOSITORY {repo} FETCH").collect()
    except Exception as exc:  # a stale clone is still a usable skeleton
        fetch_error = str(exc)[:400]
    where = f"@{repo}/branches/{branch_segment(branch_name)}/" + (f"{folder}/" if folder else "")
    listed = session.sql(f"LIST {where}").collect()
    if not listed and fetch_error:
        raise RuntimeError(f"FETCH failed and the clone has no {branch_name} files: {fetch_error}")
    read = lambda sql: [r[0] for r in session.sql(sql).collect()]  # noqa: E731
    names = [str((row.as_dict() if hasattr(row, "as_dict") else {"name": row[0]}).get("name") or "") for row in listed]
    return skeleton_from_listing(names, branch_name, folder,
                                 lambda rel: read_repo_text(read, repo, branch_segment(branch_name), rel), limit)


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


def push_pending(plan: Dict[str, Any], publisher: bool) -> Dict[str, Any]:
    """Snowflake git clones are read-only; pushes happen in CODEGEN.PUBLISH_DBT_PR via the GitHub API."""
    if not plan.get("push"):
        return {"status": "NOT_REQUESTED"}
    return {
        "status": "PENDING_PUBLISH" if publisher else "NOT_CONFIGURED",
        "branch": plan.get("cut_branch"),
        "detail": ("Publishing the branch and pull request through the GitHub API."
                   if publisher else
                   "GitHub publishing is not set up. Snowflake git repositories are read-only from SQL; "
                   "set up CODEGEN.PUBLISH_DBT_PR to push branches and open pull requests."),
    }


DBT_STAGE = "CODEGEN.DBT_STAGE"
RUN_ID_TEXT = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def run_project_prefix(run_id: str, codegen_prefix: str = "GDP_") -> str:
    """Auto-named compile-only projects are CODEGEN.<standard prefix><first 18 hex of run id>_V<version>."""
    return codegen_prefix.upper() + run_id.replace("-", "")[:18].upper() + "_V"


def run_project_prefixes(run_id: str) -> list:
    from services.common.standard import PRESETS

    return sorted({run_project_prefix(run_id, p["codegen_prefix"]) for p in PRESETS.values()})


def run_workspace_cleanup(execute: Execute, run_id: str) -> Dict[str, Any]:
    """Remove a run's staged dbt workspace (models, compile target, STTM export) and its auto-named
    DBT PROJECT objects. Never raises; user-named projects and pushed git branches are left alone."""
    assert RUN_ID_TEXT.match(run_id or ""), f"not a run id: {run_id}"
    out: Dict[str, Any] = {"run_id": run_id, "files_removed": 0, "projects_dropped": [], "errors": []}
    try:
        out["files_removed"] = len(execute(f"REMOVE @{DBT_STAGE}/{run_id}/") or [])
    except Exception as exc:
        out["errors"].append(f"REMOVE @{DBT_STAGE}/{run_id}/: {str(exc)[:300]}")
    for prefix in run_project_prefixes(run_id):
        try:
            listed = _lower_rows(execute(f"SHOW DBT PROJECTS LIKE '{prefix}%' IN SCHEMA CODEGEN") or [])
        except Exception as exc:
            listed = []
            out["errors"].append(f"SHOW DBT PROJECTS: {str(exc)[:300]}")
        for row in listed:
            name = str(row.get("name") or "")
            if not name.upper().startswith(prefix):
                continue
            try:
                execute(f"DROP DBT PROJECT IF EXISTS CODEGEN.{quote_exact(name)}")
                out["projects_dropped"].append(name)
            except Exception as exc:
                out["errors"].append(f"DROP DBT PROJECT {name}: {str(exc)[:300]}")
    return out
