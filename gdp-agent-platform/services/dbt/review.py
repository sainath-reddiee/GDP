"""Cortex skill review: check a generated dbt file against the DBT-ONBOARD-SOURCE skill and propose a fix.

The rules engine (services.dbt.onboard) is the source of truth; this pass is advisory. Findings cite the skill
rule they rely on, and any revised file is validated so it cannot drop or invent refs/sources or touch tables
outside the STTM before an engineer can accept it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from services.common.llm import DEFAULT_MODEL
from services.dbt.enhance import parse_complete

Rows = Callable[..., List[Dict[str, Any]]]
SKILL = "GDP-DBT-ONBOARD-SOURCE"
SECTIONS = ("## GDP Standard Rules", "## Standard Rules", "## STTM Anomalies", "### STTM Anomalies", "## Transformation", "# references/sttm-mapping-rules.md")
REF = re.compile(r"\{\{\s*(ref|source)\(([^)]*)\)\s*\}\}")

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["summary", "findings", "revised_content"],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "rule", "message", "line_hint"],
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                    "rule": {"type": "string", "description": "Skill rule cited, e.g. 'Rule 2: casts'"},
                    "message": {"type": "string"},
                    "line_hint": {"type": "string", "description": "Short code excerpt the finding is about"},
                },
            },
        },
        "revised_content": {"type": "string", "description": "Full corrected file, or empty string if no change"},
    },
}


def skill_excerpt(content: str, limit: int = 9000) -> str:
    """Rule-bearing parts of the skill text first (standard rules, anomalies, rulebook), then the rest."""
    text = content or ""
    if "SKILL RULES:" in text:
        return text[:limit]
    picked: List[str] = []
    for marker in SECTIONS:
        at = text.find(marker)
        if at >= 0:
            end = text.find("\n## ", at + len(marker))
            picked.append(text[at: end if end > 0 else at + 4000][:4000])
    body = "\n\n".join(dict.fromkeys(picked)) or text
    return body[:limit]


def review_prompt(path: str, content: str, skill: str, context: str, generation_notes: str) -> str:
    return (
        "You review dbt code generated for Snowflake by the DBT-ONBOARD-SOURCE skill. Check the file ONLY "
        "against the skill rules below and the approved STTM context. Report concrete violations (wrong cast, "
        "missing NULLIF(TRIM()) on text, INNER join, wrong dedup order, HKEY including audit/SKEY columns, unmapped "
        "column with a type suffix, unsafe identifiers, Jinja errors). Cite the rule. Do not invent tables, columns "
        "or refs that are not already in the file or the STTM. If changes are needed, return the FULL corrected file "
        "in revised_content, else an empty string.\n\n"
        f"=== SKILL RULES ({SKILL}) ===\n{skill}\n\n"
        f"=== STTM CONTEXT ===\n{context[:4000]}\n\n"
        f"=== GENERATOR NOTES ===\n{generation_notes[:2000]}\n\n"
        f"=== FILE: {path} ===\n{content[:10000]}\n"
    )


def refs(text: str) -> set:
    return {(kind, re.sub(r"\s+", "", args)) for kind, args in REF.findall(text or "")}


def validate_revision(original: str, revised: str) -> List[str]:
    """Reasons to reject an AI revision. Empty list = safe to offer."""
    problems: List[str] = []
    if not revised.strip():
        return problems
    added = refs(revised) - refs(original)
    removed = refs(original) - refs(revised)
    if added:
        problems.append(f"adds refs/sources not in the generated file: {sorted(a[1] for a in added)}")
    if removed:
        problems.append(f"drops refs/sources: {sorted(r[1] for r in removed)}")
    if revised.count("{{") != revised.count("}}") or revised.count("{%") != revised.count("%}"):
        problems.append("unbalanced Jinja delimiters")
    if re.search(r"\b(drop|truncate|delete\s+from|grant|alter)\b", revised, re.I) and not re.search(
            r"\b(drop|truncate|delete\s+from|grant|alter)\b", original, re.I):
        problems.append("introduces DDL/DML statements")
    if len(revised) > 3 * max(len(original), 400):
        problems.append("revision is far larger than the original")
    return problems


def review_file(fetch_rows: Rows, path: str, content: str, skill_content: str, context: str = "",
                generation_notes: str = "", model: Optional[str] = None, max_tokens: int = 8000) -> Dict[str, Any]:
    chosen = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    prompt = review_prompt(path, content, skill_excerpt(skill_content), context, generation_notes)
    rows = fetch_rows(
        "SELECT AI_COMPLETE(model => %s, prompt => %s, model_parameters => PARSE_JSON(%s), "
        "response_format => PARSE_JSON(%s), show_details => TRUE) AS R",
        (chosen, prompt, json.dumps({"temperature": 0, "max_tokens": int(max_tokens)}),
         json.dumps({"type": "json", "schema": REVIEW_SCHEMA})),
    )
    details = (rows[0] or {}).get("r") if rows else None
    if details is None and rows:
        details = rows[0].get("R")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = {}
    output = (details or {}).get("structured_output") or []
    raw = output[0].get("raw_message") if output else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        fallback = parse_complete(details, chosen)
        raw = {"summary": fallback.get("summary") or "", "findings": [], "revised_content": ""}
    revised = str(raw.get("revised_content") or "")
    if revised.strip() == content.strip():
        revised = ""
    problems = validate_revision(content, revised)
    findings = [f for f in raw.get("findings") or [] if isinstance(f, dict)][:30]
    return {
        "file_path": path,
        "summary": str(raw.get("summary") or ""),
        "findings": findings,
        "revised_content": "" if problems else revised,
        "rejected_revision": problems,
        "model": (details or {}).get("model") or chosen,
        "usage": (details or {}).get("usage") or {},
    }
