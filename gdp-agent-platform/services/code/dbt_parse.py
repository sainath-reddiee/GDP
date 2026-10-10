"""Turn repository files into searchable chunks and lineage edges (pure, standard library only).

dbt-aware: dbt_project.yml locates model, macro, test and snapshot folders; a model becomes one chunk with its
ref()/source() targets and output columns; each {% macro %} is its own chunk; schema.yml entries (models, sources)
become chunks with their columns and tests. Other SQL is split by statement, Python by top-level def/class, Markdown
by heading. Secrets never get in: credential files are skipped and key/token assignments are redacted.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_FILE_BYTES = 400_000
MAX_CHUNK_LINES = 160
TEXT_SUFFIXES = (".sql", ".yml", ".yaml", ".md", ".py", ".txt", ".toml", ".jinja", ".j2")

SKIP_FILES = re.compile(
    r"(^|/)(profiles\.ya?ml|\.env(\..*)?|.*\.(pem|key|p12|pfx|crt)|id_rsa.*|credentials.*|secrets?\.(ya?ml|json|toml))$", re.I)
SKIP_DIRS = re.compile(r"(^|/)(\.git|node_modules|target|dbt_packages|dbt_modules|logs|venv|\.venv|__pycache__)/", re.I)
# key names ending in a credential word (db_password, AWS_SECRET_ACCESS_KEY, "client_secret"), then : or =, then a value
SECRET_LINE = re.compile(
    r"""(?im)(["']?[\w.\-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credentials?)["']?
         [ 	]*[:=][ 	]*)(["']?)([^\s"'#,}]{3,}|(?<=["'])[^"'
]{3,}(?=["']))""", re.X)
SECRET_TOKENS = re.compile(r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|glpat-[A-Za-z0-9_\-]{20,}"
                           r"|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9_\-]{20,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")
PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
URL_CREDENTIALS = re.compile(r"(://)[^/\s:@]+:[^@\s/]+@")
JINJA_VALUE = re.compile(r"^\{\{|env_var\s*\(|^\$\{|^%\(")

REF = re.compile(r"""ref\(\s*['"]([\w.\-]+)['"](?:\s*,\s*['"]([\w.\-]+)['"])?\s*\)""")
SOURCE = re.compile(r"""source\(\s*['"]([\w.\-]+)['"]\s*,\s*['"]([\w.\-]+)['"]\s*\)""")
MACRO = re.compile(r"\{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%\}(.*?)\{%-?\s*endmacro\s*-?%\}", re.S)
CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
ALIAS = re.compile(r"\bas\s+([A-Za-z_][\w$]*)\s*(?:,|\n|$)", re.I)
SQL_OBJECT = re.compile(
    r"\b(?:create\s+(?:or\s+replace\s+)?(?:(?:secure|transient|temporary|temp|materialized|dynamic|recursive)\s+)*"
    r"(?:table|view|procedure|function|stream|task|stage)\s+(?:if\s+not\s+exists\s+)?"
    r"|merge\s+into\s+|insert\s+(?:overwrite\s+)?into\s+|update\s+)([\w.\"$]+)", re.I)
JINJA_CALL = re.compile(r"\{\{[^}]*?\b([A-Za-z_]\w*)\s*\(", re.S)


def scrub(text: str) -> str:
    """Redact credential assignments, private key blocks, credentials in URLs and well-known token shapes. A value that
    is a reference rather than a secret ({{ env_var('X') }}, ${X}) is kept, since it tells the model how secrets flow."""
    text = PEM_BLOCK.sub("<redacted private key>", text)
    text = URL_CREDENTIALS.sub(lambda m: m.group(1) + "<redacted>@", text)

    def line(m: "re.Match[str]") -> str:
        value = m.group(3)
        if JINJA_VALUE.search(value.strip()) or value.strip().lower() in ("none", "null", "true", "false", "<redacted>"):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}<redacted>"
    text = SECRET_LINE.sub(line, text)
    return SECRET_TOKENS.sub("<redacted>", text)


def wanted(path: str, include: Optional[List[str]] = None, exclude: Optional[List[str]] = None) -> bool:
    p = path.replace("\\", "/").lstrip("/")
    if SKIP_FILES.search(p) or SKIP_DIRS.search("/" + p):
        return False
    if not p.lower().endswith(TEXT_SUFFIXES):
        return False
    if include and not any(fnmatch.fnmatch(p, g) for g in include):
        return False
    return not (exclude and any(fnmatch.fnmatch(p, g) for g in exclude))


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _paths(project_yml: str, key: str, default: List[str]) -> List[str]:
    m = re.search(rf"^{key}\s*:\s*\[(.*?)\]", project_yml or "", re.M)
    if m:
        return [p.strip().strip("'\"").strip("/") for p in m.group(1).split(",") if p.strip()]
    m = re.search(rf"^{key}\s*:\s*\n((?:\s+-\s*.+\n?)+)", project_yml or "", re.M)
    if m:
        return [ln.strip()[1:].strip().strip("'\"").strip("/") for ln in m.group(1).splitlines() if ln.strip().startswith("-")]
    return default


def projects(files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Every dbt project in the repository (a folder with dbt_project.yml) and its folders."""
    out = []
    for path, text in files.items():
        if path.split("/")[-1] != "dbt_project.yml":
            continue
        root = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        name = (re.search(r"^name\s*:\s*['\"]?([\w\-]+)", text, re.M) or [None, root.strip("/") or "dbt"])[1]
        out.append({"root": root, "name": name,
                    "models": _paths(text, "model-paths", ["models"]), "macros": _paths(text, "macro-paths", ["macros"]),
                    "tests": _paths(text, "test-paths", ["tests"]), "snapshots": _paths(text, "snapshot-paths", ["snapshots"])})
    return sorted(out, key=lambda p: -len(p["root"]))


def _role(path: str, dbt: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    for p in dbt:
        if not path.startswith(p["root"]):
            continue
        rel = path[len(p["root"]):]
        for kind, key in (("model", "models"), ("macro", "macros"), ("test", "tests"), ("snapshot", "snapshots")):
            if any(rel == d or rel.startswith(d + "/") for d in p[key]):
                return kind, p["name"]
        return "project", p["name"]
    return None, None


def _windows(text: str, start: int = 1) -> List[Tuple[int, int, str]]:
    lines = text.splitlines()
    out = []
    for i in range(0, max(1, len(lines)), MAX_CHUNK_LINES):
        part = lines[i:i + MAX_CHUNK_LINES]
        if any(ln.strip() for ln in part):
            out.append((start + i, start + i + len(part) - 1, "\n".join(part)))
    return out


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def output_columns(sql: str) -> List[str]:
    """Columns the final SELECT exposes (aliases and bare identifiers); good enough for retrieval, not a parser."""
    body = re.sub(r"\{#.*?#\}|--[^\n]*", "", sql, flags=re.S)
    selects = [m.start() for m in re.finditer(r"\bselect\b", body, re.I)]
    if not selects:
        return []
    tail = body[selects[-1] + 6:]
    end = re.search(r"\bfrom\b", tail, re.I)
    clause = tail[: end.start()] if end else tail
    cols = []
    for part in re.split(r",(?![^()]*\))", clause):
        part = part.strip()
        if not part or part == "*":
            continue
        alias = ALIAS.search(part + "\n")
        if alias:
            cols.append(alias.group(1))
        elif re.fullmatch(r"[\w.\"]+", part):
            cols.append(part.split(".")[-1].strip('"'))
    return [c.upper() for c in dict.fromkeys(cols)][:200]


def _model_chunk(path: str, text: str, project: Optional[str], kind: str) -> Dict[str, Any]:
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    refs = sorted({m.group(2) or m.group(1) for m in REF.finditer(text)})
    sources = sorted({f"{a}.{b}" for a, b in SOURCE.findall(text)})
    config = CONFIG.search(text)
    lines = text.count("\n") + 1
    return {"path": path, "start_line": 1, "end_line": lines, "kind": "DBT_MODEL" if kind != "snapshot" else "DBT_SNAPSHOT",
            "name": name, "text": text if lines <= MAX_CHUNK_LINES * 2 else "\n".join(text.splitlines()[:MAX_CHUNK_LINES * 2]),
            "refs": refs, "sources": sources, "columns": output_columns(text), "tests": [], "project": project,
            "materialized": (re.search(r"materialized\s*=\s*['\"](\w+)", config.group(1)) or [None, None])[1] if config else None}


def _macros(path: str, text: str, project: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for m in MACRO.finditer(text):
        out.append({"path": path, "start_line": _line_of(text, m.start()), "end_line": _line_of(text, m.end()),
                    "kind": "DBT_MACRO", "name": m.group(1), "text": m.group(0), "refs": [], "sources": [],
                    "columns": [], "tests": [], "project": project, "args": m.group(2).strip()})
    return out


def _yaml_entries(text: str) -> List[Dict[str, Any]]:
    """models:/sources: entries of a schema.yml without a YAML library: name, line span, columns and tests."""
    lines = text.splitlines()
    out: List[Dict[str, Any]] = []
    section = None
    current: Optional[Dict[str, Any]] = None
    item_indent = None
    for i, raw in enumerate(lines):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        top = re.match(r"^(models|sources|seeds|snapshots|exposures|metrics|semantic_models)\s*:", raw)
        if top:
            if current:
                current["end"] = i
                out.append(current)
                current = None
            section, item_indent = top.group(1), None
            continue
        item = re.match(r"^(\s*)-\s+name\s*:\s*['\"]?([\w\-.]+)", raw)
        if section and item and (item_indent is None or len(item.group(1)) <= item_indent):
            if current:
                current["end"] = i
                out.append(current)
            item_indent = len(item.group(1))
            current = {"section": section, "name": item.group(2), "start": i + 1, "end": None, "columns": [], "tests": [],
                       "tables": []}
            continue
        if current is None:
            continue
        col = re.match(r"^\s*-\s+name\s*:\s*['\"]?([\w\-.]+)", raw)
        if col and indent > (item_indent or 0):
            (current["tables"] if current["section"] == "sources" else current["columns"]).append(col.group(1).upper())
        for test in ("unique", "not_null", "accepted_values", "relationships", "dbt_utils\\.[\\w]+", "dbt_expectations\\.[\\w]+"):
            if re.search(rf"^\s*-?\s*{test}\b", raw):
                current["tests"].append(re.search(rf"{test}", raw).group(0))
    if current:
        current["end"] = len(lines)
        out.append(current)
    return out


def _schema_chunks(path: str, text: str, project: Optional[str]) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    out = []
    for e in _yaml_entries(text):
        body = "\n".join(lines[e["start"] - 1:e["end"]])
        kind = "DBT_SOURCE" if e["section"] == "sources" else "DBT_SCHEMA_YML"
        out.append({"path": path, "start_line": e["start"], "end_line": e["end"], "kind": kind, "name": e["name"],
                    "text": body, "refs": [], "sources": [f"{e['name']}.{t.lower()}" for t in e["tables"]] if kind == "DBT_SOURCE" else [],
                    "columns": e["columns"] + (e["tables"] if kind == "DBT_SOURCE" else []),
                    "tests": sorted(set(e["tests"])), "project": project})
    return out


def _sql_statements(path: str, text: str) -> List[Dict[str, Any]]:
    out = []
    offset = 0
    for stmt in re.split(r";\s*\n", text):
        start = _line_of(text, text.find(stmt, offset)) if stmt.strip() else 1
        offset += len(stmt)
        if not stmt.strip():
            continue
        name = (SQL_OBJECT.search(stmt) or [None, None])[1]
        for s, e, part in _windows(stmt, start):
            out.append({"path": path, "start_line": s, "end_line": e, "kind": "SQL", "name": (name or "").strip('"') or None,
                        "text": part, "refs": [], "sources": [], "columns": output_columns(part), "tests": [], "project": None})
    return out


def _python(path: str, text: str) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [{"path": path, "start_line": s, "end_line": e, "kind": "PY_FUNC", "name": None, "text": t, "refs": [],
                 "sources": [], "columns": [], "tests": [], "project": None} for s, e, t in _windows(text)]
    lines = text.splitlines()
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            for s, e, part in _windows("\n".join(lines[node.lineno - 1:end]), node.lineno):
                out.append({"path": path, "start_line": s, "end_line": e, "kind": "PY_FUNC", "name": node.name, "text": part,
                            "refs": [], "sources": [], "columns": [], "tests": [], "project": None})
    return out


def _markdown(path: str, text: str) -> List[Dict[str, Any]]:
    out = []
    heads = [m.start() for m in re.finditer(r"^#{1,3} ", text, re.M)] or [0]
    if heads[0] != 0:
        heads.insert(0, 0)
    for i, start in enumerate(heads):
        end = heads[i + 1] if i + 1 < len(heads) else len(text)
        part = text[start:end].strip()
        if not part:
            continue
        title = (re.match(r"#{1,3} (.+)", part) or [None, None])[1]
        for s, e, t in _windows(part, _line_of(text, start)):
            out.append({"path": path, "start_line": s, "end_line": e, "kind": "DOC", "name": title, "text": t, "refs": [],
                        "sources": [], "columns": [], "tests": [], "project": None})
    return out


def parse_file(path: str, text: str, dbt: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    text = scrub(text.replace("\r\n", "\n"))
    role, project = _role(path, dbt)
    lower = path.lower()
    if lower.endswith((".sql", ".jinja", ".j2")):
        if role == "macro":
            return _macros(path, text, project) or _sql_statements(path, text)
        if role in ("model", "snapshot"):
            return [_model_chunk(path, text, project, role)]
        if role == "test":
            return [{"path": path, "start_line": 1, "end_line": text.count("\n") + 1, "kind": "DBT_TEST",
                     "name": path.rsplit("/", 1)[-1].rsplit(".", 1)[0], "text": text, "refs": sorted({m.group(2) or m.group(1) for m in REF.finditer(text)}),
                     "sources": [], "columns": [], "tests": [], "project": project}]
        return _sql_statements(path, text)
    if lower.endswith((".yml", ".yaml")):
        if role and re.search(r"^(models|sources)\s*:", text, re.M):
            return _schema_chunks(path, text, project)
        return [{"path": path, "start_line": s, "end_line": e, "kind": "CONFIG", "name": path.rsplit("/", 1)[-1], "text": t,
                 "refs": [], "sources": [], "columns": [], "tests": [], "project": project} for s, e, t in _windows(text)]
    if lower.endswith(".py"):
        return _python(path, text)
    if lower.endswith(".md"):
        return _markdown(path, text)
    return [{"path": path, "start_line": s, "end_line": e, "kind": "DOC", "name": None, "text": t, "refs": [], "sources": [],
             "columns": [], "tests": [], "project": None} for s, e, t in _windows(text)]


def edges(chunks: Iterable[Dict[str, Any]], known_macros: Iterable[str] = ()) -> List[Dict[str, str]]:
    """REF and SOURCE lineage from models, MACRO_USE where a model or macro calls a macro defined in the repository
    (`known_macros`: macros already indexed from files that did not change)."""
    chunks = list(chunks)
    macros = {c["name"] for c in chunks if c["kind"] == "DBT_MACRO" and c.get("name")} | {m for m in known_macros if m}
    out: List[Dict[str, str]] = []
    for c in chunks:
        if c["kind"] in ("DBT_MODEL", "DBT_SNAPSHOT", "DBT_TEST"):
            out += [{"from_name": c["name"], "to_name": r, "kind": "REF", "path": c["path"], "chunk": c.get("chunk_id")}
                    for r in c.get("refs") or []]
            out += [{"from_name": c["name"], "to_name": s, "kind": "SOURCE", "path": c["path"], "chunk": c.get("chunk_id")}
                    for s in c.get("sources") or []]
        if c["kind"] in ("DBT_MODEL", "DBT_SNAPSHOT", "DBT_MACRO", "DBT_TEST") and macros:
            used = {m for m in JINJA_CALL.findall(c["text"]) if m in macros and m != c.get("name")}
            if c["kind"] == "DBT_MACRO":
                used |= {m for m in macros if m != c["name"] and re.search(rf"\b{re.escape(m)}\s*\(", c["text"])}
            out += [{"from_name": c["name"], "to_name": m, "kind": "MACRO_USE", "path": c["path"], "chunk": c.get("chunk_id")}
                    for m in sorted(used)]
    seen, unique = set(), []
    for e in out:
        key = (e["from_name"], e["to_name"], e["kind"])
        if key not in seen and e["from_name"] and e["to_name"]:
            seen.add(key)
            unique.append(e)
    return unique


def chunk_id(repo_id: str, c: Dict[str, Any]) -> str:
    return sha(f"{repo_id}|{c['path']}|{c['start_line']}|{c['kind']}|{c.get('name') or ''}")


def parse_repo(files: Dict[str, str], repo_id: str = "", dbt: Optional[List[Dict[str, Any]]] = None,
               known_macros: Iterable[str] = ()) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], List[Dict[str, Any]]]:
    """(chunks, edges, dbt projects) for files already filtered with `wanted`. `dbt` may come from the whole repository
    when only changed files are parsed."""
    dbt = dbt if dbt is not None else projects(files)
    chunks: List[Dict[str, Any]] = []
    for path in sorted(files):
        if len(files[path]) > MAX_FILE_BYTES:
            continue
        for c in parse_file(path, files[path], dbt):
            c["tokens"] = tokens(c["text"])
            c["chunk_id"] = chunk_id(repo_id, c)
            chunks.append(c)
    return chunks, edges(chunks, known_macros), dbt
