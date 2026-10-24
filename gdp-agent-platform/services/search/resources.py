"""Search across the platform's resources by meaning, not only by name (pure; the API is apps/api/app/search_api.py).

Hybrid retrieval:
- semantic: Cortex Search (keyword + vector with reranking) over CORE.RESOURCE_SEARCH (workflows, domains, sources,
  runs and target tables, snowflake/cortex_search/resource_search.sql) and CORE.KNOWLEDGE_SEARCH (glossary, rules,
  patterns), so "client accounts" finds the CUSTOMER domain and "why did the yardi load break" finds the failed run;
- keyword: the same resources scored here on their names and descriptions, with the platform's synonyms ("client" is
  "customer", "ingest" is "land", "approval" is "review") and word stems, so an exact name always ranks first and the
  search still works before the search service is deployed;
- the two rankings are fused by reciprocal rank (`fuse`), each result saying why it matched.

A query also carries intent (`parse`): "waiting on me", "my approvals" keep what waits on the viewer; "failed",
"broken" keep what failed or needs attention; "domains", "sources", "runs", "tables", "workflows", "glossary" keep
that kind. The words that only state intent are not searched for.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

KINDS = ("WORKFLOW", "DOMAIN", "SOURCE", "RUN", "TABLE", "KNOWLEDGE")
KIND_LABEL = {"WORKFLOW": "Workflow", "DOMAIN": "Domain", "SOURCE": "Source", "RUN": "Run", "TABLE": "Table",
              "KNOWLEDGE": "Knowledge"}
SERVICE = "CORE.RESOURCE_SEARCH"
COLUMNS = ["RESOURCE_ID", "KIND", "TITLE", "SUBTITLE", "DOMAIN_ID", "DOMAIN_NAME", "STATUS"]
RRF_K = 60
MAX_LIMIT = 50

# words that mean the same thing on this platform (business and data engineering vocabulary)
SYNONYMS: Tuple[Tuple[str, ...], ...] = (
    ("customer", "client", "account", "buyer", "consumer", "cust"),
    ("company", "organization", "organisation", "org", "firm", "vendor", "supplier", "party", "employer"),
    ("property", "building", "asset", "site", "parcel", "real estate", "premises"),
    ("lease", "tenancy", "rental", "rent"),
    ("tenant", "occupier", "lessee"),
    ("opportunity", "deal", "pipeline", "oppty", "pursuit", "lead"),
    ("employee", "worker", "staff", "headcount", "workforce", "people", "hr"),
    ("finance", "gl", "ledger", "accounting", "ar", "ap"),
    ("source", "system", "feed", "connection"),
    ("onboard", "ingest", "land", "load", "import", "register", "connect"),
    ("domain", "subject area", "business area"),
    ("table", "entity", "model", "dataset"),
    ("column", "field", "attribute"),
    ("mapping", "map", "sttm", "transformation", "lineage"),
    ("approve", "approval", "review", "sign off", "signoff", "gate", "decide"),
    ("fail", "failed", "failure", "broken", "error", "break", "down", "crash"),
    ("wait", "waiting", "pending", "blocked", "stuck", "queue"),
    ("quality", "dq", "check", "checks", "soda", "test", "tests"),
    ("code", "dbt", "pr", "pull request"),
    ("glossary", "definition", "meaning", "term", "rule", "policy"),
    ("enhance", "extend", "add", "widen", "delta", "change"),
    ("new", "create", "start"),
)

# intent words: what they filter on; they are not searched for
# only phrases that cannot be part of a topic ("how to do X" is not intent, nor "error handling rule")
_MINE = re.compile(r"\b(waiting on me|waiting for me|assigned to me|my (?:actions?|approvals?|reviews?|work|turn|queue))\b")
_FAILED = re.compile(r"\b(failed|failing|failures?|broken|needs attention)\b")
_KIND_WORDS = {
    "WORKFLOW": r"workflows?|requests?",
    "DOMAIN": r"domains?",
    "SOURCE": r"sources?|systems?",
    "RUN": r"runs?|pipelines?|jobs?",
    "TABLE": r"tables?|entities|datasets?",
    "KNOWLEDGE": r"glossary|definitions?|knowledge|rules?|what is|what does .* mean|meaning of",
}
_STOP = {"the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "with", "from", "by", "is", "are", "all", "show",
         "me", "find", "list", "which", "what", "where", "that", "this", "any", "my", "i", "it", "its", "be", "do", "does",
         "did", "why", "how", "get", "need", "please", "mean", "means", "meaning", "about"}


def stem(word: str) -> str:
    """A light English stem: enough for "loads"/"loading"/"loaded" and "policies"/"policy" to meet."""
    w = word.lower()
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suffix) + 2 and w.endswith(suffix) and not w.endswith("ss"):
            return w[: -len(suffix)] + keep
    return w


def tokens(text: Any) -> List[str]:
    """Lower-case words of a text, names split on _ . - and camel case ("PROPERTY_CORE" is "property core")."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text or ""))
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


_GROUPS: Dict[str, int] = {}
for _i, _group in enumerate(SYNONYMS):
    for _w in _group:
        for _t in tokens(_w):
            _GROUPS.setdefault(stem(_t), _i)


def related(term: str) -> List[str]:
    """The stems a term stands for: itself and its synonyms."""
    s = stem(term)
    g = _GROUPS.get(s)
    if g is None:
        return [s]
    out = [s] + [stem(t) for w in SYNONYMS[g] for t in tokens(w)]
    return list(dict.fromkeys(out))


def parse(query: str) -> Dict[str, Any]:
    """{text, terms, kinds, mine, failed}: the words to search for, and the filters the query asks for."""
    q = " ".join(str(query or "").lower().split())[:500]
    mine = bool(_MINE.search(q))
    failed = bool(_FAILED.search(q))
    kinds = [k for k, pat in _KIND_WORDS.items() if re.search(rf"\b(?:{pat})\b", q)]
    text = _MINE.sub(" ", q)
    # a kind word filters only when something else is searched for ("property tables"); alone it is the search
    for k, pat in _KIND_WORDS.items():
        if k in kinds and k != "KNOWLEDGE":
            text = re.sub(rf"\b(?:{pat})\b", " ", text)
    terms = [t for t in tokens(text) if t not in _STOP]
    if failed:
        terms = [t for t in terms if stem(t) not in {stem(w) for w in ("failed", "failing", "failure", "failures", "broken")}]
    return {"text": " ".join(terms) or q, "terms": terms, "kinds": kinds, "mine": mine, "failed": failed}


def lexical_score(terms: List[str], doc: Dict[str, Any]) -> Tuple[float, List[str]]:
    """How well a resource's own words answer the terms: a whole title 6, a title word 3, a synonym in the title 2, a
    word of the description 1, a synonym there 0.5, a name word starting with the term 1 (a description word 0.5). With why it matched."""
    if not terms:
        return 0.0, []
    title = [stem(t) for t in tokens(doc.get("title"))]
    body = [stem(t) for t in tokens(" ".join(str(doc.get(k) or "") for k in ("subtitle", "text", "domain_name")))]
    tset, bset = set(title), set(body)
    score, why = 0.0, []
    for term in terms:
        s = stem(term)
        if s in tset:
            score += 3
            why.append(f"name: {term}")
            continue
        syn = [r for r in related(term)[1:] if r in tset]
        if syn:
            score += 2
            why.append(f"{term} ~ {syn[0]}")
            continue
        if s in bset:
            score += 1
            why.append(f"text: {term}")
            continue
        syn = [r for r in related(term)[1:] if r in bset]
        if syn:
            score += 0.5
            why.append(f"{term} ~ {syn[0]}")
            continue
        if len(s) >= 3 and any(t.startswith(s) for t in tset):
            score += 1
            why.append(f"name starts: {term}")
        elif len(s) >= 3 and any(t.startswith(s) for t in bset):
            score += 0.5
            why.append(f"text starts: {term}")
    phrase = " ".join(stem(t) for t in terms)
    if len(terms) > 1 and phrase and phrase in " ".join(title):
        score += 6
    if title and [stem(t) for t in terms] == title:
        score += 6
    # every term should count for something: a partial match ranks below a full one
    matched = len(why)
    return (score * matched / len(terms), why) if matched else (0.0, [])


def keyword_rank(terms: List[str], docs: Iterable[Dict[str, Any]], limit: int = MAX_LIMIT) -> List[Tuple[str, float, List[str]]]:
    scored = []
    for d in docs:
        s, why = lexical_score(terms, d)
        if s > 0:
            scored.append((d["key"], s, why))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]


def fuse(*rankings: List[str], k: int = RRF_K, weights: Optional[List[float]] = None) -> Dict[str, float]:
    """Reciprocal rank fusion: a result's score is the sum over the rankings it is in of weight / (k + rank)."""
    out: Dict[str, float] = {}
    for i, ranking in enumerate(rankings):
        w = (weights or [1.0] * len(rankings))[i]
        for rank, key in enumerate(ranking, start=1):
            out[key] = out.get(key, 0.0) + w / (k + rank)
    return out


def key(kind: str, resource_id: str) -> str:
    return f"{kind}:{resource_id}"


def cortex_request(text: str, kinds: Optional[List[str]] = None, limit: int = 20) -> Dict[str, Any]:
    """A SEARCH_PREVIEW request over CORE.RESOURCE_SEARCH, filtered to the kinds asked for (knowledge excluded: it has
    its own service)."""
    wanted = [k for k in (kinds or KINDS) if k != "KNOWLEDGE"]
    req: Dict[str, Any] = {"query": text.strip()[:2000], "columns": COLUMNS, "limit": max(1, min(int(limit), MAX_LIMIT))}
    if wanted and len(wanted) < len(KINDS) - 1:
        req["filter"] = {"@or": [{"@eq": {"KIND": k}} for k in wanted]} if len(wanted) > 1 else {"@eq": {"KIND": wanted[0]}}
    return req


def href(kind: str, resource_id: str, domain_id: Optional[str] = None, state: Optional[str] = None) -> str:
    """Where a result opens in the Data OS."""
    if kind == "WORKFLOW":
        return f"/os/intake?view=selected&request={resource_id}"
    if kind == "DOMAIN":
        return f"/os/knowledge/domains/{resource_id}"
    if kind == "SOURCE":
        return "/os/design/sources"
    if kind == "RUN":
        # where the run is worked, as the boards' stage rule puts it: being onboarded on Enable, designed, built
        if state and re.match(r"(CREATED|SOURCE_REGISTERED|ACCESS_|LANDING_(PENDING|RUNNING))", state):
            return "/os/enable"
        design = state and re.search(r"LANDING_COMPLETE|PROFIL|DOMAIN_IDENTIFIED|MAPPING|STTM_(PENDING|REVIEW)", state)
        return f"/os/{'design' if design else 'build'}?view=selected&run={resource_id}"
    if kind == "TABLE":
        return f"/os/knowledge/domains/{domain_id}" if domain_id else "/os/knowledge"
    return f"/os/knowledge?id={resource_id}"


def merge(semantic: List[Dict[str, Any]], keyword: List[Tuple[str, float, List[str]]], docs: Dict[str, Dict[str, Any]],
          limit: int = 20) -> List[Dict[str, Any]]:
    """The fused results: semantic hits (already ranked by Cortex) and keyword hits, by reciprocal rank; a result in
    both ranks highest. `docs` holds what is known of each resource by key (semantic hits fill in what is missing)."""
    sem_keys = [s["key"] for s in semantic]
    kw_keys = [k for k, _s, _w in keyword]
    # an exact keyword hit (a whole name) should beat a merely similar meaning, so keyword counts a little more
    scores = fuse(sem_keys, kw_keys, weights=[1.0, 1.2])
    why = {k: w for k, _s, w in keyword}
    sem = {s["key"]: s for s in semantic}
    out = []
    for k, score in sorted(scores.items(), key=lambda x: (-x[1], x[0])):
        d = {**sem.get(k, {}), **docs.get(k, {})}
        if not d.get("title"):
            continue
        reasons = list(why.get(k, []))
        if k in sem:
            reasons.append("similar meaning")
        out.append({"kind": d["kind"], "id": d["id"], "title": d["title"], "subtitle": d.get("subtitle"),
                    "domain_id": d.get("domain_id"), "domain_name": d.get("domain_name"), "status": d.get("status"),
                    "href": d.get("href") or href(d["kind"], d["id"], d.get("domain_id"), d.get("status")),
                    "score": round(score, 5), "why": reasons[:3], "match": "both" if k in sem and k in why
                    else "semantic" if k in sem else "keyword"})
        if len(out) >= limit:
            break
    return out
