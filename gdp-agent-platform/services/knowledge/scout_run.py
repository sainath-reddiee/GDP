"""The skill scout's scan: read approved knowledge and how it was used, find themes worth a skill (services.knowledge
.scout), and for each, up to max_per_run: generalise the material, draft a playbook for any client, generalise the
draft again, test it against what the stage uses today, and file it in CORE.SKILL_PROPOSAL.

A proposal that passes becomes PROPOSED with a DRAFT version in CORE.SKILL_REGISTRY (ORIGIN AI, CONFIG.scout holding
where it came from, and no label, so nothing loads it). One that fails (a check error, a worse verdict, or a client
name that survived) is DISCARDED with the reason, so the next scan does not draft it again until its evidence doubles.
AI cost is recorded under SKILLS. Runs from the worker (weekly) or from the Skills page (one cluster at a time).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from services.knowledge import scout
from services.ops.redact import redact

AGENT = "skill_scout"
STAGE = "SKILLS"
INSTRUCTIONS = ("Write a playbook any client can use, not a description of one client. Keep every placeholder written "
                "like <DATABASE>, <SCHEMA>, <TABLE>, <DOMAIN>, <DAG> exactly as it is; never name a client, a company, a "
                "system, a database or a table. Ground every rule in the material; mark anything else as an assumption.")
MATERIAL_SQL = """SELECT K.KNOWLEDGE_ID, K.LINEAGE_ID, K.DOMAIN_ID, D.DOMAIN_NAME, K.KNOWLEDGE_TYPE, K.TITLE, K.CONTENT, K.TAGS,
                         K.SOURCE_REFERENCE, K.ORIGIN, K.VERIFIED_BY, K.REVIEWED_BY, O.USES, O.RUNS, O.OBSERVED, O.HELPFULNESS
                    FROM CORE.DOMAIN_KNOWLEDGE K JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                    LEFT JOIN (SELECT LINEAGE_ID, SUM(USES) AS USES, SUM(RUNS) AS RUNS, SUM(OBSERVED) AS OBSERVED,
                                      AVG(HELPFULNESS) AS HELPFULNESS FROM CORE.KNOWLEDGE_OUTCOME GROUP BY LINEAGE_ID) O
                      ON O.LINEAGE_ID = K.LINEAGE_ID
                   WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND ARRAY_CONTAINS(K.KNOWLEDGE_TYPE::VARIANT, PARSE_JSON(%s))
                   ORDER BY COALESCE(K.UPDATED_AT, K.CREATED_AT) DESC LIMIT 2000"""
# before knowledge outcomes exist (or without the grant): no usage, approval by people only
MATERIAL_SQL_PLAIN = """SELECT K.KNOWLEDGE_ID, K.LINEAGE_ID, K.DOMAIN_ID, D.DOMAIN_NAME, K.KNOWLEDGE_TYPE, K.TITLE, K.CONTENT,
                               K.TAGS, K.SOURCE_REFERENCE, K.ORIGIN, K.VERIFIED_BY, K.REVIEWED_BY
                          FROM CORE.DOMAIN_KNOWLEDGE K JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                         WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE'
                           AND ARRAY_CONTAINS(K.KNOWLEDGE_TYPE::VARIANT, PARSE_JSON(%s))
                         ORDER BY COALESCE(K.UPDATED_AT, K.CREATED_AT) DESC LIMIT 2000"""
LEXICON_SQL = {
    "DOMAIN": "SELECT DOMAIN_NAME AS N FROM CORE.DOMAIN_REGISTRY",
    "DATABASE": "SELECT DISTINCT TARGET_DATABASE AS N FROM CORE.TARGET_TABLE_REGISTRY",
    "SCHEMA": "SELECT DISTINCT TARGET_SCHEMA AS N FROM CORE.TARGET_TABLE_REGISTRY",
    "TABLE": "SELECT DISTINCT TARGET_TABLE AS N FROM CORE.TARGET_TABLE_REGISTRY",
    "DAG": "SELECT DISTINCT DAG_ID AS N FROM CORE.DAG",
    "REPO": "SELECT NAME AS N FROM CORE.REPO",
    "ENV": "SELECT NAME AS N FROM CORE.AIRFLOW_ENV",
    "SOURCE": "SELECT SOURCE_SYSTEM_NAME AS N FROM CORE.SOURCE_REGISTRY",
}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def load_settings(db: Any) -> Dict[str, Any]:
    try:
        found = db.query("""SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT
                             ORDER BY VERSION DESC LIMIT 1""", (scout.KEY,))
        return scout.settings(_json(found[0]["config_value"]) if found else None)
    except Exception:
        return scout.settings(None)


def material(db: Any) -> List[Dict[str, Any]]:
    types = json.dumps(list(scout.TYPES))
    try:
        found = db.query(MATERIAL_SQL, (types,))
    except Exception:
        found = db.query(MATERIAL_SQL_PLAIN, (types,))
    out = []
    for r in found:
        row = dict(r)
        row["tags"] = _json(row.get("tags")) or []
        out.append(row)
    return out


def names(db: Any) -> List[tuple]:
    rows: Dict[str, List[str]] = {}
    for kind, sql in LEXICON_SQL.items():
        try:
            rows[kind] = [r.get("n") for r in db.query(sql)]
        except Exception:
            rows[kind] = []
    return scout.lexicon(rows)


def previous(db: Any) -> Dict[str, Dict[str, Any]]:
    """The newest proposal per cluster key (empty before V067)."""
    try:
        found = db.query("""SELECT CLUSTER_KEY, STATUS, EVIDENCE_COUNT, PROPOSAL_ID FROM CORE.SKILL_PROPOSAL
                            QUALIFY ROW_NUMBER() OVER (PARTITION BY CLUSTER_KEY ORDER BY CREATED_AT DESC) = 1""")
    except Exception:
        return {}
    return {r["cluster_key"]: dict(r) for r in found}


def skills(db: Any) -> Dict[str, Any]:
    """{names, cited (knowledge keys skills already rest on), by_stage: {stage: [{skill_name, description}]},
    production: {skill_name: content}}."""
    from services.knowledge.usage import STAGE_SKILLS

    registry = db.query("""SELECT SKILL_ID, SKILL_NAME, DESCRIPTION, CONFIG, EVAL_JSON FROM CORE.SKILL_REGISTRY
                            WHERE STATUS IN ('ACTIVE', 'DRAFT')""")
    cited = set()
    for r in registry:
        for blob in (_json(r.get("config")), _json(r.get("eval_json"))):
            if isinstance(blob, dict):
                for key in (blob.get("used_knowledge") or []) + ((blob.get("scout") or {}).get("evidence_refs") or []):
                    cited.add(str(key))
    try:
        bound = db.query("SELECT STAGE, SKILL_NAME FROM CORE.SKILL_STAGE_BINDING WHERE ENABLED")
        stage_names: Dict[str, List[str]] = {}
        for b in bound:
            stage_names.setdefault(str(b["stage"]).upper(), []).append(b["skill_name"])
    except Exception:
        stage_names = {k: list(v) for k, v in STAGE_SKILLS.items()}
    describe = {r["skill_name"]: r.get("description") for r in registry}
    by_stage = {stage: [{"skill_name": n, "description": describe.get(n)} for n in ns] for stage, ns in stage_names.items()}
    return {"names": sorted({r["skill_name"] for r in registry}), "cited": cited, "by_stage": by_stage}


def _production(db: Any, stage: str, stage_skills: List[Dict[str, Any]]) -> tuple:
    """(content, label) of the first production skill bound to the stage, else ("", "no skill")."""
    for s in stage_skills:
        found = db.query("""SELECT R.CONTENT, R.VERSION FROM CORE.SKILL_LABEL L JOIN CORE.SKILL_REGISTRY R ON R.SKILL_ID = L.SKILL_ID
                             WHERE L.SKILL_NAME = %s AND L.LABEL = 'production' LIMIT 1""", (s["skill_name"],))
        if found and found[0].get("content"):
            return found[0]["content"], f"{s['skill_name']} (production at {stage})"
    return "", "no skill"


def plan(db: Any, conf: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The dry run (no AI): the clusters, which would be drafted next and why the others are skipped."""
    conf = conf or load_settings(db)
    found = scout.clusters(material(db), conf)
    prev, known = previous(db), skills(db)
    out = []
    for c in found:
        reason = scout.skip_reason(c, prev.get(c["key"]), known["cited"], known["by_stage"].get(c["stage"], []))
        out.append({"key": c["key"], "type": c["type"], "stage": c["stage"], "theme": c["theme"], "items": len(c["items"]),
                    "domains": c["domains"], "runs": c["runs"], "helpfulness": c["helpfulness"], "rank": c["rank"],
                    "skip": reason, "titles": [redact(str(i.get("title") or ""))[:120] for i in c["items"][:5]]})
    return {"settings": conf, "clusters": out, "due": [o for o in out if not o["skip"]][:conf["max_per_run"]]}


def _record(db: Any, row: Dict[str, Any]) -> str:
    proposal_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CORE.SKILL_PROPOSAL (PROPOSAL_ID, CLUSTER_KEY, STATUS, SKILL_ID, SKILL_NAME, TITLE, STAGE,
                         EVIDENCE_COUNT, DOMAIN_COUNT, EVIDENCE, REPLACEMENTS, TRANSFER_SCORE, TRANSFER_LABEL, EVAL, ISSUES,
                         DISCARD_REASON, MODEL)
                  SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), %s, %s, PARSE_JSON(NULLIF(%s, '')),
                         PARSE_JSON(%s), %s, %s""",
               (proposal_id, row["key"], row["status"], row.get("skill_id"), row.get("skill_name"), row.get("title"),
                row["stage"], row["evidence_count"], row["domains"], json.dumps(row["evidence"], default=str),
                json.dumps(row.get("replacements") or [], default=str), row.get("transfer_score"),
                row.get("transfer_label"), json.dumps(row["eval"], default=str) if row.get("eval") else "",
                json.dumps(row.get("issues") or [], default=str), (row.get("discard_reason") or "")[:2000] or None,
                row.get("model")))
    return proposal_id


def draft_one(db: Any, cluster: Dict[str, Any], known: Dict[str, Any], lexicon: List[tuple],
              complete: Callable[..., Any]) -> Dict[str, Any]:
    """Generalise, draft, generalise again, test and file one cluster. Returns the proposal row as filed."""
    from services.knowledge import skill_builder as sb
    from services.knowledge import skill_drafts
    from services.knowledge.usage import STAGE_SKILLS

    evidence = [{"knowledge_id": i.get("knowledge_id"), "lineage_id": i.get("lineage_id"),
                 "title": redact(str(i.get("title") or ""))[:200], "domain": i.get("domain_name"),
                 "runs": i.get("runs"), "helpfulness": i.get("helpfulness")} for i in cluster["items"][:40]]
    base = {"key": cluster["key"], "stage": cluster["stage"], "evidence_count": len(cluster["items"]),
            "domains": cluster["domains"], "evidence": evidence, "title": None}
    counts: Dict[str, int] = {}
    items, residual = [], []
    for i in cluster["items"][:40]:
        title, c1, r1 = scout.generalise(str(i.get("title") or ""), lexicon)
        content, c2, r2 = scout.generalise(str(i.get("content") or ""), lexicon)
        for c in (c1, c2):
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
        residual += r1 + r2
        items.append({"knowledge_type": i.get("knowledge_type"), "title": title, "content": content,
                      "source_reference": i.get("source_reference") or i.get("knowledge_id")})

    def replaced() -> List[Dict[str, Any]]:
        return [{"from_kind": k.lower(), "placeholder": scout.PLACEHOLDER[k], "count": v} for k, v in sorted(counts.items())]

    if residual:
        return {**base, "status": "DISCARDED", "replacements": replaced(),
                "discard_reason": "client specific names could not be generalised: " + ", ".join(sorted(set(residual))[:5])}
    try:
        categories = [r["category_id"] for r in db.query("SELECT CATEGORY_ID FROM CORE.SKILL_CATEGORY")]
    except Exception:
        categories = []
    category = scout.CATEGORY_OF_STAGE.get(cluster["stage"], "general")
    categories = categories or [category, "general"]
    stages = list(STAGE_SKILLS)
    out = skill_drafts.draft(complete, "knowledge", categories=categories, stages=stages, existing_names=known["names"],
                             knowledge=items, instructions=INSTRUCTIONS, category_id=category,
                             knowledge_keys=[str(i["source_reference"]) for i in items])
    d = out["draft"]
    taken = {n.upper().replace("_", "-") for n in known["names"]}
    if d["name"].replace("_", "-") in taken:   # never a new version of someone else's skill
        d["name"] = f"{d['name'][:57]}-SCOUT"
    for section in d.get("sections") or []:
        section["markdown"], c, r = scout.generalise(str(section.get("markdown") or ""), lexicon)
        residual += r
        for k, v in c.items():
            counts[k] = counts.get(k, 0) + v
    for key in ("title", "description"):
        d[key], _, r = scout.generalise(str(d.get(key) or ""), lexicon)
        residual += r
    content = sb.assemble(d)
    issues = sb.check(d, content, known["names"], knowledge_keys=[str(i["source_reference"]) for i in items])
    base.update({"skill_name": d["name"], "title": str(d.get("title") or d["name"])[:512], "issues": issues,
                 "model": out["model"], "replacements": replaced()})
    if residual:
        return {**base, "status": "DISCARDED",
                "discard_reason": "the draft named client specific things: " + ", ".join(sorted(set(residual))[:5])}
    errors = [i["message"] for i in issues if i["level"] == "error"]
    if errors:
        return {**base, "status": "DISCARDED", "discard_reason": errors[0]}
    tests = d.get("tests") or []
    if len(tests) < 2:
        return {**base, "status": "DISCARDED", "discard_reason": "the draft has fewer than two tests, so it cannot be checked"}
    baseline, label = _production(db, cluster["stage"], known["by_stage"].get(cluster["stage"], []))
    summary = skill_drafts.evaluate(complete, content, tests, baseline, label)
    trimmed = {k: summary.get(k) for k in ("total", "candidate_passed", "baseline_passed", "candidate_rate",
                                           "baseline_rate", "candidate_avg", "baseline_avg", "baseline_label",
                                           "verdict", "model", "tested_at")}
    score = scout.transfer(cluster["domains"], summary, sum(counts.values()), len(content))
    base.update({"eval": trimmed, "transfer_score": score["score"], "transfer_label": score["label"]})
    if summary.get("verdict") == "worse":
        return {**base, "status": "DISCARDED", "discard_reason": f"it did worse than {label} on its own tests"}
    config = {"scout": {"cluster_key": cluster["key"], "stage": cluster["stage"], "evidence_refs": [str(i["source_reference"]) for i in items],
                        "evidence_count": len(cluster["items"]), "domains": cluster["domains"], "transfer": score,
                        "replacements": replaced()}}
    note = (f"Proposed by the skill scout from {len(cluster['items'])} approved knowledge items in "
            f"{cluster['domains']} domain(s) ({cluster['type'].lower().replace('_', ' ')}, used in {cluster['runs']} runs).")
    skill_id = skill_drafts.insert_skill(db, d["name"], str(d.get("description") or "")[:1000], category, content, note,
                                         "AI", trimmed, config)
    return {**base, "status": "PROPOSED", "skill_id": skill_id}


def scan(db: Any, *, limit: Optional[int] = None, keys: Optional[List[str]] = None,
         complete: Optional[Callable[..., Any]] = None, still_held: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Draft and file the due clusters (at most `limit`, default the setting; `keys` picks clusters). Never raises for
    one cluster."""
    from services.ops.sqlio import as_session

    conf = load_settings(db)
    planned = plan(db, conf)
    by_key = {c["key"]: c for c in scout.clusters(material(db), conf)}
    wanted = [c for c in planned["clusters"] if not c["skip"] and (not keys or c["key"] in keys)]
    wanted = wanted[:min(limit or conf["max_per_run"], conf["max_per_run"])]
    session = as_session(db)

    def call(prompt: str, schema: Dict[str, Any], max_tokens: int):
        if complete is not None:
            return complete(prompt, schema, max_tokens)
        from services.common.audit import record_cost
        from services.common.llm import complete_json

        started = time.time()
        obj, usage, model = complete_json(session, prompt, schema, max_tokens=min(int(max_tokens), 16000), stage=STAGE)
        try:
            record_cost(session, None, STAGE, model, usage, int((time.time() - started) * 1000), tool_calls=1, agent=AGENT)
        except Exception:
            pass
        return obj, usage, model

    known, lex = skills(db), names(db)
    out: Dict[str, Any] = {"clusters": len(planned["clusters"]), "proposed": [], "discarded": [], "errors": []}
    for c in wanted:
        if still_held is not None and not still_held():
            break
        try:
            row = draft_one(db, by_key[c["key"]], known, lex, call)
            proposal_id = _record(db, row)
            entry = {"proposal_id": proposal_id, "key": c["key"], "skill_name": row.get("skill_name"),
                     "reason": row.get("discard_reason")}
            out["proposed" if row["status"] == "PROPOSED" else "discarded"].append(entry)
            if row.get("skill_name"):
                known["names"].append(row["skill_name"])
        except Exception as exc:
            out["errors"].append(f"{c['key'][:60]}: {redact(str(exc))[:200]}")
    return out


def weekly(db: Any, job: str = "skill_scout", now: Optional[datetime] = None,
           still_held: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """The worker's entry: a scan when the cadence says so, remembered in the job's own lease row."""
    conf = load_settings(db)
    if not conf["enabled"]:
        return {"enabled": False}
    now = now or datetime.now(timezone.utc)
    found = db.query("SELECT CURSOR_VALUE FROM CORE.JOB_LEASE WHERE JOB_NAME = %s", (job,))
    cursor = found[0].get("cursor_value") if found else None
    if not scout.due(cursor, now, conf["cadence_days"]):
        return {"enabled": True, "due": False}
    try:
        db.query("SELECT 1 AS X FROM CORE.SKILL_PROPOSAL LIMIT 1")
    except Exception:
        return {"enabled": True, "due": True, "detail": "the skill scout needs migration V067"}
    result = scan(db, still_held=still_held)
    db.execute("UPDATE CORE.JOB_LEASE SET CURSOR_VALUE = %s WHERE JOB_NAME = %s", (now.isoformat(), job))
    return {"enabled": True, "due": True, **result}
