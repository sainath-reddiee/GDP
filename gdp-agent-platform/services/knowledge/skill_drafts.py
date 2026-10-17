"""The skill builder's engine, shared by the Skills page (apps/api/app/skill_builder_api.py) and the skill scout
(services/knowledge/scout_run.py): the knowledge a draft is distilled from, one draft, the candidate-versus-baseline
evaluation, and filing a new skill version.

`complete(prompt, schema, max_tokens) -> (object, usage, model)` is injected: the API passes its own Cortex call (the
model a person chose, HTTP errors), the worker passes services.common.llm.complete_json. Nothing here publishes: a new
version is a DRAFT; labels (candidate, production) are moved by people on the Skills page.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.knowledge import skill_builder as sb

Complete = Callable[[str, Dict[str, Any], int], Tuple[Dict[str, Any], Dict[str, Any], str]]

KNOWLEDGE_TYPES = ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "SODA_PATTERN", "MODEL_DEFINITION", "GLOSSARY",
                   "BUSINESS_RULE", "NAMING_STANDARD", "COLUMN_RULE", "DBT_PATTERN", "EXCEPTION"]
# knowledge a reviewer's rejections leave behind, per skill category (evidence for "improve")
FEEDBACK_TYPES = {"data-quality": ["SODA_PATTERN"], "mapping": ["MAPPING_PATTERN"], "sttm": ["TRANSFORMATION_RULE"],
                  "data-modeling": ["MODEL_DEFINITION"], "profiling": ["COLUMN_RULE"], "dbt": ["DBT_PATTERN", "TRANSFORMATION_RULE"]}
CATEGORY_TYPE = {"profiling": "PROFILING", "mapping": "MAPPING", "sttm": "STTM", "data-quality": "SODA", "dbt": "DBT",
                 "validation": "VALIDATION", "source-onboarding": "SOURCE_ONBOARDING"}


def knowledge_material(db: Any, domain_id: Optional[str], types: List[str], days: int) -> List[Dict[str, Any]]:
    """Approved, current knowledge of the given types (one domain or all), newest first, at most 80 items."""
    return db.query(
        """SELECT K.KNOWLEDGE_TYPE, K.TITLE, K.CONTENT, K.SOURCE_REFERENCE, D.DOMAIN_NAME
             FROM CORE.DOMAIN_KNOWLEDGE K JOIN CORE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
            WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND (%s IS NULL OR K.DOMAIN_ID = %s)
              AND ARRAY_CONTAINS(K.KNOWLEDGE_TYPE::VARIANT, PARSE_JSON(%s))
              AND COALESCE(K.UPDATED_AT, K.CREATED_AT) >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP())
            ORDER BY COALESCE(K.UPDATED_AT, K.CREATED_AT) DESC LIMIT 80""",
        (domain_id, domain_id, json.dumps(types), days))


def skill_names(db: Any) -> List[str]:
    return [r["skill_name"] for r in db.query("SELECT DISTINCT SKILL_NAME FROM CORE.SKILL_REGISTRY")]


def draft(complete: Complete, mode: str, *, categories: List[str], stages: List[str], existing_names: List[str],
          goal: str = "", answers: Optional[List[Dict[str, Any]]] = None, knowledge: Optional[List[Dict[str, Any]]] = None,
          document: str = "", document_name: str = "", existing: Optional[Dict[str, Any]] = None,
          feedback: Optional[List[str]] = None, instructions: str = "", category_id: Optional[str] = None,
          knowledge_keys: Optional[List[str]] = None, max_tokens: int = 12000) -> Dict[str, Any]:
    """{draft, content, issues, model, usage}: one structured draft, normalised, assembled and checked."""
    prompt = sb.draft_prompt(mode, goal=goal, answers=answers, knowledge=knowledge, document=document,
                             document_name=document_name, existing=existing, feedback=feedback,
                             instructions=instructions, categories=categories, stages=stages)
    if category_id:
        prompt += f"\n\nThe user chose the category {category_id}."
    raw, usage, model = complete(prompt, sb.DRAFT_SCHEMA, max_tokens)
    out = sb.normalize(raw, categories, stages, keep_name=existing["name"] if existing else None)
    if category_id and category_id in categories:
        out["category"] = category_id
    content = sb.assemble(out)
    issues = sb.check(out, content, existing_names, improving=existing["name"] if existing else None,
                      knowledge_keys=knowledge_keys)
    return {"draft": out, "content": content, "issues": issues, "model": model, "usage": usage}


def evaluate(complete: Complete, content: str, tests: List[Dict[str, Any]], baseline: str = "",
             label: str = "no skill") -> Dict[str, Any]:
    """Answer each test with the candidate and with the baseline (another skill's content, or none), then let a judge
    grade both against the expectations: services.knowledge.skill_builder.score plus the answers and timings."""
    started = time.time()
    cand, u1, model = complete(sb.answer_prompt(content, tests), sb.ANSWER_SCHEMA, 6000)
    base, u2, _ = complete(sb.answer_prompt(baseline, tests), sb.ANSWER_SCHEMA, 6000)
    a = {int(x.get("index", -1)): x.get("answer", "") for x in cand.get("answers") or []}
    b = {int(x.get("index", -1)): x.get("answer", "") for x in base.get("answers") or []}
    judged, u3, _ = complete(sb.judge_prompt(tests, a, b), sb.JUDGE_SCHEMA, 4000)
    summary = sb.score(tests, judged.get("results") or [], label)
    for row in summary["rows"]:
        row["candidate_answer"] = a.get(row["index"], "")
        row["baseline_answer"] = b.get(row["index"], "")
    summary.update({"model": model, "ms": int((time.time() - started) * 1000),
                    "tokens": {"candidate": u1.get("total_tokens"), "baseline": u2.get("total_tokens"), "judge": u3.get("total_tokens")},
                    "tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return summary


def insert_skill(db: Any, name: str, description: str, category_id: str, content: str, change_note: str,
                 origin: str = "AI", eval_json: Optional[Dict[str, Any]] = None,
                 config: Optional[Dict[str, Any]] = None) -> str:
    """A brand-new skill, revision 1, as a DRAFT version that nothing loads. The SKILL_ID."""
    skill_id = str(uuid.uuid4())
    db.execute("""INSERT INTO CORE.SKILL_REGISTRY (SKILL_ID, SKILL_NAME, SKILL_TYPE, VERSION, STAGE_PATH, CHECKSUM, STATUS,
                         DESCRIPTION, CONTENT, IS_CURRENT, CREATED_BY, REVISION, ORIGIN, CATEGORY_ID, CHANGE_NOTE, EVAL_JSON,
                         CONFIG)
                  SELECT %s, %s, %s, '0.1.0', %s, %s, 'DRAFT', %s, %s, FALSE, CURRENT_USER(), 1, %s, %s, %s,
                         PARSE_JSON(NULLIF(%s, '')), PARSE_JSON(NULLIF(%s, ''))""",
               (skill_id, name, CATEGORY_TYPE.get(category_id, "DOMAIN"), f"ui/{name.lower()}/0.1.0/SKILL.md",
                hashlib.sha256(content.encode("utf-8")).hexdigest(), description, content, origin, category_id,
                change_note, json.dumps(eval_json) if eval_json else "", json.dumps(config, default=str) if config else ""))
    return skill_id
