"""Rewrite references to moved platform objects: `<OLD_SCHEMA>.<NAME>` becomes `CORE.<NEW_NAME>`.

    python -m infrastructure.consolidation.codemod [--check] [paths...]

Driven only by services.common.platform_schema.MOVED, so strings that merely look qualified (app privilege codes such
as OPS.VIEW, JIRA.READ, KNOWLEDGE.EDIT) are never touched. Deploy-rendered SQL templates (snowflake/procedures,
views, cortex_search, agents) get `{{database}}.{{schema}}.<NAME>`; everything else gets the literal schema. Applied
migrations (V001 to V061) and generated files are skipped. --check exits non-zero if anything would change.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.common.platform_schema import MOVED, PLATFORM_SCHEMA  # noqa: E402

DEFAULT_PATHS = ("services", "apps/api", "infrastructure", "snowflake", "tests", "docs", "airflow_plugins", "docker",
                 "README.md")
TEMPLATE_DIRS = ("snowflake/procedures", "snowflake/views", "snowflake/cortex_search", "snowflake/agents")
SKIP = ("snowflake/database/migrations/", "snowflake/views/compat_legacy.sql", "services/common/platform_schema.py",
        "infrastructure/consolidation/", "tests/unit/test_platform_schema.py")
EXTS = {".py", ".sql", ".md", ".yaml", ".yml", ".json", ".j2", ".jinja", ".txt", ".toml"}

_DB = r"(?:\{\{\s*database\s*\}\}|\{[^{}\s]+\}|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_$]*)"
_NAMES = {}
for (schema, name), (_, new, _) in MOVED.items():
    _NAMES.setdefault(schema, {})[name] = new
PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_$])(?P<db>{_DB}\.)?(?P<schema>{'|'.join(sorted(_NAMES, key=len, reverse=True))})\."
    rf"(?P<name>[A-Z][A-Z0-9_]*)(?![A-Za-z0-9_$])"
)


def rewrite(text: str, template: bool) -> str:
    def sub(m: re.Match) -> str:
        new = _NAMES[m.group("schema")].get(m.group("name"))
        if new is None:
            return m.group(0)
        db = m.group("db") or ""
        if template and db.replace(" ", "").startswith("{{database}}"):
            return f"{{{{database}}}}.{{{{schema}}}}.{new}"
        if db.upper() == f"{PLATFORM_SCHEMA}.":  # CORE.<schema>.<name> is not a database-qualified name
            return m.group(0)
        return f"{db}{PLATFORM_SCHEMA}.{new}"

    return PATTERN.sub(sub, text)


def files(paths):
    for raw in paths:
        p = ROOT / raw
        candidates = [p] if p.is_file() else sorted(p.rglob("*")) if p.exists() else []
        for f in candidates:
            rel = f.relative_to(ROOT).as_posix()
            if not f.is_file() or f.suffix not in EXTS or "__pycache__" in rel or "node_modules" in rel:
                continue
            if any(rel.startswith(s) for s in SKIP):
                continue
            yield f, rel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    args = parser.parse_args()
    changed = []
    for f, rel in files(args.paths):
        text = f.read_bytes().decode("utf-8")  # keeps CRLF/LF as committed
        new = rewrite(text, template=rel.startswith(TEMPLATE_DIRS))
        if new != text:
            changed.append((rel, sum(1 for _ in PATTERN.finditer(text))))
            if not args.check:
                f.write_bytes(new.encode("utf-8"))
    for rel, n in changed:
        print(f"{rel}: {n}")
    print(f"{len(changed)} file(s) {'would change' if args.check else 'changed'}")
    if args.check and changed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
