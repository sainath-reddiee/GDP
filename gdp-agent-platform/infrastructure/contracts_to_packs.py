"""Build domain/<domain>/domain_pack.json from the GDP dbt skill's domain contracts.

    python infrastructure/contracts_to_packs.py            # writes domain/{company,opportunity,property}/domain_pack.json

The packs are committed and reviewed; this script is rerun only when a contract changes. Target columns in a pack
are the contract's view. At seed time the live DEV_GDP_SILVER_DB columns win for names, types and order, and the
contract supplies semantics (notes, HKEY list, reference lookups). What markdown cannot express (detection signals,
Property's targets from the mapping rules, source systems) is declared in DOMAINS below.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.knowledge.contracts import named_section, parse_contract  # noqa: E402

SKILL = ROOT / "snowflake" / "skills" / "gdp" / "dbt-onboard-source"
CONTRACTS = SKILL / "references"
TECHNICAL = re.compile(r"(_SKEY|_HKEY|_SKEY_NEW)$|^SOURCE_UNIQUE_ID$|^GDP_")
GRAIN = "one row per SOURCE_UNIQUE_ID per REF_GDP_SOURCE_SYSTEM_SKEY"
BUSINESS_KEYS = ["SOURCE_UNIQUE_ID", "REF_GDP_SOURCE_SYSTEM_SKEY"]

DOMAINS: Dict[str, Dict[str, Any]] = {
    "company": {
        "name": "COMPANY",
        "description": "Companies and organizations (customers, vendors, clients, legal entities): a COMPANY_CORE hub "
                       "with address, industry, segment, relationship and hierarchy spokes.",
        "signals": {
            "tables": {"COMPANY": 3, "CUSTOMER": 3, "CUST": 2, "VENDOR": 3, "ORGANIZATION": 3, "ORG": 2, "CLIENT": 3,
                       "ACCOUNT": 2, "PARTY": 2, "FIRM": 2, "SUPPLIER": 2, "EMPLOYER": 2},
            "columns": {"DUNS": 3, "NAICS": 3, "SIC": 2, "NAME1": 3, "LEGAL": 2, "ALIAS": 2, "TRADE": 1, "EFF_STATUS": 2,
                        "PARENT": 1, "HIERARCHY": 2, "INDUSTRY": 2, "SEGMENT": 1, "WEBSITE": 2, "REGISTRATION": 2,
                        "VENDOR": 2, "CUSTOMER": 2, "COMPANY": 2, "TAX": 1, "EMPLOYEE": 1},
        },
    },
    "opportunity": {
        "name": "OPPORTUNITY",
        "description": "Sales opportunities and deals: a single OPPORTUNITY_CORE hub linked to company and contact.",
        "signals": {
            "tables": {"OPPORTUNITY": 3, "OPPTY": 3, "OPP": 2, "DEAL": 3, "PIPELINE": 3, "PURSUIT": 3, "LEAD": 2,
                       "PROPOSAL": 2, "BID": 2},
            "columns": {"STAGE": 3, "DEAL": 2, "CLOSE": 2, "PIPELINE": 3, "WIN": 2, "LOSS": 2, "PROBABILITY": 3,
                        "RFP": 3, "SOP": 2, "COP": 2, "REVENUE": 1, "FEE": 1, "SERVICE": 1, "SALES": 1,
                        "MOMENTUM": 2, "FORECAST": 2, "OPPORTUNITY": 3, "__C": 1},
        },
    },
    "property": {
        "name": "PROPERTY",
        "description": "Real estate properties and buildings: PROPERTY_CORE with usage, address and owner tables.",
        "signals": {
            "tables": {"PROPERTY": 3, "BUILDING": 3, "PARCEL": 3, "ASSET": 1, "SITE": 2, "LOT": 2, "ASSESSMENT": 3,
                       "LAND": 2, "PREMISE": 2, "FACILITY": 2, "AAR": 1},
            "columns": {"PARCEL": 3, "APN": 3, "SQFT": 3, "SQ_FT": 3, "SQUARE": 2, "LOT": 2, "LAND": 2, "BUILDING": 3,
                        "YEAR_BUILT": 3, "BUILT": 2, "ZONING": 3, "USAGE": 2, "OWNER": 1, "ASSESS": 2, "FLOOR": 2,
                        "STORIES": 2, "UNITS": 1, "PROPERTY": 3, "ACRE": 2},
        },
        "extra_targets": [
            {"table": "PROPERTY_USAGE", "description": "Property usage (Pattern A: dim_property_building + "
                                                       "dim_property_usage + country + fact_aar)."},
            {"table": "PROPERTY_ADDRESS", "description": "Property address (Pattern A)."},
            {"table": "PROPERTY_OWNER", "description": "Property owners (Pattern B: dim_property_building + "
                                                       "fact_property_role + dim_party_role + dim_organization, filtered "
                                                       "to party_role_type_desc IN ('Legal Owner','True Owner'))."},
        ],
        "patterns": "Per-Target Join Graph",
    },
}

SOURCE_DOMAINS = {
    "EDP": ["property"], "LIGHTBOX": ["property"], "DIQ": ["property"], "MTA": ["company", "property"],
    "SPOC": ["company"], "INTROHIVE": ["company"], "BUSINESS_SMARTSHEET": ["company"], "CLIENT_SENTIMENT": ["company"],
    "NEWS_TO_LEADS": ["company"], "TAT": ["company"], "FPD": ["company"], "GWS_BOE": ["opportunity"],
}


def source_systems(rules: str) -> List[Dict[str, Any]]:
    section = named_section(rules, "Bronze Schema Lookup") or ""
    out = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) >= 3 and cells[0].isdigit():
            out.append({"skey": int(cells[0]), "name": cells[1], "bronze_schema": cells[2].split(" ")[0]})
    return out


def _target(spec: Dict[str, Any], schema: str, hub: str, required: List[str], casts: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = {c["name"] for c in spec["columns"]}
    is_hub = spec["table"] == hub
    hub_fk = None if is_hub else (f"{hub}_SKEY" if f"{hub}_SKEY" in names or not names else None)
    return {
        "schema": schema, "table": spec["table"], "table_type": "HUB" if is_hub else "SPOKE",
        "grain": GRAIN, "business_keys": BUSINESS_KEYS, "scd_type": "1+HIST",
        "description": spec.get("description") or f"{'Hub' if is_hub else 'Spoke'} {spec['table']} of {schema}.",
        "model_spec": {
            "role": "hub" if is_hub else "spoke", "hub": hub, "hub_fk": hub_fk,
            "hkey_columns": spec.get("hkey_columns") or [],
            "reference_ctes": spec.get("reference_ctes") or {},
            "reference_joins": spec.get("reference_joins") or [],
            "minimum_mapping": required if is_hub else ["source_unique_id"],
            "casts": [c for c in casts if c["target_column"] in names] if names else casts,
        },
        "columns": [{
            "name": c["name"], "type": c["type"] or "TEXT", "nullable": c["nullable"],
            "definition": c.get("notes"), "business_key": c["name"] in BUSINESS_KEYS,
        } for c in spec["columns"]],
    }


def build(domain: str, rules: str) -> Dict[str, Any]:
    meta = DOMAINS[domain]
    text = (CONTRACTS / f"{domain}-contract.md").read_text(encoding="utf-8")
    c = parse_contract(text)
    schema, hub = c["schema"] or meta["name"], c["hub"] or f"{meta['name']}_CORE"
    specs = c["targets"] or [{"table": hub, "columns": []}]
    specs += [{**t, "columns": []} for t in meta.get("extra_targets", []) if t["table"] not in {s["table"] for s in specs}]
    targets = [_target(s, schema, hub, c["required_mapping"], c["casts"]) for s in specs]
    key = meta["name"].lower()
    knowledge: List[Dict[str, Any]] = []
    for t in targets:
        spec = t["model_spec"]
        knowledge.append({
            "key": f"{key}.model.{t['table'].lower()}", "type": "MODEL_DEFINITION", "title": f"{t['table']} ({spec['role']})",
            "content": (f"{t['description']} Grain: {GRAIN}. "
                        + (f"Spoke of {hub} through {spec['hub_fk']}. " if spec["hub_fk"] else "")
                        + (f"HKEY over {', '.join(spec['hkey_columns'])}." if spec["hkey_columns"] else "")),
            "content_json": {"target_table": t["table"], "role": spec["role"], "hub_fk": spec["hub_fk"],
                             "hkey_columns": spec["hkey_columns"], "column_count": len(t["columns"])},
        })
        if spec["reference_ctes"]:
            knowledge.append({
                "key": f"{key}.pattern.{t['table'].lower()}.references", "type": "DBT_PATTERN",
                "title": f"{t['table']} reference lookups",
                "content": "Reference CTEs:\n" + ",\n\n".join(spec["reference_ctes"].values()) + "\n\nJoins:\n"
                           + "\n".join(f"left join {j['cte']} as {j['alias']} on {j['condition']}"
                                       for j in spec["reference_joins"]),
            })
        for col in t["columns"]:
            if col["definition"] and not TECHNICAL.search(col["name"]):
                knowledge.append({
                    "key": f"{key}.glossary.{t['table'].lower()}.{col['name'].lower()}", "type": "GLOSSARY",
                    "title": f"{t['table']}.{col['name']}", "content": col["definition"],
                    "content_json": {"target_column": col["name"], "target_table": t["table"],
                                     "definition": col["definition"], "synonyms": []},
                })
    for cast in c["casts"]:
        knowledge.append({
            "key": f"{key}.transform.{cast['target_column'].lower()}", "type": "TRANSFORMATION_RULE",
            "title": f"{cast['target_column']} cast", "content": f"{cast['source_type']} to {cast['target_type']}: {cast['cast']}",
            # Mapping applies `expression` with {col} as the source column (services.mapping.features).
            "content_json": {"target_column": cast["target_column"], "transformation": cast["cast"],
                             "expression": re.sub(r"\bo\.col\b", "{col}", cast["cast"]),
                             "target_type": cast["target_type"]},
        })
    for title in ("Multi-Source-Table Extraction Pattern", "Multi-Target Onboarding Pattern", "Reference Implementation",
                  "Bronze MTA Source Tables"):
        section = named_section(text, title)
        if section:
            knowledge.append({"key": f"{key}.pattern.{re.sub(r'[^a-z]+', '_', title.lower()).strip('_')}",
                              "type": "DBT_PATTERN", "title": title, "content": section})
    if meta.get("patterns"):
        section = named_section(rules, meta["patterns"])
        if section:
            knowledge.append({"key": f"{key}.pattern.join_graph", "type": "DBT_PATTERN", "title": meta["patterns"],
                              "content": section})
    knowledge.append({
        "key": f"{key}.guide.minimum_mapping", "type": "ONBOARDING_GUIDE", "title": f"{meta['name']} minimum mapping",
        "content": "A source must map at least: " + "; ".join(c["required_mapping"]),
        "content_json": {"required": c["required_mapping"]},
    })
    sources = [s for s in source_systems(rules) if domain in SOURCE_DOMAINS.get(s["name"], [])]
    known = named_section(text, "Known Sources")
    if known:
        knowledge.append({"key": f"{key}.guide.known_sources", "type": "ONBOARDING_GUIDE",
                          "title": f"{meta['name']} sources", "content": known})
    return {
        "domain": {"name": meta["name"], "standard": "GDP", "description": meta["description"], "owner": "GDP Data Office",
                   "silver_database": c["database"] or "DEV_GDP_SILVER_DB", "silver_schema": schema,
                   "contract": (CONTRACTS / f"{domain}-contract.md").relative_to(ROOT).as_posix(),
                   "signals": meta["signals"], "source_systems": sources},
        "targets": targets,
        "knowledge": knowledge,
    }


def main() -> None:
    rules = (CONTRACTS / "sttm-mapping-rules.md").read_text(encoding="utf-8")
    for domain in DOMAINS:
        pack = build(domain, rules)
        out = ROOT / "domain" / domain / "domain_pack.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
        print(f"{out.relative_to(ROOT)}: {len(pack['targets'])} targets, "
              f"{sum(len(t['columns']) for t in pack['targets'])} columns, {len(pack['knowledge'])} knowledge items")


if __name__ == "__main__":
    main()
