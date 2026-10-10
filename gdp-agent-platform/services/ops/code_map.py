"""DAG to code mapping (pure): where a DAG's file lives in the connected DAG repository, and which dbt models a task
runs. Used by the incident context (PR O3) to reach the code graph's impact() for the models.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from typing import List, Optional

# where Airflow keeps the DAG folder: MWAA first, then the common images
DAG_FOLDER_PREFIXES = ("/usr/local/airflow/dags/", "/opt/airflow/dags/", "/home/airflow/dags/", "/usr/local/airflow/plugins/")
SELECT_FLAGS = {"--select", "-s", "--models", "-m"}
DBT_COMMANDS = {"run", "build", "test", "seed", "snapshot", "compile", "ls", "list"}
COSMOS_SUFFIXES = ("run", "test", "build", "seed", "snapshot")


def dag_relative_path(fileloc: Optional[str]) -> Optional[str]:
    """The DAG file's path relative to the DAG folder (MWAA prefix /usr/local/airflow/dags/ stripped)."""
    if not fileloc:
        return None
    path = str(fileloc).replace("\\", "/").strip()
    for prefix in DAG_FOLDER_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    if "/dags/" in path:
        return path.split("/dags/", 1)[1]
    return path.lstrip("/")


def candidate_repo_path(fileloc: Optional[str], repo_path: Optional[str] = None) -> Optional[str]:
    """The DAG file inside the repository: the repo mapping (the folder that is Airflow's DAG folder, or the exact file
    when it ends in .py) joined with the path relative to the DAG folder."""
    if repo_path and str(repo_path).strip().endswith(".py"):
        return str(repo_path).strip().strip("/")
    relative = dag_relative_path(fileloc)
    if not relative:
        return None
    base = (repo_path or "").strip().strip("/")
    return posixpath.normpath(posixpath.join(base, relative)) if base else relative


def _split_commands(command: str) -> List[List[str]]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    commands, current = [], []
    for token in tokens:
        if token in ("&&", "||", ";", "|"):
            commands.append(current)
            current = []
            continue
        if token.endswith(";") and len(token) > 1:
            current.append(token[:-1])
            commands.append(current)
            current = []
            continue
        current.append(token)
    commands.append(current)
    return [c for c in commands if c]


def dbt_selectors(command: Optional[str]) -> List[str]:
    """Selectors from dbt commands in a shell command (BashOperator, KubernetesPodOperator args joined):
    `dbt run --select a b+ --exclude c`, `-s`, `--models`, `-m`. Order kept, duplicates dropped."""
    if not command:
        return []
    out: List[str] = []
    for tokens in _split_commands(str(command)):
        names = [posixpath.basename(t) for t in tokens]
        if "dbt" not in names:
            continue
        i = names.index("dbt")
        if not any(t in DBT_COMMANDS for t in tokens[i + 1:]):
            continue
        j = i + 1
        while j < len(tokens):
            token = tokens[j]
            flag, _, inline = token.partition("=")
            if flag in SELECT_FLAGS:
                values = [inline] if inline else []
                j += 1
                while not inline and j < len(tokens) and not tokens[j].startswith("-"):
                    values.append(tokens[j])
                    j += 1
                for value in values:
                    for part in re.split(r"[\s]+", value.strip()):
                        if part and part not in out:
                            out.append(part)
                continue
            j += 1
    return out


def cosmos_model(task_id: Optional[str]) -> Optional[str]:
    """The dbt node of an Astronomer Cosmos task id: 'group.stg_orders.run', 'stg_orders.test', 'stg_orders_run',
    'raw_customers_seed'. None when the id does not look like a Cosmos task."""
    if not task_id:
        return None
    parts = str(task_id).split(".")
    if len(parts) >= 2 and parts[-1] in COSMOS_SUFFIXES:
        return parts[-2] or None
    last = parts[-1]
    for suffix in COSMOS_SUFFIXES:
        if last.endswith("_" + suffix) and len(last) > len(suffix) + 1:
            return last[: -(len(suffix) + 1)]
    return None


def task_models(task_id: Optional[str], operator: Optional[str] = None, command: Optional[str] = None) -> List[str]:
    """Best guess of the dbt selectors a task runs: the command's selectors, else the Cosmos node for Cosmos
    operators (DbtRunLocalOperator, DbtTestKubernetesOperator, ...)."""
    found = dbt_selectors(command)
    if found:
        return found
    if operator and str(operator).startswith("Dbt") and "Cloud" not in str(operator):
        model = cosmos_model(task_id)
        return [model] if model else []
    if operator is None and command is None:
        model = cosmos_model(task_id)
        return [model] if model else []
    return []


_DBT_IN_SOURCE = re.compile(r"""\bdbt\s+(?:run|build|test|seed|snapshot|compile|ls|list)\b[^'"\n]*""")
_TASK_ID_LITERAL = re.compile(r"""task_id\s*=\s*['"]([^'"]+)['"]""")


def dbt_commands_in_source(text: Optional[str]) -> List[str]:
    """dbt command lines written as string literals in a DAG file (bash_command='dbt run --select x', f-strings)."""
    return [m.group(0).strip() for m in _DBT_IN_SOURCE.finditer(str(text or ""))]


def source_task_models(text: Optional[str], task_id: Optional[str] = None) -> List[str]:
    """dbt selectors a task runs, read from the DAG source: the stretch from `task_id='<task>'` up to the next task_id
    (the operator's arguments, with task_id written first as usual), else every dbt command in the file when no task
    is given."""
    source = str(text or "")
    if task_id:
        found = list(_TASK_ID_LITERAL.finditer(source))
        for i, m in enumerate(found):
            if m.group(1) == task_id:
                end = found[i + 1].start() if i + 1 < len(found) else len(source)
                source = source[m.start():end]
                break
        else:
            return []
    out: List[str] = []
    for command in dbt_commands_in_source(source):
        for selector in dbt_selectors(command):
            if selector not in out:
                out.append(selector)
    return out


def selector_model(selector: Optional[str]) -> Optional[str]:
    """The model a dbt selector names ('2+stg_orders+' -> stg_orders, 'model:x' -> x, 'models/stg/x.sql' -> x); None for
    tag:, source:, path to a folder and other methods that do not name one model."""
    value = str(selector or "").strip().lstrip("@")
    value = re.sub(r"^\d*\+", "", value)
    value = re.sub(r"\+\d*$", "", value)
    if ":" in value:
        method, _, rest = value.partition(":")
        if method != "model":
            return None
        value = rest
    if "/" in value:
        if not value.endswith(".sql"):
            return None
        value = posixpath.basename(value)
    if value.endswith(".sql"):
        value = value[:-4]
    if "*" in value or not re.fullmatch(r"[A-Za-z0-9_.]+", value or ""):
        return None
    return value.split(".")[-1] or None
