"""The skill scout (pure): which approved knowledge keeps being used, grouped into themes worth a reusable skill, made
safe to carry to another client, and scored for how well it would transfer.

  approved     reviewed or verified by a person, written by a person or imported from a pack, or observed to help
               (HELPFULNESS >= min_helpfulness once enough decisions exist)
  clusters     by knowledge type and theme (a shared tag, else similar titles); kept with at least min_items items
               and either min_domains domains or min_runs runs that used them; ranked by runs x helpfulness x spread
  generalise   client names (domains, target databases, schemas and tables, DAGs, repositories, environments, source
               systems) become placeholders such as <DATABASE>.<SCHEMA>.<TABLE>, then secrets and personal data are
               masked; a name that survives is a hard failure, so nothing client specific is ever proposed
  transfer     0..100 from how many domains it came from, how much it beat the baseline, and how specific it still is

The scan itself (reading, drafting, testing, filing) is services/knowledge/scout_run.py.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

TYPES = ("MAPPING_PATTERN", "TRANSFORMATION_RULE", "SODA_PATTERN", "DBT_PATTERN", "MODEL_DEFINITION", "NAMING_STANDARD",
         "COLUMN_RULE")
STAGE_OF_TYPE = {"MAPPING_PATTERN": "MAPPING", "TRANSFORMATION_RULE": "STTM", "SODA_PATTERN": "SODA", "DBT_PATTERN": "DBT",
                 "MODEL_DEFINITION": "MODELING", "NAMING_STANDARD": "MODELING", "COLUMN_RULE": "PROFILING"}
CATEGORY_OF_STAGE = {"MAPPING": "mapping", "STTM": "sttm", "SODA": "data-quality", "DBT": "dbt", "MODELING": "data-modeling",
                     "PROFILING": "profiling"}
APPROVING_ORIGINS = ("USER", "PACK_IMPORT")
KEY = "SKILL_SCOUT"
DEFAULTS: Dict[str, Any] = {"enabled": True, "cadence_days": 7, "max_per_run": 3, "min_items": 5, "min_domains": 2,
                            "min_runs": 5, "min_helpfulness": 0.6, "min_observed": 3, "title_similarity": 0.35}
LIMITS = {"cadence_days": (1, 90), "max_per_run": (1, 10), "min_items": (2, 100), "min_domains": (1, 20),
          "min_runs": (0, 10000), "min_helpfulness": (0.0, 1.0), "min_observed": (0, 1000), "title_similarity": (0.1, 0.9)}
PLACEHOLDER = {"DATABASE": "<DATABASE>", "SCHEMA": "<SCHEMA>", "TABLE": "<TABLE>", "DOMAIN": "<DOMAIN>", "DAG": "<DAG>",
               "REPO": "<REPOSITORY>", "ENV": "<ENVIRONMENT>", "SOURCE": "<SOURCE_SYSTEM>"}
# words any data team uses: a name equal to one of these is replaced only where it is written exactly as the name
# (upper case, as an identifier), so prose such as "customer orders" keeps reading naturally
GENERIC = {"CUSTOMER", "CUSTOMERS", "ORDER", "ORDERS", "PRODUCT", "PRODUCTS", "SALES", "ACCOUNT", "ACCOUNTS", "DATE",
           "DATES", "EVENTS", "EVENT", "USERS", "USER", "ITEMS", "ITEM", "PAYMENTS", "PAYMENT", "INVOICE", "INVOICES",
           "RAW", "STAGING", "STG", "CORE", "MART", "MARTS", "GOLD", "SILVER", "BRONZE", "PUBLIC", "ANALYTICS", "DEV",
           "PROD", "TEST", "QA", "MAIN", "FINANCE", "HR", "MARKETING", "GENERAL", "DIM", "FACT", "LANDING"}
GENERIC_TAGS = {"OPS", "GIT", "PULL_REQUEST", "CLOSED", "AI", "AGENT", "USER", "REVIEW", "LEARNED", "AUTO", "PACK"}
QUALIFIED = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*\b")
_TOKEN = re.compile(r"[a-z0-9]+")


def settings(stored: Any) -> Dict[str, Any]:
    """The scout's settings with defaults; values outside their limits are clamped."""
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key == "enabled":
                out[key] = value is not False
            elif key in LIMITS:
                try:
                    low, high = LIMITS[key]
                    number = float(value)
                    out[key] = min(high, max(low, number)) if isinstance(DEFAULTS[key], float) else int(min(high, max(low, number)))
                except (TypeError, ValueError):
                    pass
    return out


def validate(value: Any) -> Tuple[Dict[str, Any], List[str]]:
    if not isinstance(value, dict):
        return dict(DEFAULTS), ["the skill scout settings must be an object"]
    problems = [f"unknown setting {k}" for k in value if k not in DEFAULTS]
    for key, (low, high) in LIMITS.items():
        if key in value:
            try:
                if not low <= float(value[key]) <= high:
                    problems.append(f"{key} must be between {low} and {high}")
            except (TypeError, ValueError):
                problems.append(f"{key} must be a number")
    return settings(value), problems


def due(cursor: Optional[str], now: datetime, cadence_days: int) -> bool:
    """Whether a scan is due: never ran, or the last one is cadence_days old."""
    if not cursor:
        return True
    try:
        last = datetime.fromisoformat(str(cursor))
    except ValueError:
        return True
    if last.tzinfo is None and now.tzinfo is not None:
        last = last.replace(tzinfo=now.tzinfo)
    return now - last >= timedelta(days=int(cadence_days))


# ---------------------------------------------------------------- what counts and how it groups

def approved(item: Dict[str, Any], conf: Dict[str, Any]) -> bool:
    if item.get("verified_by") or item.get("reviewed_by"):
        return True
    if str(item.get("origin") or "").upper() in APPROVING_ORIGINS:
        return True
    observed = float(item.get("observed") or 0)
    return item.get("helpfulness") is not None and observed >= conf["min_observed"] \
        and float(item["helpfulness"]) >= conf["min_helpfulness"]


def _tokens(text: Any) -> set:
    return set(_TOKEN.findall(str(text or "").lower()))


def similarity(a: Any, b: Any) -> float:
    x, y = _tokens(a), _tokens(b)
    return round(len(x & y) / len(x | y), 4) if x and y else 0.0


def _theme(item: Dict[str, Any], domain_names: set) -> Optional[str]:
    tags = item.get("tags") or []
    for tag in tags if isinstance(tags, list) else []:
        t = re.sub(r"[^A-Z0-9_]+", "_", str(tag).upper()).strip("_")
        if t and t not in GENERIC_TAGS and t not in domain_names and len(t) >= 3:
            return t
    return None


def clusters(items: Iterable[Dict[str, Any]], conf: Dict[str, Any]) -> List[Dict[str, Any]]:
    """[{key, type, stage, theme, items, domains, runs, helpfulness, rank}] of approved knowledge, best first."""
    approved_items = [i for i in items if str(i.get("knowledge_type") or "") in TYPES and approved(i, conf)]
    domain_names = {str(i.get("domain_name") or "").upper() for i in approved_items}
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    loose: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in approved_items:
        theme = _theme(item, domain_names)
        if theme:
            groups[f"{item['knowledge_type']}:{theme}"].append(item)
        else:
            loose[item["knowledge_type"]].append(item)
    for ktype, rest in loose.items():   # similar titles, greedily around the first unplaced item
        seeds: List[Tuple[str, List[Dict[str, Any]]]] = []
        for item in rest:
            for seed_title, members in seeds:
                if similarity(seed_title, item.get("title")) >= conf["title_similarity"]:
                    members.append(item)
                    break
            else:
                seeds.append((str(item.get("title") or ""), [item]))
        for seed_title, members in seeds:
            words = "_".join(sorted(_tokens(seed_title))[:4]).upper() or "UNTITLED"
            groups[f"{ktype}:~{words}"].extend(members)
    out = []
    for key, members in groups.items():
        ktype, _, theme = key.partition(":")
        domains = {m.get("domain_id") for m in members if m.get("domain_id")}
        runs = sum(int(m.get("runs") or 0) for m in members)
        helps = [float(m["helpfulness"]) for m in members if m.get("helpfulness") is not None]
        helpfulness = round(sum(helps) / len(helps), 3) if helps else 0.5
        if len(members) < conf["min_items"] or (len(domains) < conf["min_domains"] and runs < conf["min_runs"]):
            continue
        spread = 1 + 0.5 * (len(domains) - 1)
        out.append({"key": key[:256], "type": ktype, "stage": STAGE_OF_TYPE[ktype], "theme": theme.lstrip("~"),
                    "items": members, "domains": len(domains), "runs": runs, "helpfulness": helpfulness,
                    "rank": round(max(runs, 1) * helpfulness * spread, 3)})
    return sorted(out, key=lambda c: (-c["rank"], c["key"]))


def theme_words(cluster: Dict[str, Any]) -> str:
    """The words at least half of the cluster's titles share (what the theme is about), plus its tag."""
    titles = [_tokens(i.get("title")) for i in cluster["items"]]
    counts: Dict[str, int] = defaultdict(int)
    for words in titles:
        for w in words:
            counts[w] += 1
    common = sorted(w for w, n in counts.items() if n * 2 >= len(titles))
    return " ".join(common + [str(cluster.get("theme") or "").lower()]).strip()


def skip_reason(cluster: Dict[str, Any], previous: Optional[Dict[str, Any]], cited: set,
                stage_skills: List[Dict[str, Any]], similar_at: float = 0.5) -> Optional[str]:
    """Why a cluster is not drafted now, or None. previous: the newest proposal for the cluster key."""
    n = len(cluster["items"])
    if previous:
        status = str(previous.get("status") or "").upper()
        before = int(previous.get("evidence_count") or 0)
        if status == "PROPOSED":
            return "already proposed"
        if status in ("DISMISSED", "DISCARDED") and n < 2 * max(before, 1):
            return f"{status.lower()} before; proposed again once its evidence doubles ({n} of {2 * max(before, 1)})"
    refs = {str(i.get("source_reference") or i.get("knowledge_id")) for i in cluster["items"]}
    if refs and len(refs & cited) * 2 >= len(refs):
        return "a skill already cites most of this knowledge"
    title = theme_words(cluster)
    for skill in stage_skills:
        if similarity(title, f"{skill.get('skill_name')} {skill.get('description')}") >= similar_at:
            return f"similar to {skill.get('skill_name')}, already used at {cluster['stage']}"
    return None


# ---------------------------------------------------------------- making it safe to carry to another client

def lexicon(rows: Dict[str, Iterable[Any]]) -> List[Tuple[str, str]]:
    """[(kind, name)] longest first, from {kind: names}; names under three characters are ignored."""
    seen, out = set(), []
    for kind, names in rows.items():
        for name in names or []:
            value = str(name or "").strip()
            if len(value) >= 3 and (kind, value.upper()) not in seen:
                seen.add((kind, value.upper()))
                out.append((kind, value))
    return sorted(out, key=lambda kv: (-len(kv[1]), kv[0]))


def generalise(text: str, names: List[Tuple[str, str]]) -> Tuple[str, Dict[str, int], List[str]]:
    """(generalised text, {kind: replacements}, residual identifiers). Residuals: a client name still present, or a
    three-part name that is not made of placeholders."""
    from services.ops.redact import redact

    out = str(text or "")
    counts: Dict[str, int] = defaultdict(int)
    for kind, name in names:
        generic = name.upper() in GENERIC
        pattern = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(name.upper() if generic else name)}(?![A-Za-z0-9_$])",
                             0 if generic else re.IGNORECASE)
        out, n = pattern.subn(PLACEHOLDER[kind], out)
        counts[kind] += n
    out = redact(out)
    residual = []
    for kind, name in names:
        if name.upper() in GENERIC:
            continue
        if re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", out, re.IGNORECASE):
            residual.append(f"{kind.lower()} name")
    for found in QUALIFIED.findall(out):
        if not all(p.startswith("<") or p.upper() in GENERIC for p in found.split(".")):
            residual.append(f"qualified name {found[:60]}")
    return out, {k: v for k, v in counts.items() if v}, sorted(set(residual))[:10]


def transfer(domains: int, eval_summary: Optional[Dict[str, Any]], replacements: int, characters: int) -> Dict[str, Any]:
    """{score, label, parts}: 40 for spread (four domains or more), 40 for beating the baseline, 20 for how little had
    to be replaced (dense replacements mean the playbook leans on one client's names)."""
    spread = 40.0 * min(max(domains, 1), 4) / 4
    if eval_summary:
        gain = float(eval_summary.get("candidate_rate") or 0) - float(eval_summary.get("baseline_rate") or 0)
        quality = 40.0 * max(0.0, min(1.0, 0.5 + gain))
    else:
        quality = 20.0
    density = 1000.0 * replacements / max(characters, 1)
    specificity = 20.0 * max(0.0, 1 - density / 10)
    score = round(spread + quality + specificity, 1)
    label = "transfers well" if score >= 70 else "transfers with care" if score >= 45 else "client specific"
    return {"score": score, "label": label,
            "parts": {"spread": round(spread, 1), "quality": round(quality, 1), "specificity": round(specificity, 1)}}
