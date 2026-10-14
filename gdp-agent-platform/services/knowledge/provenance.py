"""How a domain is built (pure): where each piece of its knowledge came from, drawn as sources feeding the domain's
parts, plus a build timeline. Sources are the knowledge rows' ORIGIN (repository pack, people, the Copilot, what runs
learned in mapping, STTM, dbt, data quality, profiling, modeling, QA), the skill contract the pack was generated from,
and the connected code repositories. Nothing here talks to Snowflake.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

# ORIGIN -> (source id, label, what it means)
SOURCES: Dict[str, tuple] = {
    "SEED": ("pack", "Repository pack", "Shipped with the platform: the domain pack file, generated from the skill contract"),
    "PACK_IMPORT": ("pack", "Repository pack", "Imported from a domain pack file"),
    "USER": ("people", "People", "Written or edited by a person in the app"),
    "COPILOT": ("copilot", "Copilot", "Saved from a Copilot answer, after review"),
    "DOMAIN_AI": ("ai", "AI drafting", "Drafted by AI from the domain's own data, after review"),
    "MAPPING": ("mapping", "Learned in mapping", "Learned from approved source-to-target mappings in runs"),
    "STTM": ("mapping", "Learned in mapping", "Learned from approved STTM documents"),
    "DBT": ("dbt", "Learned in dbt", "Learned from dbt code generated and approved in runs"),
    "SODA": ("quality", "Learned in data quality", "Learned from data quality checks and their results"),
    "QUALITY": ("quality", "Learned in data quality", "Learned from data quality checks and their results"),
    "PROFILING": ("profiling", "Learned in profiling", "Learned from profiled source tables"),
    "MODELING": ("modeling", "Learned in modeling", "Learned from approved data models"),
    "QA": ("qa", "Learned in QA", "Learned from QA tests and sign-offs"),
    "CONFLUENCE": ("confluence", "Confluence", "Extracted from linked Confluence pages, after review"),
    "DOCUMENT": ("document", "Documents", "Extracted from uploaded documents, after review"),
    "CODE": ("code", "Code repositories", "Derived from the connected code repositories, after review"),
}
# KNOWLEDGE_TYPE -> (part id, label)
PARTS: Dict[str, tuple] = {
    "MODEL_DEFINITION": ("model", "Data model"), "STTM_TEMPLATE": ("model", "Data model"),
    "BUSINESS_RULE": ("rules", "Business rules"), "TRANSFORMATION_RULE": ("rules", "Business rules"),
    "EXCEPTION": ("rules", "Business rules"), "GLOSSARY": ("glossary", "Glossary"),
    "NAMING_STANDARD": ("standards", "Standards and guides"), "ONBOARDING_GUIDE": ("standards", "Standards and guides"),
    "DBT_PATTERN": ("patterns", "Build patterns"), "MAPPING_PATTERN": ("patterns", "Build patterns"),
    "SODA_PATTERN": ("tests", "Tests and checks"), "QA_TEST": ("tests", "Tests and checks"),
}
PART_ORDER = ["model", "rules", "glossary", "standards", "patterns", "tests", "other"]


def _low(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def source_of(origin: Optional[str]) -> tuple:
    return SOURCES.get(str(origin or "").upper(), ("other", "Other", "Origin not recorded"))


def part_of(kind: Optional[str]) -> tuple:
    return PARTS.get(str(kind or "").upper(), ("other", "Other knowledge"))


def build(domain: Dict[str, Any], rows: Iterable[Dict[str, Any]], usage: Optional[Dict[str, int]] = None,
          repos: Iterable[Dict[str, Any]] = (), versions: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """domain: the /api/domains/{id} detail (contract, origin, targets); rows: current knowledge items; usage:
    knowledge_id -> uses in the last 90 days; repos: [{name, matched}] code repositories and how many of their models
    or files name the domain's target tables; versions: the domain's recorded versions."""
    usage = usage or {}
    rows = [_low(r) for r in rows]
    sources: Dict[str, Dict[str, Any]] = {}
    parts: Dict[str, Dict[str, Any]] = {}
    edges: Counter = Counter()
    samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sid, label, meaning = source_of(r.get("origin"))
        pid, plabel = part_of(r.get("knowledge_type"))
        s = sources.setdefault(sid, {"id": sid, "label": label, "meaning": meaning, "count": 0, "verified": 0, "used": 0,
                                     "draft": 0, "types": Counter(), "people": set()})
        s["count"] += 1
        s["verified"] += 1 if r.get("verified_by") else 0
        s["used"] += 1 if usage.get(str(r.get("knowledge_id"))) else 0
        s["draft"] += 1 if str(r.get("status") or "").upper() == "DRAFT" else 0
        s["types"][str(r.get("knowledge_type") or "OTHER")] += 1
        if sid == "people" and r.get("created_by"):
            s["people"].add(str(r["created_by"]))
        parts.setdefault(pid, {"id": pid, "label": plabel, "count": 0})["count"] += 1
        edges[(sid, pid)] += 1
        samples[sid].append({"knowledge_id": r.get("knowledge_id"), "title": r.get("title"), "type": r.get("knowledge_type"),
                             "ref": r.get("source_reference"), "verified": bool(r.get("verified_by")),
                             "uses": int(usage.get(str(r.get("knowledge_id"))) or 0), "status": r.get("status")})
    name = str((domain.get("domain") or {}).get("domain_name") or "").lower()
    upstream: List[Dict[str, Any]] = []
    if "pack" in sources:   # the pack file, and the skill contract it was generated from
        sources["pack"]["files"] = [f"domain/{name}/domain_pack.json"] if domain.get("origin") == "repository" else []
        if domain.get("contract"):
            upstream.append({"id": "contract", "label": "Skill contract", "file": domain["contract"], "feeds": "pack",
                             "meaning": "The dbt skill's domain contract; the repository pack is generated from it"})
    targets = domain.get("targets") or []
    if targets:
        parts["targets"] = {"id": "targets", "label": "Target tables", "count": len(targets)}
        if "pack" in sources:
            edges[("pack", "targets")] += len(targets)
    for repo in repos:
        if int(repo.get("matched") or 0) <= 0:
            continue
        s = sources.setdefault("code", {"id": "code", "label": "Code repositories", "meaning": SOURCES["CODE"][2], "count": 0,
                                        "verified": 0, "used": 0, "draft": 0, "types": Counter(), "people": set(), "repos": []})
        s.setdefault("repos", []).append({"name": repo.get("name"), "matched": int(repo["matched"])})
        s["count"] += int(repo["matched"])
        edges[("code", "targets")] += int(repo["matched"])
    out_sources = []
    for s in sorted(sources.values(), key=lambda x: -x["count"]):
        items = sorted(samples.get(s["id"], []), key=lambda i: (-i["uses"], str(i["title"] or "")))
        out_sources.append({**{k: v for k, v in s.items() if k not in ("types", "people")},
                            "types": dict(s["types"].most_common()), "people": sorted(s["people"])[:10],
                            "items": items[:12]})
    ordered_parts = sorted(parts.values(), key=lambda p: (["targets"] + PART_ORDER).index(p["id"]) if p["id"] in ["targets"] + PART_ORDER else 99)
    total = len(rows)
    return {
        "sources": out_sources, "upstream": upstream, "parts": ordered_parts,
        "edges": [{"source": a, "part": b, "count": n} for (a, b), n in edges.most_common()],
        "totals": {"items": total, "verified": sum(1 for r in rows if r.get("verified_by")),
                   "used": sum(1 for r in rows if usage.get(str(r.get("knowledge_id")))),
                   "draft": sum(1 for r in rows if str(r.get("status") or "").upper() == "DRAFT")},
        "timeline": timeline(rows, versions),
    }


def timeline(rows: List[Dict[str, Any]], versions: Iterable[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """Domain versions and the days knowledge arrived, by source, newest first (at most 30 entries)."""
    events: List[Dict[str, Any]] = []
    for v in versions:
        v = _low(v)
        events.append({"at": str(v.get("created_at") or v.get("changed_at") or "")[:19], "kind": "version",
                       "label": f"Version {v.get('version')}: {str(v.get('change_kind') or v.get('kind') or 'change').lower()}",
                       "detail": v.get("change_note") or v.get("note") or "", "by": v.get("created_by") or v.get("changed_by")})
    days: Dict[tuple, int] = Counter()
    for r in rows:
        day = str(r.get("created_at") or "")[:10]
        if day:
            days[(day, source_of(r.get("origin"))[1])] += 1
    for (day, label), n in days.items():
        events.append({"at": day, "kind": "knowledge", "label": f"{label}: {n} item{'s' if n != 1 else ''}", "detail": "", "by": None})
    return sorted(events, key=lambda e: e["at"], reverse=True)[:30]


def manifest(domain: Dict[str, Any], *, standard: Optional[str] = None, bindings: Iterable[Dict[str, Any]] = (),
             loaded: Iterable[Dict[str, Any]] = (), repos: Iterable[Dict[str, Any]] = (), files: Iterable[Dict[str, Any]] = (),
             runs: Iterable[Dict[str, Any]] = (), rows: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Exactly what the domain is built with, for a developer: the skill and contract it was generated from, the pack
    file, the skills each stage loads for it (and the versions its runs actually loaded), the code repositories and
    files that define its tables, the runs that taught it, and the people who wrote or verified it."""
    name = str((domain.get("domain") or {}).get("domain_name") or "").lower()
    contract = str(domain.get("contract") or "")
    parts = contract.split("/")
    skill = None
    if len(parts) >= 4 and parts[0] == "snowflake" and parts[1] == "skills":   # snowflake/skills/<standard>/<skill>/...
        skill = {"standard": parts[2].upper(), "name": parts[3], "path": "/".join(parts[:4]), "contract": contract}
    rows = [_low(r) for r in rows]
    std = str(standard or "").upper()
    stage_skills: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in (_low(b) for b in bindings):
        if b.get("enabled") is False or str(b.get("standard") or "ANY").upper() not in ("ANY", std):
            continue
        stage_skills[str(b.get("stage"))].append({"skill": b.get("skill_name"), "position": b.get("position")})
    used = defaultdict(list)
    for l in (_low(l) for l in loaded):
        used[str(l.get("skill"))].append({"version": l.get("version"), "loads": int(l.get("n") or 0),
                                          "runs": int(l.get("runs") or 0), "last": str(l.get("last") or "")[:19]})
    stages = [{"stage": s, "skills": [{**k, "loaded": used.get(str(k["skill"]), [])} for k in sorted(v, key=lambda x: x.get("position") or 0)]}
              for s, v in sorted(stage_skills.items())]
    files_by_repo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in (_low(f) for f in files):
        files_by_repo[str(f.get("repo_id"))].append({"path": f.get("path"), "name": f.get("name"), "kind": f.get("kind")})
    out_repos = [{**{k: r.get(k) for k in ("name", "git_url", "branch", "last_commit", "last_indexed_at")},
                  "files": files_by_repo.get(str(r.get("repo_id")), [])[:25]} for r in (_low(r) for r in repos)]
    people = sorted({str(r["created_by"]) for r in rows if str(r.get("origin") or "").upper() == "USER" and r.get("created_by")}
                    | {str(r["verified_by"]) for r in rows if r.get("verified_by")})
    pack_items = sum(1 for r in rows if str(r.get("origin") or "").upper() in ("SEED", "PACK_IMPORT"))
    return {
        "skill": skill,
        "pack": {"file": f"domain/{name}/domain_pack.json", "generator": "infrastructure/contracts_to_packs.py" if skill else None,
                 "items": pack_items, "shipped": domain.get("origin") == "repository"} if pack_items or domain.get("origin") == "repository" else None,
        "standard": std or None, "stages": stages, "repos": out_repos,
        "runs": [{k: _low(r).get(k) for k in ("run_id", "run_name", "items", "kinds", "current_state")} for r in runs][:20],
        "people": people,
    }


def _state(ok: int, total: int) -> str:
    """ok: everything is there and trusted; partial: some of it; missing: none of it; none: nothing expected."""
    if total <= 0:
        return "none"
    return "ok" if ok >= total else "partial" if ok > 0 else "missing"


def flow(domain: Dict[str, Any], manifest_: Dict[str, Any], rows: Iterable[Dict[str, Any]], *,
         domain_model: Optional[Dict[str, Any]] = None, table_models: Iterable[Dict[str, Any]] = (),
         sttm: Iterable[Dict[str, Any]] = (), ingestion: Iterable[Dict[str, Any]] = (), jira: Iterable[Dict[str, Any]] = (),
         documents: Iterable[Dict[str, Any]] = (), proposed: int = 0, stale: int = 0) -> Dict[str, Any]:
    """The domain's development lifecycle as two lanes, each step with what it holds and how far it can be trusted:
    existing tables (in Silver) go through the data model and the knowledge base to Silver modeling, mapping and code;
    tables still to ingest come from Jira stories or spreadsheets through SnapLogic ingestion."""
    rows = [_low(r) for r in rows]
    targets = domain.get("targets") or []
    silver = domain.get("silver") or {}
    tm = [_low(t) for t in table_models]
    st = [_low(s) for s in sttm]
    ing = [_low(i) for i in ingestion]
    jr = [_low(j) for j in jira]
    docs = [_low(d) for d in documents]
    dm = _low(domain_model or {})
    verified = sum(1 for r in rows if r.get("verified_by"))
    approved_sttm = sum(1 for s in st if str(s.get("status") or "").upper() == "APPROVED")
    code_files = sum(len(r.get("files") or []) for r in manifest_.get("repos") or [])
    skill = manifest_.get("skill") or {}
    model_items: List[Dict[str, Any]] = []
    if skill:
        model_items.append({"label": "Contract: " + str(skill.get("contract") or "").split("/")[-1], "kind": "contract", "state": "ok"})
    if dm.get("model_id"):
        label = "Domain model v%s (approved)" % (dm.get("approved_version") or "?")
        if dm.get("open_version"):
            label += ", v%s open" % dm.get("open_version")
        model_items.append({"label": label, "kind": "domain_model", "href": "/dashboard/modeling/" + str(dm["model_id"]),
                            "state": "ok" if dm.get("approved_version") else "partial"})
    for d in docs:
        model_items.append({"label": "%s: %s" % (d.get("kind_label") or "Document", d.get("title")), "kind": d.get("kind") or "document",
                            "href": d.get("url"), "state": "ok" if d.get("current") else "partial"})
    existing = {
        "id": "existing", "title": "Existing tables in Silver", "role": "Domain",
        "facts": ["%d tables in %s.%s" % (len(targets), silver.get("database") or "?", silver.get("schema") or "?")],
        "items": [{"label": t.get("target_table"), "kind": "table"} for t in targets][:20],
        "state": "ok" if targets else "missing",
        "steps": [
            {"id": "model", "title": "Data model", "role": "Contract, domain model, Confluence, Erwin",
             "facts": ["%d source%s" % (len(model_items), "" if len(model_items) == 1 else "s"),
                       "%d documents linked" % len(docs) if docs else "No Erwin model or Confluence page linked"],
             "items": model_items, "state": "ok" if dm.get("approved_version") and model_items else "partial" if model_items else "missing"},
            {"id": "knowledge", "title": "Knowledge base", "role": "Data steward",
             "facts": ["%d items, %d verified by a person" % (len(rows), verified), "%d proposed, waiting for review" % proposed,
                       "%d due for review or never verified" % stale],
             "items": [], "state": _state(verified, len(rows)) if rows else "missing"},
            {"id": "modeling", "title": "Extend modeling in Silver", "role": "Data modeler",
             "facts": ["%d table models" % len(tm)],
             "items": [{"label": "%s v%s" % (t.get("table_name"), t.get("current_version") or 1), "kind": "table_model",
                        "href": "/dashboard/modeling/tables/" + str(t.get("table_model_id"))} for t in tm][:15],
             "state": _state(len(tm), len(targets)) if targets else "none"},
            {"id": "mapping", "title": "Mapping (STTM)", "role": "Data analyst",
             "facts": ["%d mappings, %d approved" % (len(st), approved_sttm)],
             "items": [{"label": "%s v%s (%s)" % (s.get("target_entity"), s.get("version"), str(s.get("status") or "").lower()), "kind": "sttm",
                        "href": "/dashboard/sttm/" + str(s.get("catalog_id")),
                        "state": "ok" if str(s.get("status") or "").upper() == "APPROVED" else "partial"} for s in st][:15],
             "state": _state(approved_sttm, len(targets)) if targets else "none"},
            {"id": "code", "title": "Code", "role": "Engineering",
             "facts": ["%d repositories, %d files name the domain's tables" % (len(manifest_.get("repos") or []), code_files)],
             "items": [{"label": "%s: %s" % (r.get("name"), f.get("path")), "kind": "file"}
                       for r in manifest_.get("repos") or [] for f in r.get("files") or []][:15],
             "state": "ok" if targets and code_files >= len(targets) else "partial" if code_files else "missing"},
        ],
    }
    keys = sorted({str(j.get("issue_key")) for j in jr if j.get("issue_key")})
    sheets = [d for d in docs if str(d.get("kind") or "") in ("excel", "spreadsheet")]
    active = sum(1 for i in ing if i.get("is_active"))
    ingest = {
        "id": "ingest", "title": "Tables to ingest from source", "role": "Onboarding",
        "facts": ["%d SnapLogic configurations land data for this domain" % len(ing)],
        "items": [{"label": "%s.%s.%s" % (i.get("landing_database"), i.get("landing_schema"), i.get("landing_table")), "kind": "landing",
                   "state": "ok" if i.get("is_active") else "partial"} for i in ing][:15],
        "state": "ok" if ing else "none",
        "steps": [
            {"id": "requests", "title": "Jira stories and spreadsheets", "role": "Requests",
             "facts": ["%d Jira issues linked" % len(keys), "%d spreadsheets attached" % len(sheets)],
             "items": [{"label": k, "kind": "jira", "href": "/tickets?tab=ticket&key=" + k} for k in keys][:15]
                      + [{"label": d.get("title"), "kind": "excel", "href": d.get("url")} for d in sheets][:5],
             "state": "ok" if keys or sheets else "none"},
            {"id": "snaplogic", "title": "Data ingestion (SnapLogic)", "role": "Engineering",
             "facts": ["%d active of %d" % (active, len(ing))], "items": [],
             "state": "ok" if ing and active == len(ing) else "partial" if ing else "none"},
        ],
    }
    return {"lanes": [existing, ingest]}


def needs_verification(rows: Iterable[Dict[str, Any]], usage: Optional[Dict[str, int]] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Active items no person has verified, most used first: what stages rely on without anyone having checked it."""
    usage = usage or {}
    out = []
    for r in (_low(r) for r in rows):
        if r.get("verified_by") or str(r.get("status") or "").upper() != "ACTIVE":
            continue
        sid, label, _ = source_of(r.get("origin"))
        out.append({"knowledge_id": r.get("knowledge_id"), "title": r.get("title"), "type": r.get("knowledge_type"),
                    "source": label, "source_id": sid, "ref": r.get("source_reference"),
                    "uses": int(usage.get(str(r.get("knowledge_id"))) or 0), "created_at": r.get("created_at")})
    return sorted(out, key=lambda i: (-i["uses"], str(i["title"] or "")))[:limit]
