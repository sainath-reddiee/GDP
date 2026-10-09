"""Skill versions, release labels, categories and stage bindings.

Every skill version is an immutable KNOWLEDGE.SKILL_REGISTRY row. Which version a stage loads:
  1. the run's override (CORE.RUN_SKILL_OVERRIDE), set when someone tries a candidate on one run
  2. the version holding the "production" label (KNOWLEDGE.SKILL_LABEL)
  3. the newest ACTIVE revision
Which skills a stage loads comes from KNOWLEDGE.SKILL_STAGE_BINDING (enabled, ordered), falling back to the built-in
STAGE_SKILLS map when no binding exists yet. The pure helpers here are shared by the seed, the procedures and the API.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

LABELS = ("production", "candidate")

# folder prefix (relative to snowflake/skills) -> category, checked longest first
FOLDER_CATEGORY = {
    "gdp/bronze-schema-ddl-extraction": "source-onboarding", "gdp/schema-ddl-extraction": "source-onboarding",
    "gdp/dbt-onboard-source": "dbt", "gdp/silver-model": "dbt", "gdp/gold-model": "dbt",
    "gdp/dev-datareadiness-check": "validation", "gdp/qa-datareadiness-check": "validation",
    "gdp_domain": "domain-standards", "gdp": "domain-standards",
    "modeling": "data-modeling", "mapping": "mapping", "profiling": "profiling", "soda": "data-quality",
    "source_onboarding": "source-onboarding", "sttm": "sttm", "validation": "validation",
}
# words in a sub-skill name -> category (the modeling umbrella holds profiling and mapping playbooks too)
NAME_CATEGORY = [
    (("PROFIL", "COLUMN-DESCRIPTION", "DEEP-QUALITY", "SEMANTIC-COLUMN"), "profiling"),
    (("MAPPING",), "mapping"),
    (("SODA", "QUALITY", "READINESS"), "data-quality"),
    (("DBT", "DDL", "SILVER", "GOLD"), "dbt"),
    (("VALIDAT",), "validation"),
]
TYPE_CATEGORY = {"PROFILING": "profiling", "MAPPING": "mapping", "STTM": "sttm", "SODA": "data-quality",
                 "DBT": "dbt", "VALIDATION": "validation", "SOURCE_ONBOARDING": "source-onboarding",
                 "DOMAIN": "domain-standards"}
CATEGORY_IDS = {"source-onboarding", "profiling", "data-modeling", "mapping", "sttm", "data-quality", "dbt",
                "validation", "domain-standards", "general"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:64]


def category_for(folder: str, declared: Optional[str], skill_type: str, name: str,
                 parent: Optional[str] = None) -> str:
    """Category a repository skill belongs to: frontmatter `category:` wins, then the folder, then the type."""
    if declared and slug(declared):
        return slug(declared)
    upper = (name or "").upper()
    if parent:  # sub-skills: their own name says more than the umbrella folder
        for words, cat in NAME_CATEGORY:
            if any(w in upper for w in words):
                return cat
    folder = (folder or "").strip("/")
    for prefix in sorted(FOLDER_CATEGORY, key=len, reverse=True):
        if folder == prefix or folder.startswith(prefix + "/"):
            return FOLDER_CATEGORY[prefix]
    return TYPE_CATEGORY.get((skill_type or "").upper(), "general")


def version_key(version: Any) -> Tuple:
    """Semantic order: 1.10.0 after 1.9.0; build metadata after '+' and text parts sort after numbers."""
    core = str(version or "0").split("+", 1)[0]
    out: List[Tuple[int, Any]] = []
    for part in re.split(r"[.\-]", core):
        out.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(out)


def next_version(base: Any, existing: Iterable[Any] = ()) -> str:
    """Patch bump of the base version (build metadata dropped), skipping versions already taken: 1.0.0+ab12 -> 1.0.1."""
    parts = [int(p) if p.isdigit() else 0 for p in str(base or "1.0.0").split("+", 1)[0].split("-", 1)[0].split(".")]
    parts = (parts + [0, 0, 0])[:3]
    taken = {str(v).split("+", 1)[0] for v in existing}
    while True:
        parts[2] += 1
        candidate = ".".join(str(p) for p in parts)
        if candidate not in taken:
            return candidate


def name_variants(name: str) -> List[str]:
    raw = (name or "").strip().upper()
    return sorted({raw, raw.replace("_", "-"), raw.replace("-", "_")})


def split_files(content: str) -> List[Dict[str, str]]:
    """SKILL.md body plus every bundled file (the seed appends each under '# <relpath>')."""
    from services.knowledge.usage import FILE_HEADER

    text = content or ""
    marks = list(FILE_HEADER.finditer(text))
    files = [{"path": "SKILL.md", "content": text[: marks[0].start()] if marks else text}]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        files.append({"path": m.group(0).strip().lstrip("#").strip(), "content": text[m.end(): end]})
    return [{"path": f["path"], "content": f["content"].strip("\n")} for f in files]


def diff_files(old: str, new: str) -> List[Dict[str, Any]]:
    """Per file: status (added, removed, changed, same), +/- counts and line ops [(op, old_no, new_no, text)]."""
    a = {f["path"]: f["content"] for f in split_files(old)}
    b = {f["path"]: f["content"] for f in split_files(new)}
    out = []
    for path in list(a) + [p for p in b if p not in a]:
        left, right = a.get(path, "").splitlines(), b.get(path, "").splitlines()
        status = "added" if path not in a else "removed" if path not in b else "same" if left == right else "changed"
        ops: List[List[Any]] = []
        plus = minus = 0
        if status != "same":
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
                if tag == "equal":
                    for k in range(i2 - i1):
                        ops.append([" ", i1 + k + 1, j1 + k + 1, left[i1 + k]])
                    continue
                for k in range(i1, i2):
                    ops.append(["-", k + 1, None, left[k]])
                    minus += 1
                for k in range(j1, j2):
                    ops.append(["+", None, k + 1, right[k]])
                    plus += 1
        out.append({"path": path, "status": status, "added": plus, "removed": minus, "ops": ops})
    return out


def compact(ops: List[List[Any]], context: int = 3) -> List[List[Any]]:
    """Keep changed lines plus `context` lines around them; a ['…', None, None, n] row marks n hidden lines."""
    keep = set()
    for i, op in enumerate(ops):
        if op[0] != " ":
            keep.update(range(max(0, i - context), min(len(ops), i + context + 1)))
    out: List[List[Any]] = []
    hidden = 0
    for i, op in enumerate(ops):
        if i in keep:
            if hidden:
                out.append(["…", None, None, hidden])
                hidden = 0
            out.append(op)
        else:
            hidden += 1
    if hidden:
        out.append(["…", None, None, hidden])
    return out


def pick(versions: List[Dict[str, Any]], override_id: Optional[str], production_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The version a stage loads (pure form of the SQL resolver, used by tests and the API)."""
    by_id = {v["skill_id"]: v for v in versions}
    if override_id and override_id in by_id:
        return by_id[override_id]
    if production_id and production_id in by_id:
        return by_id[production_id]
    active = [v for v in versions if str(v.get("status") or "").upper() == "ACTIVE"]
    return max(active, key=lambda v: (int(v.get("revision") or 0), version_key(v.get("version")))) if active else None


def bound(bindings: Iterable[Dict[str, Any]], stage: str, standard: str, fallback: List[str]) -> List[str]:
    """Skill names a stage loads, in order: enabled bindings for the stage, GDP-only ones only for GDP runs."""
    rows = [b for b in bindings if str(b.get("stage") or "").upper() == stage.upper()]
    if not rows:
        return list(fallback)
    rows.sort(key=lambda b: (int(b.get("position") or 100), str(b.get("skill_name"))))
    return [str(b["skill_name"]) for b in rows
            if b.get("enabled") is not False and (str(b.get("standard") or "ANY").upper() != "GDP" or standard == "GDP")]


# ---------------------------------------------------------------- Snowpark side (procedures)

RESOLVE_SQL = """
SELECT R.SKILL_ID, R.SKILL_NAME, R.SKILL_TYPE, R.VERSION, R.REVISION, R.STAGE_PATH, R.CHECKSUM, R.DESCRIPTION,
       R.CONTENT, R.CONFIG, R.STATUS,
       CASE WHEN O.SKILL_ID IS NOT NULL THEN 'override' WHEN L.SKILL_ID IS NOT NULL THEN 'production' ELSE 'latest' END AS PICKED_BY
  FROM KNOWLEDGE.SKILL_REGISTRY R
  LEFT JOIN CORE.RUN_SKILL_OVERRIDE O ON O.SKILL_ID = R.SKILL_ID AND O.RUN_ID = {run}
  LEFT JOIN KNOWLEDGE.SKILL_LABEL L ON L.SKILL_ID = R.SKILL_ID AND L.LABEL = 'production'
 WHERE R.SKILL_NAME IN ({names}) AND R.STATUS <> 'RETIRED'
   AND (O.SKILL_ID IS NOT NULL OR L.SKILL_ID IS NOT NULL OR R.STATUS = 'ACTIVE')
 ORDER BY (O.SKILL_ID IS NOT NULL) DESC, (L.SKILL_ID IS NOT NULL) DESC, R.REVISION DESC NULLS LAST, R.CREATED_AT DESC
 LIMIT 1
"""


def resolve_sql(names: List[str], placeholder: str = "?") -> str:
    return RESOLVE_SQL.format(run=placeholder, names=", ".join([placeholder] * len(names)))


def resolve(session, name: str, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from services.common.sql import rows

    names = name_variants(name)
    try:
        found = rows(session, resolve_sql(names), [run_id or ""] + names)
    except Exception:  # before V021: the old "current" row
        found = rows(session, "SELECT SKILL_ID, SKILL_NAME, SKILL_TYPE, VERSION, STAGE_PATH, CHECKSUM, DESCRIPTION, CONTENT, "
                              "CONFIG, STATUS, 'production' AS PICKED_BY FROM KNOWLEDGE.SKILL_REGISTRY "
                              "WHERE IS_CURRENT AND STATUS = 'ACTIVE' AND SKILL_NAME IN ("
                              + ", ".join(["?"] * len(names)) + ") ORDER BY CREATED_AT DESC LIMIT 1", names)
    return found[0] if found else None


def stage_skills(session, stage: str, standard: str = "GDP") -> List[str]:
    """Bound skills for a stage (falls back to the built-in map before any binding exists)."""
    from services.common.sql import rows
    from services.knowledge.usage import GDP_ONLY_SKILLS, STAGE_SKILLS

    fallback = [n for n in STAGE_SKILLS.get(stage, []) if standard == "GDP" or n not in GDP_ONLY_SKILLS]
    try:
        bindings = rows(session, "SELECT STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD FROM KNOWLEDGE.SKILL_STAGE_BINDING "
                                 "WHERE STAGE = ?", [stage.upper()])
    except Exception:
        return fallback
    return bound([{k.lower(): v for k, v in b.items()} for b in bindings], stage, standard, fallback)
