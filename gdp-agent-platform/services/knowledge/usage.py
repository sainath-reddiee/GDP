"""Skills the factory must load before a stage acts.

GDP playbooks live under snowflake/skills/gdp. The Cortex modeling playbooks live under
snowflake/skills/modeling. Procedures call use_skills so the run records which playbook
was applied; the agent loads the same names through KNOWLEDGE.LOAD_SKILL.
"""

from __future__ import annotations

import re
from typing import Dict, List

from services.common.sql import rows
from services.knowledge.procedures import load_skill, load_skill_for

STAGE_SKILLS: Dict[str, List[str]] = {
    "LANDING": ["BRONZE-SCHEMA-DDL-EXTRACTION", "SCHEMA-DDL-EXTRACTION"],
    "PROFILING": ["COLUMN-PROFILING", "AI-COLUMN-DESCRIPTIONS", "AI-DEEP-QUALITY-ANALYSIS"],
    "DOMAIN": ["AI-DATA-MODELING"],
    "MODELING": ["AI-DATA-MODELING", "AI-MODEL-GENERATION", "SEMANTIC-COLUMN-CLUSTERING", "MODEL-JSON-TO-DDL"],
    "MAPPING": ["AI-SCHEMA-MAPPING", "MAPPING-VALIDATION", "MAPPING-BUSINESS-RULES",
                "MAPPING-APPROVAL-WORKFLOW", "MAPPING-PATTERN-LIBRARY"],
    "STTM": ["GDP-DBT-ONBOARD-SOURCE"],
    "SODA": ["SODA_SKILL", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK"],
    "DBT": ["GDP-DBT-ONBOARD-SOURCE", "SILVER-MODEL", "GDP_DOMAIN_SKILL"],
    "VALIDATION": ["MAPPING-VALIDATION", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK"],
}

GDP_ONLY_SKILLS = {"GDP-DBT-ONBOARD-SOURCE", "GDP_DOMAIN_SKILL"}


def stage_skills(stage: str, standard: str = "GDP") -> List[str]:
    """Skills a stage loads for a run: GDP convention skills only when the run follows the GDP standard."""
    names = STAGE_SKILLS.get(stage, [])
    return names if standard == "GDP" else [n for n in names if n not in GDP_ONLY_SKILLS]


FORBIDDEN_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|MERGE|COPY|GRANT|REVOKE|CALL|EXECUTE)\b",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def use_skills(session, names: List[str], excerpt: int = 1200, run_id: str | None = None) -> str:
    """Load each skill (audited against the run) and return a short excerpt for the stage prompt. A skill that is not
    registered is skipped so one missing playbook never blocks a stage."""
    parts = []
    for name in names:
        try:
            skill = load_skill_for(session, name, run_id)
        except AssertionError:
            continue
        parts.append(f"[{skill['skill_name']} v{skill['version']}]\n{(skill.get('content') or '')[:excerpt]}")
    return "\n\n".join(parts)


def bound_skills(session, stage: str, standard: str = "GDP") -> List[str]:
    """Skills bound to a stage in Admin (enabled, in order); the built-in map until bindings exist."""
    from services.knowledge.skills import stage_skills as bound

    return bound(session, stage, standard)


def use_stage(session, stage: str, standard: str = "GDP", run_id: str | None = None, excerpt: int = 1200) -> str:
    """use_skills for every skill bound to the stage."""
    return use_skills(session, bound_skills(session, stage, standard), excerpt, run_id)


DBT_SKILL = "GDP-DBT-ONBOARD-SOURCE"
RULE_SECTIONS = ("## GDP Standard Rules", "## Architecture Overview")
FILE_HEADER = re.compile(r"\n\n# [\w./-]+\.(?:md|sql|yml|yaml|html)\n")


def skill_file(content: str, rel: str) -> str:
    """One bundled file from a skill's CONTENT (the seed appends each extra file under '# <path>')."""
    marker = f"\n\n# {rel}\n"
    at = (content or "").find(marker)
    if at < 0:
        return ""
    start = at + len(marker)
    nxt = FILE_HEADER.search(content, start)
    return content[start: nxt.start() if nxt else len(content)].strip()


def skill_section(content: str, heading: str) -> str:
    at = (content or "").find(heading)
    if at < 0:
        return ""
    end = content.find("\n## ", at + len(heading))
    nxt = FILE_HEADER.search(content, at)
    stops = [p for p in (end, nxt.start() if nxt else -1) if p > 0]
    return content[at: min(stops) if stops else len(content)].strip()


def compose_domain_context(skill_content: str, domain: str | None = None, target: str | None = None,
                           definitions: List[str] | None = None, budget: int = 6000) -> str:
    """Skill rules, the domain contract (target section first) and the target's model definition, within budget."""
    parts: List[str] = []
    if definitions:
        parts.append("TARGET MODEL:\n" + "\n".join(definitions))
    contract = skill_file(skill_content, f"references/{(domain or '').lower()}-contract.md") if domain else ""
    if contract:
        picked = [skill_section(contract, "## Required Mapping Keys")]
        if target:
            heading = re.search(rf"^#{{2,3}} [^\n]*\b{re.escape(target.upper())}\b[^\n]*$", contract, re.MULTILINE)
            if heading:
                picked.append(skill_section(contract, heading.group(0)))
        focus = "\n\n".join(p for p in picked if p) or contract
        parts.append(f"{domain.upper()} DOMAIN CONTRACT:\n" + focus[: budget // 2])
    rules = [s for s in (skill_section(skill_content, h) for h in RULE_SECTIONS) if s]
    if rules:
        parts.append("SKILL RULES:\n" + "\n\n".join(rules))
    text = "\n\n".join(parts) or (skill_content or "")
    return text[:budget]


def domain_context(session, domain_id: str | None, target: str | None = None, budget: int = 6000,
                   standard: str = "GDP") -> str:
    """Prompt context for mapping, STTM and dbt: the GDP skill rules (GDP runs only) plus the domain's contract."""
    skill = load_skill(session, DBT_SKILL) if standard == "GDP" else {}
    domain = None
    definitions: List[str] = []
    if domain_id:
        found = rows(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
        domain = found[0]["DOMAIN_NAME"] if found else None
        if target:
            definitions = [f"{r['TITLE']}: {r['CONTENT']}" for r in rows(
                session, """SELECT TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                            WHERE DOMAIN_ID = ? AND IS_CURRENT AND KNOWLEDGE_TYPE = 'MODEL_DEFINITION'
                              AND CONTENT_JSON:target_table::STRING = ?""", [domain_id, target.upper()])]
    return compose_domain_context(skill.get("content") or "", domain, target, definitions, budget)


def assert_safe_transformation(expression: str | None) -> None:
    """mapping-validation: transformation SQL may cast and decode, never change objects."""
    # keywords inside string literals are data (DECODE(col, 'D', 'DELETE')), not statements
    if expression and FORBIDDEN_SQL.search(STRING_LITERAL.sub("''", expression)):
        raise ValueError("MAPPING_VALIDATION: transformation contains a statement the mapping skill forbids")
