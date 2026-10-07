"""Knowledge items as something people manage: list with filters, add, edit (new version), retire, restore, history,
and an AI answer over search hits.

Items seeded from repository packs (CREATED_BY = 'SEED') are read-only here: every deploy re-applies them, so an edit
or retirement would be undone (and could leave two current versions). People add their own items next to them.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.knowledge.validate import KNOWLEDGE_TYPES, normalize_content

STRUCTURED = {"GLOSSARY", "BUSINESS_RULE", "TRANSFORMATION_RULE", "MAPPING_PATTERN"}
STATUSES = ("ACTIVE", "RETIRED", "DRAFT")
MAX_LIMIT = 200


def list_query(domain_id: Optional[str], knowledge_type: Optional[str], status: Optional[str], q: Optional[str],
               offset: int, limit: int) -> Tuple[str, List[Any]]:
    """WHERE clause and binds (%s style) for the current version of each item."""
    where, params = ["K.IS_CURRENT"], []
    if domain_id:
        where.append("K.DOMAIN_ID = %s"); params.append(domain_id)
    if knowledge_type:
        assert knowledge_type in KNOWLEDGE_TYPES, f"unknown knowledge type {knowledge_type}"
        where.append("K.KNOWLEDGE_TYPE = %s"); params.append(knowledge_type)
    if status:
        assert status in STATUSES, f"status must be one of {STATUSES}"
        where.append("K.STATUS = %s"); params.append(status)
    if q and q.strip():
        where.append("(K.TITLE ILIKE %s OR K.CONTENT ILIKE %s OR K.SOURCE_REFERENCE ILIKE %s)")
        like = f"%{q.strip()[:200]}%"
        params += [like, like, like]
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    return " AND ".join(where), params + [limit, max(0, int(offset or 0))]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:40] or "item"


def prepare_item(knowledge_type: str, title: str, content: str, content_json: Any) -> Tuple[Dict[str, Any], List[str]]:
    """(row fields, problems). Structured types need usable content_json; content text is always required."""
    problems: List[str] = []
    kind = str(knowledge_type or "").upper()
    if kind not in KNOWLEDGE_TYPES:
        problems.append(f"unknown knowledge type {knowledge_type}")
    title = str(title or "").strip()[:500]
    content = str(content or "").strip()[:8000]
    if not title:
        problems.append("a title is required")
    if not content:
        problems.append("content is required")
    cj = None
    if content_json not in (None, "", {}):
        if isinstance(content_json, str):
            try:
                content_json = json.loads(content_json)
            except ValueError:
                problems.append("structured content is not valid JSON")
                content_json = None
        if content_json is not None:
            cj = normalize_content(kind, content_json)
            if cj is None and kind in STRUCTURED:
                problems.append(f"structured content is not usable for {kind} (it needs at least a target_column)")
    elif kind in ("GLOSSARY", "TRANSFORMATION_RULE", "MAPPING_PATTERN"):
        problems.append(f"{kind} needs structured content (target column and its details)")
    return {"knowledge_type": kind, "title": title, "content": content, "content_json": cj}, problems


def new_key(domain_name: str, knowledge_type: str, title: str) -> str:
    return f"{str(domain_name).lower()}.{knowledge_type.lower()}.{slug(title)}.{uuid.uuid4().hex[:6]}"


def editable(created_by: Optional[str]) -> Tuple[bool, Optional[str]]:
    if str(created_by or "").upper() == "SEED":
        return False, "Comes from a repository pack and is re-applied on every deploy; add your own item instead."
    return True, None


ANSWER_SCHEMA = {"type": "object", "properties": {
    "answer": {"type": "string"},
    "cited": {"type": "array", "items": {"type": "integer"}}}, "required": ["answer", "cited"]}


def answer(session, database: str, question: str, domain_name: Optional[str] = None,
           knowledge_type: Optional[str] = None) -> Dict[str, Any]:
    """Search, then answer from the hits only, citing them by number (citations outside the hits are dropped)."""
    from services.common.llm import complete_json
    from services.knowledge.search import search

    hits = search(session, database, question, domain_name, knowledge_type, limit=8)
    if not hits:
        return {"answer": "No knowledge matches that question.", "citations": [], "hits": [], "model": None, "usage": {}}
    numbered = "\n".join(f"[{i + 1}] {h.get('DOMAIN_NAME')} · {h.get('KNOWLEDGE_TYPE')} · {h.get('TITLE')}: "
                         f"{str(h.get('CONTENT') or '')[:700]}" for i, h in enumerate(hits))
    prompt = ("Answer the question using only the numbered knowledge items. Cite the numbers you used. If they do not "
              f"answer it, say so.\n\nQUESTION: {question[:2000]}\n\nITEMS:\n{numbered}")
    output, usage, model = complete_json(session, prompt, ANSWER_SCHEMA, max_tokens=1500, stage="KNOWLEDGE")
    cited = sorted({int(n) for n in output.get("cited") or [] if isinstance(n, (int, float)) and 1 <= int(n) <= len(hits)})
    return {"answer": str(output.get("answer") or ""), "citations": [hits[n - 1] for n in cited], "hits": hits,
            "model": model, "usage": usage}
