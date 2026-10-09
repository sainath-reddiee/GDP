"""Seed domain pack, skills, platform config and mapping weights (idempotent MERGE)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.knowledge.skills import category_for
from services.knowledge.packs import (GDP_DOMAIN_ID, GDP_TABLE_ID, LiveColumns, _dumps, _stable_id,  # noqa: F401
                                      domain_id, merge, merge_columns, pack_rows)

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = ROOT / "domain"
DOMAIN_PACK = DOMAIN_DIR / "gdp" / "domain_pack.json"
SKILLS_DIR = ROOT / "snowflake" / "skills"
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)

SCORING_CONFIG_ID = "00000000-0000-4000-a000-000000000010"

DEFAULT_WEIGHTS = {
    "semantic": 0.22, "keyword": 0.18, "datatype": 0.14, "statistical": 0.12,
    "domain": 0.14, "context": 0.08, "historical": 0.12,
}
DEFAULT_THRESHOLDS = {"auto_suggest": 0.78, "human_review": 0.45}

PLATFORM_CONFIG = [
    ("LLM_MODEL", "claude-sonnet-4-5", "Cortex AI_COMPLETE model"),
    ("EMBED_MODEL", "snowflake-arctic-embed-l-v2.0", "1024-d embedding model matching COLUMN_EMBEDDING"),
    ("MAPPING_TOP_K", 3, "Candidates kept per source column"),
    ("SKILLS_AUTO_PROMOTE_REPO", True,
     "A changed repository skill goes straight to production while no person has moved its production label"),
    ("DOMAIN_CONFIDENCE_THRESHOLD", 0.3, "Below this, identify_domain asks for confirmation"),
    ("CREDITS_PER_MILLION_TOKENS", {"default": 0, "claude-sonnet-4-5": 0}, "Cost estimate rates"),
    ("CATALOG_DISPLAY", {"hidden_target_tables": ["COMPLETE_EMPLOYEE_DETAILS"], "hidden_target_databases": ["ALATION_POC"],
      "hidden_target_schemas": ["GDP_SILVER"], "hidden_target_ids": ["00000000-0000-4000-a000-000000000002"],
      "hidden_domain_names": ["GDP"], "strip_tokens": ["GDP"]},
     "Catalog objects hidden from task screens and product tokens stripped from suggested names (editable in Admin)"),
]


def load_domain_pack() -> Dict[str, Any]:
    return json.loads(DOMAIN_PACK.read_text(encoding="utf-8"))


def load_domain_packs() -> List[Dict[str, Any]]:
    """Every domain/<name>/domain_pack.json; GDP first so its fixed IDs are assigned before the others."""
    from services.knowledge.validate import validate_pack

    packs = []
    for path in sorted(DOMAIN_DIR.glob("*/domain_pack.json")):
        pack = json.loads(path.read_text(encoding="utf-8"))
        problems = validate_pack(pack)
        assert not problems, f"{path.parent.name} domain pack is invalid: " + "; ".join(problems[:10])
        packs.append(pack)
    return sorted(packs, key=lambda pk: (pk["domain"]["name"] != "GDP", pk["domain"]["name"]))


def _frontmatter_meta(raw: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    current = None
    for line in raw.splitlines():
        if current and (line.startswith(" ") or line.startswith("\t")):
            meta[current] = (meta[current] + " " + line.strip()).strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current = key.strip()
        value = value.strip()
        if value in {">", "|", ">-", "|-"}:
            value = ""
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[current] = value
    return meta


def _skill_type(name: str, meta: Dict[str, str]) -> str:
    if meta.get("type"):
        return meta["type"].upper()[:32]
    lowered = name.lower()
    if any(token in lowered for token in ("profil", "description", "quality", "cluster")):
        return "PROFILING"
    if "mapping" in lowered or "schema-mapping" in lowered:
        return "MAPPING"
    if any(token in lowered for token in ("valid", "readiness", "sandbox")):
        return "VALIDATION"
    if any(token in lowered for token in ("dbt", "silver", "gold", "ddl", "model")):
        return "DBT"
    return "DOMAIN"


def parse_skill(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    matched = FRONTMATTER.match(text)
    if matched:
        meta = _frontmatter_meta(matched.group(1))
        body = matched.group(2).strip()
    else:
        meta = {"name": path.parent.name, "description": path.parent.name.replace("-", " ")}
        body = text.strip()
    extras = []
    for extra in sorted(path.parent.rglob("*")):
        if not extra.is_file() or extra.resolve() == path.resolve():
            continue
        if extra.name == "config.json" or extra.suffix.lower() not in {".md", ".sql", ".yml", ".yaml", ".html"}:
            continue
        rel = extra.relative_to(path.parent).as_posix()
        extras.append(f"\n\n# {rel}\n" + extra.read_text(encoding="utf-8", errors="replace"))
    content = body + "".join(extras)
    config_path = path.parent / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else None
    raw_name = (meta.get("name") or path.parent.name).strip()
    name = raw_name.upper()
    rel = path.parent.relative_to(SKILLS_DIR).as_posix()
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    parent = (meta.get("parent_skill") or "").strip().upper() or None
    skill_type = _skill_type(name, meta)
    return {
        "name": name,
        "source_name": raw_name,
        "type": skill_type,
        # declared version, or a content-derived one so an edited playbook becomes a new version, never an overwrite
        "version": (meta.get("version") or "").strip() or f"1.0.0+{checksum[:8]}",
        "category": category_for(rel, meta.get("category"), skill_type, name, parent),
        "parent": parent,
        "description": re.sub(r"\s+", " ", meta.get("description") or "")[:1000],
        "content": content,
        "config": config,
        "checksum": checksum,
        "domain": meta.get("domain"),
        "folder": rel,
        "path": path,
        "files": [path] + [p for p in path.parent.rglob("*") if p.is_file() and p != path],
    }


def list_skills() -> List[Dict[str, Any]]:
    return [parse_skill(p) for p in sorted(SKILLS_DIR.rglob("SKILL.md"))]


def domain_rows(database: str, live: Optional[LiveColumns] = None) -> Dict[str, Any]:
    """Rows for every domain pack. GDP keeps its fixed domain/table IDs and column-ID scheme so existing runs resolve."""
    out: Dict[str, Any] = {"domains": [], "tables": [], "columns": [], "knowledge": [], "drift": []}
    for pack in load_domain_packs():
        pack["domain"]["origin"] = "repository"  # re-registered on every deploy; not deletable from the UI
        for key, values in pack_rows(pack, database, live).items():
            out[key].extend(values)
    return out


def silver_locations() -> List[Tuple[str, str]]:
    return sorted({(p["domain"]["silver_database"], p["domain"]["silver_schema"]) for p in load_domain_packs()
                   if p["domain"].get("silver_database") and p["domain"].get("silver_schema")})


def fetch_live_columns(cur) -> Tuple[LiveColumns, List[str]]:
    """Exact target columns from DEV_GDP_SILVER_DB.<DOMAIN>; unreachable schemas fall back to the contract."""
    from services.source.identifiers import format_data_type

    live: LiveColumns = {}
    log: List[str] = []
    for db, schema in silver_locations():
        try:
            cur.execute(
                f"""SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                           IS_NULLABLE FROM {db}.INFORMATION_SCHEMA.COLUMNS
                     WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION""", (schema,))
            for t, c, dt, ln, pr, sc, nl in cur.fetchall():
                live.setdefault((schema, t), []).append(
                    {"name": c, "type": format_data_type(dt, ln, pr, sc), "nullable": nl == "YES"})
            log.append(f"silver {db}.{schema}: {len({k for k in live if k[0] == schema})} tables read")
        except Exception as exc:
            log.append(f"silver {db}.{schema}: not readable ({str(exc)[:120]}); contract columns used")
    return live, log


def skill_rows() -> List[Tuple]:
    out = []
    for skill in list_skills():
        skill_domain = GDP_DOMAIN_ID if (skill.get("domain") or "").upper() == "GDP" else None
        out.append((
            _stable_id("skill", skill["name"], skill["checksum"]), skill["name"], skill["type"],
            skill_domain, skill["version"], f"{skill['folder']}/{_stage_dir(skill)}/SKILL.md",
            skill["checksum"], "ACTIVE", skill["description"], skill["content"], skill["config"], True,
            skill["category"], skill["parent"],
        ))
    return out


def _stage_dir(skill: Dict[str, Any]) -> str:
    """Stage folder of one version: version plus content hash, so two contents never share a folder."""
    return f"{skill['version'].split('+')[0]}-{skill['checksum'][:8]}"


def config_rows() -> List[Tuple]:
    return [(key, value, desc, 1, True) for key, value, desc in PLATFORM_CONFIG]


def scoring_rows() -> List[Tuple]:
    return [(SCORING_CONFIG_ID, None, 1, DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS, True)]


def seed_skills(cur, database: str) -> List[str]:
    """Register repository skills as immutable versions and move labels.

    A repository version is matched on (name, checksum): unchanged files register nothing. A changed file becomes a
    new revision. production follows the repository only while no person has moved it (MOVED_BY = 'SEED') and
    SKILLS_AUTO_PROMOTE_REPO is on; otherwise the new version becomes the candidate, for someone to review and promote.
    """
    log: List[str] = []
    reg = f"{database}.KNOWLEDGE.SKILL_REGISTRY"
    cur.execute(f"""SELECT CONFIG_VALUE FROM {database}.CORE.PLATFORM_CONFIG
                     WHERE CONFIG_KEY = 'SKILLS_AUTO_PROMOTE_REPO' AND IS_CURRENT ORDER BY VERSION DESC LIMIT 1""")
    found = cur.fetchall()
    auto = True if not found else str(found[0][0]).strip().lower() not in ("false", "0", '"false"')
    for row in skill_rows():
        skill_id, name, stype, domain, version, path, checksum, status, desc, content, config, _, category, parent = row
        cur.execute(f"SELECT SKILL_ID FROM {reg} WHERE SKILL_NAME = %s AND CHECKSUM = %s ORDER BY CREATED_AT LIMIT 1",
                    (name, checksum))
        hit = cur.fetchall()
        if hit:
            skill_id = hit[0][0]
            # metadata that is not part of the version itself
            cur.execute(f"""UPDATE {reg} SET CATEGORY_ID = %s, PARENT_SKILL = NULLIF(%s, ''), ORIGIN = COALESCE(ORIGIN, 'REPOSITORY')
                             WHERE SKILL_ID = %s""", (category, parent or "", skill_id))
            new = False
        else:
            cur.execute(f"""INSERT INTO {reg} (SKILL_ID, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, VERSION, STAGE_PATH, CHECKSUM,
                                 STATUS, DESCRIPTION, CONTENT, CONFIG, IS_CURRENT, CREATED_BY, REVISION, ORIGIN,
                                 CATEGORY_ID, PARENT_SKILL, CHANGE_NOTE)
                             SELECT %s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s, %s, PARSE_JSON(NULLIF(%s, '')), FALSE, 'SEED',
                                    COALESCE((SELECT MAX(REVISION) FROM {reg} WHERE SKILL_NAME = %s), 0) + 1, 'REPOSITORY',
                                    %s, NULLIF(%s, ''), 'Repository version'""",
                        (skill_id, name, stype, domain or "", version, path, checksum, status, desc, content,
                         _dumps(config), name, category, parent or ""))
            new = True
        cur.execute(f"SELECT LABEL, SKILL_ID, MOVED_BY FROM {database}.KNOWLEDGE.SKILL_LABEL WHERE SKILL_NAME = %s", (name,))
        labels = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        production = labels.get("production")
        if production is None or (new and auto and production[1] == "SEED"):
            _move_label(cur, database, name, "production", skill_id, production[0] if production else None)
            if new and production:
                log.append(f"skill {name}: production moved to the new repository version {version}")
        elif new and production[0] != skill_id:
            _move_label(cur, database, name, "candidate", skill_id, (labels.get("candidate") or (None,))[0])
            log.append(f"skill {name}: new repository version {version} is the candidate (production was set by a person)")
    # IS_CURRENT mirrors production so readers that predate labels keep working
    cur.execute(f"""UPDATE {reg} R SET IS_CURRENT = (R.SKILL_ID IN (SELECT SKILL_ID FROM {database}.KNOWLEDGE.SKILL_LABEL
                                                                     WHERE LABEL = 'production'))""")
    # stage bindings: missing rows only, admin edits are never touched
    from services.knowledge.usage import GDP_ONLY_SKILLS, STAGE_SKILLS

    for stage, names in STAGE_SKILLS.items():
        for pos, name in enumerate(names):
            cur.execute(f"""MERGE INTO {database}.KNOWLEDGE.SKILL_STAGE_BINDING T
                             USING (SELECT %s AS STAGE, %s AS SKILL_NAME) S ON T.STAGE = S.STAGE AND T.SKILL_NAME = S.SKILL_NAME
                             WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, UPDATED_BY)
                             VALUES (S.STAGE, S.SKILL_NAME, TRUE, %s, %s, 'SEED')""",
                        (stage, name, (pos + 1) * 10, "GDP" if name in GDP_ONLY_SKILLS else "ANY"))
    return log


def _move_label(cur, database: str, name: str, label: str, skill_id: str, previous: Optional[str]) -> None:
    import uuid

    cur.execute(f"""MERGE INTO {database}.KNOWLEDGE.SKILL_LABEL T
                     USING (SELECT %s AS SKILL_NAME, %s AS LABEL, %s AS SKILL_ID) S
                        ON T.SKILL_NAME = S.SKILL_NAME AND T.LABEL = S.LABEL
                     WHEN MATCHED THEN UPDATE SET SKILL_ID = S.SKILL_ID, MOVED_BY = 'SEED', MOVED_AT = CURRENT_TIMESTAMP(),
                          NOTE = 'Repository deploy'
                     WHEN NOT MATCHED THEN INSERT (SKILL_NAME, LABEL, SKILL_ID, MOVED_BY, NOTE)
                          VALUES (S.SKILL_NAME, S.LABEL, S.SKILL_ID, 'SEED', 'Repository deploy')""", (name, label, skill_id))
    cur.execute(f"""INSERT INTO {database}.KNOWLEDGE.SKILL_LABEL_HISTORY
                     (EVENT_ID, SKILL_NAME, LABEL, FROM_SKILL_ID, TO_SKILL_ID, MOVED_BY, NOTE)
                     VALUES (%s, %s, %s, NULLIF(%s, ''), %s, 'SEED', 'Repository deploy')""",
                (str(uuid.uuid4()), name, label, previous or "", skill_id))


def seed_platform(cur, database: str) -> List[str]:
    """MERGE domain packs, skills, config and scoring weights; PUT skill files to the stage. Returns log lines."""
    live, log = fetch_live_columns(cur)
    data = domain_rows(database, live)
    for d in data["drift"]:
        if d["live"] and (d["missing_in_silver"] or d["not_in_contract"]):
            log.append(f"drift {d['domain']}.{d['table']}: contract-only {d['missing_in_silver'][:6]}, "
                       f"silver-only {d['not_in_contract'][:6]}")
        elif not d["live"]:
            log.append(f"{d['domain']}.{d['table']}: no live silver table; {d['columns']} contract columns used"
                       + ("" if d["columns"] else " (registered inactive, knowledge only)"))
    merge(cur.execute, database, data)

    for skill in list_skills():
        root = skill["path"].parent
        dest_root = f"@{database}.KNOWLEDGE.SKILL_STAGE/{skill['folder']}/{_stage_dir(skill)}"
        for local in skill["files"]:
            rel = local.relative_to(root).as_posix()
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            dest = f"{dest_root}/{parent}" if parent else dest_root
            cur.execute(f"PUT 'file://{local.as_posix()}' {dest} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
    log.extend(seed_skills(cur, database))

    # Settings an admin changed (a version above the seeded one) belong to the admin: the deploy leaves them alone,
    # otherwise re-marking the seeded version as current would leave two current rows.
    cur.execute(f"SELECT DISTINCT CONFIG_KEY FROM {database}.CORE.PLATFORM_CONFIG WHERE VERSION > 1")
    edited = {r[0] for r in cur.fetchall()}
    for key, value, desc, version, current in config_rows():
        if key in edited:
            log.append(f"config {key}: kept the admin's value")
            continue
        cur.execute(
            f"""MERGE INTO {database}.CORE.PLATFORM_CONFIG t
                USING (SELECT %s AS CONFIG_KEY, PARSE_JSON(%s) AS CONFIG_VALUE, %s AS DESCRIPTION,
                              %s AS VERSION, %s AS IS_CURRENT, 'SEED' AS CREATED_BY) s
                   ON t.CONFIG_KEY = s.CONFIG_KEY AND t.VERSION = s.VERSION
                WHEN MATCHED THEN UPDATE SET CONFIG_VALUE = s.CONFIG_VALUE, DESCRIPTION = s.DESCRIPTION,
                     IS_CURRENT = s.IS_CURRENT
                WHEN NOT MATCHED THEN INSERT (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
                     VALUES (s.CONFIG_KEY, s.CONFIG_VALUE, s.DESCRIPTION, s.VERSION, s.IS_CURRENT, s.CREATED_BY)""",
            (key, json.dumps(value), desc, version, current),
        )

    cur.execute(
        f"""MERGE INTO {database}.MAPPING.MAPPING_SCORING_CONFIG t
            USING (SELECT %s AS CONFIG_ID, NULL AS DOMAIN_ID, %s AS VERSION, PARSE_JSON(%s) AS WEIGHTS,
                          PARSE_JSON(%s) AS THRESHOLDS, TRUE AS ACTIVE_FLAG, 'SEED' AS CREATED_BY) s
               ON t.CONFIG_ID = s.CONFIG_ID
            WHEN MATCHED THEN UPDATE SET WEIGHTS = s.WEIGHTS, THRESHOLDS = s.THRESHOLDS, ACTIVE_FLAG = s.ACTIVE_FLAG
            WHEN NOT MATCHED THEN INSERT (CONFIG_ID, DOMAIN_ID, VERSION, WEIGHTS, THRESHOLDS, ACTIVE_FLAG, CREATED_BY)
                 VALUES (s.CONFIG_ID, s.DOMAIN_ID, s.VERSION, s.WEIGHTS, s.THRESHOLDS, s.ACTIVE_FLAG, s.CREATED_BY)""",
        (SCORING_CONFIG_ID, 1, json.dumps(DEFAULT_WEIGHTS), json.dumps(DEFAULT_THRESHOLDS)),
    )
    return log
