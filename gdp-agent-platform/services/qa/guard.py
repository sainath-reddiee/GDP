"""Read-only guard for test SQL written by people or by Cortex.

A test query must be one SELECT (optionally WITH ...), must not contain statements or functions that change
state, and may only read the run's own source tables, its target and the domain hub, by fully qualified name.

The query is read left to right by one tokenizer that follows Snowflake's lexical rules: single-quoted strings
('' and backslash escapes), $$ strings, double-quoted names ("" escapes), and --, // and /* */ comments. Comments
are dropped and string contents are never read as SQL, so a '--' inside a string cannot hide the rest of the query
and a keyword inside a string is only text. Every FROM list item (comma joins included), every JOIN item and every
subquery or CTE body is checked; CTE names may be referenced. Table functions (TABLE(...), FLATTEN, GENERATOR, ...),
RESULT_SCAN, IDENTIFIER(), stages, INFORMATION_SCHEMA and ACCOUNT_USAGE are refused. It has no dependencies, so it
runs unchanged inside Snowpark procedures.
"""

from __future__ import annotations

import re
from typing import Iterable, List, NamedTuple, Optional, Set, Tuple

# kept for services.soda.custom, which screens short Soda conditions with it
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|GRANT|REVOKE|CALL|EXECUTE|COPY|PUT|GET|REMOVE|"
    r"UNDROP|USE|SET|UNSET|BEGIN|COMMIT|ROLLBACK)\b|SYSTEM\$", re.IGNORECASE)
FORBIDDEN_WORDS = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "DROP", "CREATE", "ALTER", "GRANT", "REVOKE",
                   "CALL", "EXECUTE", "COPY", "PUT", "GET", "REMOVE", "UNDROP", "USE", "SET", "UNSET", "BEGIN",
                   "COMMIT", "ROLLBACK"}
# functions whose argument list uses FROM as a keyword: EXTRACT(YEAR FROM d), TRIM(' ' FROM c), ...
FROM_FUNCTIONS = {"EXTRACT", "TRIM", "SUBSTRING", "POSITION", "OVERLAY"}
# reserved words that end a FROM list (reserved, so they can never be an unquoted alias)
CLAUSE_END = {"WHERE", "GROUP", "HAVING", "QUALIFY", "ORDER", "UNION", "MINUS", "INTERSECT", "CONNECT", "START",
              "SELECT"}
SYSTEM_SCHEMAS = {"INFORMATION_SCHEMA", "ACCOUNT_USAGE", "READER_ACCOUNT_USAGE", "ORGANIZATION_USAGE"}
OPERATORS = ("::", "||", "<=", ">=", "<>", "!=", "=>")


class Token(NamedTuple):
    kind: str    # id (unquoted word), qid ("quoted name"), str, num, var ($name, $1), punct
    text: str    # id: as written; qid: the name without quotes; str: the contents; punct: the operator
    start: int
    end: int

    @property
    def word(self) -> str:
        return self.text.upper() if self.kind == "id" else ""


def tokenize(sql: str) -> Tuple[List[Token], Optional[str]]:
    """(tokens, error). Comments are dropped; an unterminated string, quoted name or comment is an error."""
    out: List[Token] = []
    i, n = 0, len(sql)
    while i < n:
        ch, two = sql[i], sql[i:i + 2]
        if ch.isspace():
            i += 1
        elif two in ("--", "//"):
            # ends at the first line break of any kind: ending early only shows the guard more, never less
            ends = [e for e in (sql.find("\n", i), sql.find("\r", i)) if e >= 0]
            i = min(ends) + 1 if ends else n
        elif two == "/*":
            end = sql.find("*/", i + 2)
            if end < 0:
                return out, "unterminated /* comment"
            i = end + 2
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                elif sql[j] == "'" and sql[j + 1:j + 2] == "'":
                    j += 2
                elif sql[j] == "'":
                    break
                else:
                    j += 1
            if j >= n:
                return out, "unterminated string"
            out.append(Token("str", sql[i + 1:j], i, j + 1))
            i = j + 1
        elif two == "$$":
            end = sql.find("$$", i + 2)
            if end < 0:
                return out, "unterminated $$ string"
            out.append(Token("str", sql[i + 2:end], i, end + 2))
            i = end + 2
        elif ch == '"':
            j, name = i + 1, []
            while j < n:
                if sql[j] == '"' and sql[j + 1:j + 2] == '"':
                    name.append('"')
                    j += 2
                elif sql[j] == '"':
                    break
                else:
                    name.append(sql[j])
                    j += 1
            if j >= n:
                return out, "unterminated quoted name"
            out.append(Token("qid", "".join(name), i, j + 1))
            i = j + 1
        elif ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            out.append(Token("id", sql[i:j], i, j))
            i = j
        elif ch.isdigit():
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "." or (sql[j] in "+-" and sql[j - 1] in "eE")):
                j += 1
            out.append(Token("num", sql[i:j], i, j))
            i = j
        elif ch == "$":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            out.append(Token("var", sql[i:j], i, j))
            i = j
        else:
            text = two if two in OPERATORS else ch
            out.append(Token("punct", text, i, i + len(text)))
            i += len(text)
    return out, None


def _part(token: Token) -> str:
    return token.text if token.kind == "qid" else token.text.upper()


def _allowed_parts(name: str) -> Optional[Tuple[Tuple[str, bool, str], ...]]:
    """An allowed DB.SCHEMA.TABLE as ((normalised, quoted, as written), ...); None when it is not three parts."""
    tokens, error = tokenize(name or "")
    parts = [t for t in tokens if t.kind in ("id", "qid")]
    if error or len(parts) != 3 or len(tokens) != 5:
        return None
    return tuple((_part(t), t.kind == "qid", t.text) for t in parts)


def _matches(ref: Tuple[str, ...], allowed: Tuple[Tuple[str, bool, str], ...]) -> bool:
    """Unquoted names compare upper-case and a quoted part keeps its case, so "dim_customer" is not DIM_CUSTOMER.
    An allowed name recorded unquoted in mixed case (as a registry may hold it) also matches that exact spelling."""
    return all(got == norm or (not quoted and got == raw) for got, (norm, quoted, raw) in zip(ref, allowed))


class _Checker:
    def __init__(self, tokens: List[Token], sql: str, allowed: Iterable[str]):
        self.t, self.sql = tokens, sql
        self.allow = [a for a in (_allowed_parts(x) for x in allowed) if a]
        self.ctes: Set[str] = set()
        self.problems: List[str] = []

    def add(self, problem: str) -> None:
        if problem not in self.problems:
            self.problems.append(problem)

    def at(self, k: int) -> Optional[Token]:
        return self.t[k] if 0 <= k < len(self.t) else None

    def is_punct(self, k: int, text: str) -> bool:
        tok = self.at(k)
        return tok is not None and tok.kind == "punct" and tok.text == text

    def word(self, k: int) -> str:
        tok = self.at(k)
        return tok.word if tok else ""

    def close(self, k: int) -> int:
        """Index of the ')' matching the '(' at k; len(tokens) when it is unbalanced."""
        depth = 0
        for j in range(k, len(self.t)):
            if self.is_punct(j, "("):
                depth += 1
            elif self.is_punct(j, ")"):
                depth -= 1
                if depth == 0:
                    return j
        return len(self.t)

    def collect_ctes(self) -> None:
        """Every WITH [RECURSIVE] name [(cols)] AS (...) [, name AS (...)] in the query, nested ones included."""
        for k, tok in enumerate(self.t):
            if tok.word != "WITH":
                continue
            j = k + 1 + (self.word(k + 1) == "RECURSIVE")
            while True:
                name = self.at(j)
                if name is None or name.kind not in ("id", "qid"):
                    break
                j += 1
                if self.is_punct(j, "("):
                    j = self.close(j) + 1
                if self.word(j) != "AS" or not self.is_punct(j + 1, "("):
                    break
                self.ctes.add(_part(name))
                j = self.close(j + 1) + 1
                if not self.is_punct(j, ","):
                    break
                j += 1

    def item(self, k: int) -> None:
        """One FROM or JOIN item starting at token k."""
        while self.word(k) == "LATERAL":
            k += 1
        tok = self.at(k)
        if tok is None or (tok.kind == "punct" and tok.text in (")", ",", ";")):
            self.add("a FROM or JOIN is missing its table")
            return
        if tok.kind == "punct" and tok.text == "(":
            inner = self.at(k + 1)
            if inner is not None and inner.word not in ("SELECT", "WITH", "VALUES"):
                self.items(k + 1)  # a parenthesised FROM list or join: (A a JOIN B b ON ...), ((A a), B b)
            return  # a subquery: its own FROM and JOIN items are checked where they stand
        if (tok.kind == "punct" and tok.text == "@") or tok.kind == "str":
            self.add("stage references are not allowed")
            return
        if tok.kind == "var":
            self.add("variables are not allowed as table names")
            return
        if tok.word == "VALUES":
            return
        if tok.kind not in ("id", "qid"):
            self.add(f"{tok.text!r} is not a table name")
            return
        parts, first, j = [_part(tok)], tok, k + 1
        while self.is_punct(j, "."):
            nxt = self.at(j + 1)
            if nxt is not None and nxt.kind in ("id", "qid"):
                parts.append(_part(nxt))
                j += 2
            else:  # DB..TABLE (the default schema)
                parts.append("")
                j += 1
        raw = self.sql[first.start:self.t[j - 1].end]
        if self.is_punct(j, "("):
            self.add("IDENTIFIER() is not allowed" if first.word == "IDENTIFIER" and len(parts) == 1
                     else "table functions are not allowed")
            return
        if any(p.upper() in SYSTEM_SCHEMAS for p in parts):
            self.add(f"{raw}: system views (INFORMATION_SCHEMA, ACCOUNT_USAGE) are not allowed")
            return
        if len(parts) == 1 and parts[0] in self.ctes:
            return
        if len(parts) != 3 or "" in parts:
            self.add(f"{raw}: use the fully qualified DATABASE.SCHEMA.TABLE name")
        elif not any(_matches(tuple(parts), a) for a in self.allow):
            self.add(f"{raw} is not one of this run's source or target tables")

    def items(self, k: int) -> None:
        """A FROM list from token k: the first item, then every item after a comma at the same depth."""
        self.item(k)
        depth, j = 0, k
        while j < len(self.t):
            tok = self.t[j]
            if tok.kind == "punct":
                if tok.text == "(":
                    depth += 1
                elif tok.text == ")":
                    if depth == 0:
                        return
                    depth -= 1
                elif depth == 0 and tok.text == ";":
                    return
                elif depth == 0 and tok.text == ",":
                    self.item(j + 1)
            elif depth == 0 and j > k and tok.word in CLAUSE_END:
                return
            j += 1

    def walk(self) -> None:
        stack: List[str] = []
        for k, tok in enumerate(self.t):
            if tok.kind == "punct":
                if tok.text == "(":
                    stack.append(self.word(k - 1))
                elif tok.text == ")":
                    if stack:
                        stack.pop()
                    else:
                        self.add("unbalanced parentheses")
                elif tok.text == "@":
                    self.add("stage references are not allowed")
                continue
            if tok.kind == "qid" and tok.text.upper() in SYSTEM_SCHEMAS:
                self.add("system views (INFORMATION_SCHEMA, ACCOUNT_USAGE) are not allowed")
            if tok.kind != "id":
                continue
            word = tok.word
            if word in FORBIDDEN_WORDS or word.startswith("SYSTEM$"):
                self.add(f"'{tok.text}' is not allowed in a read-only test")
            elif word in SYSTEM_SCHEMAS:
                self.add("system views (INFORMATION_SCHEMA, ACCOUNT_USAGE) are not allowed")
            elif word == "RESULT_SCAN":
                self.add("RESULT_SCAN is not allowed")
            elif word == "IDENTIFIER" and self.is_punct(k + 1, "("):
                self.add("IDENTIFIER() is not allowed")
            elif word == "JOIN":
                self.item(k + 1)
            elif word == "FROM":
                if stack and stack[-1] in FROM_FUNCTIONS:
                    continue  # EXTRACT(YEAR FROM col) and friends
                if self.word(k - 1) == "DISTINCT" and self.word(k - 2) in ("IS", "NOT"):
                    continue  # a IS [NOT] DISTINCT FROM b
                if self.word(k + 1) in ("FIRST", "LAST") and self.is_punct(k - 1, ")"):
                    continue  # NTH_VALUE(x, 2) FROM FIRST
                self.items(k + 1)
        if stack:
            self.add("unbalanced parentheses")


def check(sql: str, allowed: Iterable[str]) -> Tuple[bool, List[str], str]:
    """Returns (ok, problems, cleaned_sql). `allowed` holds fully qualified DB.SCHEMA.TABLE names."""
    text = (sql or "").strip()
    if not text:
        return False, ["empty query"], ""
    tokens, error = tokenize(text)
    if error:
        return False, [error], text
    # trailing semicolons, and the comments after them, are dropped
    last = len(tokens)
    while last and tokens[last - 1].kind == "punct" and tokens[last - 1].text == ";":
        last -= 1
    cleaned = text[:tokens[last].start].rstrip() if last < len(tokens) else text.rstrip()
    tokens = tokens[:last]
    checker = _Checker(tokens, cleaned, allowed)
    if any(t.kind == "punct" and t.text == ";" for t in tokens):
        checker.add("only one statement is allowed")
    if not tokens or tokens[0].word not in ("SELECT", "WITH"):
        checker.add("a test query must start with SELECT or WITH")
    checker.collect_ctes()
    checker.walk()
    return not checker.problems, checker.problems, cleaned
