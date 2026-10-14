"""The reference inputs the engine uses (KNOWLEDGE.REFERENCE_INPUT, V057): the latest ready input of a kind for a domain
and layer. `query(sql, params)` -> rows; works from the API (%s) and from Snowpark (?) through the caller's query."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

Query = Callable[[str, list], List[Dict[str, Any]]]


def _get(row: Dict[str, Any], key: str) -> Any:
    return row.get(key) if key in row else row.get(key.upper())


def latest(query: Query, domain_id: str, layer: str, kind: str) -> Optional[Dict[str, Any]]:
    """{reference_id, title, parsed} of the newest READY input of `kind` for the layer (or for any layer)."""
    try:
        found = query("""SELECT REFERENCE_ID, TITLE, PARSED FROM KNOWLEDGE.REFERENCE_INPUT
                          WHERE DOMAIN_ID = ? AND KIND = ? AND STATUS = 'READY' AND COALESCE(LAYER, 'ANY') IN ('ANY', ?)
                          ORDER BY IFF(LAYER = ?, 0, 1), CREATED_AT DESC LIMIT 1""", [domain_id, kind, layer, layer])
    except Exception:   # before V057
        return None
    if not found:
        return None
    parsed = _get(found[0], "PARSED")
    return {"reference_id": _get(found[0], "REFERENCE_ID"), "title": _get(found[0], "TITLE"),
            "parsed": json.loads(parsed) if isinstance(parsed, str) else (parsed or {})}
