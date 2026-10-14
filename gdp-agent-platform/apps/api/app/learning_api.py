"""The learning loop over HTTP: learn rules from a domain's reviewer decisions, replay a knowledge change before it is
approved, and the questions the engine asks a domain's owner (services/learning, V058).

  POST /api/domains/{id}/learning/learn?layer=     learn, replay and propose (to the knowledge inbox)
  GET  /api/domains/{id}/learning?layer=           the learned proposals, recent replays, and open questions
  POST /api/knowledge/{knowledge_id}/replay        replay one knowledge item on the domain's past runs
  GET  /api/domains/{id}/questions?layer=&status=  a domain's questions
  GET  /api/runs/{run_id}/questions                a run's questions
  POST /api/questions/{question_id}/answer         the owner's answer (becomes ACTIVE knowledge)
  POST /api/questions/{question_id}/dismiss        not worth answering
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import Db
from app.main import _snowflake_error, current_db, domain_detail
from app.source_invoke import invoke_source

router = APIRouter()


def _layer(layer: Optional[str]) -> str:
    return "GOLD" if str(layer or "").upper() == "GOLD" else "SILVER"


def _js(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _missing(exc: Exception) -> bool:
    return "does not exist" in str(exc).lower()


def _call(db: Db, fn, *args):
    try:
        return invoke_source(db, fn, *args)
    except HTTPException:
        raise
    except (AssertionError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        raise _snowflake_error(exc) from exc


@router.post("/api/domains/{domain_id}/learning/learn")
def learn(domain_id: str, layer: str = "SILVER", db: Db = Depends(current_db)):
    """Learn the habits behind the domain's reviewer decisions in a layer, replay each on its past runs, and propose
    the ones that do not make mapping worse (a steward approves them in the knowledge inbox)."""
    from services.learning.learn import learn_from_decisions

    domain_detail(domain_id, db)
    return _call(db, learn_from_decisions, domain_id, _layer(layer))


@router.post("/api/knowledge/{knowledge_id}/replay")
def replay_knowledge(knowledge_id: str, db: Db = Depends(current_db)):
    from services.learning.learn import replay_knowledge as run

    return _call(db, run, knowledge_id)


@router.get("/api/domains/{domain_id}/learning")
def learning(domain_id: str, layer: str = "SILVER", db: Db = Depends(current_db)):
    lay = _layer(layer)
    learned = db.query("""SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, STATUS, CONTENT_JSON:evidence AS EVIDENCE,
                                 CONTENT_JSON:replay AS REPLAY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                            FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                           WHERE DOMAIN_ID = %s AND SOURCE_REFERENCE LIKE 'learned.%%' AND COALESCE(LAYER, 'ANY') IN ('ANY', %s)
                             AND STATUS IN ('PROPOSED', 'ACTIVE', 'REJECTED')
                           QUALIFY ROW_NUMBER() OVER (PARTITION BY LINEAGE_ID ORDER BY VERSION DESC) = 1
                           ORDER BY CREATED_AT DESC LIMIT 50""", (domain_id, lay))
    for r in learned:
        r["evidence"], r["replay"] = _js(r.get("evidence")), _js(r.get("replay"))
    deployed, evals, open_questions = True, [], 0
    try:
        evals = db.query("""SELECT SUBJECT_KIND, SUBJECT_ID, VERDICT, RESULT:delta AS DELTA, RESULT:runs::NUMBER AS RUNS,
                                   CREATED_BY, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                              FROM KNOWLEDGE.CHANGE_EVAL WHERE DOMAIN_ID = %s AND LAYER = %s
                             ORDER BY CREATED_AT DESC LIMIT 20""", (domain_id, lay))
        for e in evals:
            e["delta"] = _js(e.get("delta"))
        open_questions = int((db.query("""SELECT COUNT(*) AS N FROM KNOWLEDGE.OPEN_QUESTION
                                           WHERE DOMAIN_ID = %s AND LAYER = %s AND STATUS = 'OPEN'""", (domain_id, lay))
                              or [{"n": 0}])[0]["n"] or 0)
    except Exception as exc:
        if not _missing(exc):
            raise _snowflake_error(exc) from exc
        deployed = False
    decisions = db.query("""SELECT COUNT(*) AS N FROM MAPPING.MAPPING_DECISION D JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = D.RUN_ID
                             WHERE D.IS_CURRENT AND R.DOMAIN_ID = %s AND COALESCE(R.PIPELINE, 'SILVER') = %s
                               AND R.DELETED_AT IS NULL AND COALESCE(R.ENVIRONMENT, '') <> 'TEST'""", (domain_id, lay))
    return {"layer": lay, "deployed": deployed, "decisions": int(decisions[0]["n"] or 0) if decisions else 0,
            "learned": learned, "evals": evals, "open_questions": open_questions}


def _question_rows(db: Db, where: str, params: tuple) -> Dict[str, Any]:
    try:
        found = db.query(f"""SELECT QUESTION_ID, DOMAIN_ID, LAYER, RUN_ID, STAGE, SUBJECT_KEY, KIND, QUESTION, CONTEXT, OPTIONS,
                                    STATUS, ANSWER, ANSWERED_BY, TO_VARCHAR(ANSWERED_AT, 'YYYY-MM-DD HH24:MI') AS ANSWERED_AT,
                                    KNOWLEDGE_ID, TO_VARCHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI') AS CREATED_AT
                               FROM KNOWLEDGE.OPEN_QUESTION WHERE {where}
                              ORDER BY IFF(STATUS = 'OPEN', 0, 1), CREATED_AT DESC LIMIT 100""", params)
    except Exception as exc:
        if _missing(exc):
            return {"questions": [], "deployed": False}
        raise _snowflake_error(exc) from exc
    for q in found:
        for k in ("context", "options", "answer"):
            q[k] = _js(q.get(k))
    return {"questions": found, "deployed": True}


@router.get("/api/domains/{domain_id}/questions")
def domain_questions(domain_id: str, layer: Optional[str] = None, status: str = "OPEN", db: Db = Depends(current_db)):
    where, params = "DOMAIN_ID = %s", [domain_id]
    if layer:
        where += " AND LAYER = %s"
        params.append(_layer(layer))
    if status.upper() in ("OPEN", "ANSWERED", "DISMISSED"):
        where += " AND STATUS = %s"
        params.append(status.upper())
    return _question_rows(db, where, tuple(params))


@router.get("/api/runs/{run_id}/questions")
def run_questions(run_id: str, db: Db = Depends(current_db)):
    return _question_rows(db, "RUN_ID = %s", (run_id,))


class AnswerIn(BaseModel):
    choice: Optional[str] = Field(default=None, max_length=255)
    text: Optional[str] = Field(default=None, max_length=2000)


@router.post("/api/questions/{question_id}/answer")
def answer_question(question_id: str, body: AnswerIn, db: Db = Depends(current_db)):
    from services.learning.questions import answer

    out = _call(db, answer, question_id, body.choice, body.text)
    from app.knowledge_api import _changed

    _changed()
    return out


@router.post("/api/questions/{question_id}/dismiss")
def dismiss_question(question_id: str, db: Db = Depends(current_db)):
    try:
        db.execute("""UPDATE KNOWLEDGE.OPEN_QUESTION SET STATUS = 'DISMISSED', ANSWERED_BY = CURRENT_USER(),
                             ANSWERED_AT = CURRENT_TIMESTAMP() WHERE QUESTION_ID = %s AND STATUS = 'OPEN'""", (question_id,))
    except Exception as exc:
        raise _snowflake_error(exc) from exc
    return {"question_id": question_id, "status": "DISMISSED"}
