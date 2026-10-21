"""Environment bindings: logical names in stored SQL, resolved per environment (DEV, QA, PROD or a client database).

QA pack SQL and Data Quality suite files can name tables through a logical reference instead of a fixed database:

    SELECT ... FROM {{silver}}.COMPANY.COMPANY_CORE        silver bound to a database: DEV_GDP_SILVER_DB
    SELECT ... FROM {{company}}.COMPANY_CORE               company bound to a database and schema: DEV_GDP_SILVER_DB.COMPANY
    SELECT ... FROM {{domain}}.COMPANY_CORE                the pack's own domain: its Silver DATABASE.SCHEMA

The bindings live in CORE.PLATFORM_CONFIG under ENVIRONMENT_BINDINGS (versioned, edited in Admin, Environments):

    {"current": "DEV",
     "environments": [{"name": "DEV", "description": "...", "bindings": {"silver": "DEV_GDP_SILVER_DB"}},
                      {"name": "DA",  "description": "...", "bindings": {"silver": "DA_GDP_SILVER_DB"}}]}

`current` is the environment this deployment's tables belong to; the others are targets a pack can be run against.
Fully qualified SQL keeps working as it is: to run it on another environment it is first rewritten to logical names
with the current environment's bindings (to_logical), then resolved with the target's (resolve). References are
found with the QA guard's tokenizer, so names inside strings and comments are never rewritten. Pure, apart from
load() and domain_location(), which read the platform tables.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.qa.guard import Token, tokenize

CONFIG_KEY = "ENVIRONMENT_BINDINGS"
DEFAULT: Dict[str, Any] = {"current": None, "environments": []}
BINDING = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENVIRONMENT = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
PLAIN = re.compile(r"^[A-Z_][A-Z0-9_$]*$")
DOMAIN = "domain"           # reserved: the pack's own domain, never bound by hand
RESERVED = {DOMAIN}
MAX_ENVIRONMENTS = 20
MAX_BINDINGS = 50


# ---------------------------------------------------------------- locations


def _part(token: Token) -> str:
    return token.text if token.kind == "qid" else token.text.upper()


def location_parts(text: str) -> Optional[Tuple[str, ...]]:
    """DATABASE or DATABASE.SCHEMA as normalised parts (unquoted names upper-case, quoted ones as written), or None."""
    tokens, error = tokenize(str(text or "").strip())
    if error or not tokens or len(tokens) not in (1, 3):
        return None
    names, dots = tokens[0::2], tokens[1::2]
    if any(t.kind not in ("id", "qid") or (t.kind == "qid" and not t.text) for t in names):
        return None
    if any(t.kind != "punct" or t.text != "." for t in dots):
        return None
    return tuple(_part(t) for t in names)


def render_parts(parts: Sequence[str]) -> str:
    """Parts as SQL: plain upper-case names bare, anything else quoted."""
    return ".".join(p if PLAIN.match(p) else '"' + p.replace('"', '""') + '"' for p in parts)


def normalise_location(text: str) -> Optional[str]:
    parts = location_parts(text)
    return render_parts(parts) if parts else None


# ---------------------------------------------------------------- the setting


def validate(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(cleaned setting, problems). Nothing is saved while problems remain."""
    if value in (None, ""):
        return dict(DEFAULT), []
    if not isinstance(value, dict):
        return dict(DEFAULT), ["environment bindings must be an object with current and environments"]
    problems: List[str] = []
    envs = value.get("environments") or []
    if not isinstance(envs, list):
        return dict(DEFAULT), ["environments must be a list"]
    if len(envs) > MAX_ENVIRONMENTS:
        problems.append(f"at most {MAX_ENVIRONMENTS} environments")
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, env in enumerate(envs[:MAX_ENVIRONMENTS]):
        if not isinstance(env, dict):
            problems.append(f"environment {i + 1}: must be an object")
            continue
        name = str(env.get("name") or "").strip().upper()
        if not ENVIRONMENT.match(name):
            problems.append(f"environment {i + 1}: names are letters, digits, _ and - (starting with a letter), "
                            "at most 32 characters")
            continue
        if name in seen:
            problems.append(f"environment {name} appears twice")
            continue
        seen.add(name)
        raw = env.get("bindings") or {}
        if not isinstance(raw, dict):
            problems.append(f"{name}: bindings must be an object of name -> DATABASE or DATABASE.SCHEMA")
            continue
        if len(raw) > MAX_BINDINGS:
            problems.append(f"{name}: at most {MAX_BINDINGS} bindings")
        bindings: Dict[str, str] = {}
        for key, target in list(raw.items())[:MAX_BINDINGS]:
            key = str(key or "").strip().lower()
            if not BINDING.match(key):
                problems.append(f"{name}: binding {key or '(empty)'}: names are lower-case letters, digits and _")
                continue
            if key in RESERVED:
                problems.append(f"{name}: {{{{{key}}}}} is reserved (it is the pack's own domain)")
                continue
            if key in bindings:
                problems.append(f"{name}: binding {key} appears twice")
                continue
            where = normalise_location(str(target or ""))
            if not where:
                problems.append(f"{name}: {key} must be DATABASE or DATABASE.SCHEMA")
                continue
            bindings[key] = where
        out.append({"name": name, "description": str(env.get("description") or "").strip()[:500],
                    "bindings": dict(sorted(bindings.items()))})
    current = str(value.get("current") or "").strip().upper() or None
    if out and not current:
        problems.append("choose the environment this deployment's tables belong to (current)")
    elif current and current not in seen:
        problems.append(f"the current environment {current} is not in the list")
    return {"current": current, "environments": out}, problems


def environment(config: Optional[Dict[str, Any]], name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The named environment, or the current one when no name is given; None when it is not configured."""
    config = config or DEFAULT
    wanted = str(name or config.get("current") or "").strip().upper()
    return next((e for e in config.get("environments") or [] if str(e.get("name") or "").upper() == wanted), None)


def bindings_of(config: Optional[Dict[str, Any]], name: Optional[str] = None) -> Dict[str, str]:
    env = environment(config, name)
    return dict((env or {}).get("bindings") or {})


def is_current(config: Optional[Dict[str, Any]], name: Optional[str]) -> bool:
    """True when `name` is empty or names the current environment."""
    if not name:
        return True
    return str(name).strip().upper() == str((config or {}).get("current") or "").strip().upper()


# ---------------------------------------------------------------- references in SQL


def _references(tokens: List[Token]) -> List[Tuple[int, int, str]]:
    """(start, end, name) of every {{ name }} in the token stream; strings and comments never hold one."""
    out = []
    for k in range(len(tokens) - 4):
        a, b, name, c, d = tokens[k:k + 5]
        if (a.kind == b.kind == c.kind == d.kind == "punct" and a.text == b.text == "{" and c.text == d.text == "}"
                and name.kind == "id" and b.start == a.end and d.start == c.end):
            out.append((a.start, d.end, name.text.lower()))
    return out


def references(sql: str) -> List[str]:
    """The logical names a query uses, in order of first use."""
    tokens, _ = tokenize(sql or "")
    return list(dict.fromkeys(name for _, _, name in _references(tokens)))


def is_logical(sql: str) -> bool:
    return bool(references(sql))


def resolve(sql: str, bindings: Dict[str, str], environment_name: str = "") -> Tuple[str, List[str]]:
    """(SQL with every {{name}} replaced by its binding, problems). A name with no binding stays as written and is
    reported, so the guard never sees half-resolved SQL as valid."""
    text = sql or ""
    tokens, error = tokenize(text)
    if error:
        return text, [error]
    known = {k.lower(): v for k, v in (bindings or {}).items()}
    out, last, problems = [], 0, []
    where = f" in environment {environment_name}" if environment_name else ""
    for start, end, name in _references(tokens):
        value = known.get(name)
        if value is None:
            hint = ("the pack has no domain location" if name == DOMAIN
                    else "add it in Admin, Environments")
            message = f"{{{{{name}}}}} is not bound{where} ({hint})"
            if message not in problems:
                problems.append(message)
            continue
        out.append(text[last:start])
        out.append(value)
        last = end
    out.append(text[last:])
    return "".join(out), problems


def _chains(tokens: List[Token]) -> List[List[Token]]:
    """Every dotted name (A.B.C ...) that starts a chain: its first part is not itself after a dot."""
    out = []
    k = 0
    while k < len(tokens):
        tok = tokens[k]
        after_dot = k > 0 and tokens[k - 1].kind == "punct" and tokens[k - 1].text == "."
        if tok.kind in ("id", "qid") and not after_dot:
            chain, j = [tok], k + 1
            while (j + 1 < len(tokens) and tokens[j].kind == "punct" and tokens[j].text == "."
                   and tokens[j + 1].kind in ("id", "qid")):
                chain.append(tokens[j + 1])
                j += 2
            out.append(chain)
            k = j
        else:
            k += 1
    return out


def _by_location(bindings: Dict[str, str]) -> List[Tuple[Tuple[str, ...], str]]:
    """(parts, name), DATABASE.SCHEMA bindings before DATABASE ones, then by name, so the most specific wins."""
    found = []
    for name, target in sorted((bindings or {}).items()):
        parts = location_parts(target)
        if parts and name not in RESERVED:
            found.append((parts, name))
    return sorted(found, key=lambda pn: (-len(pn[0]), pn[1]))


def to_logical(sql: str, bindings: Dict[str, str]) -> str:
    """Fully qualified names written with logical references wherever a binding covers their database (or database
    and schema): DEV_GDP_SILVER_DB.COMPANY.COMPANY_CORE -> {{silver}}.COMPANY.COMPANY_CORE. Only names of three or
    more parts are rewritten, so an alias.column is never taken for a database."""
    text = sql or ""
    tokens, error = tokenize(text)
    if error:
        return text
    locations = _by_location(bindings)
    edits: List[Tuple[int, int, str]] = []
    for chain in _chains(tokens):
        if len(chain) < 3:
            continue
        parts = tuple(_part(t) for t in chain)
        for loc, name in locations:
            if len(chain) > len(loc) and parts[:len(loc)] == loc:
                edits.append((chain[0].start, chain[len(loc) - 1].end, "{{" + name + "}}"))
                break
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    return text


def rebase(sql: str, source: Dict[str, str], target: Dict[str, str], target_name: str = "",
           extra: Optional[Dict[str, str]] = None) -> Tuple[str, List[str]]:
    """SQL written for the source environment (fully qualified or logical), resolved for the target."""
    return resolve(to_logical(sql, source), {**(target or {}), **(extra or {})}, target_name)


def rebase_tables(tables: Iterable[str], source: Dict[str, str], target: Dict[str, str]) -> List[str]:
    """The target environment's names of the source environment's tables. A table no binding covers has no name in
    the target and is left out, so a run on another environment can never read the current one by accident."""
    out = []
    for table in tables:
        logical = to_logical(table, source)
        if logical == table:
            continue
        resolved, problems = resolve(logical, target)
        if not problems:
            out.append(resolved)
    return list(dict.fromkeys(out))


def rebase_location(location: Optional[str], source: Dict[str, str], target: Dict[str, str]) -> Optional[str]:
    """A DATABASE.SCHEMA of the source environment in the target, or None when no binding covers it."""
    if not location:
        return None
    moved = rebase_tables([f"{location}.X"], source, target)
    return moved[0][:-2] if moved else None


# ---------------------------------------------------------------- Snowpark reads


def load(session) -> Dict[str, Any]:
    """The current setting (DEFAULT before anyone saved one)."""
    from services.common.sql import config_value

    try:
        value = config_value(session, CONFIG_KEY, None)
    except Exception:
        return dict(DEFAULT)
    cleaned, _ = validate(value)
    return cleaned


def domain_location(session, domain_id: Optional[str]) -> Optional[str]:
    """The domain's Silver DATABASE.SCHEMA in the current environment: its configured silver location, else the
    database and schema most of its active target tables live in."""
    from services.common.sql import rows

    if not domain_id:
        return None
    try:
        found = rows(session, """SELECT CONFIG:silver_database::VARCHAR AS DB, CONFIG:silver_schema::VARCHAR AS SCH
                                   FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?""", [domain_id])
        if found and found[0].get("DB") and found[0].get("SCH"):
            return normalise_location(f"{found[0]['DB']}.{found[0]['SCH']}")
        found = rows(session, """SELECT TARGET_DATABASE AS DB, TARGET_SCHEMA AS SCH, COUNT(*) AS N
                                   FROM CORE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = ? AND ACTIVE_FLAG
                                  GROUP BY 1, 2 ORDER BY N DESC LIMIT 1""", [domain_id])
    except Exception:
        return None
    if found and found[0].get("DB") and found[0].get("SCH"):
        return normalise_location(f"{found[0]['DB']}.{found[0]['SCH']}")
    return None
