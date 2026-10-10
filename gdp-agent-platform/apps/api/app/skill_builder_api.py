"""AI skill builder: clarifying questions, drafts from four sources, tests before publishing, and creating a skill.

Every AI call uses the SKILLS stage model (Admin, Model per stage) and is recorded in AUDIT.COST_USAGE. Nothing is
published here: a new skill or version is saved as the candidate, and production stays a separate, governed step.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _account_models, _model_for, _record_cost, _snowflake_error, current_db
from app.skills_api import STAGES, _categories, _labels, _move, _name
from services.knowledge import skill_builder as sb

router = APIRouter()

KNOWLEDGE_TYPES = ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "SODA_PATTERN", "MODEL_DEFINITION", "GLOSSARY",
                   "BUSINESS_RULE", "NAMING_STANDARD", "COLUMN_RULE", "DBT_PATTERN", "EXCEPTION"]
# knowledge a reviewer's rejections leave behind, per skill category (evidence for "improve")
FEEDBACK_TYPES = {"data-quality": ["SODA_PATTERN"], "mapping": ["MAPPING_PATTERN"], "sttm": ["TRANSFORMATION_RULE"],
                  "data-modeling": ["MODEL_DEFINITION"], "profiling": ["COLUMN_RULE"], "dbt": ["DBT_PATTERN", "TRANSFORMATION_RULE"]}
CATEGORY_TYPE = {"profiling": "PROFILING", "mapping": "MAPPING", "sttm": "STTM", "data-quality": "SODA", "dbt": "DBT",
                 "validation": "VALIDATION", "source-onboarding": "SOURCE_ONBOARDING"}


def _allowed_models(db: Db) -> list[str]:
    try:
        return [m["name"] for m in _account_models(db).get("models") or [] if m.get("available") is not False]
    except Exception:
        return []


@router.get("/api/skills/builder/models")
def builder_models(db: Db = Depends(current_db)):
    """Models the builder may use: the SKILLS stage default from Admin, and the account's available models."""
    return {"default": _model_for(db, "SKILLS"), "models": _allowed_models(db)}


def _complete(db: Db, prompt: str, schema: dict, max_tokens: int = 8000, model: Optional[str] = None) -> tuple[dict, dict, str]:
    if model:
        allowed = _allowed_models(db)
        if allowed and model not in allowed:
            raise HTTPException(400, f"Model {model} is not available in this account")
    model = model or _model_for(db, "SKILLS")
    started = time.time()
    try:
        result, query_id = db.query_with_id(
            "SELECT AI_COMPLETE(model => %s, prompt => %s, "
            f"model_parameters => {{'temperature': 0, 'max_tokens': {int(max_tokens)}}}, "
            "response_format => PARSE_JSON(%s), show_details => TRUE) AS R",
            (model, prompt, json.dumps({"type": "json", "schema": schema})))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    details = result[0]["r"] if result else None
    details = json.loads(details) if isinstance(details, str) else (details or {})
    usage = {**(details.get("usage") or {}), "query_id": query_id}
    _record_cost(db, None, "SKILLS", details.get("model", model), usage, started)
    structured = details.get("structured_output") or []
    if not structured:
        raise HTTPException(502, "Cortex returned no structured answer. Try again or shorten the material.")
    raw = structured[0].get("raw_message")
    try:
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError as exc:
        raise HTTPException(502, "Cortex returned an answer that was not valid JSON. Try again.") from exc
    usage["ms"] = int((time.time() - started) * 1000)
    return raw, usage, details.get("model", model)


def _names(db: Db) -> list[str]:
    return [r["skill_name"] for r in db.query("SELECT DISTINCT SKILL_NAME FROM KNOWLEDGE.SKILL_REGISTRY")]


def _production(db: Db, skill: str) -> Optional[dict]:
    pid = (_labels(db, skill).get(skill, {}).get("production") or {}).get("skill_id")
    sql = ("SELECT SKILL_ID, SKILL_NAME, VERSION, DESCRIPTION, CONTENT, CATEGORY_ID FROM KNOWLEDGE.SKILL_REGISTRY WHERE "
           + ("SKILL_ID = %s" if pid else "SKILL_NAME = %s AND STATUS = 'ACTIVE' ORDER BY REVISION DESC LIMIT 1"))
    found = db.query(sql, (pid or skill,))
    return found[0] if found else None


class Questions(BaseModel):
    goal: str = Field(min_length=10, max_length=4000)
    category_id: Optional[str] = None
    model: Optional[str] = Field(default=None, max_length=120)


@router.post("/api/skills/builder/questions")
def builder_questions(body: Questions, db: Db = Depends(current_db)):
    out, usage, model = _complete(db, sb.questions_prompt(body.goal, body.category_id, STAGES), sb.QUESTIONS_SCHEMA, 2000, body.model)
    questions = [q for q in out.get("questions") or [] if str(q.get("question") or "").strip()][:6]
    return {"questions": questions, "model": model, "tokens": usage.get("total_tokens")}


class Draft(BaseModel):
    mode: Literal["interview", "knowledge", "document", "improve"]
    goal: str = Field(default="", max_length=4000)
    answers: list[dict] = Field(default_factory=list)
    category_id: Optional[str] = None
    domain_id: Optional[str] = None
    knowledge_types: list[str] = Field(default_factory=list)
    days: int = Field(default=180, ge=1, le=3650)
    document_text: str = Field(default="", max_length=400_000)
    document_name: str = Field(default="", max_length=300)
    skill_name: Optional[str] = None
    instructions: str = Field(default="", max_length=2000)
    model: Optional[str] = Field(default=None, max_length=120)


@router.post("/api/skills/builder/draft")
def builder_draft(body: Draft, db: Db = Depends(current_db)):
    """One draft from the chosen source. Returns the draft, the assembled content, checks and what it was based on."""
    categories = [c["category_id"] for c in _categories(db)]
    provenance: dict[str, Any] = {"mode": body.mode}
    knowledge, existing, feedback, keys = None, None, None, None
    if body.mode == "interview" and not body.goal.strip():
        raise HTTPException(400, "Describe the goal of the skill")
    if body.mode == "knowledge":
        types = [t for t in (body.knowledge_types or KNOWLEDGE_TYPES) if t in KNOWLEDGE_TYPES]
        knowledge = db.query(
            """SELECT K.KNOWLEDGE_TYPE, K.TITLE, K.CONTENT, K.SOURCE_REFERENCE, D.DOMAIN_NAME
                 FROM KNOWLEDGE.DOMAIN_KNOWLEDGE K JOIN KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
                WHERE K.IS_CURRENT AND K.STATUS = 'ACTIVE' AND (%s IS NULL OR K.DOMAIN_ID = %s)
                  AND ARRAY_CONTAINS(K.KNOWLEDGE_TYPE::VARIANT, PARSE_JSON(%s))
                  AND COALESCE(K.UPDATED_AT, K.CREATED_AT) >= DATEADD(DAY, -%s, CURRENT_TIMESTAMP())
                ORDER BY COALESCE(K.UPDATED_AT, K.CREATED_AT) DESC LIMIT 80""",
            (body.domain_id, body.domain_id, json.dumps(types), body.days))
        if not knowledge:
            raise HTTPException(400, "No approved knowledge matches this domain, type and time window. Widen the filters.")
        keys = [k["source_reference"] for k in knowledge if k.get("source_reference")]
        provenance.update({"domain_id": body.domain_id, "knowledge_types": types, "knowledge_items": len(knowledge)})
    if body.mode == "document":
        if len(body.document_text.strip()) < 200:
            raise HTTPException(400, "The document is too short to build a skill from (at least 200 characters).")
        provenance.update({"document": body.document_name, "characters": len(body.document_text),
                           "truncated": len(body.document_text) > sb.MAX_DOC})
    if body.mode == "improve":
        if not body.skill_name:
            raise HTTPException(400, "Choose the skill to improve")
        skill = _name(db, body.skill_name)
        prod = _production(db, skill)
        if not prod:
            raise HTTPException(404, "That skill has no active version to improve")
        existing = {"name": skill, "version": prod["version"], "description": prod["description"], "content": prod["content"]}
        variants = json.dumps(sorted({skill, skill.replace("_", "-"), skill.replace("-", "_")}))
        feedback = []
        try:
            for r in db.query(
                    """SELECT F.TOOL_NAME, F.ERROR, R.RUN_NAME FROM AUDIT.AGENT_TOOL_CALL F
                         LEFT JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = F.RUN_ID
                        WHERE F.STATUS = 'FAILED' AND F.CREATED_AT >= DATEADD(DAY, -60, CURRENT_TIMESTAMP())
                          AND F.RUN_ID IN (SELECT RUN_ID FROM AUDIT.AGENT_TOOL_CALL WHERE TOOL_NAME = 'load_skill'
                                             AND ARRAY_CONTAINS(UPPER(INPUT_JSON:skill::STRING)::VARIANT, PARSE_JSON(%s)))
                        ORDER BY F.CREATED_AT DESC LIMIT 15""", (variants,)):
                feedback.append(f"Run {r.get('run_name') or ''} failed in {r['tool_name']}: {r.get('error') or ''}")
        except Exception:
            pass
        types = FEEDBACK_TYPES.get(prod.get("category_id") or "", [])
        if types:
            for r in db.query("""SELECT KNOWLEDGE_TYPE, TITLE, CONTENT FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                                  WHERE ((IS_CURRENT AND STATUS IN ('DRAFT', 'RETIRED')) OR STATUS = 'REJECTED')
                                    AND ARRAY_CONTAINS(KNOWLEDGE_TYPE::VARIANT, PARSE_JSON(%s))
                                    AND COALESCE(UPDATED_AT, CREATED_AT) >= DATEADD(DAY, -60, CURRENT_TIMESTAMP())
                                  ORDER BY COALESCE(UPDATED_AT, CREATED_AT) DESC LIMIT 15""", (json.dumps(types),)):
                feedback.append(f"Rejected or retired by a reviewer [{r['knowledge_type']}] {r['title']}: {str(r.get('content') or '')[:300]}")
        provenance.update({"base_skill_id": prod["skill_id"], "base_version": prod["version"], "evidence": len(feedback)})
    prompt = sb.draft_prompt(body.mode, goal=body.goal, answers=body.answers, knowledge=knowledge,
                             document=body.document_text, document_name=body.document_name, existing=existing,
                             feedback=feedback, instructions=body.instructions,
                             categories=categories, stages=STAGES)
    if body.category_id:
        prompt += f"\n\nThe user chose the category {body.category_id}."
    raw, usage, model = _complete(db, prompt, sb.DRAFT_SCHEMA, 12000, body.model)
    draft = sb.normalize(raw, categories, STAGES, keep_name=existing["name"] if existing else None)
    if body.category_id and body.category_id in categories:
        draft["category"] = body.category_id
    content = sb.assemble(draft)
    issues = sb.check(draft, content, _names(db), improving=existing["name"] if existing else None, knowledge_keys=keys)
    provenance["used_knowledge"] = draft.get("used_knowledge")
    return {"draft": draft, "content": content, "issues": issues, "provenance": provenance, "model": model,
            "tokens": usage.get("total_tokens"), "feedback": feedback or []}


class Check(BaseModel):
    draft: dict
    accepted: Optional[list[bool]] = None
    improving: Optional[str] = None


@router.post("/api/skills/builder/check")
def builder_check(body: Check, db: Db = Depends(current_db)):
    """Re-assemble and re-check after the user edits or rejects sections (no AI)."""
    content = sb.assemble(body.draft, body.accepted)
    return {"content": content, "issues": sb.check(body.draft, content, _names(db), improving=body.improving)}


class Test(BaseModel):
    content: str = Field(min_length=20, max_length=400_000)
    tests: list[dict] = Field(min_length=1, max_length=8)
    skill_name: Optional[str] = None  # compare with this skill's production version; none = with no skill at all
    model: Optional[str] = Field(default=None, max_length=120)


@router.post("/api/skills/builder/test")
def builder_test(body: Test, db: Db = Depends(current_db)):
    tests = [t for t in body.tests if str(t.get("input") or "").strip() and str(t.get("expectation") or "").strip()]
    if not tests:
        raise HTTPException(400, "Add at least one test with an input and an expectation")
    baseline, label = "", "no skill"
    if body.skill_name:
        try:
            prod = _production(db, _name(db, body.skill_name))
        except HTTPException:
            prod = None
        if prod:
            baseline, label = prod["content"] or "", f"production v{str(prod['version']).split('+')[0]}"
    started = time.time()
    cand, u1, model = _complete(db, sb.answer_prompt(body.content, tests), sb.ANSWER_SCHEMA, 6000, body.model)
    base, u2, _ = _complete(db, sb.answer_prompt(baseline, tests), sb.ANSWER_SCHEMA, 6000, body.model)
    a = {int(x.get("index", -1)): x.get("answer", "") for x in cand.get("answers") or []}
    b = {int(x.get("index", -1)): x.get("answer", "") for x in base.get("answers") or []}
    judged, u3, _ = _complete(db, sb.judge_prompt(tests, a, b), sb.JUDGE_SCHEMA, 4000, body.model)
    summary = sb.score(tests, judged.get("results") or [], label)
    for row in summary["rows"]:
        row["candidate_answer"] = a.get(row["index"], "")
        row["baseline_answer"] = b.get(row["index"], "")
    summary.update({"model": model, "ms": int((time.time() - started) * 1000),
                    "tokens": {"candidate": u1.get("total_tokens"), "baseline": u2.get("total_tokens"), "judge": u3.get("total_tokens")},
                    "tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return summary


class NewSkill(BaseModel):
    name: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=20, max_length=1000)
    category_id: str = Field(default="general", max_length=64)
    content: str = Field(min_length=20, max_length=400_000)
    change_note: str = Field(min_length=3, max_length=2000)
    origin: Literal["AI", "USER"] = "AI"
    eval: Optional[dict] = None


@router.post("/api/skills")
def create_skill(body: NewSkill, db: Db = Depends(current_db)):
    """A brand-new skill: revision 1, saved as a draft candidate. Nothing loads it until it is promoted and bound."""
    name = body.name.strip().upper()
    if not sb.NAME.match(name):
        raise HTTPException(400, "Name must be 3-64 characters of A-Z, 0-9, - or _")
    if name.replace("_", "-") in {n.upper().replace("_", "-") for n in _names(db)}:
        raise HTTPException(409, f"A skill named {name} already exists")
    issues = [i for i in sb.check({"name": name, "description": body.description, "sections": [1, 2], "tests": [1, 2]},
                                  body.content, []) if i["level"] == "error"]
    if issues:
        raise HTTPException(400, issues[0]["message"])
    skill_id = str(uuid.uuid4())
    db.execute("""INSERT INTO KNOWLEDGE.SKILL_REGISTRY (SKILL_ID, SKILL_NAME, SKILL_TYPE, VERSION, STAGE_PATH, CHECKSUM, STATUS,
                         DESCRIPTION, CONTENT, IS_CURRENT, CREATED_BY, REVISION, ORIGIN, CATEGORY_ID, CHANGE_NOTE, EVAL_JSON)
                  SELECT %s, %s, %s, '0.1.0', %s, %s, 'DRAFT', %s, %s, FALSE, CURRENT_USER(), 1, %s, %s, %s,
                         PARSE_JSON(NULLIF(%s, ''))""",
               (skill_id, name, CATEGORY_TYPE.get(body.category_id, "DOMAIN"), f"ui/{name.lower()}/0.1.0/SKILL.md",
                hashlib.sha256(body.content.encode("utf-8")).hexdigest(), body.description, body.content, body.origin,
                body.category_id, body.change_note, json.dumps(body.eval) if body.eval else ""))
    _move(db, name, "candidate", skill_id, body.change_note)
    return {"skill_name": name, "skill_id": skill_id, "version": "0.1.0"}
