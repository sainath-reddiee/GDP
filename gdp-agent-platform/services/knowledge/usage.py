"""Skills the factory must load before a stage acts.

GDP playbooks live under snowflake/skills/gdp. The Cortex modeling playbooks live under
snowflake/skills/modeling. Procedures call use_skills so the run records which playbook
was applied; the agent loads the same names through CORE.LOAD_SKILL.
"""

from __future__ import annotations

import re
from typing import Dict, List

from services.common.sql import rows
from services.knowledge.procedures import load_skill, load_skill_for
from services.knowledge.writer import layer_sql

STAGE_SKILLS: Dict[str, List[str]] = {
    "LANDING": ["BRONZE-SCHEMA-DDL-EXTRACTION", "SCHEMA-DDL-EXTRACTION"],
    "PROFILING": ["COLUMN-PROFILING", "AI-COLUMN-DESCRIPTIONS", "AI-DEEP-QUALITY-ANALYSIS"],
    "DOMAIN": ["AI-DATA-MODELING"],
    "MODELING": ["AI-DATA-MODELING", "AI-MODEL-GENERATION", "SEMANTIC-COLUMN-CLUSTERING", "MODEL-JSON-TO-DDL"],
    "MAPPING": ["AI-SCHEMA-MAPPING", "MAPPING-VALIDATION", "MAPPING-BUSINESS-RULES",
                "MAPPING-APPROVAL-WORKFLOW", "MAPPING-PATTERN-LIBRARY", "GOLD-MODEL"],
    "STTM": ["GDP-DBT-ONBOARD-SOURCE", "GOLD-MODEL"],
    "SODA": ["SODA_SKILL", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK"],
    "DBT": ["GOLD-MODEL", "GDP-DBT-ONBOARD-SOURCE", "SILVER-MODEL", "GDP_DOMAIN_SKILL"],
    "VALIDATION": ["MAPPING-VALIDATION", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK", "GOLD-MODEL"],
}

GDP_ONLY_SKILLS = {"GDP-DBT-ONBOARD-SOURCE", "GDP_DOMAIN_SKILL", "GOLD-MODEL"}
# each pipeline's own playbooks: bronze to silver hub code is never in front of a Gold run, the gold model never in
# front of a Silver run (the Admin bindings carry the same scope, V055)
SILVER_ONLY_SKILLS = {"GDP-DBT-ONBOARD-SOURCE", "SILVER-MODEL", "BRONZE-SCHEMA-DDL-EXTRACTION", "SCHEMA-DDL-EXTRACTION",
                      "GDP_DOMAIN_SKILL"}
GOLD_ONLY_SKILLS = {"GOLD-MODEL"}


def pipeline_of_skill(name: str) -> str:
    """SILVER, GOLD or ANY: the built-in pipeline scope of a skill."""
    n = str(name or "").upper()
    return "SILVER" if n in SILVER_ONLY_SKILLS else "GOLD" if n in GOLD_ONLY_SKILLS else "ANY"


def stage_skills(stage: str, standard: str = "GDP", pipeline: str = "SILVER") -> List[str]:
    """Skills a stage loads for a run: GDP convention skills only when the run follows the GDP standard, and only the
    playbooks of the run's pipeline."""
    names = [n for n in STAGE_SKILLS.get(stage, []) if pipeline_of_skill(n) in ("ANY", str(pipeline or "SILVER").upper())]
    return names if standard == "GDP" else [n for n in names if n not in GDP_ONLY_SKILLS]


FORBIDDEN_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|MERGE|COPY|GRANT|REVOKE|CALL|EXECUTE)\b",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


FRONT_MATTER = re.compile(r"\A\s*---\n.*?\n---\s*\n", re.DOTALL)
EXCERPT = 2000  # characters of each skill's guidance in a stage prompt


def prompt_excerpt(content: str, limit: int = EXCERPT) -> str:
    """The part of a skill worth a prompt's space: without the YAML front matter (metadata, not guidance) and the
    bundled reference files appended after the SKILL.md body, cut at a paragraph boundary near `limit`."""
    text = FRONT_MATTER.sub("", content or "", count=1)
    bundled = FILE_HEADER.search(text)
    if bundled:
        text = text[:bundled.start()]
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("\n\n", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + "\n..."


def use_skills(session, names: List[str], excerpt: int = EXCERPT, run_id: str | None = None) -> str:
    """Load each skill (audited against the run) and return its guidance for the stage prompt (prompt_excerpt). A
    skill that is not registered is skipped so one missing playbook never blocks a stage."""
    parts = []
    for name in names:
        try:
            skill = load_skill_for(session, name, run_id)
        except AssertionError:
            continue
        parts.append(f"[{skill['skill_name']} v{skill['version']}]\n{prompt_excerpt(skill.get('content') or '', excerpt)}")
    return "\n\n".join(parts)


def run_scope(session, run_id: str | None) -> tuple:
    """(domain id, pipeline) a run works in: (None, SILVER) without a run, or when it cannot be read."""
    if not run_id:
        return None, "SILVER"
    from services.common.standard import run_pipeline

    try:
        found = rows(session, "SELECT DOMAIN_ID, PIPELINE FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
    except Exception:
        try:   # before V054
            found = rows(session, "SELECT DOMAIN_ID FROM CORE.WORKFLOW_RUN WHERE RUN_ID = ?", [run_id])
        except Exception:
            return None, "SILVER"
    return (found[0]["DOMAIN_ID"], run_pipeline(found[0])) if found else (None, "SILVER")


def run_domain(session, run_id: str | None) -> str | None:
    """The domain a run works in (None without a run, or when it cannot be read)."""
    return run_scope(session, run_id)[0]


def bound_skills(session, stage: str, standard: str = "GDP", run_id: str | None = None) -> List[str]:
    """Skills bound to a stage in Admin for the run's pipeline (enabled, in order; the built-in map until bindings
    exist), with the skills attached to the run's domain applied (added, or in place of the one they replace)."""
    from services.knowledge.skills import stage_skills as bound

    domain_id, pipeline = run_scope(session, run_id)
    return bound(session, stage, standard, domain_id, pipeline)


def use_stage(session, stage: str, standard: str = "GDP", run_id: str | None = None, excerpt: int = EXCERPT) -> str:
    """use_skills for every skill the stage loads for this run."""
    return use_skills(session, bound_skills(session, stage, standard, run_id), excerpt, run_id)


DBT_SKILL = "GDP-DBT-ONBOARD-SOURCE"
GOLD_SKILLS = ("GOLD-MODEL", "GDP-POST-MDM-DATA-MODEL")
RULE_SECTIONS = ("## GDP Standard Rules", "## Architecture Overview", "## Key Differences: Gold vs. Silver",
                 "## Key Rules (Quick Reference)", "## Column Naming Conventions (Gold)", "## 3. Resolution chain",
                 "## 6. Top gotchas")
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
                           definitions: List[str] | None = None, budget: int = 6000,
                           examples: List[str] | None = None) -> str:
    """Skill rules, the domain contract (target section first), the target's model definition and its accepted
    correct-output examples, within budget."""
    parts: List[str] = []
    if definitions:
        parts.append("TARGET MODEL:\n" + "\n".join(definitions))
    if examples:
        parts.append("CORRECT OUTPUT EXAMPLES (rows exactly as the target must hold them; match their formats):\n"
                     + "\n\n".join(examples)[: budget // 3])
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


def _skill_text(session, names) -> str:
    parts = []
    for name in names:
        try:
            parts.append(load_skill(session, name).get("content") or "")
        except AssertionError:
            continue
    return "\n\n".join(p for p in parts if p)


def domain_context(session, domain_id: str | None, target: str | None = None, budget: int = 6000,
                   standard: str = "GDP", pipeline: str = "SILVER") -> str:
    """Prompt context for mapping, STTM and dbt: the pipeline's GDP skill rules (GDP runs only: bronze to silver hub
    rules for Silver, the gold model and post-MDM resolution rules for Gold) plus the domain's contract, the target's
    model definition and examples (items scoped to the pipeline)."""
    gold = str(pipeline or "").upper() == "GOLD"
    skill = {"content": _skill_text(session, GOLD_SKILLS if gold else (DBT_SKILL,))} if standard == "GDP" else {}
    domain = None
    definitions: List[str] = []
    examples: List[str] = []
    if domain_id:
        found = rows(session, "SELECT DOMAIN_NAME FROM CORE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
        domain = found[0]["DOMAIN_NAME"] if found else None
        if target:
            items = rows(session, """SELECT KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON FROM CORE.DOMAIN_KNOWLEDGE
                                     WHERE DOMAIN_ID = ? AND IS_CURRENT AND KNOWLEDGE_TYPE IN ('MODEL_DEFINITION', 'EXAMPLE')
                                       AND CONTENT_JSON:target_table::STRING = ? AND """ + layer_sql(),
                         [domain_id, target.upper(), "GOLD" if gold else "SILVER"])
            definitions = [f"{r['TITLE']}: {r['CONTENT']}" for r in items if r["KNOWLEDGE_TYPE"] == "MODEL_DEFINITION"]
            from services.knowledge.examples import as_context

            examples = as_context([r for r in items if r["KNOWLEDGE_TYPE"] == "EXAMPLE"])
        # what the domain owner answered when the engine asked (services/learning/questions.py)
        for r in rows(session, """SELECT CONTENT FROM CORE.DOMAIN_KNOWLEDGE
                                   WHERE DOMAIN_ID = ? AND IS_CURRENT AND STATUS = 'ACTIVE' AND KNOWLEDGE_TYPE = 'BUSINESS_RULE'
                                     AND SOURCE_REFERENCE LIKE 'question.%' AND """ + layer_sql() + """
                                   ORDER BY UPDATED_AT DESC LIMIT 20""", [domain_id, "GOLD" if gold else "SILVER"]):
            definitions.append(f"Domain owner's answer: {r['CONTENT']}")
    return compose_domain_context(skill.get("content") or "", domain, target, definitions, budget, examples)


def assert_safe_transformation(expression: str | None) -> None:
    """mapping-validation: transformation SQL may cast and decode, never change objects."""
    # keywords inside string literals are data (DECODE(col, 'D', 'DELETE')), not statements
    if expression and FORBIDDEN_SQL.search(STRING_LITERAL.sub("''", expression)):
        raise ValueError("MAPPING_VALIDATION: transformation contains a statement the mapping skill forbids")
