"""Seed domain pack, skills, platform config and mapping weights (idempotent MERGE)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    ("DOMAIN_CONFIDENCE_THRESHOLD", 0.3, "Below this, identify_domain asks for confirmation"),
    ("CREDITS_PER_MILLION_TOKENS", {"default": 0, "claude-sonnet-4-5": 0}, "Cost estimate rates"),
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
    return {
        "name": name,
        "source_name": raw_name,
        "type": _skill_type(name, meta),
        "version": (meta.get("version") or "1.0.0").strip() or "1.0.0",
        "description": re.sub(r"\s+", " ", meta.get("description") or "")[:1000],
        "content": content,
        "config": config,
        "checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
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
            _stable_id("skill", skill["name"], skill["version"]), skill["name"], skill["type"],
            skill_domain, skill["version"], f"{skill['folder']}/{skill['version']}/SKILL.md",
            skill["checksum"], "ACTIVE", skill["description"], skill["content"], skill["config"], True,
        ))
    return out


def config_rows() -> List[Tuple]:
    return [(key, value, desc, 1, True) for key, value, desc in PLATFORM_CONFIG]


def scoring_rows() -> List[Tuple]:
    return [(SCORING_CONFIG_ID, None, 1, DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS, True)]


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
        dest_root = f"@{database}.KNOWLEDGE.SKILL_STAGE/{skill['folder']}/{skill['version']}"
        for local in skill["files"]:
            rel = local.relative_to(root).as_posix()
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            dest = f"{dest_root}/{parent}" if parent else dest_root
            cur.execute(f"PUT 'file://{local.as_posix()}' {dest} AUTO_COMPRESS = FALSE OVERWRITE = TRUE")

    for row in skill_rows():
        cur.execute(
            f"""MERGE INTO {database}.KNOWLEDGE.SKILL_REGISTRY t
                USING (SELECT %s AS SKILL_ID, %s AS SKILL_NAME, %s AS SKILL_TYPE, NULLIF(%s, '') AS DOMAIN_ID,
                              %s AS VERSION, %s AS STAGE_PATH, %s AS CHECKSUM, %s AS STATUS,
                              %s AS DESCRIPTION, %s AS CONTENT, PARSE_JSON(NULLIF(%s, '')) AS CONFIG,
                              %s AS IS_CURRENT, 'SEED' AS CREATED_BY) s
                   ON t.SKILL_ID = s.SKILL_ID
                WHEN MATCHED THEN UPDATE SET SKILL_NAME = s.SKILL_NAME, SKILL_TYPE = s.SKILL_TYPE,
                     DOMAIN_ID = s.DOMAIN_ID, VERSION = s.VERSION, STAGE_PATH = s.STAGE_PATH,
                     CHECKSUM = s.CHECKSUM, STATUS = s.STATUS, DESCRIPTION = s.DESCRIPTION,
                     CONTENT = s.CONTENT, CONFIG = s.CONFIG, IS_CURRENT = s.IS_CURRENT
                WHEN NOT MATCHED THEN INSERT (SKILL_ID, SKILL_NAME, SKILL_TYPE, DOMAIN_ID, VERSION,
                     STAGE_PATH, CHECKSUM, STATUS, DESCRIPTION, CONTENT, CONFIG, IS_CURRENT, CREATED_BY)
                     VALUES (s.SKILL_ID, s.SKILL_NAME, s.SKILL_TYPE, s.DOMAIN_ID, s.VERSION, s.STAGE_PATH,
                             s.CHECKSUM, s.STATUS, s.DESCRIPTION, s.CONTENT, s.CONFIG, s.IS_CURRENT, s.CREATED_BY)""",
            (row[0], row[1], row[2], row[3] or "", row[4], row[5], row[6], row[7], row[8], row[9],
             _dumps(row[10]), row[11]),
        )
    current = [row[0] for row in skill_rows()]
    cur.execute(f"""UPDATE {database}.KNOWLEDGE.SKILL_REGISTRY SET IS_CURRENT = FALSE
                     WHERE CREATED_BY = 'SEED' AND IS_CURRENT
                       AND NOT ARRAY_CONTAINS(SKILL_ID::VARIANT, PARSE_JSON(%s)::ARRAY)""", (json.dumps(current),))

    for key, value, desc, version, current in config_rows():
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
