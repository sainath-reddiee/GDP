"""Turn repository files into searchable chunks and lineage edges (pure, standard library only).

dbt-aware: dbt_project.yml locates model, macro, test and snapshot folders; a model becomes one chunk with its
ref()/source() targets and output columns; each {% macro %} is its own chunk; schema.yml entries (models, sources)
become chunks with their columns and tests. Other SQL is split by statement, Python by top-level def/class, Markdown
by heading. Secrets never get in: credential files are skipped and key/token assignments are redacted.

Edges make the code graph (services/code/graph.py): REF and SOURCE (dbt lineage), MACRO_USE, CALLS and IMPORTS
(Python), READS and WRITES (tables a SQL statement or a model reads with a hard-coded name, and objects it writes).
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_FILE_BYTES = 400_000
# bump when chunks or edges change shape: the next refresh of every repository re-parses all its files once
PARSER_VERSION = 3
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
TABLE_NAME = r"((?:\"[^\"]+\"|[A-Za-z_][\w$]*)(?:\.(?:\"[^\"]+\"|[A-Za-z_][\w$]*)){0,2})"
READ_FROM = re.compile(rf"\b(?:from|join|using)\s+{TABLE_NAME}", re.I)
WRITE_TO = re.compile(
    rf"\b(?:create\s+(?:or\s+replace\s+)?(?:(?:secure|transient|temporary|temp|materialized|dynamic)\s+)*(?:table|view)\s+"
    rf"(?:if\s+not\s+exists\s+)?|insert\s+(?:overwrite\s+)?into\s+|merge\s+into\s+|update\s+|delete\s+from\s+){TABLE_NAME}", re.I)
CTE_NAME = re.compile(r"(?:\bwith\s+(?:recursive\s+)?|,\s*)([A-Za-z_][\w$]*)\s+as\s*\(", re.I)
NOT_TABLES = {"select", "lateral", "table", "values", "unnest", "flatten", "dual", "information_schema", "identifier",
              "generator", "result_scan", "the", "a", "an", "set", "where", "as", "on", "jinja_expr"}
SQL_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|\{%.*?%\}|\{#.*?#\}|'(?:[^']|'')*'", re.S)
JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.S)


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


def table_refs(sql: str) -> Tuple[List[str], List[str]]:
    """(tables read with a hard-coded name, objects written). Jinja, comments, strings and CTE names are ignored, so a
    dbt model's ref()/source() never show up here: what remains in a model is a hard-coded table."""
    # a {{ ref() }} becomes a placeholder table, so its alias is never taken for a table name
    clean = SQL_NOISE.sub(" ", JINJA_EXPR.sub(" jinja_expr ", sql))
    ctes = {m.lower() for m in CTE_NAME.findall(clean)}
    writes = sorted({w.replace('"', "") for w in WRITE_TO.findall(clean) if w.lower() not in NOT_TABLES})
    reads = set()
    for name in READ_FROM.findall(clean):
        bare = name.replace('"', "")
        last = bare.split(".")[-1].lower()
        if last in NOT_TABLES or bare.lower() in ctes or bare in writes:
            continue
        reads.add(bare)
    return sorted(reads), writes


def module_name(path: str) -> str:
    """services/code/graph.py -> services.code.graph; pkg/__init__.py -> pkg."""
    stem = path[:-3] if path.endswith(".py") else path
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _model_chunk(path: str, text: str, project: Optional[str], kind: str) -> Dict[str, Any]:
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    refs = sorted({m.group(2) or m.group(1) for m in REF.finditer(text)})
    sources = sorted({f"{a}.{b}" for a, b in SOURCE.findall(text)})
    config = CONFIG.search(text)
    lines = text.count("\n") + 1
    return {"path": path, "start_line": 1, "end_line": lines, "kind": "DBT_MODEL" if kind != "snapshot" else "DBT_SNAPSHOT",
            "name": name, "text": text if lines <= MAX_CHUNK_LINES * 2 else "\n".join(text.splitlines()[:MAX_CHUNK_LINES * 2]),
            "refs": refs, "sources": sources, "columns": output_columns(text), "tests": [], "project": project,
            "reads": table_refs(text)[0],
            "materialized": (re.search(r"materialized\s*=\s*['\"](\w+)", config.group(1)) or [None, None])[1] if config else None}


def _macros(path: str, text: str, project: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    for m in MACRO.finditer(text):
        out.append({"path": path, "start_line": _line_of(text, m.start()), "end_line": _line_of(text, m.end()),
                    "kind": "DBT_MACRO", "name": m.group(1), "text": m.group(0), "refs": [], "sources": [],
                    "columns": [], "tests": [], "project": project, "args": m.group(2).strip()})
    return out


TEST_NAMES = ("unique", "not_null", "accepted_values", "relationships", r"dbt_utils\.\w+", r"dbt_expectations\.\w+",
              r"elementary\.\w+")
TEST_LINE = re.compile(r"^\s*-?\s*(" + "|".join(TEST_NAMES) + r")\b")
NAME_ITEM = re.compile(r"^(\s*)-\s+name\s*:\s*['\"]?([\w\-.]+)")
INLINE_TESTS = re.compile(r"^\s*(?:data_)?tests\s*:\s*\[(.*)\]")
TEST_NAME = re.compile(r"^(" + "|".join(TEST_NAMES) + r")$")


def _yaml_entries(text: str) -> List[Dict[str, Any]]:
    """models:/sources: entries of a schema.yml without a YAML library: name, line span, columns, tests (one per
    definition: 'not_null:ORDER_ID' for a column test, 'unique_combination_of_columns' for a model test) and, for
    sources, their tables ('raw.orders') kept apart from the tables' columns."""
    lines = text.splitlines()
    out: List[Dict[str, Any]] = []
    section = None
    current: Optional[Dict[str, Any]] = None
    item_indent = None
    child_indent = None   # first nested "- name" level: columns of a model, tables of a source
    col, col_indent, table = None, None, None
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
        item = NAME_ITEM.match(raw)
        if section and item and (item_indent is None or len(item.group(1)) <= item_indent):
            if current:
                current["end"] = i
                out.append(current)
            item_indent = len(item.group(1))
            current = {"section": section, "name": item.group(2), "start": i + 1, "end": None, "columns": [], "tests": [],
                       "tables": [], "described": False}
            child_indent, col, col_indent, table = None, None, None, None
            continue
        if current is None:
            continue
        if col is not None and indent <= (col_indent or 0) and not item:
            col = None  # left the column's block
        if item and indent > (item_indent or 0):
            name = item.group(2).upper()
            if child_indent is None or indent < child_indent:
                child_indent = indent
            if current["section"] == "sources" and indent == child_indent:
                table, col = name, None
                current["tables"].append(name)
            else:
                col, col_indent = name, indent
                current["columns"].append(name)
            continue
        if re.match(r"^\s*description\s*:", raw) and indent <= (item_indent or 0) + 2:
            current["described"] = True
        where = f"{table}.{col}" if (table and col) else (col or table)
        inline = INLINE_TESTS.match(raw)
        if inline:  # tests: [unique, not_null]
            for name in (x.strip().strip("'\"") for x in inline.group(1).split(",")):
                if TEST_NAME.match(name):
                    current["tests"].append(f"{name}:{where}" if where else name)
            continue
        test = TEST_LINE.match(raw)
        if test:
            current["tests"].append(f"{test.group(1)}:{where}" if where else test.group(1))
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
                    "columns": list(dict.fromkeys(e["columns"])), "tests": list(dict.fromkeys(e["tests"])), "project": project,
                    "described": e["described"]})
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
        reads, writes = table_refs(stmt)
        for i, (s, e, part) in enumerate(_windows(stmt, start)):
            out.append({"path": path, "start_line": s, "end_line": e, "kind": "SQL", "name": (name or "").strip('"') or None,
                        "text": part, "refs": [], "sources": [], "columns": output_columns(part), "tests": [], "project": None,
                        "reads": reads if i == 0 else [], "writes": writes if i == 0 else []})
    return out


def _python(path: str, text: str) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [{"path": path, "start_line": s, "end_line": e, "kind": "PY_FUNC", "name": None, "text": t, "refs": [],
                 "sources": [], "columns": [], "tests": [], "project": None} for s, e, t in _windows(text)]
    lines = text.splitlines()
    module = module_name(path)
    imports = sorted(python_imports(tree, module))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            calls = sorted(python_calls(node) - {node.name})
            for i, (s, e, part) in enumerate(_windows("\n".join(lines[node.lineno - 1:end]), node.lineno)):
                out.append({"path": path, "start_line": s, "end_line": e, "kind": "PY_FUNC", "name": node.name, "text": part,
                            "refs": [], "sources": [], "columns": [], "tests": [], "project": None, "module": module,
                            "calls": calls if i == 0 else [], "imports": imports if not out else []})
    return out


def python_calls(node: ast.AST) -> set:
    """Names a function or class calls: f(), obj.f(), module.f()."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name):
                found.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                found.add(fn.attr)
    return found


def python_imports(tree: ast.AST, module: str) -> set:
    """Modules a file imports, with relative imports resolved against the file's package."""
    package = module.rsplit(".", 1)[0] if "." in module else ""
    found = set()
    for sub in ast.walk(tree):
        if isinstance(sub, ast.Import):
            found |= {a.name for a in sub.names}
        elif isinstance(sub, ast.ImportFrom):
            base = sub.module or ""
            if sub.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (sub.level - 1)] if sub.level > 1 else parts
                base = ".".join([*parts, base] if base else parts)
            if base:
                found.add(base)
    return found


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


def edges(chunks: Iterable[Dict[str, Any]], known_macros: Iterable[str] = (),
          known_defs: Iterable[str] = ()) -> List[Dict[str, str]]:
    """REF and SOURCE lineage from models, MACRO_USE where a model or macro calls a macro defined in the repository,
    CALLS between Python functions and classes of the repository and IMPORTS of modules, READS of hard-coded tables and
    WRITES of objects. `known_macros` / `known_defs`: names already indexed from files that did not change."""
    chunks = list(chunks)
    macros = {c["name"] for c in chunks if c["kind"] == "DBT_MACRO" and c.get("name")} | {m for m in known_macros if m}
    defs = {c["name"] for c in chunks if c["kind"] == "PY_FUNC" and c.get("name")} | {d for d in known_defs if d}
    out: List[Dict[str, str]] = []
    for c in chunks:
        if c["kind"] == "PY_FUNC" and c.get("name"):
            out += [{"from_name": c["name"], "to_name": f, "kind": "CALLS", "path": c["path"], "chunk": c.get("chunk_id")}
                    for f in c.get("calls") or [] if f in defs]
            out += [{"from_name": c["module"], "to_name": m, "kind": "IMPORTS", "path": c["path"], "chunk": c.get("chunk_id")}
                    for m in c.get("imports") or [] if c.get("module") and m != c.get("module")]
        if c["kind"] in ("SQL", "DBT_MODEL", "DBT_SNAPSHOT"):
            source = c.get("name") or c["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            out += [{"from_name": source, "to_name": t, "kind": "READS", "path": c["path"], "chunk": c.get("chunk_id")}
                    for t in c.get("reads") or [] if t.split(".")[-1].upper() != source.split(".")[-1].upper()]
            out += [{"from_name": source, "to_name": t, "kind": "WRITES", "path": c["path"], "chunk": c.get("chunk_id")}
                    for t in c.get("writes") or [] if t.split(".")[-1].upper() != source.split(".")[-1].upper()]
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
               known_macros: Iterable[str] = (), known_defs: Iterable[str] = ()
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], List[Dict[str, Any]]]:
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
    return chunks, edges(chunks, known_macros, known_defs), dbt
