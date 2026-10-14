"""The MDM layer as data (pure): which domains Reltio masters, how a pre-MDM silver record reaches its mastered id
through the cross-reference, and which gold dimension carries it; and which domains consume them.

A domain's `domain/<name>/mdm.json` holds its contract (from the gdp-post-mdm-data-model skill):
  role MASTERED   pre_mdm core, source_systems, cross_reference (the bridge), golden record, gold dimension, dbt
                  source names, watermark, and EXCEPTION knowledge (the known defects to avoid)
  role CONSUMER   consumes: [{domain, silver_column (pre-MDM skey the silver hub carries), gold_column}]

Rendering follows the skill's canonical SQL: match on the source key AND the upper source-system name, use the full
cross-reference (never the STAGE delta), active and latest rows only, the active dimension row with the lowest skey
per mastered id, and -1 when nothing resolves.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

ROLES = ("MASTERED", "CONSUMER")
IDENT = re.compile(r"^[A-Z][A-Z0-9_$]*$")
REQUIRED = {
    "pre_mdm": ("database", "schema", "table", "skey", "source_key", "source_system_skey", "active_column"),
    "source_systems": ("database", "schema", "table", "skey", "name"),
    "cross_reference": ("database", "schema", "table", "source_key", "system_name", "mastered_id", "active_column",
                        "latest_order"),
    "golden": ("database", "schema", "table", "mastered_id"),
    "gold": ("database", "schema", "dimension", "skey", "mastered_id", "active_column"),
}
NAME_KEYS = ("schema", "table", "skey", "source_key", "source_system_skey", "active_column", "name", "system_name",
             "mastered_id", "entity_id", "deleted_column", "status_column", "dimension")


def validate(name: str, contract: Optional[Dict[str, Any]], known_domains: Iterable[str] = ()) -> List[str]:
    """Problems with a domain's MDM contract (empty when it is usable)."""
    if not contract:
        return []
    problems: List[str] = []
    role = str(contract.get("role") or "").upper()
    if role not in ROLES:
        return [f"{name}: mdm role must be one of {', '.join(ROLES)}"]
    if role == "MASTERED":
        for part, keys in REQUIRED.items():
            section = contract.get(part)
            if not isinstance(section, dict):
                problems.append(f"{name}: mdm.{part} is missing")
                continue
            for k in keys:
                if section.get(k) in (None, "", []):
                    problems.append(f"{name}: mdm.{part}.{k} is missing")
            for k in NAME_KEYS:
                v = section.get(k)
                if isinstance(v, str) and v and not IDENT.match(v):
                    problems.append(f"{name}: mdm.{part}.{k} {v!r} is not an identifier")
        for k in contract.get("knowledge") or []:
            if not (isinstance(k, dict) and k.get("key") and k.get("type") and k.get("title") and k.get("content")):
                problems.append(f"{name}: an mdm knowledge item needs key, type, title and content")
    else:
        known = {str(d).upper() for d in known_domains}
        consumes = contract.get("consumes")
        if not isinstance(consumes, list) or not consumes:
            problems.append(f"{name}: a consumer lists the mastered domains it consumes")
        for c in consumes or []:
            d = str((c or {}).get("domain") or "").upper()
            if not d:
                problems.append(f"{name}: a consumed domain needs a name")
            elif known and d not in known:
                problems.append(f"{name}: consumes {d}, which is not a mastered domain")
            for k in ("silver_column", "gold_column"):
                if not IDENT.match(str((c or {}).get(k) or "")):
                    problems.append(f"{name}: consumes {d}: {k} is missing or not an identifier")
    return problems


def _fq(section: Dict[str, Any], env: str, table: Optional[str] = None) -> str:
    db = str(section["database"]).replace("{ENV}", env.upper())
    return f"{db}.{section['schema']}.{table or section['table']}"


def _xref_filter(x: Dict[str, Any]) -> str:
    parts = [str(x["active_column"]).lower()]
    if x.get("deleted_column") and str(x["deleted_column"]).upper() == "MDM_DELETE_DATE":
        parts.append(f"{x['deleted_column'].lower()} is null")
    excluded = [s for s in x.get("exclude_systems") or [] if s]
    if excluded:
        parts.append(f"{x['system_name'].lower()} not in ({', '.join(repr(s) for s in excluded)})")
    return " and ".join(parts)


def resolution_sql(contract: Dict[str, Any], env: str = "DEV") -> str:
    """Every active pre-MDM record with its mastered id and gold skey (-1 when unresolved), as plain SQL: the skill's
    canonical pattern A (company) or P (property), generated from the contract."""
    pre, rss, x, gold = contract["pre_mdm"], contract["source_systems"], contract["cross_reference"], contract["gold"]
    ssn = "ssn"
    latest = ", ".join(o.lower() for o in x["latest_order"])
    return f"""with dim as (
  select {gold['skey'].lower()}, {gold['mastered_id'].lower()}
  from {_fq(gold, env, gold['dimension'])}
  where {gold['active_column'].lower()}
  qualify row_number() over (partition by {gold['mastered_id'].lower()} order by {gold['skey'].lower()}) = 1
), xref as (   -- full cross-reference, never the STAGE delta
  select {x['source_key'].lower()}, upper({x['system_name'].lower()}) {ssn}, {x['mastered_id'].lower()}
  from {_fq(x, env)}
  where {_xref_filter(x)}
  qualify row_number() over (partition by {x['source_key'].lower()}, {x['system_name'].lower()}
          order by {latest}) = 1
)
select p.{pre['skey'].lower()}, r.{rss['name'].lower()}, p.{pre['source_key'].lower()},
       x.{x['mastered_id'].lower()}, nvl(d.{gold['skey'].lower()}, {int(gold.get('unresolved', -1))}) {gold['skey'].lower()}
from {_fq(pre, env)} p
join {_fq(rss, env)} r
  on p.{pre['source_system_skey'].lower()} = r.{rss['skey'].lower()}
left join xref x on p.{pre['source_key'].lower()} = x.{x['source_key'].lower()}
                and upper(r.{rss['name'].lower()}) = x.{ssn}
left join dim d on x.{x['mastered_id'].lower()} = d.{gold['mastered_id'].lower()}
where p.{pre['active_column'].lower()}
qualify row_number() over (partition by p.{pre['skey'].lower()} order by d.{gold['skey'].lower()} nulls last) = 1"""


def coverage_sql(contract: Dict[str, Any], env: str = "DEV") -> str:
    """Active pre-MDM records per source system and how many have an active cross-reference row (skill §3a)."""
    pre, rss, x = contract["pre_mdm"], contract["source_systems"], contract["cross_reference"]
    return f"""with x as (select {x['source_key'].lower()}, upper({x['system_name'].lower()}) ssn, {x['mastered_id'].lower()}
           from {_fq(x, env)} where {_xref_filter(x)})
select upper(r.{rss['name'].lower()}) source_system, count(*) records, count(x.{x['mastered_id'].lower()}) matched,
       round(100 * matched / nullif(records, 0), 2) matched_pct
from {_fq(pre, env)} p
join {_fq(rss, env)} r on p.{pre['source_system_skey'].lower()} = r.{rss['skey'].lower()}
left join x on p.{pre['source_key'].lower()} = x.{x['source_key'].lower()} and upper(r.{rss['name'].lower()}) = x.ssn
where p.{pre['active_column'].lower()} group by 1 order by 2 desc"""


def dbt_resolution(contract: Dict[str, Any], *, input_alias: str, input_skey: str, out_column: Optional[str] = None) -> Dict[str, str]:
    """dbt CTEs and the join that resolve a consumer's pre-MDM skey (`input_alias.input_skey`) to the gold skey of
    this mastered domain: {ctes, join, column}. Uses ref() for the pre-MDM core and the dimension (DAG edges) and
    source() for the cross-reference and source systems."""
    pre, rss, x, gold = contract["pre_mdm"], contract["source_systems"], contract["cross_reference"], contract["gold"]
    src = contract.get("dbt_sources") or {}
    xs, ss = src.get("cross_reference") or ["mdm_source", x["table"].lower()], src.get("source_systems") or ["silver_shared", rss["table"].lower()]
    core = src.get("pre_mdm") or pre["table"].lower()
    name = (gold["dimension"]).lower()
    out = (out_column or gold["skey"]).lower()
    latest = ", ".join(o.lower() for o in x["latest_order"])
    ctes = f"""{name}_dim as (
    select {gold['skey'].lower()}, {gold['mastered_id'].lower()}
    from {{{{ ref('{gold.get('dbt_ref') or name}') }}}}
    where {gold['active_column'].lower()}
    qualify row_number() over (partition by {gold['mastered_id'].lower()} order by {gold['skey'].lower()}) = 1
),
{name}_xref as (   -- full cross-reference, never the STAGE delta
    select {x['source_key'].lower()}, upper({x['system_name'].lower()}) ssn, {x['mastered_id'].lower()}
    from {{{{ source('{xs[0]}', '{xs[1]}') }}}}
    where {_xref_filter(x)}
    qualify row_number() over (partition by {x['source_key'].lower()}, {x['system_name'].lower()} order by {latest}) = 1
),
{name}_resolution as (
    select c.{pre['skey'].lower()}, nvl(d.{gold['skey'].lower()}, {int(gold.get('unresolved', -1))}) as {out}
    from {{{{ ref('{core}') }}}} c
    join {{{{ source('{ss[0]}', '{ss[1]}') }}}} r on c.{pre['source_system_skey'].lower()} = r.{rss['skey'].lower()}
    left join {name}_xref x on c.{pre['source_key'].lower()} = x.{x['source_key'].lower()} and upper(r.{rss['name'].lower()}) = x.ssn
    left join {name}_dim d on x.{x['mastered_id'].lower()} = d.{gold['mastered_id'].lower()}
    where c.{pre['active_column'].lower()}
    qualify row_number() over (partition by c.{pre['skey'].lower()} order by d.{gold['skey'].lower()} nulls last) = 1
)"""
    join = f"left join {name}_resolution {name}_r on {input_alias}.{input_skey.lower()} = {name}_r.{pre['skey'].lower()}"
    return {"ctes": ctes, "join": join, "column": f"nvl({name}_r.{out}, {int(gold.get('unresolved', -1))}) as {out}"}


def chain(name: str, contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The entity-level path of a mastered domain, for lineage: pre-MDM core, cross-reference, golden record, gold
    dimension, each hop with its join keys."""
    pre, x, gold, golden = contract["pre_mdm"], contract["cross_reference"], contract["gold"], contract["golden"]
    return [
        {"layer": "SILVER_PRE_MDM", "object": f"{pre['schema']}.{pre['table']}", "key": [pre["source_key"], pre["source_system_skey"]]},
        {"layer": "MDM", "object": f"{x['schema']}.{x['table']}", "key": [x["source_key"], x["system_name"]],
         "kind": "MDM_MATCH", "cardinality": x.get("cardinality", "N:1")},
        {"layer": "SILVER_POST_MDM", "object": f"{golden['schema']}.{golden['table']}", "key": [golden["mastered_id"]], "kind": "MDM_GOLDEN"},
        {"layer": "GOLD", "object": f"{gold['schema']}.{gold['dimension']}", "key": [gold["mastered_id"]], "kind": "BUILD",
         "skey": gold["skey"]},
    ]
