"""Cortex Search over domain knowledge (hybrid keyword + vector, with attribute filters)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.common.sql import rows, variant

SERVICE = "KNOWLEDGE.KNOWLEDGE_SEARCH"
COLUMNS = ["KNOWLEDGE_ID", "TITLE", "CONTENT", "DOMAIN_NAME", "KNOWLEDGE_TYPE", "VERSION", "SOURCE_REFERENCE"]
MAX_LIMIT = 20


def search_request(query: str, domain: Optional[str] = None, knowledge_type: Optional[str] = None,
                   limit: int = 5) -> Dict[str, Any]:
    assert query and query.strip(), "query is required"
    filters: List[Dict[str, Any]] = [{"@eq": {"STATUS": "ACTIVE"}}]
    if domain:
        filters.append({"@eq": {"DOMAIN_NAME": domain}})
    if knowledge_type:
        filters.append({"@eq": {"KNOWLEDGE_TYPE": knowledge_type}})
    return {"query": query.strip()[:2000], "columns": COLUMNS,
            "filter": {"@and": filters} if len(filters) > 1 else filters[0],
            "limit": max(1, min(int(limit), MAX_LIMIT))}


def search(session, database: str, query: str, domain: Optional[str] = None,
           knowledge_type: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
    request = search_request(query, domain, knowledge_type, limit)
    result = rows(session, "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(?, ?) AS R",
                  [f"{database}.{SERVICE}", json.dumps(request)])
    return variant(result[0]["R"]).get("results", [])
