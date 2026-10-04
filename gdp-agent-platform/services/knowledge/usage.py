"""Skills the factory must load before a stage acts.

GDP playbooks live under snowflake/skills/gdp. The Cortex modeling playbooks live under
snowflake/skills/modeling. Procedures call use_skills so the run records which playbook
was applied; the agent loads the same names through KNOWLEDGE.LOAD_SKILL.
"""

from __future__ import annotations

import re
from typing import Dict, List

from services.knowledge.procedures import load_skill

STAGE_SKILLS: Dict[str, List[str]] = {
    "LANDING": ["BRONZE-SCHEMA-DDL-EXTRACTION", "SCHEMA-DDL-EXTRACTION"],
    "PROFILING": ["COLUMN-PROFILING", "AI-COLUMN-DESCRIPTIONS", "AI-DEEP-QUALITY-ANALYSIS"],
    "DOMAIN": ["AI-DATA-MODELING"],
    "MAPPING": ["AI-SCHEMA-MAPPING", "MAPPING-VALIDATION", "MAPPING-BUSINESS-RULES",
                "MAPPING-APPROVAL-WORKFLOW", "MAPPING-PATTERN-LIBRARY"],
    "STTM": ["DBT-ONBOARD-SOURCE"],
    "SODA": ["SODA_SKILL", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK"],
    "DBT": ["DBT-ONBOARD-SOURCE", "SILVER-MODEL", "GDP_DOMAIN_SKILL"],
    "VALIDATION": ["MAPPING-VALIDATION", "DEV-DATAREADINESS-CHECK", "QA-DATAREADINESS-CHECK"],
}

FORBIDDEN_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|MERGE|COPY|GRANT|REVOKE|CALL|EXECUTE)\b",
    re.IGNORECASE,
)


def use_skills(session, names: List[str], excerpt: int = 1200) -> str:
    """Load each skill (audited) and return a short excerpt for the stage prompt."""
    parts = []
    for name in names:
        skill = load_skill(session, name)
        parts.append(f"[{skill['skill_name']} v{skill['version']}]\n{(skill.get('content') or '')[:excerpt]}")
    return "\n\n".join(parts)


def assert_safe_transformation(expression: str | None) -> None:
    """mapping-validation: transformation SQL may cast and decode, never change objects."""
    if expression and FORBIDDEN_SQL.search(expression):
        raise ValueError("MAPPING_VALIDATION: transformation contains a statement the mapping skill forbids")
