"""Domain QA packs as files (pure): domain/<name>/qa/<pack>.sql, so packs can be versioned in Git and seeded at deploy.

Format: a pack header, then one block per query. Header lines start with "-- @"; everything until the next
"-- @query" line is the query's SQL.

    -- @pack Referential integrity
    -- @description Every spoke row resolves to its hub
    -- @tags reconciliation, referential-integrity

    -- @query orphan_company_address
    -- @severity ERROR
    -- @description Addresses whose COMPANY_CORE_SKEY has no hub row
    -- @expect 0 rows
    -- @tags orphan-keys
    SELECT a.COMPANY_CORE_SKEY FROM DEV_GDP_SILVER_DB.COMPANY.COMPANY_ADDRESS a
    LEFT JOIN DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE c ON c.COMPANY_CORE_SKEY = a.COMPANY_CORE_SKEY
    WHERE c.COMPANY_CORE_SKEY IS NULL

render() writes the same format back, so a pack exported from the UI can be committed as is.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

DIRECTIVE = re.compile(r"^\s*--\s*@(\w+)\s*(.*?)\s*$")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
SEVERITIES = ("ERROR", "WARN", "INFO")
PACK_KEYS = {"pack", "description", "tags"}
QUERY_KEYS = {"query", "severity", "description", "expect", "tags"}


def _tags(text: str) -> List[str]:
    return [t.strip().lower().lstrip("#") for t in re.split(r"[,\s]+", text or "") if t.strip()]


def parse(text: str, default_name: str = "") -> Dict[str, Any]:
    """{name, description, tags, queries: [{query_name, severity, description, expectation, tags, sql_expression}]}.
    Raises ValueError with the line number on a malformed file."""
    pack: Dict[str, Any] = {"name": default_name, "description": "", "tags": [], "queries": []}
    current: Dict[str, Any] | None = None
    sql: List[str] = []

    def close(line_no: int) -> None:
        if current is None:
            return
        body = "\n".join(sql).strip().rstrip(";").strip()
        if not body:
            raise ValueError(f"line {line_no}: query {current['query_name']} has no SQL")
        current["sql_expression"] = body
        pack["queries"].append(current)

    for n, line in enumerate((text or "").splitlines(), start=1):
        m = DIRECTIVE.match(line)
        if not m:
            if current is not None:
                sql.append(line)
            elif line.strip() and not line.strip().startswith("--"):
                raise ValueError(f"line {n}: SQL before the first -- @query line")
            continue
        key, value = m.group(1).lower(), m.group(2)
        if key == "query":
            close(n)
            if not NAME.match(value):
                raise ValueError(f"line {n}: query names are letters, digits and _ (starting with a letter): {value!r}")
            if any(q["query_name"].lower() == value.lower() for q in pack["queries"]):
                raise ValueError(f"line {n}: query {value} appears twice")
            current, sql = {"query_name": value, "severity": "ERROR", "description": "", "expectation": "0 rows",
                            "tags": []}, []
        elif current is None:
            if key not in PACK_KEYS:
                raise ValueError(f"line {n}: unknown pack directive @{key}")
            if key == "pack":
                pack["name"] = value
            elif key == "description":
                pack["description"] = value
            else:
                pack["tags"] = _tags(value)
        else:
            if key not in QUERY_KEYS:
                raise ValueError(f"line {n}: unknown query directive @{key}")
            if key == "severity":
                if value.upper() not in SEVERITIES:
                    raise ValueError(f"line {n}: severity must be one of {list(SEVERITIES)}")
                current["severity"] = value.upper()
            elif key == "description":
                current["description"] = value
            elif key == "expect":
                current["expectation"] = value or "0 rows"
            else:
                current["tags"] = _tags(value)
    close(len((text or "").splitlines()) + 1)
    if not pack["name"].strip():
        raise ValueError("the pack has no -- @pack name")
    if not pack["queries"]:
        raise ValueError("the pack has no queries")
    return pack


def render(pack: Dict[str, Any]) -> str:
    out = [f"-- @pack {pack['name']}"]
    if pack.get("description"):
        out.append(f"-- @description {pack['description']}")
    if pack.get("tags"):
        out.append(f"-- @tags {', '.join(pack['tags'])}")
    for q in pack.get("queries") or []:
        out += ["", f"-- @query {q['query_name']}", f"-- @severity {q.get('severity') or 'ERROR'}"]
        if q.get("description"):
            out.append(f"-- @description {q['description']}")
        if (q.get("expectation") or "0 rows") != "0 rows":
            out.append(f"-- @expect {q['expectation']}")
        if q.get("tags"):
            out.append(f"-- @tags {', '.join(q['tags'])}")
        out.append(q["sql_expression"].strip())
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- importing a plain .sql file

READ_ONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def split_sql(text: str) -> List[str]:
    """Statements separated by ';', ignoring semicolons inside quotes and comments (comments are kept)."""
    out, buf, i, n = [], [], 0, len(text or "")
    quote: str | None = None
    while i < n:
        ch, nxt = text[i], text[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == quote:
                if nxt == quote:            # an escaped quote: '' or ""
                    buf.append(nxt)
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and nxt == "-":
            end = text.find("\n", i)
            end = n if end < 0 else end
            buf.append(text[i:end])
            i = end - 1
        elif ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            buf.append(text[i:end])
            i = end - 1
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()[:60].rstrip("_")
    return slug if slug and slug[0].isalpha() else ""


def parse_any(text: str, default_name: str = "") -> Dict[str, Any]:
    """A pack from a .sql file: the platform's annotated format (-- @pack / -- @query), or plain SQL statements
    separated by ';'. In plain SQL the comment lines just above a query become its description, and its name
    comes from the first of them (else query_<n>). Statements that are not read-only queries are left out and
    listed in `skipped`."""
    if any(DIRECTIVE.match(line) and DIRECTIVE.match(line).group(1).lower() == "query" for line in (text or "").splitlines()):
        return {**parse(text, default_name), "skipped": []}
    pack: Dict[str, Any] = {"name": default_name, "description": "", "tags": [], "queries": [], "skipped": []}
    names = set()
    for n, statement in enumerate(split_sql(text), start=1):
        lines = statement.splitlines()
        notes = []
        while lines and (lines[0].strip().startswith("--") or not lines[0].strip()):
            note = lines.pop(0).strip().lstrip("-").strip()
            if note:
                notes.append(note)
        sql = "\n".join(lines).strip()
        while True:   # a leading /* block comment */ is a note too
            m = re.match(r"^/\*(.*?)\*/\s*", sql, re.S)
            if not m:
                break
            if m.group(1).strip():
                notes.append(" ".join(m.group(1).split()))
            sql = sql[m.end():]
        if not sql:
            continue
        if not READ_ONLY.match(sql):
            pack["skipped"].append(f"statement {n}: not a read-only query ({' '.join(sql.split())[:60]})")
            continue
        name = _slug(notes[0]) if notes else ""
        name = name or f"query_{len(pack['queries']) + 1}"
        base, k = name, 2
        while name.lower() in names:
            name, k = f"{base}_{k}", k + 1
        names.add(name.lower())
        pack["queries"].append({"query_name": name, "severity": "ERROR", "description": " ".join(notes)[:4000],
                                "expectation": "0 rows", "tags": [], "sql_expression": sql})
    if not pack["name"].strip():
        raise ValueError("give the pack a name")
    if not pack["queries"]:
        raise ValueError("the file has no read-only queries" + (f" ({len(pack['skipped'])} other statements)" if pack["skipped"] else ""))
    return pack
