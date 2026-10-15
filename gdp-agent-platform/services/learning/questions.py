"""Ask instead of guessing: where the engine is unsure, the domain owner is asked a few targeted questions, and each
answer becomes knowledge every later run uses (CORE.OPEN_QUESTION, V058).

  MAPPING   a source column whose best target is ambiguous or low in confidence, and that nothing already decides
            (no reviewer pattern, gold rule or reference STTM): "which target does it feed?", with the top candidates
            and "not loaded" as options. The answer is a reviewer pattern (or, for "not loaded", an anti-pattern),
            ACTIVE at once, so the next mapping ranks it first.
  NOTE      an assumption a proposal made and says so ("confirm what makes a row unique"): answered in words, kept as
            an owner's answer the stages' prompts read.

A question is asked once per subject: an open or answered one is not asked again.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

NOT_LOADED = "NOT_LOADED"
ASSUMPTION = re.compile(r"\b(confirm|assumed|assume|which column|which table|is it|unclear)\b", re.I)


def mapping_questions(ranked_by_source: Iterable[Tuple[Dict[str, Any], List[Dict[str, Any]]]], target_table: str, *,
                      min_confidence: float = 0.6, limit: int = 8) -> List[Dict[str, Any]]:
    """Questions for the source columns the mapping is least sure of (pure), lowest confidence first."""
    asked = []
    tt = str(target_table or "").upper().split(".")[-1]
    for s, ranked in ranked_by_source:
        if not ranked:
            continue
        top = ranked[0]
        ev = top.get("evidence") or {}
        decided = ev.get("gold_rule") or ev.get("reference_sttm") or (top.get("scores") or {}).get("historical", 0) >= 0.99
        if decided or not (top.get("ambiguous") or float(top.get("confidence") or 0) < min_confidence):
            continue
        st, col = str(s.get("table_name") or "").upper().split(".")[-1], str(s["column_name"]).upper()
        options = [str(c["target"]["column_name"]).upper() for c in ranked[:3]]
        asked.append({
            "kind": "MAPPING", "subject_key": f"mapping.{tt}.{st}.{col}", "confidence": float(top.get("confidence") or 0),
            "question": f"Which {tt} column does {st}.{col} feed?",
            "options": options + [NOT_LOADED],
            "context": {"source_table": st, "source_column": col, "target_table": tt, "data_type": s.get("data_type"),
                        "description": s.get("description"), "values": (s.get("values") or [])[:8],
                        "candidates": [{"target": str(c["target"]["column_name"]).upper(), "score": round(float(c.get("final_score") or 0), 3),
                                        "definition": c["target"].get("definition")} for c in ranked[:3]]}})
    asked.sort(key=lambda q: q["confidence"])
    return asked[:limit]


def note_questions(notes: Iterable[str], stage: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Questions for the assumptions a proposal states (pure)."""
    out = []
    for n in notes:
        text = str(n or "").strip()
        if text and ASSUMPTION.search(text):
            key = hashlib.md5(f"{stage}|{text}".encode("utf-8")).hexdigest()[:16]
            out.append({"kind": "NOTE", "subject_key": f"note.{stage.lower()}.{key}", "question": text, "options": [],
                        "context": {"stage": stage}})
    return out[:limit]


def ask(session, domain_id: str, layer: str, run_id: Optional[str], stage: str, questions: List[Dict[str, Any]]) -> int:
    """Keep the questions not asked before (open or answered); returns how many were added. Nothing before V058."""
    from services.common.sql import insert_rows, rows

    if not questions or not domain_id:
        return 0
    try:
        keys = [q["subject_key"] for q in questions]
        known = {r["SUBJECT_KEY"] for r in rows(session, f"""SELECT SUBJECT_KEY FROM CORE.OPEN_QUESTION
                     WHERE DOMAIN_ID = ? AND STATUS IN ('OPEN', 'ANSWERED')
                       AND SUBJECT_KEY IN ({', '.join('?' for _ in keys)})""", [domain_id, *keys])}
        new = [q for q in questions if q["subject_key"] not in known]
        if new:
            insert_rows(session, "CORE.OPEN_QUESTION",
                        ["QUESTION_ID", "DOMAIN_ID", "LAYER", "RUN_ID", "STAGE", "SUBJECT_KEY", "KIND", "QUESTION",
                         "CONTEXT", "OPTIONS"],
                        ["?", "?", "?", "NULLIF(?, '')", "?", "?", "?", "?", "PARSE_JSON(?)", "PARSE_JSON(?)"],
                        [[str(uuid.uuid4()), domain_id, layer, run_id or "", stage, q["subject_key"], q["kind"],
                          q["question"][:4000], q.get("context") or {}, q.get("options") or []] for q in new])
        return len(new)
    except Exception:
        return 0


def knowledge_for(question: Dict[str, Any], choice: Optional[str], text: Optional[str]) -> Dict[str, Any]:
    """The knowledge an answer becomes (pure): {kind, key, title, content, content_json}."""
    from services.mapping.feedback import pattern

    ctx = question.get("context") or {}
    if question.get("kind") == "MAPPING":
        choice = str(choice or "").upper()
        options = [str(o).upper() for o in question.get("options") or []]
        assert choice in options, f"choose one of {', '.join(options)}"
        top = (ctx.get("candidates") or [{}])[0].get("target")
        note = (text or "").strip() or "Answered by the domain owner"
        if choice == NOT_LOADED:
            item = pattern(ctx["source_column"], ctx["source_table"], None, ctx["target_table"], "REJECTED", None,
                           f"Not loaded. {note}", top)
        else:
            item = pattern(ctx["source_column"], ctx["source_table"], choice, ctx["target_table"], "APPROVED", None,
                           note, top)
        return {"kind": "MAPPING_PATTERN", "key": item["source_reference"], "title": item["title"],
                "content": item["content"], "content_json": item["content_json"]}
    answer = (text or "").strip()
    assert answer, "answer the question in a sentence"
    return {"kind": "BUSINESS_RULE", "key": f"question.{question['subject_key']}"[:1000],
            "title": f"Owner's answer: {question['question']}"[:500],
            "content": f"Question: {question['question']} Answer: {answer}",
            "content_json": {"question": question["question"], "answer": answer, "subject": question["subject_key"],
                             "stage": ctx.get("stage")}}


def answer(session, question_id: str, choice: Optional[str], text: Optional[str]) -> Dict[str, Any]:
    """Record the owner's answer and write the knowledge it becomes (ACTIVE: the owner decided it)."""
    from services.common.sql import rows, variant
    from services.knowledge.writer import remember

    found = rows(session, "SELECT * FROM CORE.OPEN_QUESTION WHERE QUESTION_ID = ?", [question_id])
    assert found, "question not found"
    q = {k.lower(): v for k, v in found[0].items()}
    assert q["status"] == "OPEN", f"the question is already {str(q['status']).lower()}"
    q["context"], q["options"] = variant(q.get("context")) or {}, variant(q.get("options")) or []
    item = knowledge_for(q, choice, text)
    knowledge_id = remember(session, domain_id=q["domain_id"], kind=item["kind"], key=item["key"], title=item["title"],
                            content=item["content"], content_json=item["content_json"], tags=["QUESTION"], origin="USER",
                            run_id=q.get("run_id"), layer=q.get("layer") or "ANY",
                            change_note="Answer to a question the engine asked")
    session.sql("""UPDATE CORE.OPEN_QUESTION SET STATUS = 'ANSWERED', ANSWER = PARSE_JSON(?), ANSWERED_BY = CURRENT_USER(),
                          ANSWERED_AT = CURRENT_TIMESTAMP(), KNOWLEDGE_ID = NULLIF(?, '') WHERE QUESTION_ID = ?""",
                params=[json.dumps({"choice": choice, "text": text}), knowledge_id or "", question_id]).collect()
    return {"question_id": question_id, "status": "ANSWERED", "knowledge_id": knowledge_id, "kind": item["kind"]}
