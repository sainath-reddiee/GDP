"""Read-only guard for test SQL written by people or by Cortex.

A test query must be one SELECT (optionally WITH ...), must not contain statements or functions that change
state, and may only read the run's own source tables, its target and the domain hub, by fully qualified name.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|GRANT|REVOKE|CALL|EXECUTE|COPY|PUT|GET|REMOVE|"
    r"UNDROP|USE|SET|UNSET|BEGIN|COMMIT|ROLLBACK)\b|SYSTEM\$", re.IGNORECASE)
COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
STRINGS = re.compile(r"'(?:[^']|'')*'|\$\$.*?\$\$", re.DOTALL)
CTE = re.compile(r"(?:\bWITH|,)\s+([A-Za-z_][A-Za-z0-9_$]*)\s+AS\s*\(", re.IGNORECASE)
SOURCE = re.compile(r"\b(?:FROM|JOIN)\s+([^\s,()]+)", re.IGNORECASE)
FUNCTION_FROM = re.compile(r"\b(EXTRACT|TRIM|SUBSTRING|POSITION|OVERLAY)\s*\([^()]*$", re.IGNORECASE)


def _norm(name: str) -> str:
    return ".".join(part.strip('"').upper() for part in name.split("."))


def check(sql: str, allowed: Iterable[str]) -> Tuple[bool, List[str], str]:
    """Returns (ok, problems, cleaned_sql). `allowed` holds fully qualified DB.SCHEMA.TABLE names."""
    problems: List[str] = []
    text = (sql or "").strip()
    if not text:
        return False, ["empty query"], ""
    cleaned = text.rstrip().rstrip(";").rstrip()
    bare = STRINGS.sub("''", COMMENTS.sub(" ", cleaned))
    if ";" in bare:
        problems.append("only one statement is allowed")
    if not re.match(r"^\s*(SELECT|WITH)\b", bare, re.IGNORECASE):
        problems.append("a test query must start with SELECT or WITH")
    bad = FORBIDDEN.search(bare)
    if bad:
        problems.append(f"'{bad.group(0)}' is not allowed in a read-only test")
    ctes = {m.upper() for m in CTE.findall(bare)}
    allow = {_norm(a) for a in allowed}
    for match in SOURCE.finditer(bare):
        raw = match.group(1)
        if FUNCTION_FROM.search(bare[max(0, match.start() - 80):match.start()]):
            continue  # EXTRACT(YEAR FROM col), TRIM(' ' FROM col) and friends
        name = _norm(raw)
        if name in ctes or name.startswith("TABLE"):
            if name.startswith("TABLE"):
                problems.append("table functions are not allowed")
            continue
        if name.count(".") != 2:
            problems.append(f"{raw}: use the fully qualified DATABASE.SCHEMA.TABLE name")
        elif name not in allow:
            problems.append(f"{raw} is not one of this run's source or target tables")
    return not problems, problems, cleaned
