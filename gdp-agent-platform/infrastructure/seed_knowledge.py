"""Seed domain pack, skills, platform config and mapping weights (idempotent MERGE)."""

from __future__ import annotations

import hashlib
import json
import uuid
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.source.pipelines import DEFAULT_FORM as INTAKE_FORM
from services.knowledge.skills import category_for
from services.knowledge.packs import (GDP_DOMAIN_ID, GDP_TABLE_ID, LiveColumns, _dumps, _stable_id,  # noqa: F401
                                      domain_id, merge, merge_columns, pack_rows)

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = ROOT / "domain"
DOMAIN_PACK = DOMAIN_DIR / "gdp" / "domain_pack.json"
RUNBOOK_PACK = DOMAIN_DIR / "general" / "airflow_runbook_pack.json"
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
    ("INGESTION_INTAKE_FORM", INTAKE_FORM,
     "Questions the Source Hub asks for an external pipeline; answers go to CORE.INGESTION_PIPELINE_CATALOG"),
]


def load_domain_pack() -> Dict[str, Any]:
    return json.loads(DOMAIN_PACK.read_text(encoding="utf-8"))


def load_domain_packs() -> List[Dict[str, Any]]:
    """Every domain/<name>/domain_pack.json; GDP first so its fixed IDs are assigned before the others. A domain's
    mdm.json (its MDM contract, kept apart so regenerating the pack never drops it) is merged in: the contract into
    the domain's config as `mdm`, its knowledge items into the pack's knowledge."""
    from services.knowledge.validate import validate_pack
    from services.mdm import contract as mdm

    packs = []
    for path in sorted(DOMAIN_DIR.glob("*/domain_pack.json")):
        pack = json.loads(path.read_text(encoding="utf-8"))
        extra = path.parent / "mdm.json"
        if extra.exists():
            pack = with_mdm(pack, json.loads(extra.read_text(encoding="utf-8")))
        problems = validate_pack(pack)
        assert not problems, f"{path.parent.name} domain pack is invalid: " + "; ".join(problems[:10])
        packs.append(pack)
    mastered = [p["domain"]["name"] for p in packs if (p["domain"].get("mdm") or {}).get("role") == "MASTERED"]
    for p in packs:
        problems = mdm.validate(p["domain"]["name"], p["domain"].get("mdm"), mastered)
        assert not problems, "; ".join(problems[:10])
    return sorted(packs, key=lambda pk: (pk["domain"]["name"] != "GDP", pk["domain"]["name"]))


def with_mdm(pack: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    """The pack with its MDM contract: knowledge items appended (not duplicated), the rest as domain config `mdm`."""
    out = {**pack, "domain": dict(pack["domain"]), "knowledge": list(pack.get("knowledge") or [])}
    known = {k.get("key") for k in out["knowledge"]}
    out["knowledge"] += [k for k in contract.get("knowledge") or [] if k.get("key") not in known]
    out["domain"]["mdm"] = {k: v for k, v in contract.items() if k != "knowledge"}
    return out


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
    reg = f"{database}.CORE.SKILL_REGISTRY"
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
        cur.execute(f"SELECT LABEL, SKILL_ID, MOVED_BY FROM {database}.CORE.SKILL_LABEL WHERE SKILL_NAME = %s", (name,))
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
    cur.execute(f"""UPDATE {reg} R SET IS_CURRENT = (R.SKILL_ID IN (SELECT SKILL_ID FROM {database}.CORE.SKILL_LABEL
                                                                     WHERE LABEL = 'production'))""")
    # stage bindings: missing rows only, admin edits are never touched
    from services.knowledge.usage import GDP_ONLY_SKILLS, STAGE_SKILLS, pipeline_of_skill

    for stage, names in STAGE_SKILLS.items():
        for pos, name in enumerate(names):
            try:   # V055: each binding's pipeline (Silver, Gold or both)
                cur.execute(f"""MERGE INTO {database}.CORE.SKILL_STAGE_BINDING T
                                 USING (SELECT %s AS STAGE, %s AS SKILL_NAME) S ON T.STAGE = S.STAGE AND T.SKILL_NAME = S.SKILL_NAME
                                 WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, PIPELINE, UPDATED_BY)
                                 VALUES (S.STAGE, S.SKILL_NAME, TRUE, %s, %s, %s, 'SEED')""",
                            (stage, name, (pos + 1) * 10, "GDP" if name in GDP_ONLY_SKILLS else "ANY", pipeline_of_skill(name)))
            except Exception:
                cur.execute(f"""MERGE INTO {database}.CORE.SKILL_STAGE_BINDING T
                                 USING (SELECT %s AS STAGE, %s AS SKILL_NAME) S ON T.STAGE = S.STAGE AND T.SKILL_NAME = S.SKILL_NAME
                                 WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, UPDATED_BY)
                                 VALUES (S.STAGE, S.SKILL_NAME, TRUE, %s, %s, 'SEED')""",
                            (stage, name, (pos + 1) * 10, "GDP" if name in GDP_ONLY_SKILLS else "ANY"))
    return log


def _move_label(cur, database: str, name: str, label: str, skill_id: str, previous: Optional[str]) -> None:
    import uuid

    cur.execute(f"""MERGE INTO {database}.CORE.SKILL_LABEL T
                     USING (SELECT %s AS SKILL_NAME, %s AS LABEL, %s AS SKILL_ID) S
                        ON T.SKILL_NAME = S.SKILL_NAME AND T.LABEL = S.LABEL
                     WHEN MATCHED THEN UPDATE SET SKILL_ID = S.SKILL_ID, MOVED_BY = 'SEED', MOVED_AT = CURRENT_TIMESTAMP(),
                          NOTE = 'Repository deploy'
                     WHEN NOT MATCHED THEN INSERT (SKILL_NAME, LABEL, SKILL_ID, MOVED_BY, NOTE)
                          VALUES (S.SKILL_NAME, S.LABEL, S.SKILL_ID, 'SEED', 'Repository deploy')""", (name, label, skill_id))
    cur.execute(f"""INSERT INTO {database}.CORE.SKILL_LABEL_HISTORY
                     (EVENT_ID, SKILL_NAME, LABEL, FROM_SKILL_ID, TO_SKILL_ID, MOVED_BY, NOTE)
                     VALUES (%s, %s, %s, NULLIF(%s, ''), %s, 'SEED', 'Repository deploy')""",
                (str(uuid.uuid4()), name, label, previous or "", skill_id))


def snapshot_domains(cur, database: str) -> List[str]:
    """A domain version for every domain whose content changed (the first deploy records each baseline)."""
    from services.knowledge.domain_versions import snapshot

    def qualify(sql: str) -> str:
        # the deploy connection has no current database, so every schema-qualified name needs the database too
        return re.sub(r"(?<![\w.\"])(KNOWLEDGE|CORE)\.", rf"{database}.\1.", sql)

    def query(sql: str, params: tuple) -> List[Dict[str, Any]]:
        cur.execute(qualify(sql), params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def execute(sql: str, params: tuple) -> None:
        cur.execute(qualify(sql), params)

    log: List[str] = []
    try:
        cur.execute(f"SELECT DOMAIN_ID, DOMAIN_NAME FROM {database}.CORE.DOMAIN_REGISTRY")
        domains = cur.fetchall()
    except Exception as exc:
        return [f"domain versions skipped: {exc}"]
    for did, name in domains:
        try:
            version = snapshot(query, execute, did, "DEPLOY", "Recorded by deploy", "DEPLOY")
        except Exception as exc:
            log.append(f"domain {name}: version not recorded ({exc})")
            continue
        if version:
            log.append(f"domain {name}: version {version} recorded")
    return log


def seed_domain_models(cur, database: str) -> List[str]:
    """Model Workbench baselines (V039): an APPROVED version 1 per domain that registers tables and has no model."""
    from services.modeling.domain_model import CursorSession, backfill

    try:
        cur.execute(f"USE DATABASE {database}")
    except Exception as exc:
        return [f"domain models: skipped ({str(exc)[:120]})"]
    return backfill(CursorSession(cur))


SEED_CODE = [ROOT / "infrastructure" / "seed_knowledge.py", ROOT / "services" / "knowledge",
             ROOT / "services" / "qa", ROOT / "services" / "modeling", ROOT / "services" / "catalog" / "dq_suite.py"]


def seed_inputs_digest() -> str:
    """Checksum of everything the seed reads from the repository (domain packs and QA packs, skills, seeded config
    and weights) and of the code that writes it, so a deploy runs the seed only when one of them changed."""
    h = hashlib.sha256()
    files = sorted(p for p in DOMAIN_DIR.rglob("*") if p.is_file()) + sorted(p for p in SKILLS_DIR.rglob("*") if p.is_file())
    for root in SEED_CODE:
        files += [root] if root.is_file() else sorted(root.rglob("*.py"))
    for path in files:
        if "__pycache__" in path.parts:
            continue
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(path.read_bytes())
    h.update(json.dumps([config_rows(), scoring_rows()], sort_keys=True, default=str).encode())
    return h.hexdigest()


def governed_domains(cur, database: str) -> Dict[str, Any]:
    """Domains whose registry follows an approved Model Workbench version: {domain id: approved version}."""
    try:
        cur.execute(f"SELECT DOMAIN_ID, APPROVED_VERSION FROM {database}.CORE.DOMAIN_MODEL "
                    "WHERE APPROVED_VERSION IS NOT NULL AND COALESCE(LAYER, 'SILVER') = 'SILVER'")
        return {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        return {}  # before V039


def _shape(spec: Dict[str, Any]) -> Dict[str, List[str]]:
    from services.modeling import spec_v2

    return {str(e["entity_name"]).upper(): sorted(str(a["name"]).upper() for a in e.get("attributes") or []
                                                   if str(a.get("status") or "").upper() != "RETIRED")
            for e in spec_v2.active(spec)["design"]["entities"]}


def pack_changes(approved: Dict[str, Any], pack: Dict[str, Any], model_id: str) -> Optional[Dict[str, Any]]:
    """The approved model with the pack applied, when the pack adds, drops or reshapes a table; None when its tables
    and columns are what the approved model has (pure). Tables the pack no longer has are retired in the result
    (shown in the review, never deleted). Descriptions alone do not count: a deploy would otherwise open a draft for
    every wording difference between a pack and the model people approved."""
    from services.modeling import importers, spec_v2

    base = spec_v2.normalize_v2(approved, model_id)
    incoming = spec_v2.spec_from_pack(pack, model_id)
    if _shape(base) == _shape(incoming):
        return None
    return importers.merge(base, incoming, model_id, replace=True)


def pack_digest(pack: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(pack.get("targets") or [], sort_keys=True, default=str).encode()).hexdigest()[:12]


def pack_drafts(cur, database: str, governed: Dict[str, Any]) -> List[str]:
    """A changed pack of a governed domain becomes a draft model version for review in the Model Workbench (its
    tables are not re-applied directly, see drop_governed). Skipped while another version is open, and for a pack
    already proposed once (its digest is in a version's change note), so a rejected draft is not reopened."""
    if not governed:
        return []
    from services.knowledge.packs import domain_id
    from services.modeling import domain_model as dm

    try:
        cur.execute(f"USE DATABASE {database}")
    except Exception as exc:
        return [f"pack drafts: skipped ({str(exc)[:120]})"]
    session = dm.CursorSession(cur)
    log: List[str] = []
    for pack in load_domain_packs():
        name = pack["domain"]["name"]
        did = domain_id(name)
        if did not in governed or name == "GDP":
            continue
        model_id = dm.model_id_for(did)
        digest = pack_digest(pack)
        note = f"Pack update from domain/{name.lower()}/domain_pack.json ({digest})"
        try:
            versions = dm._versions(session, model_id)
            if any(digest in str(v.get("change_note") or "") for v in versions):
                continue
            merged = pack_changes(dm._spec(session, model_id, int(governed[did])), pack, model_id)
            if merged is None:
                continue
            opened = dm._open(versions)
            if opened:
                log.append(f"domain {name}: pack changed but version {opened['version']} is already open; "
                           "pack not applied (import it into that draft in the Model Workbench)")
                continue
            draft = dm.open_draft(session, model_id, "SYSTEM_DEPLOY", note=note)
            saved = dm.save_draft(session, model_id, json.dumps({"etag": draft["etag"], "spec": merged, "change_note": note},
                                                                default=str), "SYSTEM_DEPLOY")
        except Exception as exc:
            log.append(f"domain {name}: pack not saved as a draft ({str(exc)[:160]})")
            continue
        counts = ", ".join(f"{v} {k.lower().replace('_', ' ')}" for k, v in (saved.get("counts") or {}).items() if v)
        log.append(f"domain {name}: pack saved as draft v{draft['version']}" + (f" ({counts})" if counts else "")
                   + "; approve it in the Model Workbench")
    return log


def retired_knowledge(current: List[Dict[str, Any]], packs: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(domain id, source reference) of seeded pack knowledge a pack no longer has (pure). Only items the deploy
    wrote and nobody edited: origin and author SEED, version 1, with the pack's own key prefix (QA pack and runbook
    items have their own seeders)."""
    from services.knowledge.packs import domain_id

    keys = {domain_id(p["domain"]["name"]): (p["domain"]["name"].lower() + ".", {k["key"] for k in p.get("knowledge") or []})
            for p in packs}
    out = []
    for r in current:
        r = {k.lower(): v for k, v in r.items()}
        found = keys.get(r.get("domain_id"))
        ref = str(r.get("source_reference") or "")
        if (found and ref.startswith(found[0]) and ref not in found[1] and str(r.get("origin") or "").upper() == "SEED"
                and str(r.get("created_by") or "").upper() == "SEED" and int(r.get("version") or 0) == 1):
            out.append((r["domain_id"], ref))
    return out


def retire_dropped_knowledge(cur, database: str) -> List[str]:
    try:
        cur.execute(f"""SELECT DOMAIN_ID, SOURCE_REFERENCE, ORIGIN, CREATED_BY, VERSION FROM {database}.CORE.DOMAIN_KNOWLEDGE
                         WHERE IS_CURRENT AND STATUS = 'ACTIVE' AND ORIGIN = 'SEED'""")
        names = [d[0] for d in cur.description]
        current = [dict(zip(names, row)) for row in cur.fetchall()]
    except Exception as exc:
        return [f"knowledge retire: skipped ({str(exc)[:120]})"]
    gone = retired_knowledge(current, load_domain_packs())
    for did, ref in gone:
        cur.execute(f"UPDATE {database}.CORE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED', "
                    f"UPDATED_AT = CURRENT_TIMESTAMP() WHERE DOMAIN_ID = %s AND SOURCE_REFERENCE = %s AND IS_CURRENT",
                    (did, ref))
    return [f"knowledge: {len(gone)} item(s) no longer in their domain pack retired"] if gone else []


def drop_governed(data: Dict[str, Any], governed: Dict[str, Any]) -> List[str]:
    """Leave the target tables and columns of governed domains alone: their approved model version is the source of
    truth, and re-applying the repository pack would undo approved changes. A pack change reaches such a domain by
    a draft version (pack_drafts, run by the deploy before this) approved in the Model Workbench."""
    if not governed:
        return []
    names = {row[0]: row[1] for row in data["domains"]}
    skipped = {row[0] for row in data["tables"] if row[1] in governed}
    hit = sorted({row[1] for row in data["tables"] if row[1] in governed})
    data["tables"] = [row for row in data["tables"] if row[0] not in skipped]
    data["columns"] = [row for row in data["columns"] if row[1] not in skipped]
    data["drift"] = [d for d in data["drift"] if d.get("domain") not in {names.get(i) for i in hit}]
    return [f"domain {names.get(i, i)}: target tables follow its approved model v{governed[i]}; pack tables not "
            "re-applied (a changed pack is saved as a draft version to approve in the Model Workbench)" for i in hit]


def load_dq_suite_files() -> List[Tuple[str, str, Path]]:
    """(domain name, file text, file) for every domain/<dir>/dq/*.dq.json: portable Data Quality suites written with
    logical table names (services/catalog/dq_suite.portable)."""
    from services.catalog.dq_suite import parse_portable

    out = []
    for path in sorted(DOMAIN_DIR.glob("*/dq/*.dq.json")):
        pack_file = path.parent.parent / "domain_pack.json"
        name = (json.loads(pack_file.read_text(encoding="utf-8"))["domain"]["name"] if pack_file.exists()
                else path.parent.parent.name.upper())
        text = path.read_text(encoding="utf-8")
        try:
            parse_portable(text)
        except ValueError as exc:
            raise AssertionError(f"{path.relative_to(ROOT)}: {exc}") from exc
        out.append((name, text, path))
    return out


def seed_dq_suite_files(cur, database: str) -> List[str]:
    """Repository suite files into this environment: names resolved with its bindings (Admin, Environments, or the
    environment file's [environment]), new checks added to the table's draft for a DQ approver to approve. A file
    whose checks the table already has changes nothing."""
    from services.catalog.dq_suite import import_suite
    from services.modeling.domain_model import CursorSession

    files = load_dq_suite_files()
    if not files:
        return []
    try:
        cur.execute(f"USE DATABASE {database}")
    except Exception as exc:
        return [f"dq suite files: skipped ({str(exc)[:120]})"]
    log = []
    for _, text, path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            result = import_suite(CursorSession(cur), text, "DEPLOY")
            log.append(f"dq suite file {rel}: {result['table']}, {result.get('added', 0)} checks added to the draft"
                       if result.get("added") else f"dq suite file {rel}: unchanged")
        except Exception as exc:
            log.append(f"dq suite file {rel}: skipped ({str(exc)[:160]})")
    return log


def seed_environment(cur, database: str, environment: Optional[Dict[str, Any]]) -> List[str]:
    """The environment file's [environment] (name and bindings) as ENVIRONMENT_BINDINGS version 1, when no one has
    saved bindings yet. Bindings edited in Admin, Environments are never replaced by a deploy."""
    from services.common import environments as envs

    if not environment or not environment.get("name"):
        return []
    try:
        cur.execute(f"SELECT COUNT(*) FROM {database}.CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s", (envs.CONFIG_KEY,))
        if cur.fetchall()[0][0]:
            return ["environment bindings: kept the saved value"]
        value, problems = envs.validate({"current": environment["name"],
                                         "environments": [{"name": environment["name"],
                                                           "description": environment.get("description") or "",
                                                           "bindings": environment.get("bindings") or {}}]})
        if problems:
            return [f"environment bindings: not seeded ({'; '.join(problems[:3])})"]
        cur.execute(f"""INSERT INTO {database}.CORE.PLATFORM_CONFIG
                          (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
                        SELECT %s, PARSE_JSON(%s), %s, 1, TRUE, CURRENT_USER()""",
                    (envs.CONFIG_KEY, json.dumps(value), "Environment databases from the environment file"))
    except Exception as exc:
        return [f"environment bindings: skipped ({str(exc)[:120]})"]
    return [f"environment bindings: {environment['name']} ({len(value['environments'][0]['bindings'])} bindings)"]


def seed_snaplogic(cur, database: str, snaplogic: Optional[Dict[str, Any]]) -> List[str]:
    """The environment file's [snaplogic] as the SNAPLOGIC_SYNC setting version 1, when no one has saved it yet. The
    setting edited in Admin is never replaced by a deploy."""
    from services.source import snaplogic_config as sc

    if not snaplogic:
        return []
    try:
        cur.execute(f"SELECT COUNT(*) FROM {database}.CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s", (sc.KEY,))
        if cur.fetchall()[0][0]:
            return ["snaplogic sync: kept the saved setting"]
        value, problems = sc.validate_setting({**sc.DEFAULT_SETTING, "enabled": snaplogic.get("enabled") is True})
        if problems:
            return [f"snaplogic sync: not seeded ({'; '.join(problems[:3])})"]
        cur.execute(f"""INSERT INTO {database}.CORE.PLATFORM_CONFIG
                          (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
                        SELECT %s, PARSE_JSON(%s), %s, 1, TRUE, CURRENT_USER()""",
                    (sc.KEY, json.dumps(value), "SnapLogic config tables from the environment file"))
    except Exception as exc:
        return [f"snaplogic sync: skipped ({str(exc)[:120]})"]
    return [f"snaplogic sync: {'on' if value['enabled'] else 'off'}, writing {database}.{sc.MASTER} and {database}.{sc.DETAIL}"]


def seed_dq_suites(cur, database: str) -> List[str]:
    """An approved version 1 for every Data Quality Store table without a suite (V040)."""
    from services.catalog.dq_suite import backfill
    from services.modeling.domain_model import CursorSession

    try:
        cur.execute(f"USE DATABASE {database}")
    except Exception as exc:
        return [f"dq suites: skipped ({str(exc)[:120]})"]
    return backfill(CursorSession(cur))


def _cursor_query(cur):
    """A %s-bind query function over a connector cursor, returning rows as dicts."""

    def query(sql: str, params: tuple) -> List[Dict[str, Any]]:
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    return query


def seed_platform(cur, database: str) -> List[str]:
    """MERGE domain packs, skills, config and scoring weights; PUT skill files to the stage. Returns log lines.
    Target tables of domains governed by an approved model are not re-applied from the packs."""
    live, log = fetch_live_columns(cur)
    data = domain_rows(database, live)
    governed = governed_domains(cur, database)
    log.extend(pack_drafts(cur, database, governed))
    log.extend(drop_governed(data, governed))
    for d in data["drift"]:
        if d["live"] and (d["missing_in_silver"] or d["not_in_contract"]):
            log.append(f"drift {d['domain']}.{d['table']}: contract-only {d['missing_in_silver'][:6]}, "
                       f"silver-only {d['not_in_contract'][:6]}")
        elif not d["live"]:
            log.append(f"{d['domain']}.{d['table']}: no live silver table; {d['columns']} contract columns used"
                       + ("" if d["columns"] else " (registered inactive, knowledge only)"))
    merge(cur.execute, database, data, query=_cursor_query(cur))  # keeps domain edits made in the app
    log.extend(retire_dropped_knowledge(cur, database))
    log.extend(snapshot_domains(cur, database))
    log.extend(seed_qa_packs(cur, database))
    log.extend(check_pack_bindings(cur, database))
    log.extend(seed_domain_models(cur, database))
    log.extend(seed_dq_suites(cur, database))
    log.extend(seed_dq_suite_files(cur, database))
    log.extend(seed_runbooks(cur, database))

    for skill in list_skills():
        root = skill["path"].parent
        dest_root = f"@{database}.CORE.SKILL_STAGE/{skill['folder']}/{_stage_dir(skill)}"
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
        f"""MERGE INTO {database}.CORE.MAPPING_SCORING_CONFIG t
            USING (SELECT %s AS CONFIG_ID, NULL AS DOMAIN_ID, %s AS VERSION, PARSE_JSON(%s) AS WEIGHTS,
                          PARSE_JSON(%s) AS THRESHOLDS, TRUE AS ACTIVE_FLAG, 'SEED' AS CREATED_BY) s
               ON t.CONFIG_ID = s.CONFIG_ID
            WHEN MATCHED THEN UPDATE SET WEIGHTS = s.WEIGHTS, THRESHOLDS = s.THRESHOLDS, ACTIVE_FLAG = s.ACTIVE_FLAG
            WHEN NOT MATCHED THEN INSERT (CONFIG_ID, DOMAIN_ID, VERSION, WEIGHTS, THRESHOLDS, ACTIVE_FLAG, CREATED_BY)
                 VALUES (s.CONFIG_ID, s.DOMAIN_ID, s.VERSION, s.WEIGHTS, s.THRESHOLDS, s.ACTIVE_FLAG, s.CREATED_BY)""",
        (SCORING_CONFIG_ID, 1, json.dumps(DEFAULT_WEIGHTS), json.dumps(DEFAULT_THRESHOLDS)),
    )
    return log


# ---------------------------------------------------------------- Airflow runbooks: domain/general/airflow_runbook_pack.json


def load_runbooks() -> List[Dict[str, Any]]:
    """The shipped AIRFLOW_RUNBOOK items (the Airflow knowledge pack's runbooks)."""
    if not RUNBOOK_PACK.exists():
        return []
    return list(json.loads(RUNBOOK_PACK.read_text(encoding="utf-8")).get("knowledge") or [])


class _CursorDb:
    """A connector cursor as an ops Db (query/execute/execute_count, %s binds, lower case names), with the platform
    tables qualified by the deployed database, so the shared knowledge writer runs at deploy time."""

    def __init__(self, cur, database: str):
        self.cur, self.database = cur, database

    def _sql(self, sql: str) -> str:
        return re.sub(r"(?<![\w.])CORE\.", f"{self.database}.CORE.", sql)

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.cur.execute(self._sql(sql), params or None)
        names = [str(d[0]).lower() for d in (self.cur.description or [])]
        return [dict(zip(names, row)) for row in self.cur.fetchall()] if names else []

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.cur.execute(self._sql(sql), params or None)

    def execute_count(self, sql: str, params: tuple = ()) -> int:
        self.cur.execute(self._sql(sql), params or None)
        return int(self.cur.rowcount or 0)


def seed_runbooks(cur, database: str) -> List[str]:
    """Write the shipped runbooks into the GENERAL domain (origin SEED, ACTIVE). A new version only when the file
    changed, and never over a runbook a person edited in Knowledge (its current version is not from the seed)."""
    from services.knowledge.procedures import GENERAL_DOMAIN, ensure_domain
    from services.knowledge.writer import remember
    from services.ops.sqlio import DbSession

    items = load_runbooks()
    if not items:
        return []
    session = DbSession(_CursorDb(cur, database))
    domain_id = ensure_domain(session, GENERAL_DOMAIN, "Targets registered without a domain pack")
    written, kept = 0, 0
    for k in items:
        current = session.sql("""SELECT ORIGIN FROM CORE.DOMAIN_KNOWLEDGE WHERE DOMAIN_ID = ? AND SOURCE_REFERENCE = ?
                                    AND IS_CURRENT LIMIT 1""", params=[domain_id, k["key"]]).collect()
        if current and str(current[0]["ORIGIN"] or "").upper() != "SEED":
            kept += 1
            continue
        if remember(session, domain_id=domain_id, kind=k["type"], key=k["key"], title=k["title"], content=k["content"],
                    content_json=k.get("content_json"), tags=k.get("tags") or ["AIRFLOW", "RUNBOOK"], origin="SEED",
                    change_note="Airflow runbook pack (repository)"):
            written += 1
    return [f"airflow runbooks: {written} written, {len(items) - written - kept} unchanged, {kept} edited in the app (kept)"]


# ---------------------------------------------------------------- QA Store: domain/<name>/qa/<pack>.sql


def load_qa_packs() -> List[Tuple[str, Dict[str, Any], Path]]:
    """(domain name, parsed pack, file) for every domain/<dir>/qa/*.sql; the domain name comes from the folder's
    domain_pack.json."""
    from services.qa.pack_files import parse

    out = []
    for path in sorted(DOMAIN_DIR.glob("*/qa/*.sql")):
        pack_file = path.parent.parent / "domain_pack.json"
        name = (json.loads(pack_file.read_text(encoding="utf-8"))["domain"]["name"] if pack_file.exists()
                else path.parent.parent.name.upper())
        try:
            parsed = parse(path.read_text(encoding="utf-8"), default_name=path.stem.replace("_", " ").title())
        except ValueError as exc:
            raise AssertionError(f"{path.relative_to(ROOT)}: {exc}") from exc
        out.append((name, parsed, path))
    return out


def _saved_mapping(cur, database: str) -> Dict[str, Any]:
    """ENVIRONMENT_BINDINGS as saved in this deployment (DEFAULT when none is)."""
    from services.common import environments as envs

    try:
        cur.execute(f"SELECT CONFIG_VALUE FROM {database}.CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT "
                    "ORDER BY VERSION DESC LIMIT 1", (envs.CONFIG_KEY,))
        found = cur.fetchall()
    except Exception:
        return dict(envs.DEFAULT)
    value = found[0][0] if found else None
    return envs.validate(json.loads(value) if isinstance(value, str) else value)[0]


def pack_mappings(cur, database: str):
    """domain name -> the database per role its repository packs read with here (services/common/environments.
    effective): the saved mapping, Silver falling back to the domain pack's silver database, and its location."""
    from services.common import environments as envs

    config = _saved_mapping(cur, database)
    homes: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for path in DOMAIN_DIR.glob("*/domain_pack.json"):
        domain = json.loads(path.read_text(encoding="utf-8")).get("domain") or {}
        homes[str(domain.get("name") or "").upper()] = (domain.get("silver_database"), domain.get("silver_schema"))

    def mapping(domain_name: str) -> Dict[str, str]:
        db, schema = homes.get(str(domain_name or "").upper(), (None, None))
        location = envs.normalise_location(f"{db}.{schema}") if db and schema else None
        return envs.effective(config, db, location)
    return mapping


def check_pack_bindings(cur, database: str) -> List[str]:
    """Warn when repository QA packs use a database role this deployment maps nowhere: those queries fail until the
    role is mapped in Admin, Environments (or the environment file's [environment] seeds it). Silver falls back to the
    domain's registered Silver database and the pack's own domain needs no mapping."""
    from services.common import environments as envs

    mapping = pack_mappings(cur, database)
    missing: Dict[str, set] = {}
    for domain_name, parsed, _ in load_qa_packs():
        known = mapping(domain_name)
        for q in parsed["queries"]:
            for name in envs.references(q["sql_expression"]):
                if name not in known:
                    missing.setdefault(name, set()).add(f"{domain_name}/{parsed['name']}")
    return [f"WARNING qa packs: no {envs.label(name)} database is mapped for this deployment; {len(packs)} repository "
            "pack(s) fail until it is (Admin, Environments, or [environment] in the environment file)"
            for name, packs in sorted(missing.items())]


def seed_qa_packs(cur, database: str) -> List[str]:
    """Repository packs become APPROVED versions (SOURCE REPO) and their queries ACTIVE QA_TEST knowledge. A pack the
    app edited is never overwritten: when the repository file changed since those edits, the deploy records a conflict
    (CORE.QA_PACK_SYNC) for someone to resolve in the QA Store (services/qa/pack_git.sync_decision)."""
    from services.common.environments import shown
    from services.qa.pack_git import repo_sha, signature, sync_decision
    from services.qa.packs import knowledge_key, query_id_for, seed_rows

    mapping = pack_mappings(cur, database)

    try:
        cur.execute(f"SELECT 1 FROM {database}.CORE.DOMAIN_QA_PACKS LIMIT 1")
    except Exception:
        return ["qa packs: V037 not deployed (skipped)"]
    try:
        cur.execute(f"SELECT REPO_SHA FROM {database}.CORE.DOMAIN_QA_PACKS LIMIT 1")
        tracked = True
    except Exception:  # before V042: no repository tracking, app edits are kept as before
        tracked = False
    log: List[str] = []
    knowledge = []
    for domain_name, parsed, path in load_qa_packs():
        did = domain_id(domain_name)
        data = seed_rows(did, parsed)
        pid, meta, queries = data["pack_id"], data["meta"], data["queries"]
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        sha = repo_sha(text)
        cols = "VERSION, STATUS, SOURCE" + (", REPO_SHA" if tracked else "")
        cur.execute(f"SELECT {cols} FROM {database}.CORE.DOMAIN_QA_PACKS WHERE PACK_ID = %s ORDER BY VERSION DESC",
                    (pid,))
        versions = cur.fetchall()
        newest = ({"version": versions[0][0], "status": versions[0][1], "source": versions[0][2],
                   "repo_sha": versions[0][3] if tracked else None} if versions else None)
        current: List[Dict[str, Any]] = []
        if newest:
            cur.execute(f"SELECT QUERY_NAME, SQL_EXPRESSION, SEVERITY, EXPECTATION FROM {database}.CORE.DOMAIN_QA_QUERIES "
                        f"WHERE PACK_ID = %s AND PACK_VERSION = %s ORDER BY ORDINAL", (pid, newest["version"]))
            current = [{"query_name": r[0], "sql_expression": r[1], "severity": r[2], "expectation": r[3]}
                       for r in cur.fetchall()]
        decision = sync_decision(newest, current, sha, queries)
        if decision == "CONFLICT" and tracked and not newest.get("repo_sha"):
            # app edits made before V042 carry no repository hash: when the file still matches the pack's last seeded
            # version, the repository did not change since, so the app version is ahead (record the lineage)
            cur.execute(f"""SELECT Q.QUERY_NAME, Q.SQL_EXPRESSION, Q.SEVERITY, Q.EXPECTATION
                              FROM {database}.CORE.DOMAIN_QA_QUERIES Q
                             WHERE Q.PACK_ID = %s AND Q.PACK_VERSION = (SELECT MAX(VERSION) FROM {database}.CORE.DOMAIN_QA_PACKS
                                                                         WHERE PACK_ID = %s AND SOURCE = 'REPO')
                             ORDER BY Q.ORDINAL""", (pid, pid))
            seeded = [{"query_name": r[0], "sql_expression": r[1], "severity": r[2], "expectation": r[3]} for r in cur.fetchall()]
            if seeded and signature(seeded) == signature(queries):
                cur.execute(f"UPDATE {database}.CORE.DOMAIN_QA_PACKS SET REPO_SHA = %s, REPO_PATH = %s "
                            f"WHERE PACK_ID = %s AND VERSION = %s", (sha, rel, pid, newest["version"]))
                decision = "AHEAD"
        label = f"qa pack {domain_name}/{meta['name']}"
        if decision in ("UNCHANGED", "IN_SYNC", "AHEAD", "CONFLICT") and not tracked:
            log.append(f"{label}: {'unchanged' if decision in ('UNCHANGED', 'IN_SYNC') else 'edited in the app, kept'}")
            continue
        if decision in ("UNCHANGED", "IN_SYNC"):
            cur.execute(f"UPDATE {database}.CORE.DOMAIN_QA_PACKS SET REPO_SHA = %s, REPO_PATH = %s "
                        f"WHERE PACK_ID = %s AND VERSION = %s", (sha, rel, pid, newest["version"]))
            cur.execute(f"UPDATE {database}.CORE.QA_PACK_SYNC SET STATUS = 'RESOLVED', RESOLUTION = 'IN_SYNC', "
                        f"RESOLVED_BY = 'DEPLOY', RESOLVED_AT = CURRENT_TIMESTAMP() WHERE PACK_ID = %s AND STATUS = 'OPEN'",
                        (pid,))
            log.append(f"{label}: {'unchanged' if decision == 'UNCHANGED' else 'in step with the repository'}")
            continue
        if decision == "AHEAD":
            log.append(f"{label}: edited in the app since {rel}; kept (publish it to Git to update the file)")
            continue
        if decision == "CONFLICT":
            cur.execute(f"SELECT COUNT(*) FROM {database}.CORE.QA_PACK_SYNC WHERE PACK_ID = %s AND STATUS = 'OPEN' "
                        f"AND REPO_SHA = %s", (pid, sha))
            if not cur.fetchone()[0]:
                cur.execute(f"UPDATE {database}.CORE.QA_PACK_SYNC SET STATUS = 'RESOLVED', RESOLUTION = 'SUPERSEDED', "
                            f"RESOLVED_BY = 'DEPLOY', RESOLVED_AT = CURRENT_TIMESTAMP() WHERE PACK_ID = %s AND STATUS = 'OPEN'",
                            (pid,))
                cur.execute(f"""INSERT INTO {database}.CORE.QA_PACK_SYNC (SYNC_ID, PACK_ID, DOMAIN_ID, REPO_PATH, REPO_SHA,
                                       REPO_CONTENT, APP_VERSION, BASE_REPO_SHA, STATUS)
                                SELECT %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN'""",
                            (str(uuid.uuid4()), pid, did, rel, sha, text, newest["version"], newest.get("repo_sha")))
            log.append(f"{label}: CONFLICT, {rel} changed since the app edits; resolve it in the QA Store")
            continue
        if newest:  # UPDATE: the repository owns the pack and the file changed
            dropped = {q["query_name"].lower() for q in current} - {q["query_name"].lower() for q in queries}
            for name in dropped:
                cur.execute(f"UPDATE {database}.CORE.DOMAIN_KNOWLEDGE SET IS_CURRENT = FALSE, STATUS = 'RETIRED' "
                            f"WHERE DOMAIN_ID = %s AND SOURCE_REFERENCE = %s AND IS_CURRENT",
                            (did, knowledge_key(pid, name)))
        version = (newest["version"] + 1) if newest else 1
        cur.execute(f"UPDATE {database}.CORE.DOMAIN_QA_PACKS SET STATUS = 'SUPERSEDED' "
                    f"WHERE PACK_ID = %s AND STATUS = 'APPROVED'", (pid,))
        repo_cols, repo_vals = (", REPO_SHA, REPO_PATH", ", %s, %s") if tracked else ("", "")
        cur.execute(
            f"""INSERT INTO {database}.CORE.DOMAIN_QA_PACKS (PACK_ID, VERSION, DOMAIN_ID, NAME, DESCRIPTION, TAGS,
                       STATUS, SOURCE, CHANGE_NOTE, CREATED_BY, APPROVED_BY, APPROVED_AT{repo_cols})
                SELECT %s, %s, %s, %s, %s, PARSE_JSON(%s)::ARRAY, 'APPROVED', 'REPO', %s, 'SEED', 'SEED', CURRENT_TIMESTAMP(){repo_vals}""",
            (pid, version, did, meta["name"], meta["description"], json.dumps(meta["tags"]),
             f"Seeded from {rel}", *((sha, rel) if tracked else ())))
        for q in queries:
            cur.execute(
                f"""INSERT INTO {database}.CORE.DOMAIN_QA_QUERIES (QUERY_ID, PACK_ID, PACK_VERSION, QUERY_NAME,
                           DESCRIPTION, SQL_EXPRESSION, SEVERITY, EXPECTATION, TAGS, ORDINAL, CREATED_BY)
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s)::ARRAY, %s, 'SEED'""",
                (query_id_for(pid, q["query_name"]), pid, version, q["query_name"], q.get("description") or None,
                 q["sql_expression"], q["severity"], q["expectation"], json.dumps(q["tags"]), q["ordinal"]))
            key = knowledge_key(pid, q["query_name"])
            sql = shown(q["sql_expression"], mapping(domain_name))   # knowledge reads with real table names
            content = (f"{meta['name']}: {q['query_name']}. {q.get('description') or ''} "
                       f"Passes when: {q['expectation']}. Severity {q['severity']}.\n\n{sql}")
            knowledge.append((_stable_id("k", key), did, "QA_TEST", f"{meta['name']}: {q['query_name']}", content,
                              {"pack_id": pid, "pack": meta["name"], "version": version, "sql": sql,
                               "expected": q["expectation"], "severity": q["severity"], "tags": q["tags"]},
                              ["QA", "PACK", *q["tags"]][:20], key, "ACTIVE", version, True))
        log.append(f"{label}: version {version} ({len(queries)} queries)")
    if knowledge:
        merge(cur.execute, database, {"domains": [], "tables": [], "columns": [], "knowledge": knowledge})
    return log
