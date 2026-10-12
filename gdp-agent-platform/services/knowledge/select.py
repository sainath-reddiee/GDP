"""Knowledge picked for what a stage is working on, not just the newest rows of the domain.

Stages used to take the domain's newest N items of a type (ORDER BY UPDATED_AT DESC LIMIT 8): in a domain with more
items than that, the rule for the very column being mapped could be left out while unrelated recent ones went in.
`relevant` reads a wider window of the domain's active items and ranks them by what the stage is about: the target
table, the target columns and the source columns or tables, with recency only breaking ties. Items learned on
another target table rank last. Usage is recorded for the items actually returned, not for everything read.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from services.common.sql import rows, variant

SCAN = 400  # items of the requested types read per call, newest first


def _name(value: Any) -> str:
    return str(value or "").strip().upper().split(".")[-1]


def score(item: Dict[str, Any], target_table: Optional[str] = None, columns: Iterable[str] = (),
          sources: Iterable[str] = ()) -> float:
    """How closely one knowledge item (TITLE, CONTENT_JSON) fits the work at hand. Pure.

      +4    its target_column is one of the columns
      +2    its target_table is the target table          -3 when it names another target table
      +1    a column is mentioned in its title
      +1    its source_column or source_table is one of the sources"""
    cols = {_name(c) for c in columns if c}
    srcs = {_name(s) for s in sources if s}
    content = variant(item.get("CONTENT_JSON")) or {}
    content = content if isinstance(content, dict) else {}
    points = 0.0
    if cols and _name(content.get("target_column")) in cols:
        points += 4
    table = _name(content.get("target_table"))
    if target_table and table:
        points += 2 if table == _name(target_table) else -3
    title = str(item.get("TITLE") or "").upper()
    if cols and any(c and c in title for c in cols):
        points += 1
    if srcs and ({_name(content.get("source_column")), _name(content.get("source_table"))} & srcs):
        points += 1
    return points


def rank(items: Sequence[Dict[str, Any]], target_table: Optional[str] = None, columns: Iterable[str] = (),
         sources: Iterable[str] = (), limit: int = 8, type_order: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """`items` (newest first) ordered by score, then by type_order, then by recency; the first `limit`. Pure."""
    columns, sources = list(columns), list(sources)
    order = {t: i for i, t in enumerate(type_order)}

    def key(pair):
        index, item = pair
        return (-score(item, target_table, columns, sources), order.get(item.get("KNOWLEDGE_TYPE"), len(order)), index)

    return [item for _, item in sorted(enumerate(items), key=key)][:limit]


def relevant(session, domain_id: Optional[str], types: Sequence[str], *, target_table: Optional[str] = None,
             columns: Iterable[str] = (), sources: Iterable[str] = (), limit: int = 8,
             type_order: Sequence[str] = (), run_id: Optional[str] = None, stage: Optional[str] = None,
             scan: int = SCAN) -> List[Dict[str, Any]]:
    """The domain's active, non-operational items of `types` that best fit the target table and columns (rank).
    Each row has KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON. Records usage for the returned items when
    run_id and stage are given."""
    if not domain_id or not types:
        return []
    from services.knowledge.writer import NOT_OPERATIONAL_SQL, record_usage

    found = rows(session, f"""SELECT KNOWLEDGE_ID, KNOWLEDGE_TYPE, TITLE, CONTENT, CONTENT_JSON
                                FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                               WHERE DOMAIN_ID = ? AND IS_CURRENT AND COALESCE(STATUS, 'ACTIVE') = 'ACTIVE'
                                 AND KNOWLEDGE_TYPE IN ({', '.join(['?'] * len(types))}) AND {NOT_OPERATIONAL_SQL}
                               ORDER BY UPDATED_AT DESC NULLS LAST LIMIT {int(scan)}""", [domain_id, *types])
    picked = rank(found, target_table, columns, sources, limit, type_order)
    if run_id and stage and picked:
        record_usage(session, run_id, stage, [r["KNOWLEDGE_ID"] for r in picked])
    return picked
