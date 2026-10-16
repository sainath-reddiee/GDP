"""The Airflow task catalogue (part of the Airflow knowledge pack): what every task of every captured DAG does.

Read from Airflow itself, so it works for MWAA and a self-hosted Airflow alike: the DAG's source (GET /dagSources) and
its task list (GET /dags/{dag}/tasks: operator, downstream tasks). The source is parsed statically (Python `ast`, never
imported or run): each operator call or @task function is mapped to its task_id, and the strings in its body give the
SQL it runs, the tables it reads and writes (services.code.dbt_parse.table_refs, plus stages and COPY INTO), the dbt
selectors it runs (services.ops.code_map) and the SnapLogic pipeline it calls. When the DAG is mapped to an indexed
repository, SQL files the tasks name are read from the repository, and the repository's copy of the DAG file stands in
when Airflow does not return the source.

One row per task in CORE.DAG_TASK. A DAG is read again every CATALOG_HOURS, and its tasks are rebuilt only when its
source hash changed. Refreshing never fails a poll.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from services.code import dbt_parse
from services.ops import code_map

CATALOG_HOURS = 6
MAX_DAGS = 40            # DAG sources read per refresh, so a large environment is spread over several polls
SQL_CAP = 8000
VAR = "__VAR__"          # stands for an f-string placeholder; a table name containing it is not a real name

SQL_START = re.compile(
    r"""^\s*(?:--[^\n]*\n\s*)*(?:
        select\b(?:\s+[\d*'(]|\s+\w+\s*\(|[\s\S]*?\bfrom\b) | with\s+(?:recursive\s+)?\w+\s+as\s*\( | insert\s+(?:overwrite\s+)?into\b | merge\s+into\b
      | create\s+(?:or\s+replace\s+)?(?:\w+\s+){0,3}?(?:table|view|stage|schema|procedure|function|stream|task|sequence|pipe|file\s+format)\b
      | update\s+\S+\s+set\b | delete\s+from\b | copy\s+into\b | (?:list|ls|remove|rm)\s+@ | (?:put|get)\s+\S+\s+@
      | call\s+\w | alter\s+(?:table|warehouse|session|stage|view|task)\b | truncate\s+(?:table\s+)?\w | unload\b
      | execute\s+immediate\b | show\s+\w | describe\s+\w | use\s+(?:role|warehouse|database|schema)\b)""", re.I | re.X)
STAGE_REF = re.compile(r"@(~|%?[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){0,2})")
COPY_INTO = re.compile(rf"\bcopy\s+into\s+{dbt_parse.TABLE_NAME}", re.I)
TRUNCATE = re.compile(rf"\btruncate\s+(?:table\s+)?(?:if\s+exists\s+)?{dbt_parse.TABLE_NAME}", re.I)
SNAPLOGIC_URL = re.compile(r"/api/1/rest/(?:slsched/feed|feed|slschedule)/([^\s'\"?#]+)")
PIPELINE_KWARGS = ("pipeline", "pipeline_name", "pipeline_id", "snaplogic_pipeline", "task_name", "triggered_task")
TASK_DECORATORS = {"task"}
TASK_ID_LITERAL = re.compile(r"""task_id\s*=\s*f?['"]([^'"]+)['"]""")
STRING_LITERAL = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'', re.S)


# ---------------------------------------------------------------- parsing the DAG source (pure)

def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("partial", "override"):
        func = func.value
        if isinstance(func, ast.Call):   # Operator.partial(...).expand(...)
            func = func.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _text(node: Any) -> Optional[str]:
    """A string literal or an f-string (each placeholder written as __VAR__)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else VAR for v in node.values)
    return None


def _strings(nodes: Iterable[Any], constants: Optional[Dict[str, str]] = None) -> List[str]:
    """String literals under the nodes; a name bound to a module-level string (sql=LOAD_SQL) counts as that string."""
    out: List[str] = []

    def walk(node: Any) -> None:
        found = _text(node)
        if found is not None:
            out.append(found)
            return
        if isinstance(node, ast.Name) and constants and node.id in constants:
            out.append(constants[node.id])
            return
        for child in ast.iter_child_nodes(node):
            walk(child)

    for node in nodes:
        walk(node)
    return out


def _kw(call: ast.Call, name: str) -> Optional[str]:
    for k in call.keywords:
        if k.arg == name:
            return _text(k.value)
    return None


def _first_text(call: ast.Call, keyword: str) -> Optional[str]:
    return _kw(call, keyword) or (_text(call.args[0]) if call.args else None)


def _decorator(node: Any) -> Tuple[str, Optional[ast.Call]]:
    """('task', call) for @task, @task(...), @task.bash, @task.python(...); ('dag', ...), ('task_group', ...)."""
    call = node if isinstance(node, ast.Call) else None
    target = node.func if call else node
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        base = target.value.id
        return (base if base in TASK_DECORATORS else target.attr), call
    if isinstance(target, ast.Name):
        return target.id, call
    if isinstance(target, ast.Attribute):
        return target.attr, call
    return "", call


def _body(fn: Any) -> List[Any]:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and _text(body[0].value) is not None:
        body = body[1:]   # the docstring describes the task; it is not what the task runs
    return body


def _join(group: Optional[str], task_id: str) -> str:
    return f"{group}.{task_id}" if group else task_id


class _Parser:
    def __init__(self, tree: ast.Module):
        self.tasks: List[Dict[str, Any]] = []
        self.functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.decorated: Dict[str, Any] = {}
        self.dag_vars: Dict[str, str] = {}
        self.constants: Dict[str, str] = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and _text(node.value) is not None):
                self.constants[node.targets[0].id] = _text(node.value) or ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and "dag" in _callee(node.value).lower():
                dag_id = _first_text(node.value, "dag_id")
                for target in node.targets:
                    if dag_id and isinstance(target, ast.Name):
                        self.dag_vars[target.id] = dag_id
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(_decorator(d)[0] == "task" for d in node.decorator_list):
                    self.decorated[node.name] = node

    def helper_strings(self, nodes: List[Any], seen: Set[str]) -> List[str]:
        """Strings of the module's own functions a task body calls (one level), where its SQL often lives."""
        out: List[str] = []
        for node in nodes:
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
                fn = self.functions.get(call.func.id)
                if fn is not None and fn.name not in seen and fn.name not in self.decorated:
                    seen.add(fn.name)
                    out += _strings(_body(fn), self.constants)
        return out

    def add(self, task_id: Optional[str], operator: str, dag: Optional[str], group: Optional[str], strings: List[str],
            kwargs: Dict[str, str], line: int) -> None:
        if task_id:
            self.tasks.append({"task_id": _join(group, task_id), "dag_id": dag, "operator": operator, "strings": strings,
                               "kwargs": kwargs, "line": line})

    def visit(self, node: Any, dag: Optional[str], group: Optional[str]) -> None:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    name = _callee(ctx).lower()
                    if "taskgroup" in name or name == "task_group":
                        group = _join(group, _first_text(ctx, "group_id") or "") or group
                    elif "dag" in name:
                        dag = _first_text(ctx, "dag_id") or dag
                elif isinstance(ctx, ast.Name) and ctx.id in self.dag_vars:
                    dag = self.dag_vars[ctx.id]
            for child in node.body:
                self.visit(child, dag, group)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kinds = {_decorator(d)[0]: _decorator(d)[1] for d in node.decorator_list}
            if "task" in kinds:
                call = kinds["task"]
                body = _body(node)
                deco = next(d for d in node.decorator_list if _decorator(d)[0] == "task")
                target = deco.func if isinstance(deco, ast.Call) else deco
                flavour = target.attr if isinstance(target, ast.Attribute) else ""
                self.add((_kw(call, "task_id") if call else None) or node.name, f"@task.{flavour}" if flavour else "@task",
                         dag, group, _strings(body, self.constants) + self.helper_strings(body, set()), {}, node.lineno)
                return
            if "dag" in kinds:
                call = kinds["dag"]
                dag = (_kw(call, "dag_id") if call else None) or node.name
            elif "task_group" in kinds:
                call = kinds["task_group"]
                group = _join(group, (_kw(call, "group_id") if call else None) or node.name)
            for child in node.body:
                self.visit(child, dag, group)
            return
        if isinstance(node, ast.Call) and any(k.arg == "task_id" for k in node.keywords):
            task_id = _kw(node, "task_id")
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "override" and isinstance(func.value, ast.Name) \
                    and func.value.id in self.decorated:   # my_task.override(task_id="b")()
                fn = self.decorated[func.value.id]
                body = _body(fn)
                self.add(task_id, "@task", dag, group, _strings(body, self.constants) + self.helper_strings(body, set()), {}, node.lineno)
                return
            kwargs = {k.arg: v for k in node.keywords if k.arg and (v := _text(k.value)) is not None}
            dag_kw = next((k.value for k in node.keywords if k.arg == "dag"), None)
            if isinstance(dag_kw, ast.Name) and dag_kw.id in self.dag_vars:
                dag = self.dag_vars[dag_kw.id]
            strings = _strings(list(node.args) + [k.value for k in node.keywords if k.arg != "task_id"], self.constants)
            callable_ = next((k.value for k in node.keywords if k.arg == "python_callable"), None)
            if isinstance(callable_, ast.Name) and callable_.id in self.functions:
                fn = self.functions[callable_.id]
                strings += _strings(_body(fn), self.constants) + self.helper_strings(_body(fn), {fn.name})
            self.add(task_id, _callee(node), dag, group, strings, kwargs, node.lineno)
            return
        for child in ast.iter_child_nodes(node):
            self.visit(child, dag, group)


def _regex_tasks(text: str) -> List[Dict[str, Any]]:
    """The fallback when the source does not parse (a partial copy from the repository): each task_id literal and the
    strings up to the next one."""
    found = list(TASK_ID_LITERAL.finditer(text))
    out = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        strings = [next(g for g in s.groups() if g is not None) for s in STRING_LITERAL.finditer(text[m.end():end])]
        out.append({"task_id": m.group(1), "dag_id": None, "operator": "", "strings": strings, "kwargs": {},
                    "line": text.count("\n", 0, m.start()) + 1})
    return out


def parse_source(text: Optional[str]) -> List[Dict[str, Any]]:
    """Tasks written in a DAG file: {task_id, dag_id (None when the file does not say), operator, strings, kwargs, line}.
    task_id may hold __VAR__ where it is an f-string; it carries the task group prefix."""
    source = str(text or "")
    if not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _regex_tasks(source)
    parser = _Parser(tree)
    parser.visit(tree, None, None)
    return parser.tasks


# ---------------------------------------------------------------- what a task does (pure)

def split_statements(sql: str) -> List[str]:
    """Statements of a SQL string, split on semicolons outside quotes."""
    out, current, quote = [], [], None
    for ch in sql:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        if ch == ";":
            out.append("".join(current))
            current = []
            continue
        current.append(ch)
    out.append("".join(current))
    return [s.strip() for s in out if s.strip()]


def sql_statements(strings: Iterable[str]) -> List[str]:
    out: List[str] = []
    for s in strings:
        for statement in split_statements(str(s)):
            if SQL_START.match(statement) and statement not in out:
                out.append(statement)
    return out


def sql_files(strings: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(s.strip() for s in strings if re.fullmatch(r"[\w./\-]+\.sql", s.strip() or "")))


def _name(value: str) -> Optional[str]:
    name = value.replace('"', "").strip().upper()
    if not name or VAR in name or name.endswith("."):
        return None
    return name


def object_refs(statements: Iterable[str]) -> Tuple[List[str], List[str]]:
    """(read, written) tables and stages over the statements, each statement on its own, so a task that reads and then
    writes the same table lists it on both sides. Names are upper-cased as written; stages keep their @."""
    reads: Set[str] = set()
    writes: Set[str] = set()
    for statement in statements:
        r, w = dbt_parse.table_refs(statement)
        clean = dbt_parse.SQL_NOISE.sub(" ", statement)
        targets = {_name(t) for t in COPY_INTO.findall(clean) + TRUNCATE.findall(clean)}
        stages = {"@" + n for s in STAGE_REF.findall(clean) if (n := _name(s))}
        copy_to_stage = re.search(r"\bcopy\s+into\s+@", clean, re.I)
        writes |= {n for n in (_name(x) for x in w) if n} | {t for t in targets if t}
        reads |= {n for n in (_name(x) for x in r) if n} - {t for t in targets if t}
        if copy_to_stage:
            first = "@" + (_name(STAGE_REF.search(clean[copy_to_stage.start():]).group(1)) or "")   # type: ignore[union-attr]
            writes.add(first)
            stages.discard(first)
        reads |= stages
    return sorted(reads), sorted(writes)


def snaplogic_pipeline(task: Dict[str, Any]) -> Optional[str]:
    for s in task.get("strings") or []:
        m = SNAPLOGIC_URL.search(s)
        if m:
            return m.group(1)[:1000]
    if "snaplogic" in str(task.get("operator") or "").lower():
        for key in PIPELINE_KWARGS:
            value = (task.get("kwargs") or {}).get(key)
            if value:
                return str(value)[:1000]
    return None


def dbt_of(task: Dict[str, Any], operator: Optional[str]) -> List[str]:
    found: List[str] = []
    for s in task.get("strings") or []:
        for selector in code_map.dbt_selectors(s) if "dbt" in s else []:
            if selector not in found:
                found.append(selector)
    return found or code_map.task_models(task.get("task_id"), operator or None, None)


def describe(task: Dict[str, Any], operator: Optional[str] = None, file_sql: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """{operator, sql_text, reads, writes, dbt_selectors, snaplogic_pipeline} of one parsed task."""
    statements = sql_statements(task.get("strings") or [])
    for path in sql_files(task.get("strings") or []):
        text = (file_sql or {}).get(path)
        statements += sql_statements([text]) if text else []
    reads, writes = object_refs(statements)
    operator = operator or task.get("operator") or None
    sql_text = ";\n".join(statements)
    return {"operator": operator, "sql_text": sql_text[:SQL_CAP] if sql_text else None, "reads": reads, "writes": writes,
            "dbt_selectors": dbt_of(task, operator), "snaplogic_pipeline": snaplogic_pipeline(task)}


# ---------------------------------------------------------------- the catalogue rows of one DAG (pure)

def rest_operator(task: Dict[str, Any]) -> str:
    from services.ops.deps import task_class

    return task_class(task)


def _pattern(task_id: str) -> Optional[re.Pattern]:
    if VAR not in task_id:
        return None
    return re.compile("^" + ".+".join(re.escape(p) for p in task_id.split(VAR)) + "$")


def match_task(rest_id: str, parsed: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The parsed task behind a task id Airflow reports: the same id, else the id without its task group prefix, else
    an f-string id that fits."""
    for p in parsed:
        if p["task_id"] == rest_id:
            return p
    for p in parsed:
        if rest_id.endswith("." + p["task_id"]) or p["task_id"].endswith("." + rest_id):
            return p
    for p in parsed:
        pattern = _pattern(p["task_id"])
        if pattern and pattern.match(rest_id):
            return p
    return None


def build_rows(env_id: str, dag_id: str, source: Optional[str], rest_tasks: List[Dict[str, Any]],
               source_path: Optional[str] = None, source_hash: Optional[str] = None,
               file_sql: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """One row per task: Airflow's task list (ids, operators, edges) joined with what the source says each task runs.
    Without a task list, the tasks the source writes for this DAG."""
    parsed = parse_source(source)
    pool = [p for p in parsed if p["dag_id"] == dag_id] or [p for p in parsed if p["dag_id"] is None] or parsed
    base = {"env_id": env_id, "dag_id": dag_id, "source_path": (source_path or None) and str(source_path)[:2000],
            "source_hash": source_hash}
    rows: List[Dict[str, Any]] = []
    if rest_tasks:
        upstream: Dict[str, List[str]] = {}
        for t in rest_tasks:
            for down in t.get("downstream_task_ids") or []:
                upstream.setdefault(str(down), []).append(str(t["task_id"]))
        for t in rest_tasks:
            task_id = str(t["task_id"])
            found = match_task(task_id, pool) or {"task_id": task_id, "strings": [], "kwargs": {}}
            info = describe({**found, "task_id": task_id}, rest_operator(t) or found.get("operator"), file_sql)
            rows.append({**base, "task_id": task_id, **info, "upstream": sorted(upstream.get(task_id, [])),
                         "downstream": sorted(str(d) for d in t.get("downstream_task_ids") or [])})
        return rows
    seen: Set[str] = set()
    for p in pool:
        if p["task_id"] in seen or VAR in p["task_id"]:
            continue
        seen.add(p["task_id"])
        rows.append({**base, "task_id": p["task_id"], **describe(p, None, file_sql), "upstream": [], "downstream": []})
    return rows


def source_hash(text: Optional[str]) -> Optional[str]:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


# ---------------------------------------------------------------- reading Airflow and storing the rows

def _repo_text(db: Any, repo_id: str, path: str) -> str:
    """The file as indexed: its chunks laid back on their lines (lines no chunk covers stay blank)."""
    chunks = db.query("""SELECT START_LINE, TEXT FROM CORE.CODE_CHUNK WHERE REPO_ID = %s AND PATH = %s
                          ORDER BY START_LINE LIMIT 400""", (repo_id, path))
    lines: Dict[int, str] = {}
    for c in chunks:
        start = int(c.get("start_line") or 1)
        for i, line in enumerate(str(c.get("text") or "").splitlines()):
            lines.setdefault(start + i, line)
    return "\n".join(lines.get(i, "") for i in range(1, max(lines) + 1)) if lines else ""


def _repo_sql(db: Any, repo_id: Optional[str], paths: List[str]) -> Dict[str, str]:
    """SQL files the tasks name, read from the DAG's repository (matched on the end of the path)."""
    out: Dict[str, str] = {}
    for path in paths[:20]:
        if not repo_id:
            break
        found = db.query("""SELECT PATH FROM CORE.CODE_FILE WHERE REPO_ID = %s AND (PATH = %s OR ENDSWITH(PATH, %s))
                             ORDER BY LENGTH(PATH) LIMIT 1""", (repo_id, path.lstrip("/"), "/" + path.lstrip("/")))
        if found:
            out[path] = _repo_text(db, repo_id, found[0]["path"])
    return out


def store(db: Any, env_id: str, dag_id: str, rows: List[Dict[str, Any]], digest: Optional[str]) -> int:
    """Replace the DAG's catalogue rows and stamp the DAG with the source hash."""
    if rows:
        db.execute("""
            MERGE INTO CORE.DAG_TASK T USING (
                SELECT F.VALUE:env_id::VARCHAR AS ENV_ID, F.VALUE:dag_id::VARCHAR AS DAG_ID, F.VALUE:task_id::VARCHAR AS TASK_ID,
                       F.VALUE:operator::VARCHAR AS OPERATOR, F.VALUE:upstream::ARRAY AS UPSTREAM,
                       F.VALUE:downstream::ARRAY AS DOWNSTREAM, F.VALUE:sql_text::VARCHAR AS SQL_TEXT,
                       F.VALUE:reads::ARRAY AS READS, F.VALUE:writes::ARRAY AS WRITES,
                       F.VALUE:dbt_selectors::ARRAY AS DBT_SELECTORS, F.VALUE:snaplogic_pipeline::VARCHAR AS SNAPLOGIC_PIPELINE,
                       F.VALUE:source_path::VARCHAR AS SOURCE_PATH, F.VALUE:source_hash::VARCHAR AS SOURCE_HASH
                  FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) F) S
            ON T.ENV_ID = S.ENV_ID AND T.DAG_ID = S.DAG_ID AND T.TASK_ID = S.TASK_ID
            WHEN MATCHED THEN UPDATE SET OPERATOR = S.OPERATOR, UPSTREAM = S.UPSTREAM, DOWNSTREAM = S.DOWNSTREAM,
                 SQL_TEXT = S.SQL_TEXT, READS = S.READS, WRITES = S.WRITES, DBT_SELECTORS = S.DBT_SELECTORS,
                 SNAPLOGIC_PIPELINE = S.SNAPLOGIC_PIPELINE, SOURCE_PATH = S.SOURCE_PATH, SOURCE_HASH = S.SOURCE_HASH,
                 UPDATED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (ENV_ID, DAG_ID, TASK_ID, OPERATOR, UPSTREAM, DOWNSTREAM, SQL_TEXT, READS, WRITES,
                 DBT_SELECTORS, SNAPLOGIC_PIPELINE, SOURCE_PATH, SOURCE_HASH)
                 VALUES (S.ENV_ID, S.DAG_ID, S.TASK_ID, S.OPERATOR, S.UPSTREAM, S.DOWNSTREAM, S.SQL_TEXT, S.READS, S.WRITES,
                 S.DBT_SELECTORS, S.SNAPLOGIC_PIPELINE, S.SOURCE_PATH, S.SOURCE_HASH)""", (json.dumps(rows, default=str),))
    db.execute("""DELETE FROM CORE.DAG_TASK WHERE ENV_ID = %s AND DAG_ID = %s
                     AND NOT ARRAY_CONTAINS(TASK_ID::VARIANT, PARSE_JSON(%s)::ARRAY)""",
               (env_id, dag_id, json.dumps([r["task_id"] for r in rows])))
    db.execute("UPDATE CORE.DAG SET SOURCE_HASH = %s, CATALOGED_AT = CURRENT_TIMESTAMP() WHERE ENV_ID = %s AND DAG_ID = %s",
               (digest, env_id, dag_id))
    return len(rows)


def due_dags(db: Any, env_id: str, force: bool = False, hours: int = CATALOG_HOURS, limit: int = MAX_DAGS,
             dag_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Active DAGs never catalogued or not read for `hours`, the oldest first (all of them, or the named ones, when forced)."""
    where = "" if force else " AND (CATALOGED_AT IS NULL OR CATALOGED_AT < DATEADD(hour, %s, CURRENT_TIMESTAMP()))"
    params: List[Any] = [env_id] + ([] if force else [-abs(int(hours))])
    if dag_ids:
        where += " AND ARRAY_CONTAINS(DAG_ID::VARIANT, PARSE_JSON(%s)::ARRAY)"
        params.append(json.dumps(list(dag_ids)))
    return db.query(f"""SELECT DAG_ID, FILELOC, REPO_ID, REPO_PATH, SOURCE_HASH FROM CORE.DAG
                         WHERE ENV_ID = %s AND COALESCE(IS_ACTIVE, TRUE){where}
                         ORDER BY CATALOGED_AT NULLS FIRST, DAG_ID LIMIT {int(limit)}""", tuple(params))


def catalogue_dag(db: Any, mw: Any, env_id: str, dag: Dict[str, Any], force: bool = False,
                  sources: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Read one DAG's source; rebuild its tasks when the source changed (or when forced). `sources` caches sources by
    file, since one file often defines several DAGs."""
    dag_id = dag["dag_id"]
    sources = sources if sources is not None else {}
    fileloc = dag.get("fileloc") or ""
    repo_path = code_map.candidate_repo_path(fileloc, dag.get("repo_path")) if dag.get("repo_id") else None
    origin = "AIRFLOW"
    text = sources.get(fileloc) if fileloc else None
    if text is None:
        try:
            text = mw.dag_source(dag_id)
        except Exception:
            text = ""
        if fileloc and text:
            sources[fileloc] = text
    if not text and repo_path:
        text, origin = _repo_text(db, dag["repo_id"], repo_path), "REPO"
    if not text:
        origin = "NONE"
    digest = source_hash(text)
    if not force and digest and digest == dag.get("source_hash"):
        db.execute("UPDATE CORE.DAG SET CATALOGED_AT = CURRENT_TIMESTAMP() WHERE ENV_ID = %s AND DAG_ID = %s", (env_id, dag_id))
        return {"dag_id": dag_id, "changed": False, "source": origin}
    try:
        rest = mw.dag_tasks(dag_id)
    except Exception:
        rest = []
    parsed_strings = [s for p in parse_source(text) for s in p["strings"]]
    file_sql = _repo_sql(db, dag.get("repo_id"), sql_files(parsed_strings))
    rows = build_rows(env_id, dag_id, text, rest, repo_path or code_map.dag_relative_path(fileloc), digest, file_sql)
    store(db, env_id, dag_id, rows, digest)
    return {"dag_id": dag_id, "changed": True, "source": origin, "tasks": len(rows),
            "with_sql": sum(1 for r in rows if r.get("sql_text"))}


def refresh(db: Any, mw: Any, env_id: str, force: bool = False, limit: int = MAX_DAGS,
            dag_ids: Optional[List[str]] = None, profiles: bool = True) -> Dict[str, Any]:
    """Catalogue the DAGs that are due and refresh their profiles (AIRFLOW_DAG knowledge, services.ops.dag_profile).
    Returns {dags, changed, tasks, profiles, problems}."""
    sources: Dict[str, str] = {}
    profiled = 0
    changed: List[Dict[str, Any]] = []
    problems: List[str] = []
    dags = due_dags(db, env_id, force, limit=limit, dag_ids=dag_ids)
    for dag in dags:
        try:
            result = catalogue_dag(db, mw, env_id, dag, force, sources)
        except Exception as exc:
            problems.append(f"{dag['dag_id']}: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        if result.get("changed"):
            changed.append(result)
        if profiles:   # a new version only when the profile reads differently (new tasks, failures, durations)
            from services.ops.dag_profile import remember_profile

            profiled += 1 if remember_profile(db, env_id, dag["dag_id"]) else 0
    return {"env_id": env_id, "dags": len(dags), "changed": [c["dag_id"] for c in changed],
            "tasks": sum(int(c.get("tasks") or 0) for c in changed), "profiles": profiled, "problems": problems[:20]}


def refresh_if_due(db: Any, mw: Any, env_id: str) -> Optional[Dict[str, Any]]:
    """Catalogue whatever is due after a poll. Never raises: the catalogue is an input to analysis, not to capture."""
    try:
        return refresh(db, mw, env_id)
    except Exception as exc:
        return {"dags": 0, "problems": [f"{type(exc).__name__}"]}


def task_entry(db: Any, env_id: Optional[str], dag_id: Optional[str], task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The catalogue row of one task (the id as Airflow reports it), or None."""
    if not (dag_id and task_id):
        return None
    found = db.query("""SELECT OPERATOR, UPSTREAM, DOWNSTREAM, SQL_TEXT, READS, WRITES, DBT_SELECTORS, SNAPLOGIC_PIPELINE,
                               SOURCE_PATH FROM CORE.DAG_TASK
                         WHERE DAG_ID = %s AND TASK_ID = %s AND (%s IS NULL OR ENV_ID = %s) LIMIT 1""",
                     (dag_id, task_id, env_id, env_id))
    if not found:
        return None
    row = found[0]
    for key in ("upstream", "downstream", "reads", "writes", "dbt_selectors"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = []
        row[key] = list(value or [])
    return row
