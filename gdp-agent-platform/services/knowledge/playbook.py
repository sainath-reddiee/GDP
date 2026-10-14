"""A domain's playbook per layer (pure): which knowledge each stage of a Silver or Gold run reads, and where the
domain has none of it yet.

The stage readers are the same everywhere (services/knowledge/select.relevant, usage.domain_context, the mapping
patterns); this module only says which types matter to which layer, so the domain page can show coverage and gaps and
the person can add exactly what the next run would use.
"""

from __future__ import annotations

from typing import Any, Dict, List

# knowledge type -> (why a run reads it, the stages that read it), per layer
NEEDS: Dict[str, Dict[str, tuple]] = {
    "SILVER": {
        "GLOSSARY": ("Business terms and synonyms the mapping matches source columns with", ("MAPPING",)),
        "MAPPING_PATTERN": ("Source-to-target patterns reviewers approved before", ("MAPPING",)),
        "TRANSFORMATION_RULE": ("How a target column is computed from the source", ("STTM", "SODA")),
        "BUSINESS_RULE": ("Rules the data must follow, used in the STTM and quality checks", ("STTM", "SODA", "QA")),
        "MODEL_DEFINITION": ("What each silver table is: purpose, grain, keys", ("MAPPING", "STTM", "DBT")),
        "NAMING_STANDARD": ("Naming the code must follow", ("DBT", "VALIDATION")),
        "SODA_PATTERN": ("Data quality checks that worked for this domain", ("SODA",)),
        "QA_TEST": ("Tests a tester wrote or approved", ("QA",)),
        "EXAMPLE": ("Rows exactly as a silver table must hold them", ("STTM", "QA")),
    },
    "GOLD": {
        "MODEL_DEFINITION": ("What each gold fact, dimension and bridge is: grain, keys, measures", ("MAPPING", "STTM", "DBT")),
        "EXCEPTION": ("MDM rules: how mastered keys resolve, the cross-reference gotchas", ("MAPPING", "STTM", "DBT")),
        "BUSINESS_RULE": ("Measures and derivations the gold tables must follow", ("STTM", "SODA", "QA")),
        "TRANSFORMATION_RULE": ("How a gold column is computed from silver", ("STTM", "SODA")),
        "NAMING_STANDARD": ("Gold naming (facts, dimensions, keys)", ("DBT", "VALIDATION")),
        "SODA_PATTERN": ("Gold data quality checks (for example the unresolved key rate)", ("SODA",)),
        "QA_TEST": ("Tests a tester wrote or approved for gold", ("QA",)),
        "EXAMPLE": ("Rows exactly as a gold table must hold them", ("STTM", "QA")),
    },
}


def coverage(counts: Dict[str, Dict[str, int]], pipeline: str) -> List[Dict[str, Any]]:
    """[{type, why, stages, layer_items, shared_items, gap}] for the layer: counts are {type: {SILVER|GOLD|ANY: n}}."""
    layer = "GOLD" if str(pipeline or "").upper() == "GOLD" else "SILVER"
    out = []
    for kind, (why, stages) in NEEDS[layer].items():
        c = counts.get(kind) or {}
        own, shared = int(c.get(layer) or 0), int(c.get("ANY") or 0)
        out.append({"type": kind, "why": why, "stages": list(stages), "layer_items": own, "shared_items": shared,
                    "gap": own + shared == 0})
    return out


def layer_readiness(layer: str, facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a layer of the domain needs before its runs go well (pure). facts: role, consumes [{domain, gold_column}],
    silver_model (approved version or None), gold_model, gold_tables [{entity, columns}], gold_definitions (count of
    Gold or shared MODEL_DEFINITION items), contract_problems [str], exceptions (MDM rules count)."""
    def item(iid, label, met, detail):
        return {"id": iid, "label": label, "met": bool(met), "detail": detail}

    role = str(facts.get("role") or "").upper()
    if str(layer).upper() == "MDM":
        problems = facts.get("contract_problems") or []
        return [
            item("contract", "Reltio contract", not problems, "; ".join(problems[:3]) or "pre-MDM core, cross-reference, golden and gold tables named"),
            item("exceptions", "MDM rules", (facts.get("exceptions") or 0) > 0, f"{facts.get('exceptions') or 0} rule(s) the runs read"),
            item("consumers", "Consumers", bool(facts.get("consumers")),
                 ", ".join(facts.get("consumers") or []) or "no domain consumes it yet"),
        ]
    if str(layer).upper() != "GOLD":
        return []
    if role == "MASTERED":   # its gold is the conformed dimension (gdp-dbt, from Reltio's golden records)
        return [item("conformed", "Conformed dimension", True,
                     "Built in gdp-dbt from Reltio's golden records; consumers reference it, Gold runs never rebuild it"),
                item("consumers", "Consumers", bool(facts.get("consumers")),
                     ", ".join(facts.get("consumers") or []) or "no domain consumes it yet")]
    consumes = facts.get("consumes") or []
    tables = facts.get("gold_tables") or []
    columns = {c for t in tables for c in t.get("columns") or []}
    missing = [f"{c['domain']} ({c['gold_column']})" for c in consumes if str(c.get("gold_column") or "").upper() not in columns]
    return [
        item("mdm", "Linked to MDM", role == "CONSUMER" and bool(consumes),
             ("Consumes " + ", ".join(c["domain"] for c in consumes)) if consumes else
             ("Mastered by Reltio: its gold is the conformed dimension" if role == "MASTERED" else "Mark which mastered domains it consumes")),
        item("silver", "Approved Silver model", bool(facts.get("silver_model")), "Gold is built from the domain's Silver tables"),
        item("gold", "Approved Gold model", bool(facts.get("gold_model")),
             f"v{facts['gold_model']} with {len(tables)} table(s)" if facts.get("gold_model") else "Propose it from a Gold run"),
        item("keys", "Every mastered key in the model", bool(facts.get("gold_model")) and not missing,
             "Missing: " + ", ".join(missing) if missing else ("All consumed keys resolve to their dimensions" if consumes else "Nothing consumed")),
        item("definitions", "Gold table definitions", (facts.get("gold_definitions") or 0) >= len(tables) > 0,
             f"{facts.get('gold_definitions') or 0} definition(s) for {len(tables)} gold table(s)"),
    ]
