"""How complete and trustworthy a domain is (pure). For each target table: a model definition, business keys, a
glossary for the key columns, an STTM mapping, QA tests and data quality checks. For the knowledge: how much a person
verified, how much the stages actually use, how much waits for review. Each gap comes with the Copilot request that
would close it. Nothing here talks to Snowflake.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

CHECKS = [("model", "Model definition"), ("keys", "Business keys"), ("glossary", "Glossary for key columns"),
          ("sttm", "STTM mapping"), ("qa", "QA tests"), ("dq", "Data quality checks")]


def _low(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def _fqn(t: Dict[str, Any]) -> str:
    return ".".join(str(t.get(k) or "") for k in ("target_database", "target_schema", "target_table")).upper()


def score(domain_name: str, targets: Iterable[Dict[str, Any]], rows: Iterable[Dict[str, Any]], sttm: Set[str],
          dq: Set[str], qa_sql: Iterable[str], usage: Dict[str, int] | None = None) -> Dict[str, Any]:
    """targets: the domain's target tables (target_database/schema/table, hkey_columns); rows: current knowledge items;
    sttm and dq: target FQNs (upper) that have a mapping and data quality checks; qa_sql: the SQL of the domain's QA
    queries; usage: knowledge_id -> uses in the last 90 days."""
    usage = usage or {}
    rows = [_low(r) for r in rows]
    d = domain_name.lower()
    refs = {str(r.get("source_reference") or "").lower() for r in rows}
    titles = [(str(r.get("knowledge_type") or "").upper(), str(r.get("title") or "").upper()) for r in rows]
    qa_text = " ".join(str(s or "").upper() for s in qa_sql)
    out_targets, actions = [], []
    for t in targets:
        t = _low(t)
        table = str(t.get("target_table") or "").upper()
        keys = [str(k).upper() for k in t.get("hkey_columns") or [] if k]
        glossed = [k for k in keys if f"{d}.glossary.{k.lower()}" in refs
                   or any(kind == "GLOSSARY" and k in title for kind, title in titles)]
        checks = {
            "model": f"{d}.model.{table.lower()}" in refs or any(kind == "MODEL_DEFINITION" and title.startswith(table) for kind, title in titles),
            "keys": bool(keys),
            "glossary": bool(keys) and len(glossed) == len(keys),
            "sttm": _fqn(t) in sttm,
            "qa": bool(table) and table in qa_text,
            "dq": _fqn(t) in dq,
        }
        missing = [k for k, ok in checks.items() if not ok]
        out_targets.append({"table": table, "fqn": _fqn(t), "checks": checks, "score": round(100 * sum(checks.values()) / len(checks)),
                            "keys": keys, "glossary_missing": [k for k in keys if k not in glossed]})
        for k in missing:
            actions.append({"table": table, "check": k, "label": dict(CHECKS)[k], "prompt": _prompt(k, domain_name, table, keys, glossed)})
    total = len(rows)
    verified = sum(1 for r in rows if r.get("verified_by"))
    used = sum(1 for r in rows if usage.get(str(r.get("knowledge_id"))))
    draft = sum(1 for r in rows if str(r.get("status") or "").upper() == "DRAFT")
    tables = round(sum(t["score"] for t in out_targets) / len(out_targets)) if out_targets else 0
    trust = round(100 * (0.6 * verified + 0.4 * used) / total) if total else 0
    return {"score": round(0.7 * tables + 0.3 * trust), "tables_score": tables, "trust_score": trust,
            "knowledge": {"items": total, "verified": verified, "used_90d": used, "unused_90d": total - used, "awaiting_review": draft},
            "checks": [{"id": k, "label": label, "done": sum(1 for t in out_targets if t["checks"][k]), "of": len(out_targets)} for k, label in CHECKS],
            "targets": out_targets, "actions": actions[:40]}


def _prompt(check: str, domain: str, table: str, keys: List[str], glossed: List[str]) -> str:
    """The Copilot request that would close a gap (it drafts; a person reviews and publishes)."""
    if check == "model":
        return f"In the {domain} domain model, review {table}: draft its definition (purpose, grain, business keys) from the code, the mappings and the knowledge."
    if check == "keys":
        return f"Find the business keys of {table} in the {domain} domain from the code, the profiles and the mappings, and draft them in the domain model."
    if check == "glossary":
        todo = ", ".join(k for k in keys if k not in glossed)
        return f"Draft {domain} glossary definitions for the key columns of {table}: {todo}. Use the code, the mappings and the profiles."
    if check == "sttm":
        return f"Find or draft the source-to-target mapping for {table} in the {domain} domain."
    if check == "qa":
        return f"Draft QA queries for {table} in the {domain} domain: key uniqueness, referential integrity and required columns."
    return f"Draft data quality checks for {table} in the {domain} domain: missing keys, duplicates and freshness."
